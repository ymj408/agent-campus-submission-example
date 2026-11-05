"""Planner 协议与离线观察驱动策略。

离线 Mock 验证流程、预算/依赖/失败/续跑边界，不证明真实模型决策质量。
真实模型使用 llm.py 的 OpenAI-compatible 适配器。
"""
from __future__ import annotations

from typing import Any, Protocol

from .models import Action, PlanningTask, StepRecord


class Planner(Protocol):
    provider: str
    model: str

    def decide(self, task: PlanningTask, history: list[StepRecord]) -> Action:
        ...


def _mem(history: list[StepRecord]) -> dict[str, Any]:
    memory: dict[str, Any] = {
        "brief": None,
        "materials": None,
        "options": None,
        "estimates": {},
        "plan": None,
        "saved": None,
        "save_error": None,
        "save_args": None,
        "save_attempts": 0,
        "step_results": {},
        "failed_step": None,
        "errors": [],
    }
    for record in history:
        if record.kind != "tool" or not record.observation:
            continue
        tool = record.tool
        observation = record.observation
        if not observation.get("ok"):
            error = observation.get("error") or {}
            memory["errors"].append({"tool": tool, "error": error, "step": record.step})
            details = error.get("details") or {}
            if details.get("step_id"):
                memory["failed_step"] = details["step_id"]
            elif tool in {"generate_script", "execute_step"}:
                memory["failed_step"] = tool
            if tool == "save_plan":
                memory["save_error"] = error
                memory["save_attempts"] += 1
                memory["save_args"] = record.arguments
            continue
        data = observation.get("data") or {}
        if tool == "get_brief":
            memory["brief"] = data
        elif tool == "search_materials":
            memory["materials"] = data
        elif tool == "list_plan_options":
            memory["options"] = data.get("options") or []
        elif tool == "estimate_plan":
            plan_id = str(data.get("plan_id"))
            memory["estimates"][plan_id] = data
            memory["plan"] = data
            # 重新估算意味着开始新的方案尝试；旧的步骤失败不再阻塞。
            memory["failed_step"] = None
        elif tool in {"generate_script", "execute_step"}:
            if data.get("plan"):
                memory["plan"] = data["plan"]
            step = data.get("step") or {}
            if step.get("step_id"):
                memory["step_results"][step["step_id"]] = step
                if memory["failed_step"] == step["step_id"]:
                    memory["failed_step"] = None
        elif tool == "save_plan":
            memory["saved"] = data.get("plan")
            memory["save_error"] = None
            memory["save_attempts"] += 1
            memory["save_args"] = record.arguments
    return memory


def _failures(history: list[StepRecord], tool: str) -> int:
    return sum(
        1
        for record in history
        if record.kind == "tool"
        and record.tool == tool
        and record.observation
        and not record.observation.get("ok")
    )


class MockPlanner:
    provider = "mock"
    model = "deterministic-planning-policy-v1"

    def decide(self, task: PlanningTask, history: list[StepRecord]) -> Action:
        memory = _mem(history)
        brief = memory["brief"]
        materials = memory["materials"]
        options = memory["options"]
        estimates = memory["estimates"]
        plan = memory["plan"]
        saved = memory["saved"]
        save_error = memory["save_error"]

        if brief is None:
            last = self._last_error(history, "get_brief")
            if last and not last.get("retryable"):
                return Action.finish("tool_failure", rationale="任务读取失败且不可重试。")
            if _failures(history, "get_brief") >= 2:
                return Action.finish("tool_failure", rationale="任务读取连续失败。")
            return Action.tool_call(
                "get_brief",
                {"rationale": "先确认主题、预算、截止时间和是否已有方案。"},
                "先读取任务简报，判断信息是否完整。",
            )

        if brief.get("missing_fields"):
            return Action.finish(
                "blocked_missing_fields",
                summary="缺少预算或截止时间，不能形成可验证方案。",
                pending_items=[
                    "补充：" + "、".join(brief.get("missing_fields", []))
                ],
                rationale="关键约束缺失，不做猜测或伪造预算/截止时间。",
            )

        if brief.get("existing_plan"):
            return Action.finish(
                "plan_saved_deduplicated",
                rationale="已有同一任务的已保存方案，直接返回，避免重复执行。",
            )

        if saved:
            return Action.finish(
                "plan_saved",
                rationale="方案已保存，结束并报告费用与步骤状态。",
            )

        if save_error:
            if memory["save_args"] and memory["save_attempts"] < 2:
                return Action.tool_call(
                    "save_plan",
                    dict(memory["save_args"]),
                    "方案保存返回可重试错误，原参数重试一次；仍失败则明确报告。",
                )
            return Action.finish(
                "save_failed",
                summary=str(save_error.get("message") or "方案保存失败"),
                pending_items=["修复存储后重试；保存失败不能视为已有方案。"],
                rationale="保存失败且不可继续重试，明确报告失败，不虚报保存成功。",
            )

        if materials is None:
            last = self._last_error(history, "search_materials")
            if last and not last.get("retryable"):
                return Action.finish("incomplete_tool_failure", rationale="素材检索失败且不可重试。")
            if _failures(history, "search_materials") >= 2:
                return Action.finish("incomplete_tool_failure", rationale="素材检索连续失败。")
            return Action.tool_call(
                "search_materials",
                {
                    "query": task.theme,
                    "limit": 5,
                    "rationale": "围绕主题检索可用素材，授权过期素材应被排除。",
                },
                "先检索素材，确认是否有可用内容资产。",
            )

        if not materials.get("candidates"):
            return Action.finish(
                "incomplete_no_material",
                summary="没有检索到可用素材。",
                pending_items=["补充素材或放宽主题后重试。"],
                rationale="没有可用素材，无法负责任地制定方案。",
            )

        if options is None:
            last = self._last_error(history, "list_plan_options")
            if last and not last.get("retryable"):
                return Action.finish("incomplete_tool_failure", rationale="方案列表读取失败。")
            if _failures(history, "list_plan_options") >= 2:
                return Action.finish("incomplete_tool_failure", rationale="方案列表连续失败。")
            return Action.tool_call(
                "list_plan_options",
                {"rationale": "获取可选方案及其成本/耗时，准备预算比较。"},
                "检索完成后，列出可比较的方案模板。",
            )

        # 上一次步骤失败：优先找不含该步骤且同素材的替代方案。
        if memory.get("failed_step"):
            failed_step = self._failed_step(plan or {}, memory.get("failed_step"))
            fallback = self._choose_fallback(options, estimates, plan, failed_step)
            if fallback:
                return Action.tool_call(
                    "estimate_plan",
                    {
                        "plan_id": fallback["plan_id"],
                        "rationale": f"步骤 {failed_step} 失败，改选不含该步骤的 {fallback['plan_id']}。",
                    },
                    f"步骤 {failed_step} 失败；保留已成功结果，尝试替代方案。",
                )
            return Action.finish(
                "incomplete_tool_failure",
                summary=f"步骤 {failed_step} 失败，且没有可替代方案。",
                pending_items=[
                    f"步骤 {failed_step} 失败；请修复工具后用 --resume 续跑。",
                ],
                rationale="所有替代方案都依赖失败步骤或超预算，停止并保留成功结果。",
            )

        # 没有选中方案，或当前方案超预算/超时：尝试下一个方案。
        if plan is None or plan.get("within_budget") is not True or plan.get("deadline_ok") is False:
            next_plan = self._next_plan(options, estimates)
            if next_plan:
                return Action.tool_call(
                    "estimate_plan",
                    {
                        "plan_id": next_plan["plan_id"],
                        "rationale": "对下一个候选方案重新估算预算和截止时间。",
                    },
                    f"当前方案不可行，尝试 {next_plan['plan_id']}。",
                )
            if estimates and all(item.get("within_budget") is False for item in estimates.values()):
                return Action.finish(
                    "incomplete_no_feasible_plan",
                    pending_items=["请提高预算，或减少方案范围。"],
                    rationale="所有方案都超预算，返回不可行和缺口。",
                )
            if estimates and all(item.get("deadline_ok") is False for item in estimates.values()):
                return Action.finish(
                    "incomplete_deadline",
                    pending_items=["请放宽截止时间或减少步骤。"],
                    rationale="所有方案都无法在截止时间前完成。",
                )
            return Action.finish("incomplete", rationale="没有找到可行方案。")

        steps = plan.get("steps") or []
        pending = [step for step in steps if step.get("status") == "pending"]
        if pending:
            step = pending[0]
            if step.get("step_id") == "script":
                return Action.tool_call(
                    "generate_script",
                    {
                        "plan_id": plan["plan_id"],
                        "rationale": "方案预算与截止时间可行，先生成脚本，再执行依赖步骤。",
                    },
                    "方案已选中且预算内，生成脚本。",
                )
            return Action.tool_call(
                "execute_step",
                {
                    "plan_id": plan["plan_id"],
                    "step_id": step["step_id"],
                    "rationale": f"依赖已满足，执行下一步 {step['step_id']}。",
                },
                f"按依赖顺序执行 {step['step_id']}。",
            )

        if steps and all(step.get("status") == "succeeded" for step in steps):
            return Action.tool_call(
                "save_plan",
                {
                    "plan_id": plan["plan_id"],
                    "note": "预算内且全部步骤成功，保存方案。",
                    "rationale": "预算、截止时间和步骤状态均满足，保存方案。",
                },
                "全部步骤成功，保存方案。",
            )

        return Action.finish("incomplete", rationale="没有可继续执行的步骤，但步骤未全部成功。")

    @staticmethod
    def _last_error(history: list[StepRecord], tool: str) -> dict[str, Any] | None:
        for record in reversed(history):
            if record.kind == "tool" and record.tool == tool and record.observation:
                if not record.observation.get("ok"):
                    return record.observation.get("error") or {}
                return None
        return None

    @staticmethod
    def _failed_step(plan: dict[str, Any], hint: str | None) -> str:
        for step in plan.get("steps", []):
            if step.get("status") == "failed":
                return str(step.get("step_id"))
        return hint or "unknown"

    def _next_plan(
        self,
        options: list[dict[str, Any]],
        estimates: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        for option in options:
            if option["plan_id"] not in estimates:
                return option
        return None

    def _choose_fallback(
        self,
        options: list[dict[str, Any]],
        estimates: dict[str, dict[str, Any]],
        current: dict[str, Any],
        failed_step: str,
    ) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []
        for option in options:
            if option["plan_id"] == current.get("plan_id"):
                continue
            if failed_step in (option.get("step_ids") or []):
                continue
            if option.get("within_budget") is not True:
                continue
            candidates.append(option)
        if not candidates:
            return None
        # 同素材优先，保留脚本；其次成本更低。
        current_material = current.get("material_id")
        candidates.sort(
            key=lambda item: (
                0 if item.get("material_id") == current_material else 1,
                float(item.get("total", 0)),
            )
        )
        return candidates[0]

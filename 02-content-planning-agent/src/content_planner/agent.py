"""ReAct 执行循环与结果收口。

最终状态从工具事实（方案文件、预算/步骤状态、保存错误）推导；
模型 finish 的 status 只是 claim，不能把失败步骤或超预算方案写成成功。
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Action, AgentLimits, PlanningTask, StepRecord
from .planner import Planner
from .timeutil import CST
from .tools import ToolRuntime
from .trace import TraceRecorder

COMPLETED_STATUSES = {"plan_saved", "plan_saved_deduplicated"}
USER_ACTION_STATUSES = {
    "blocked_missing_fields",
    "incomplete_no_material",
    "incomplete_no_feasible_plan",
    "incomplete_tool_failure",
    "incomplete_deadline",
    "save_failed",
}
ALLOWED_FINISH = {
    "plan_saved",
    "plan_saved_deduplicated",
    "blocked_missing_fields",
    "incomplete_no_material",
    "incomplete_no_feasible_plan",
    "incomplete_tool_failure",
    "incomplete_deadline",
    "save_failed",
    "tool_failure",
    "incomplete",
}


class ContentPlanningAgent:
    def __init__(
        self,
        planner: Planner,
        tools: ToolRuntime,
        *,
        limits: AgentLimits | None = None,
        trace_path: str | Path | None = None,
    ):
        self.planner = planner
        self.tools = tools
        self.limits = limits or AgentLimits()
        self.trace_path = Path(trace_path).resolve() if trace_path else None

    @property
    def provider(self) -> str:
        return str(getattr(self.planner, "provider", "unknown"))

    @property
    def model(self) -> str:
        return str(getattr(self.planner, "model", "unknown"))

    def run(self, task: PlanningTask, *, trace_path: str | Path | None = None) -> dict[str, Any]:
        run_id = f"RUN-{datetime.now(CST).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        started = datetime.now(CST)
        start = time.perf_counter()
        path = Path(trace_path).resolve() if trace_path else self.trace_path
        trace = TraceRecorder(path)
        trace.run_start(
            run_id=run_id,
            task=task.to_dict(),
            provider=self.provider,
            model=self.model,
            limits=self.limits.to_dict(),
        )

        steps: list[StepRecord] = []
        history: list[StepRecord] = []
        tool_calls = 0
        finish_action: Action | None = None
        limit_reason: str | None = None
        error_message: str | None = None

        try:
            for step_no in range(1, self.limits.max_steps + 1):
                try:
                    action = self.planner.decide(task, history)
                except Exception as exc:  # noqa: BLE001
                    error_message = f"{exc.__class__.__name__}: {exc}"
                    trace.write({"event": "error", "phase": "planner", "message": error_message})
                    break

                if action.kind == "finish":
                    record = StepRecord(
                        step=step_no,
                        kind="finish",
                        tool=None,
                        arguments={},
                        rationale=action.rationale,
                        finish_claim=action.to_dict(),
                    )
                    steps.append(record)
                    finish_action = action
                    trace.step(record)
                    break

                if action.kind != "tool" or not action.tool:
                    error_message = f"Planner 返回不支持的动作：{action.kind!r}"
                    trace.write({"event": "error", "phase": "action", "message": error_message})
                    break

                if tool_calls >= self.limits.max_tool_calls:
                    limit_reason = "tool_limit"
                    trace.limit(limit_reason, len(steps), tool_calls)
                    break

                arguments = {
                    key: value for key, value in action.arguments.items() if key != "rationale"
                }
                observation = self.tools.call(action.tool, arguments)
                tool_calls += 1
                record = StepRecord(
                    step=step_no,
                    kind="tool",
                    tool=action.tool,
                    arguments=arguments,
                    rationale=action.rationale,
                    observation=observation,
                )
                steps.append(record)
                history.append(record)
                trace.step(record)
            else:
                limit_reason = "step_limit"
                trace.limit(limit_reason, len(steps), tool_calls)
        finally:
            result = self._finalize(
                task=task,
                run_id=run_id,
                steps=steps,
                finish_action=finish_action,
                limit_reason=limit_reason,
                error_message=error_message,
                started=started,
                start=start,
                tool_calls=tool_calls,
                trace_path=path,
            )
            trace.run_end(result)
            trace.close()
        return result

    def _finalize(
        self,
        *,
        task: PlanningTask,
        run_id: str,
        steps: list[StepRecord],
        finish_action: Action | None,
        limit_reason: str | None,
        error_message: str | None,
        started: datetime,
        start: float,
        tool_calls: int,
        trace_path: Path | None,
    ) -> dict[str, Any]:
        snapshot = self.tools.snapshot()
        brief = snapshot.get("brief")
        materials = snapshot.get("materials")
        current_plan = snapshot.get("current_plan")
        estimates = snapshot.get("estimates") or {}
        saved_plan = snapshot.get("saved_plan")
        save_error = snapshot.get("save_error")
        failure_history = snapshot.get("failure_history") or []
        step_results = snapshot.get("step_results") or {}
        tool_errors = snapshot.get("tool_errors") or []

        status = self._derive_status(
            task=task,
            brief=brief,
            materials=materials,
            estimates=estimates,
            current_plan=current_plan,
            saved_plan=saved_plan,
            save_error=save_error,
            finish_action=finish_action,
            limit_reason=limit_reason,
            error_message=error_message,
            deduplicated=bool(snapshot.get("last_save_deduplicated")),
            tool_errors=tool_errors,
        )
        completed = status in COMPLETED_STATUSES
        terminal = status not in {
            "step_limit_reached",
            "tool_limit_reached",
            "agent_error",
            "incomplete",
        }
        plan = saved_plan or current_plan
        plan_out = self._plan_out(plan, status, deduplicated=bool(snapshot.get("last_save_deduplicated")))
        plan_steps = list((plan or {}).get("steps") or [])
        successful = [
            {
                "step_id": step_id,
                "plan_id": result.get("plan_id"),
                "material_id": result.get("material_id"),
                "artifact_path": (result.get("result") or {}).get("artifact_path"),
                "cost": result.get("cost"),
            }
            for step_id, result in step_results.items()
            if result.get("status") == "succeeded"
        ]
        estimate = (plan or {}).get("estimate") or (plan if plan and "total" in plan else {})
        budget_limit = task.budget
        total = float(estimate.get("total", 0)) if estimate else 0.0
        budget_summary = {
            "limit": budget_limit,
            "total": round(total, 2),
            "remaining": round(budget_limit - total, 2) if budget_limit is not None else None,
            "within_budget": (total <= budget_limit) if budget_limit is not None else None,
            "over_by": round(max(0.0, total - budget_limit), 2) if budget_limit is not None else None,
        }
        pending_items = self._pending_items(
            status=status,
            task=task,
            brief=brief,
            estimates=estimates,
            current_plan=current_plan,
            save_error=save_error,
            failure_history=failure_history,
            step_results=step_results,
            tool_errors=tool_errors,
            limit_reason=limit_reason,
        )
        summary = self._summary(
            status=status,
            task=task,
            plan=plan,
            saved_plan=saved_plan,
            current_plan=current_plan,
            estimates=estimates,
            brief=brief,
            save_error=save_error,
            failure_history=failure_history,
            step_results=step_results,
            tool_errors=tool_errors,
            limit_reason=limit_reason,
            error_message=error_message,
        )
        finished = datetime.now(CST)
        result: dict[str, Any] = {
            "schema_version": 1,
            "run_id": run_id,
            "mode": "plan_and_save_only",
            "provider": self.provider,
            "model": self.model,
            "task": task.to_dict(),
            "status": status,
            "completed": completed,
            "terminal": terminal,
            "saved": completed,
            "needs_user_action": status in USER_ACTION_STATUSES,
            "summary": summary,
            "budget": budget_summary,
            "plan": plan_out,
            "plan_path": f"plans/{saved_plan['plan_id']}.json" if saved_plan else None,
            "steps": plan_steps,
            "successful_steps": successful,
            "failure_history": failure_history,
            "pending_items": pending_items,
            "finish_claim": (
                {
                    "status": finish_action.finish_status,
                    "summary": finish_action.finish_summary,
                    "pending_items": finish_action.pending_items,
                    "rationale": finish_action.rationale,
                }
                if finish_action
                else None
            ),
            "limits": self.limits.to_dict(),
            "usage": {
                "steps": len(steps),
                "tool_calls": tool_calls,
                "elapsed_ms": int((time.perf_counter() - start) * 1000),
            },
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": finished.isoformat(timespec="seconds"),
            "trace_path": str(trace_path) if trace_path else None,
            "step_records": [record.public_dict() for record in steps],
        }
        if limit_reason == "step_limit":
            result["limit"] = {"kind": "step_limit", "message": "达到最大步数，未完成"}
        elif limit_reason == "tool_limit":
            result["limit"] = {"kind": "tool_limit", "message": "达到最大工具调用次数，未完成"}
        if error_message:
            result["error"] = error_message
        return result

    def _plan_out(
        self,
        plan: dict[str, Any] | None,
        status: str,
        *,
        deduplicated: bool,
    ) -> dict[str, Any] | None:
        if not plan:
            return None
        output = dict(plan)
        output["saved"] = status in COMPLETED_STATUSES
        output["deduplicated"] = deduplicated
        return output

    def _derive_status(
        self,
        *,
        task: PlanningTask,
        brief: dict[str, Any] | None,
        materials: dict[str, Any] | None,
        estimates: dict[str, dict[str, Any]],
        current_plan: dict[str, Any] | None,
        saved_plan: dict[str, Any] | None,
        save_error: dict[str, Any] | None,
        finish_action: Action | None,
        limit_reason: str | None,
        error_message: str | None,
        deduplicated: bool,
        tool_errors: list[dict[str, Any]],
    ) -> str:
        if limit_reason == "step_limit":
            return "step_limit_reached"
        if limit_reason == "tool_limit":
            return "tool_limit_reached"
        if error_message:
            return "agent_error"
        if saved_plan:
            return "plan_saved_deduplicated" if deduplicated else "plan_saved"
        if save_error:
            return "save_failed"
        if brief and brief.get("missing_fields"):
            return "blocked_missing_fields"
        if materials is not None and not materials.get("candidates"):
            return "incomplete_no_material"
        if current_plan and any(step.get("status") == "failed" for step in current_plan.get("steps", [])):
            return "incomplete_tool_failure"
        if tool_errors and not current_plan and not estimates:
            return "incomplete_tool_failure"
        if estimates and all(item.get("within_budget") is False for item in estimates.values()):
            return "incomplete_no_feasible_plan"
        if current_plan and current_plan.get("deadline_ok") is False:
            return "incomplete_deadline"
        claim = finish_action.finish_status if finish_action else None
        if claim in ALLOWED_FINISH and claim not in COMPLETED_STATUSES:
            if claim == "incomplete_tool_failure" and current_plan:
                return claim
            if claim == "incomplete_no_feasible_plan" and estimates:
                return claim
            if claim == "incomplete_no_material" and materials is not None:
                return claim
            if claim == "blocked_missing_fields" and brief:
                return claim
            if claim == "incomplete_deadline":
                return claim
        return "incomplete"

    def _pending_items(
        self,
        *,
        status: str,
        task: PlanningTask,
        brief: dict[str, Any] | None,
        estimates: dict[str, dict[str, Any]],
        current_plan: dict[str, Any] | None,
        save_error: dict[str, Any] | None,
        failure_history: list[dict[str, Any]],
        step_results: dict[str, dict[str, Any]],
        tool_errors: list[dict[str, Any]],
        limit_reason: str | None,
    ) -> list[str]:
        items: list[str] = []
        missing = (brief or {}).get("missing_fields") or task.missing_fields
        if missing:
            labels = {"budget": "预算", "deadline": "截止时间"}
            items.append("请补充：" + "、".join(labels.get(name, name) for name in missing))
        if status == "incomplete_no_material":
            items.append("请放宽主题或补充可用素材后重试。")
        if status == "incomplete_no_feasible_plan" and estimates:
            cheapest = min(estimates.values(), key=lambda item: float(item.get("total", 0)))
            budget = task.budget
            gap = round(max(0.0, float(cheapest.get("total", 0)) - (budget or 0)), 2)
            items.append(
                f"最低可行方案 {cheapest.get('plan_id')} 需 {cheapest.get('total')}，"
                f"当前预算 {budget}，缺口 {gap}。"
            )
        if status == "incomplete_deadline":
            items.append("当前方案无法在截止时间前完成，请放宽截止时间或减少步骤。")
        failed = [
            item
            for item in failure_history
            if item.get("step_id") and not item.get("resolved_at")
        ]
        if failed and status not in COMPLETED_STATUSES:
            last = failed[-1]
            preserved = last.get("preserved_steps") or [
                key for key, value in step_results.items() if value.get("status") == "succeeded"
            ]
            items.append(
                f"步骤 {last.get('step_id')} 失败：{(last.get('error') or {}).get('message', '工具错误')}；"
                f"已保留成功结果：{', '.join(preserved) if preserved else '无'}。"
            )
            items.append("修复工具后可用同一 store 加 --resume 续跑失败步骤。")
        if status == "incomplete_tool_failure" and not failed and tool_errors:
            last_tool = tool_errors[-1]
            items.append(
                f"工具 {last_tool.get('tool')} 失败：{last_tool.get('message', '未知错误')}；"
                "需修复后重试。"
            )
        if save_error:
            items.append(f"方案保存失败：{save_error.get('message', '未知错误')}；需修复后重试。")
        if limit_reason:
            items.append("达到执行上限，未完成；不得把未执行步骤记为成功。")
        return list(dict.fromkeys(item for item in items if item))

    def _summary(
        self,
        *,
        status: str,
        task: PlanningTask,
        plan: dict[str, Any] | None,
        saved_plan: dict[str, Any] | None,
        current_plan: dict[str, Any] | None,
        estimates: dict[str, dict[str, Any]],
        brief: dict[str, Any] | None,
        save_error: dict[str, Any] | None,
        failure_history: list[dict[str, Any]],
        step_results: dict[str, dict[str, Any]],
        tool_errors: list[dict[str, Any]],
        limit_reason: str | None,
        error_message: str | None,
    ) -> str:
        if status == "step_limit_reached":
            return "达到最大步数限制，未完成；没有保存新方案。"
        if status == "tool_limit_reached":
            return "达到最大工具调用次数，未完成；没有保存新方案。"
        if status == "agent_error":
            return f"Agent 执行异常：{error_message or '未知错误'}；没有保存新方案。"
        if status in COMPLETED_STATUSES and saved_plan:
            total = (saved_plan.get("estimate") or {}).get("total")
            budget = (saved_plan.get("estimate") or {}).get("budget")
            if status == "plan_saved_deduplicated":
                return f"相同任务已有方案 {saved_plan.get('plan_id')}，未重复执行；总成本 {total}，预算 {budget}。"
            successful = [s.get("step_id") for s in saved_plan.get("steps", []) if s.get("status") == "succeeded"]
            text = (
                f"已保存方案 {saved_plan.get('plan_id')}：总成本 {total}，预算 {budget}；"
                f"步骤 {', '.join(successful)} 均成功。"
            )
            if failure_history:
                text += " 执行中发生过步骤失败，已通过替代方案或续跑完成，失败步骤未记为成功。"
            return text
        if status == "save_failed":
            message = (save_error or {}).get("message", "方案保存失败")
            return f"方案保存失败：{message}；步骤结果已保留，但没有已保存方案。"
        if status == "blocked_missing_fields":
            missing = (brief or {}).get("missing_fields") or task.missing_fields
            return f"未生成方案：缺少 {', '.join(missing)}；请补充后重试。"
        if status == "incomplete_no_material":
            return "未生成方案：没有检索到可用素材。"
        if status == "incomplete_no_feasible_plan" and estimates:
            cheapest = min(estimates.values(), key=lambda item: float(item.get("total", 0)))
            gap = round(max(0.0, float(cheapest.get("total", 0)) - (task.budget or 0)), 2)
            return (
                f"未生成方案：最低可行方案 {cheapest.get('plan_id')} 需 {cheapest.get('total')}，"
                f"预算 {task.budget}，缺口 {gap}。"
            )
        if status == "incomplete_tool_failure":
            failed = [item for item in failure_history if item.get("step_id")]
            step = failed[-1].get("step_id") if failed else "未知步骤"
            preserved = [key for key, value in step_results.items() if value.get("status") == "succeeded"]
            if not failed and tool_errors:
                last_tool = tool_errors[-1]
                return (
                    f"未保存方案：工具 {last_tool.get('tool')} 失败"
                    f"（{last_tool.get('message', '未知错误')}）；没有把未执行步骤记为成功。"
                )
            return (
                f"未保存方案：步骤 {step} 失败；已保留成功结果 "
                f"{', '.join(preserved) if preserved else '无'}，未把失败步骤记为成功。"
            )
        if status == "incomplete_deadline":
            return "未生成方案：所有候选方案都无法在截止时间前完成。"
        return "任务未完成：没有产生可验证的已保存方案。"


# 兼容更短的名字，便于测试/调用方使用。
PlannerAgent = ContentPlanningAgent

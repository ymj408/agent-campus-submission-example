"""Planner 接口与离线、可观察的决策策略。

离线 Mock 不是真实大模型：它按工具结果做条件决策，仅用于验证 ReAct 循环、
工具边界与失败处理。真实模型效果需使用 --provider openai 另行验证。
"""
from __future__ import annotations

from typing import Any, Protocol

from .models import Action, StepRecord, TaskRequest


class Planner(Protocol):
    provider: str
    model: str

    def decide(self, task: TaskRequest, history: list[StepRecord]) -> Action:
        ...


_FIELD_LABELS = {
    "title": "标题",
    "platform": "发布平台",
    "content_type": "内容类型",
    "duration_minutes": "时长（分钟）",
    "copyright": "版权状态",
    "publish_at": "发布时间",
    "summary": "简介",
}


def _mem(history: list[StepRecord]) -> dict[str, Any]:
    """从真实工具返回重建观察记忆，不读取工具内部状态。"""
    memory: dict[str, Any] = {
        "material": None,
        "audit": None,
        "slots": None,
        "confirmation": None,
        "draft": None,
        "save_error": None,
        "save_args": None,
        "save_attempts": 0,
        "lookup": None,
        "tool_errors": [],
    }
    for record in history:
        if record.kind != "tool" or not record.observation:
            continue
        tool = record.tool
        observation = record.observation
        if not observation.get("ok"):
            error = observation.get("error") or {}
            memory["tool_errors"].append({"tool": tool, "error": error, "step": record.step})
            if tool == "save_schedule_draft":
                memory["save_error"] = error
                memory["save_attempts"] += 1
                memory["save_args"] = record.arguments
            continue
        data = observation.get("data") or {}
        if tool == "get_material":
            memory["material"] = data
        elif tool == "lookup_material_source":
            memory["lookup"] = data
            if data.get("material"):
                memory["material"] = data["material"]
        elif tool == "check_rules":
            memory["audit"] = data
        elif tool == "query_slots":
            memory["slots"] = data
        elif tool == "request_confirmation":
            memory["confirmation"] = data
        elif tool == "save_schedule_draft":
            memory["draft"] = data.get("draft")
            memory["save_error"] = None
            memory["save_attempts"] += 1
            memory["save_args"] = record.arguments
    return memory


def _failures_for(history: list[StepRecord], tool: str) -> int:
    count = 0
    for record in history:
        if (
            record.kind == "tool"
            and record.tool == tool
            and record.observation
            and not record.observation.get("ok")
        ):
            count += 1
    return count


class MockPlanner:
    """离线观察驱动策略（不是大模型，结果不代表真实模型决策能力）。"""

    provider = "mock"
    model = "deterministic-observation-policy-v1"

    def decide(self, task: TaskRequest, history: list[StepRecord]) -> Action:
        memory = _mem(history)
        material = memory["material"]
        audit = memory["audit"]
        slots = memory["slots"]
        confirmation = memory["confirmation"]
        draft = memory["draft"]
        save_error = memory["save_error"]

        # 1. 读取素材；失败时最多重试一次。
        if material is None:
            failures = _failures_for(history, "get_material")
            last = self._last_error(history, "get_material")
            if last and last.get("code") == "material_not_found":
                return Action.finish(
                    "blocked_material_not_found",
                    rationale="工具明确返回素材不存在，无法继续审核或排期。",
                )
            if failures and (not last or not last.get("retryable") or failures >= 2):
                return Action.finish(
                    "tool_failure",
                    rationale="素材读取连续失败，停止并报告工具故障。",
                )
            return Action.tool_call(
                "get_material",
                {"material_id": task.material_id, "rationale": "先读取任务素材，确认字段是否完整。"},
                "先读取素材元数据，确认是否存在缺字段。",
            )

        # 2. 缺字段时先补查来源，而不是直接猜。
        if memory["lookup"] is None and material.get("missing_fields"):
            failures = _failures_for(history, "lookup_material_source")
            last = self._last_error(history, "lookup_material_source")
            if failures and (not last or not last.get("retryable") or failures >= 2):
                return Action.finish(
                    "tool_failure",
                    rationale="补充来源查询失败，无法确认缺失字段。",
                )
            return Action.tool_call(
                "lookup_material_source",
                {
                    "material_id": task.material_id,
                    "rationale": "素材字段不完整，先补查制作系统，避免凭空补全。",
                },
                "发现缺失字段，先补查来源；补不到再请求用户补充。",
            )

        # 3. 由规则引擎给出审核结论。
        if audit is None:
            failures = _failures_for(history, "check_rules")
            last = self._last_error(history, "check_rules")
            if failures and (not last or not last.get("retryable") or failures >= 2):
                return Action.finish(
                    "tool_failure",
                    rationale="规则检查工具连续失败，不能绕过审核。",
                )
            return Action.tool_call(
                "check_rules",
                {"material_id": task.material_id, "rationale": "按规则库做阻断/复核判定。"},
                "字段处理完毕后，执行规则审核，确认是否存在阻断项。",
            )

        # 4. 阻断项：缺资料则请求补充；规则风险直接阻断。
        if audit.get("decision") == "blocked":
            missing = list(audit.get("missing_fields") or [])
            if missing:
                if confirmation is None:
                    labels = "、".join(_FIELD_LABELS.get(name, name) for name in missing)
                    reasons = "；".join(audit.get("risk_reasons") or [])
                    return Action.tool_call(
                        "request_confirmation",
                        {
                            "material_id": task.material_id,
                            "reason": "missing_information",
                            "proposed_publish_at": None,
                            "questions": [f"请补充必填字段：{labels}。"],
                            "risk_summary": reasons or "素材信息不足，规则审核阻断。",
                            "rationale": "补查后仍缺必填字段，不能保存草稿，需用户补充。",
                        },
                        "规则阻断且缺资料，创建待确认项请求用户补充，不保存草稿。",
                    )
                return Action.finish(
                    "blocked_missing_info",
                    rationale="已记录资料补充请求，但用户尚未提供，不能继续保存草稿。",
                )
            reasons = "；".join(audit.get("risk_reasons") or [])
            return Action.finish(
                "blocked_rule_risk",
                summary=reasons,
                rationale="规则审核存在阻断项，直接停止，不进入排期。",
            )

        # 5. 查询请求时段与替代时段。
        if slots is None:
            failures = _failures_for(history, "query_slots")
            last = self._last_error(history, "query_slots")
            if failures and (not last or not last.get("retryable") or failures >= 2):
                return Action.finish(
                    "tool_failure",
                    rationale="时段查询连续失败，无法判断冲突。",
                )
            return Action.tool_call(
                "query_slots",
                {
                    "material_id": task.material_id,
                    "requested_publish_at": task.requested_publish_at,
                    "limit": 3,
                    "rationale": "核对请求时段是否被占用，并准备最近的替代时段。",
                },
                "审核通过后查询请求时段，避免在冲突时段保存草稿。",
            )

        # 6. 保存失败：可重试错误最多再试一次。
        if save_error is not None:
            retryable = bool(save_error.get("retryable"))
            attempts = int(memory["save_attempts"])
            if retryable and attempts < 2 and memory["save_args"]:
                return Action.tool_call(
                    "save_schedule_draft",
                    dict(memory["save_args"]),
                    "草稿保存返回可重试失败，原参数重试一次；仍失败则明确报告。",
                )
            return Action.finish(
                "save_failed",
                summary=str(save_error.get("message") or "草稿保存失败"),
                pending_items=["修复存储问题后重新提交；失败时不得视为已排期。"],
                rationale="保存失败且不可继续重试，明确报告失败，不虚报成功。",
            )

        # 7. 已有草稿：直接收口（重复请求会在这里返回同一草稿）。
        if draft:
            status = str(draft.get("status") or "draft_ready")
            finish_status = {
                "draft_pending_confirmation": "draft_saved_pending_confirmation",
                "draft_needs_review": "draft_saved_needs_review",
                "draft_ready": "draft_saved",
            }.get(status, "draft_saved")
            return Action.finish(
                finish_status,
                rationale="草稿保存结果已可观察，结束并报告草稿状态。",
            )

        requested = slots.get("requested") or {}
        requested_status = str(requested.get("status"))
        if requested_status == "available":
            return Action.tool_call(
                "save_schedule_draft",
                {
                    "material_id": task.material_id,
                    "proposed_publish_at": task.requested_publish_at,
                    "confirmation_id": None,
                    "note": "请求时段可用，按硬性要求保存草稿。",
                    "rationale": "请求时段可用，按原要求保存草稿。",
                },
                "请求时段可用，无需改时段，保存草稿。",
            )

        if not task.allow_time_change:
            return Action.finish(
                "blocked_time_change_not_allowed",
                summary=f"{requested.get('label', '')} 被占用，但用户要求不得修改发布时间。",
                pending_items=["需人工协调占用时段或由用户显式放宽时间要求。"],
                rationale="用户明确不允许改时间，不能自行选择替代时段。",
            )

        alternatives = list(slots.get("alternatives") or [])
        if not alternatives:
            return Action.finish(
                "blocked_no_available_slot",
                summary="请求时段不可用，且当前查询范围内没有替代时段。",
                pending_items=["由用户指定新的候选时段，或协调已有排期。"],
                rationale="无可用替代时段，不能保存冲突草稿。",
            )
        alternative = alternatives[0]
        if (
            confirmation is None
            or confirmation.get("proposed_publish_at") != alternative["at"]
            or confirmation.get("status") != "pending_user_confirmation"
        ):
            return Action.tool_call(
                "request_confirmation",
                {
                    "material_id": task.material_id,
                    "reason": "slot_deviation",
                    "proposed_publish_at": alternative["at"],
                    "questions": [
                        f"{requested.get('label')} 已被占用，是否同意草稿改到 {alternative['label']}？"
                    ],
                    "risk_summary": "请求时段冲突；替代时段只是建议，尚未获得用户同意。",
                    "rationale": "请求时段冲突，先创建待确认项，不能静默改时间。",
                },
                "请求时段冲突，先生成待确认项，用户同意前不保存偏离草稿。",
            )
        return Action.tool_call(
            "save_schedule_draft",
            {
                "material_id": task.material_id,
                "proposed_publish_at": alternative["at"],
                "confirmation_id": confirmation["confirmation_id"],
                "note": "替代时段，等待用户确认。",
                "rationale": "已有 pending 确认项，保存待确认草稿而不是直接发布。",
            },
            "已记录待确认项，保存带 pending 标记的替代时段草稿。",
        )

    @staticmethod
    def _last_error(history: list[StepRecord], tool: str) -> dict[str, Any] | None:
        for record in reversed(history):
            if record.kind == "tool" and record.tool == tool and record.observation:
                if not record.observation.get("ok"):
                    return record.observation.get("error") or {}
                return None
        return None

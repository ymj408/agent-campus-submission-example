"""ReAct 执行循环与权威结果收口。

循环：模型/策略选择动作 -> 执行工具 -> 观察结果 -> 再选择。
最终状态由工具侧事实（审核、草稿、错误）推导，不信任模型自报“已保存/已发布”。
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Action, AgentLimits, StepRecord, TaskRequest
from .planner import Planner
from .timeutil import CST
from .tools import ToolRuntime
from .trace import TraceRecorder

DRAFT_STATUS_MAP = {
    "draft_pending_confirmation": "draft_saved_pending_confirmation",
    "draft_needs_review": "draft_saved_needs_review",
    "draft_ready": "draft_saved",
}

COMPLETED_STATUSES = set(DRAFT_STATUS_MAP.values())
USER_ACTION_STATUSES = {
    "draft_saved_pending_confirmation",
    "draft_saved_needs_review",
    "blocked_missing_info",
    "blocked_rule_risk",
    "blocked_no_available_slot",
    "blocked_time_change_not_allowed",
    "blocked_material_not_found",
    "save_failed",
}
ALLOWED_FINISH_STATUSES = {
    "blocked_missing_info",
    "blocked_rule_risk",
    "blocked_no_available_slot",
    "blocked_time_change_not_allowed",
    "blocked_material_not_found",
    "save_failed",
    "tool_failure",
    "awaiting_user_confirmation",
    "incomplete",
    "draft_saved",
    "draft_saved_pending_confirmation",
    "draft_saved_needs_review",
}
FIELD_LABELS = {
    "title": "标题",
    "platform": "发布平台",
    "content_type": "内容类型",
    "duration_minutes": "时长（分钟）",
    "copyright": "版权状态",
    "publish_at": "发布时间",
}


class SchedulerAgent:
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

    def run(self, task: TaskRequest, *, trace_path: str | Path | None = None) -> dict[str, Any]:
        run_id = f"RUN-{datetime.now(CST).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        started = datetime.now(CST)
        start_monotonic = time.perf_counter()
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
        tool_calls_used = 0
        finish_action: Action | None = None
        limit_reason: str | None = None
        error_message: str | None = None

        try:
            for step_no in range(1, self.limits.max_steps + 1):
                try:
                    action = self.planner.decide(task, history)
                except Exception as exc:  # noqa: BLE001 - 模型/策略异常必须可观察
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
                    error_message = f"Planner 返回了不支持的动作：{action.kind!r}"
                    trace.write({"event": "error", "phase": "action", "message": error_message})
                    break

                if tool_calls_used >= self.limits.max_tool_calls:
                    limit_reason = "tool_limit"
                    trace.limit(limit_reason, len(steps), tool_calls_used)
                    break

                call_arguments = {
                    key: value
                    for key, value in action.arguments.items()
                    if key != "rationale"
                }
                observation = self.tools.call(action.tool, call_arguments)
                tool_calls_used += 1
                record = StepRecord(
                    step=step_no,
                    kind="tool",
                    tool=action.tool,
                    arguments=call_arguments,
                    rationale=action.rationale,
                    observation=observation,
                )
                steps.append(record)
                history.append(record)
                trace.step(record)
            else:
                limit_reason = "step_limit"
                trace.limit(limit_reason, len(steps), tool_calls_used)
        finally:
            result = self._finalize(
                task=task,
                run_id=run_id,
                steps=steps,
                finish_action=finish_action,
                limit_reason=limit_reason,
                error_message=error_message,
                started=started,
                start_monotonic=start_monotonic,
                tool_calls_used=tool_calls_used,
                trace_path=path,
            )
            trace.run_end(result)
            trace.close()
        return result

    # ------------------------------------------------------------ finalize
    def _finalize(
        self,
        *,
        task: TaskRequest,
        run_id: str,
        steps: list[StepRecord],
        finish_action: Action | None,
        limit_reason: str | None,
        error_message: str | None,
        started: datetime,
        start_monotonic: float,
        tool_calls_used: int,
        trace_path: Path | None,
    ) -> dict[str, Any]:
        snapshot = self.tools.snapshot()
        draft = snapshot.get("draft")
        audit = snapshot.get("audit")
        slots = snapshot.get("slots")
        confirmation = snapshot.get("confirmation")
        save_error = snapshot.get("save_error")

        status = self._derive_status(
            draft=draft,
            audit=audit,
            slots=slots,
            confirmation=confirmation,
            save_error=save_error,
            finish_action=finish_action,
            limit_reason=limit_reason,
            error_message=error_message,
            allow_time_change=task.allow_time_change,
        )
        completed = status in COMPLETED_STATUSES
        terminal = status not in {
            "step_limit_reached",
            "tool_limit_reached",
            "agent_error",
            "incomplete",
        }
        pending_items = self._pending_items(
            status=status,
            audit=audit,
            confirmation=confirmation,
            draft=draft,
            slots=slots,
            save_error=save_error,
            limit_reason=limit_reason,
        )
        summary = self._summary(
            status=status,
            task=task,
            draft=draft,
            audit=audit,
            slots=slots,
            confirmation=confirmation,
            save_error=save_error,
            limit_reason=limit_reason,
            error_message=error_message,
        )

        draft_result: dict[str, Any]
        if draft:
            draft_result = dict(draft)
            draft_result["saved"] = True
            draft_result["path"] = f"drafts/{draft.get('draft_id')}.json"
            draft_result["published"] = False
            draft_result["deduplicated"] = bool(snapshot.get("last_save_deduplicated"))
        else:
            draft_result = {"saved": False, "status": "not_saved", "published": False}
            if save_error:
                draft_result["error"] = save_error

        finished = datetime.now(CST)
        result: dict[str, Any] = {
            "schema_version": 1,
            "run_id": run_id,
            "mode": "draft_only",
            "published": False,
            "provider": self.provider,
            "model": self.model,
            "task": task.to_dict(),
            "status": status,
            "completed": completed,
            "terminal": terminal,
            "needs_user_action": status in USER_ACTION_STATUSES,
            "summary": summary,
            "audit": audit,
            "slot_query": self._slot_summary(slots),
            "confirmation": dict(confirmation) if confirmation else None,
            "draft": draft_result,
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
                "tool_calls": tool_calls_used,
                "elapsed_ms": int((time.perf_counter() - start_monotonic) * 1000),
            },
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": finished.isoformat(timespec="seconds"),
            "trace_path": str(trace_path) if trace_path else None,
            "steps": [record.public_dict() for record in steps],
        }
        if limit_reason == "step_limit":
            result["limit"] = {"kind": "step_limit", "message": "达到最大步数，未完成最终收口"}
        elif limit_reason == "tool_limit":
            result["limit"] = {"kind": "tool_limit", "message": "达到最大工具调用次数，未完成最终收口"}
        if error_message:
            result["error"] = error_message
        return result

    def _derive_status(
        self,
        *,
        draft: dict[str, Any] | None,
        audit: dict[str, Any] | None,
        slots: dict[str, Any] | None,
        confirmation: dict[str, Any] | None,
        save_error: dict[str, Any] | None,
        finish_action: Action | None,
        limit_reason: str | None,
        error_message: str | None,
        allow_time_change: bool,
    ) -> str:
        if limit_reason == "step_limit":
            return "step_limit_reached"
        if limit_reason == "tool_limit":
            return "tool_limit_reached"
        if error_message:
            return "agent_error"

        # 落盘草稿是最高优先级事实；模型 finish 的 status 不能覆盖它。
        if draft and draft.get("published") is False:
            return DRAFT_STATUS_MAP.get(str(draft.get("status")), "draft_saved")

        if save_error:
            return "save_failed"

        if audit and audit.get("decision") == "blocked":
            if audit.get("missing_fields"):
                return "blocked_missing_info"
            return "blocked_rule_risk"

        if slots:
            requested = slots.get("requested") or {}
            if requested.get("status") in {"occupied", "outside_window"}:
                if not allow_time_change:
                    return "blocked_time_change_not_allowed"
                if not slots.get("alternatives"):
                    return "blocked_no_available_slot"
                if confirmation and confirmation.get("status") == "pending_user_confirmation":
                    return "awaiting_user_confirmation"

        if confirmation and confirmation.get("status") == "pending_user_confirmation":
            return "awaiting_user_confirmation"

        claim = finish_action.finish_status if finish_action else None
        if claim in ALLOWED_FINISH_STATUSES and claim not in COMPLETED_STATUSES:
            # 只接受有独立事实支撑的阻断/失败类结论。
            if claim == "blocked_material_not_found":
                return claim
            if claim in {"tool_failure", "incomplete"}:
                return claim
            if claim == "blocked_missing_info" and audit and audit.get("missing_fields"):
                return claim
            if claim == "blocked_rule_risk" and audit and audit.get("decision") == "blocked":
                return claim
            if claim == "blocked_no_available_slot" and slots and not slots.get("alternatives"):
                return claim
            if claim == "blocked_time_change_not_allowed" and not allow_time_change:
                return claim
            if claim == "awaiting_user_confirmation" and confirmation:
                return claim
        return "incomplete"

    def _slot_summary(self, slots: dict[str, Any] | None) -> dict[str, Any] | None:
        if not slots:
            return None
        return {
            "platform": slots.get("platform"),
            "date": slots.get("date"),
            "timezone": slots.get("timezone"),
            "requested": slots.get("requested"),
            "alternatives": slots.get("alternatives"),
            "occupied_count": slots.get("occupied_count"),
            "occupied_sample": slots.get("occupied_sample"),
            "policy_window": slots.get("policy_window"),
        }

    def _pending_items(
        self,
        *,
        status: str,
        audit: dict[str, Any] | None,
        confirmation: dict[str, Any] | None,
        draft: dict[str, Any] | None,
        slots: dict[str, Any] | None,
        save_error: dict[str, Any] | None,
        limit_reason: str | None,
    ) -> list[str]:
        items: list[str] = []
        if audit:
            for violation in audit.get("violations", []):
                items.append(f"[{violation.get('rule_id')}] {violation.get('message')}")
            # 已有向用户提问的待确认项时，不再重复列字段清单。
            confirmation_pending = (
                confirmation and confirmation.get("status") == "pending_user_confirmation"
            )
            if not confirmation_pending:
                for field in audit.get("missing_fields", []):
                    items.append(f"补充必填字段：{FIELD_LABELS.get(field, field)}（{field}）")
        if confirmation and confirmation.get("status") == "pending_user_confirmation":
            items.extend(str(item) for item in confirmation.get("questions", []))
        if draft and draft.get("status") == "draft_pending_confirmation":
            items.append("确认是否接受建议时段；确认前草稿不进入发布流程。")
        if save_error:
            items.append(f"草稿保存失败：{save_error.get('message', '未知错误')}；需修复后重试。")
        if status == "blocked_no_available_slot" and slots:
            items.append("请用户指定新的候选时段，或人工协调已有排期。")
        if status == "blocked_time_change_not_allowed":
            items.append("需人工协调占用时段，或由用户显式放宽“不得改时间”的要求。")
        if status == "tool_failure":
            items.append("工具故障需修复后重试；本次未生成草稿。")
        if limit_reason:
            items.append("达到执行上限，任务未完成；不得视为已排期或已发布。")
        # 去重并保持稳定顺序。
        return list(dict.fromkeys(item for item in items if item))

    def _summary(
        self,
        *,
        status: str,
        task: TaskRequest,
        draft: dict[str, Any] | None,
        audit: dict[str, Any] | None,
        slots: dict[str, Any] | None,
        confirmation: dict[str, Any] | None,
        save_error: dict[str, Any] | None,
        limit_reason: str | None,
        error_message: str | None,
    ) -> str:
        requested_hhmm = task.requested_publish_at[11:16]
        if status == "step_limit_reached":
            suffix = (
                f"已落盘的草稿 {draft.get('draft_id')} 仍只是草稿。" if draft else "未保存草稿。"
            )
            return f"达到最大步数限制，未完成最终收口；{suffix}未发布。"
        if status == "tool_limit_reached":
            suffix = (
                f"已落盘的草稿 {draft.get('draft_id')} 仍只是草稿。" if draft else "未保存草稿。"
            )
            return f"达到最大工具调用次数，未完成最终收口；{suffix}未发布。"
        if status == "agent_error":
            return f"Agent 执行异常，未完成：{error_message or '未知错误'}；未发布。"
        if status in DRAFT_STATUS_MAP.values() and draft:
            proposed = str(draft.get("proposed_publish_at", ""))[11:16]
            if draft.get("status") == "draft_pending_confirmation":
                return (
                    f"已保存草稿 {draft.get('draft_id')}（未发布）：请求 {requested_hhmm} 存在冲突，"
                    f"建议 {proposed}，等待用户确认。"
                )
            if draft.get("status") == "draft_needs_review":
                reasons = "；".join((audit or {}).get("risk_reasons", [])[:2])
                return (
                    f"已保存草稿 {draft.get('draft_id')}（未发布）：{proposed}，"
                    f"需人工复核（{reasons or '存在规则警告'}）。"
                )
            return f"已保存草稿 {draft.get('draft_id')}（未发布）：{proposed}。"
        if status == "save_failed":
            message = (save_error or {}).get("message", "草稿保存失败")
            return f"草稿保存失败：{message}；未发布，不能视为已排期。"
        if status == "blocked_missing_info":
            missing = (audit or {}).get("missing_fields", [])
            labels = "、".join(FIELD_LABELS.get(name, name) for name in missing)
            return f"未保存草稿：素材信息不足，缺少 {labels or '必填字段'}；等待用户补充。"
        if status == "blocked_rule_risk":
            reasons = "；".join((audit or {}).get("risk_reasons", [])[:3])
            return f"未保存草稿：规则审核阻断（{reasons or '存在阻断项'}）。"
        if status == "blocked_no_available_slot":
            return f"未保存草稿：请求 {requested_hhmm} 不可用，且当前范围内无替代时段。"
        if status == "blocked_time_change_not_allowed":
            return (
                f"未保存草稿：请求 {requested_hhmm} 已被占用，用户明确要求不得改时间，"
                "需人工协调。"
            )
        if status == "blocked_material_not_found":
            return f"未保存草稿：素材 {task.material_id} 不存在。"
        if status == "awaiting_user_confirmation":
            return f"未保存草稿：存在待确认项 {confirmation.get('confirmation_id') if confirmation else ''}，等待用户确认。"
        if status == "tool_failure":
            return "未保存草稿：工具连续失败，任务中止。"
        return "任务未完成：没有产生可验证的审核/草稿结果；未发布。"

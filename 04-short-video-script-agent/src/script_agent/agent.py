"""ReAct 执行与最终脚本收口。

最终时长/规则状态由工具结果决定；模型 finish 的 script/duration 不直接采信。
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Action, AgentLimits, StepRecord, VideoTask
from .planner import Planner
from .timeutil import CST
from .tools import ToolRuntime
from .trace import TraceRecorder

COMPLETED_STATUSES = {"script_ready"}
USER_ACTION_STATUSES = {
    "blocked_missing_fields",
    "insufficient_material",
    "duration_unmet",
    "rule_blocked",
    "tool_failure",
}


class ScriptAgent:
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

    def run(self, task: VideoTask, *, trace_path: str | Path | None = None) -> dict[str, Any]:
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
        task: VideoTask,
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
        search = snapshot.get("search_result")
        estimate = snapshot.get("last_estimate")
        rule = snapshot.get("rule_check")
        estimates = snapshot.get("estimates") or []
        script = (rule or {}).get("script") or (estimate or {}).get("script") or snapshot.get("last_script")
        status = self._derive_status(
            brief=brief,
            search=search,
            estimate=estimate,
            rule=rule,
            search_failed=bool(snapshot.get("search_failed")),
            estimate_failed=bool(snapshot.get("estimate_failed")),
            check_failed=bool(snapshot.get("check_failed")),
            limit_reason=limit_reason,
            error_message=error_message,
        )
        duration = (rule or {}).get("duration") or estimate or {}
        total = duration.get("total_seconds")
        target = task.target_duration_seconds
        within = (total is not None and target is not None and total <= target)
        revisions = [
            {
                "draft_version": item.get("draft_version"),
                "total_seconds": item.get("total_seconds"),
                "target_seconds": item.get("target_seconds"),
                "within_target": item.get("within_target"),
            }
            for item in estimates
        ]
        sources = self._sources(script)
        risks = self._risks(status, rule, total, target)
        script_out = self._script_out(script, total, status, sources, risks)
        completed = status in COMPLETED_STATUSES
        terminal = status not in {
            "step_limit_reached",
            "tool_limit_reached",
            "agent_error",
            "incomplete",
        }
        pending = self._pending_items(
            status=status,
            brief=brief,
            search=search,
            estimate=estimate,
            rule=rule,
            total=total,
            target=target,
            limit_reason=limit_reason,
        )
        summary = self._summary(
            status=status,
            rule=rule,
            total=total,
            target=target,
            revisions=revisions,
            brief=brief,
            search=search,
            limit_reason=limit_reason,
            error_message=error_message,
        )
        finished = datetime.now(CST)
        result: dict[str, Any] = {
            "schema_version": 1,
            "run_id": run_id,
            "mode": "script_generation_with_checks",
            "provider": self.provider,
            "model": self.model,
            "task": task.to_dict(),
            "status": status,
            "completed": completed,
            "terminal": terminal,
            "needs_user_action": status in USER_ACTION_STATUSES,
            "summary": summary,
            "title": (script or {}).get("title"),
            "script": script_out,
            "storyboard": (script or {}).get("scenes") or [],
            "sources": sources,
            "risks": risks,
            "total_duration_seconds": total,
            "target_duration_seconds": target,
            "duration_check": {
                "target": target,
                "estimated": total,
                "within_target": within,
                "gap_seconds": round(total - target, 1) if total is not None and target is not None else None,
            },
            "rule_check": rule,
            "revisions": revisions,
            "draft_version": (script or {}).get("draft_version"),
            "missing_information": [
                f"缺少 {field}" for field in (brief or {}).get("missing_fields", [])
            ],
            "pending_items": pending,
            "model_claim": {
                "status": finish_action.finish_status if finish_action else None,
                "summary": finish_action.finish_summary if finish_action else None,
                "payload": finish_action.finish_payload if finish_action else {},
                "rationale": finish_action.rationale if finish_action else None,
            },
            "tool_errors": snapshot.get("tool_errors") or [],
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

    def _derive_status(
        self,
        *,
        brief: dict[str, Any] | None,
        search: dict[str, Any] | None,
        estimate: dict[str, Any] | None,
        rule: dict[str, Any] | None,
        search_failed: bool,
        estimate_failed: bool,
        check_failed: bool,
        limit_reason: str | None,
        error_message: str | None,
    ) -> str:
        if limit_reason == "step_limit":
            return "step_limit_reached"
        if limit_reason == "tool_limit":
            return "tool_limit_reached"
        if error_message:
            return "agent_error"
        if brief and brief.get("missing_fields"):
            return "blocked_missing_fields"
        if search_failed:
            return "tool_failure"
        if not search or not search.get("candidates"):
            return "insufficient_material"
        if estimate_failed or check_failed:
            return "tool_failure"
        if rule:
            if rule.get("decision") == "pass":
                duration = rule.get("duration") or {}
                target = duration.get("target_seconds")
                total = duration.get("total_seconds")
                if target is not None and total is not None and total <= target:
                    return "script_ready"
                return "duration_unmet"
            codes = {item.get("code") for item in rule.get("violations", [])}
            if codes and codes <= {"duration_exceeded", "target_out_of_range"}:
                return "duration_unmet"
            return "rule_blocked"
        if estimate:
            if estimate.get("within_target"):
                return "incomplete"
            return "duration_unmet"
        return "incomplete"

    def _sources(self, script: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not script:
            return []
        material_ids: set[str] = set()
        for scene in script.get("scenes", []):
            material_ids.update(str(item) for item in scene.get("material_ids", []))
            for claim in scene.get("claims", []):
                if claim.get("material_id"):
                    material_ids.add(str(claim["material_id"]))
        sources = []
        for material_id in sorted(material_ids):
            material = self.tools.kb.get(material_id)
            if material:
                sources.append(
                    {
                        "material_id": material_id,
                        "title": material.get("title"),
                        "license": material.get("license"),
                        "facts": list(material.get("facts", [])),
                    }
                )
        return sources

    def _risks(
        self,
        status: str,
        rule: dict[str, Any] | None,
        total: float | None,
        target: int | None,
    ) -> list[str]:
        risks: list[str] = []
        codes = {item.get("code") for item in (rule or {}).get("violations", [])}
        if rule:
            risks.extend(item["message"] for item in rule.get("violations", []))
        if (
            total is not None
            and target is not None
            and total > target
            and "duration_exceeded" not in codes
        ):
            risks.append(f"总时长 {total} 秒超过目标 {target} 秒")
        if status == "tool_failure":
            risks.append("工具失败，无法确认最终脚本")
        return list(dict.fromkeys(risks))

    def _script_out(
        self,
        script: dict[str, Any] | None,
        total: float | None,
        status: str,
        sources: list[dict[str, Any]],
        risks: list[str],
    ) -> dict[str, Any] | None:
        if not script:
            return None
        output = dict(script)
        output["total_duration_seconds"] = total
        output["ready"] = status == "script_ready"
        output["sources"] = sources
        output["risks"] = risks
        return output

    def _pending_items(
        self,
        *,
        status: str,
        brief: dict[str, Any] | None,
        search: dict[str, Any] | None,
        estimate: dict[str, Any] | None,
        rule: dict[str, Any] | None,
        total: float | None,
        target: int | None,
        limit_reason: str | None,
    ) -> list[str]:
        items: list[str] = []
        if status == "blocked_missing_fields":
            items.append("补充：" + "、".join((brief or {}).get("missing_fields", [])))
        if status == "insufficient_material":
            items.append("补充可用产品素材，或调整主题后重试。")
        if status == "duration_unmet":
            items.append(
                f"最短候选 {total} 秒仍超过目标 {target} 秒；需放宽时长或减少分镜。"
            )
        if status == "rule_blocked" and rule:
            items.extend(item["message"] for item in rule.get("violations", []))
        if status == "tool_failure":
            items.append("工具失败，未输出可确认的 ready 脚本。")
        if limit_reason:
            items.append("达到执行上限，未完成。")
        return list(dict.fromkeys(item for item in items if item))

    def _summary(
        self,
        *,
        status: str,
        rule: dict[str, Any] | None,
        total: float | None,
        target: int | None,
        revisions: list[dict[str, Any]],
        brief: dict[str, Any] | None,
        search: dict[str, Any] | None,
        limit_reason: str | None,
        error_message: str | None,
    ) -> str:
        if status == "step_limit_reached":
            return "达到最大步数限制，未完成；没有输出 ready 脚本。"
        if status == "tool_limit_reached":
            return "达到最大工具调用次数，未完成；没有输出 ready 脚本。"
        if status == "agent_error":
            return f"Agent 执行异常：{error_message or '未知错误'}；没有输出 ready 脚本。"
        if status == "script_ready":
            path = " → ".join(str(item.get("total_seconds")) for item in revisions)
            return (
                f"已生成 {total} 秒脚本（目标 {target} 秒；时长轨迹 {path}）；"
                f"规则检查通过，引用 P01。"
            )
        if status == "duration_unmet":
            return f"无法满足时长：最短候选 {total} 秒，目标 {target} 秒，未输出 ready 脚本。"
        if status == "rule_blocked":
            message = (rule or {}).get("violations", [{}])[0].get("message", "规则检查阻断")
            return f"规则检查未通过：{message}；未输出 ready 脚本。"
        if status == "blocked_missing_fields":
            return "未生成脚本：关键信息缺失（" + "、".join((brief or {}).get("missing_fields", [])) + "）。"
        if status == "insufficient_material":
            return "未生成脚本：没有检索到可用产品素材。"
        if status == "tool_failure":
            return "未生成脚本：工具失败，无法确认时长或规则。"
        return "任务未完成：没有产生可验证的脚本。"

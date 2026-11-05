"""ReAct 执行与权威回答收口。

最终回答不由模型自报直接决定：
- 冲突事实 → needs_confirmation，绝不选一个值；
- 回答引用 → 从 read_document 原文重建并校验；
- 无证据 → insufficient_evidence；
- 工具失败 → tool_failure，保留已检索/已读取结果。
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Action, AgentLimits, QATask, StepRecord
from .planner import Planner
from .timeutil import CST
from .tools import ToolRuntime
from .trace import TraceRecorder

COMPLETED_STATUSES = {"answered"}
USER_ACTION_STATUSES = {
    "needs_confirmation",
    "insufficient_evidence",
    "tool_failure",
}
ALLOWED_FINISH = {
    "answered",
    "needs_confirmation",
    "insufficient_evidence",
    "tool_failure",
    "incomplete",
}


class KnowledgeQAAgent:
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

    def run(self, task: QATask, *, trace_path: str | Path | None = None) -> dict[str, Any]:
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
        task: QATask,
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
        search = snapshot.get("search_result")
        reads = snapshot.get("reads") or {}
        failed_reads = snapshot.get("failed_reads") or {}
        search_failed = bool(snapshot.get("search_failed"))
        conflicts = snapshot.get("conflicts") or []
        evidence = snapshot.get("evidence") or []
        clarification = snapshot.get("clarification")
        tool_errors = snapshot.get("tool_errors") or []
        finish_payload = dict(finish_action.finish_payload) if finish_action else {}

        status = self._derive_status(
            search=search,
            evidence=evidence,
            conflicts=conflicts,
            search_failed=search_failed,
            failed_reads=failed_reads,
            limit_reason=limit_reason,
            error_message=error_message,
        )
        conflict = conflicts[0] if conflicts else None
        canonical = self._canonical_answer(status, evidence, conflict)
        citations = canonical["citations"]
        citation_check = self._citation_check(finish_payload, citations)
        completed = status in COMPLETED_STATUSES
        terminal = status not in {
            "step_limit_reached",
            "tool_limit_reached",
            "agent_error",
            "incomplete",
        }
        pending = self._pending_items(
            status=status,
            search=search,
            evidence=evidence,
            conflict=conflict,
            failed_reads=failed_reads,
            search_failed=search_failed,
            tool_errors=tool_errors,
            limit_reason=limit_reason,
        )
        missing_information = list(
            dict.fromkeys([*canonical.get("gaps", []), *pending])
        )
        summary = self._summary(
            status=status,
            canonical=canonical,
            search=search,
            evidence=evidence,
            conflict=conflict,
            failed_reads=failed_reads,
            limit_reason=limit_reason,
            error_message=error_message,
        )
        query = (search or {}).get("query")
        finished = datetime.now(CST)
        result: dict[str, Any] = {
            "schema_version": 1,
            "run_id": run_id,
            "mode": "retrieval_qa_with_citations",
            "provider": self.provider,
            "model": self.model,
            "task": task.to_dict(),
            "status": status,
            "completed": completed,
            "terminal": terminal,
            "needs_user_action": status in USER_ACTION_STATUSES,
            "answer": canonical["answer"],
            "answer_type": canonical["answer_type"],
            "answer_supported": status == "answered",
            "citations": citations,
            "citation_check": citation_check,
            "conflict": conflict,
            "follow_up_question": (
                conflict.get("question") if conflict else finish_payload.get("follow_up_question")
            ),
            "missing_information": missing_information,
            "pending_items": pending,
            "rewritten_query": query,
            "history_used": bool(query and query != task.question),
            "retrieved_doc_ids": [
                item.get("doc_id") for item in (search or {}).get("candidates", [])
            ],
            "read_doc_ids": sorted(reads.keys()),
            "search": self._search_summary(search),
            "preserved_evidence": evidence,
            "model_claim": {
                "status": finish_action.finish_status if finish_action else None,
                "summary": finish_action.finish_summary if finish_action else None,
                "payload": finish_payload,
                "rationale": finish_action.rationale if finish_action else None,
            },
            "tool_errors": tool_errors,
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
            "clarification": clarification,
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
        search: dict[str, Any] | None,
        evidence: list[dict[str, Any]],
        conflicts: list[dict[str, Any]],
        search_failed: bool,
        failed_reads: dict[str, Any],
        limit_reason: str | None,
        error_message: str | None,
    ) -> str:
        if limit_reason == "step_limit":
            return "step_limit_reached"
        if limit_reason == "tool_limit":
            return "tool_limit_reached"
        if error_message:
            return "agent_error"
        if conflicts:
            return "needs_confirmation"
        if search_failed or failed_reads:
            return "tool_failure"
        if not search or not search.get("candidates"):
            return "insufficient_evidence"
        if not evidence:
            return "insufficient_evidence"
        return "answered"

    def _canonical_answer(
        self,
        status: str,
        evidence: list[dict[str, Any]],
        conflict: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if status == "needs_confirmation" and conflict:
            value_parts = []
            citations = []
            for item in conflict["values"]:
                value_parts.append(
                    f"{item['doc_id']}《{item['title']}》v{item['version']}"
                    f"（适用 {'/'.join(item['scope_terms'])}）原文“{item['quote']}”"
                    f"对应 {item['value']} {item.get('unit') or ''}"
                )
                citations.append(
                    {
                        "doc_id": item["doc_id"],
                        "title": item["title"],
                        "version": item["version"],
                        "effective_date": item["effective_date"],
                        "scope_terms": item["scope_terms"],
                        "section_id": item["section_id"],
                        "quote": item["quote"],
                        "value": item["value"],
                        "unit": item.get("unit"),
                        "label": item.get("label"),
                    }
                )
            answer = (
                f"关于“{conflict['label']}”，现行制度存在冲突，不能擅自选择其中一个。"
                + "；".join(value_parts)
                + "。两份文档均为 active，且未提供适用范围优先级。"
                + f"请确认：{conflict['question']}"
            )
            return {"answer": answer, "answer_type": "conflict", "citations": citations, "gaps": [conflict["reason"]]}
        if status == "answered" and evidence:
            fact_key = evidence[0]["fact_key"]
            same = [item for item in evidence if item["fact_key"] == fact_key]
            first = same[0]
            answer = (
                f"根据 {first['doc_id']}《{first['title']}》v{first['version']}"
                f"（生效 {first['effective_date']}；适用 {'/'.join(first['scope_terms'])}），"
                f"{first['label']}为 {first['value']} {first.get('unit') or ''}。"
                f"原文：“{first['quote']}”"
            )
            if len(same) > 1:
                answer += f" 另有 {', '.join(item['doc_id'] for item in same[1:])} 给出相同值。"
            citations = [
                {
                    "doc_id": item["doc_id"],
                    "title": item["title"],
                    "version": item["version"],
                    "effective_date": item["effective_date"],
                    "scope_terms": item["scope_terms"],
                    "section_id": item["section_id"],
                    "quote": item["quote"],
                    "value": item["value"],
                    "unit": item.get("unit"),
                    "label": item.get("label"),
                }
                for item in same
            ]
            return {"answer": answer, "answer_type": "direct", "citations": citations, "gaps": []}
        return {"answer": None, "answer_type": "insufficient", "citations": [], "gaps": []}

    def _citation_check(
        self,
        finish_payload: dict[str, Any],
        canonical_citations: list[dict[str, Any]],
    ) -> dict[str, Any]:
        model_citations = finish_payload.get("citations") or []
        checked_model: list[dict[str, Any]] = []
        invalid: list[dict[str, Any]] = []
        for citation in model_citations:
            if not isinstance(citation, dict):
                invalid.append({"citation": citation, "reason": "citation 不是对象"})
                continue
            result = self.tools.validate_citation(
                str(citation.get("doc_id") or ""),
                str(citation.get("quote") or ""),
                citation.get("section_id"),
            )
            checked_model.append({"citation": citation, "validation": result})
            if not result.get("valid"):
                invalid.append({"citation": citation, "reason": result.get("reason")})
        canonical_checks = [
            self.tools.validate_citation(item["doc_id"], item["quote"], item.get("section_id"))
            for item in canonical_citations
        ]
        return {
            "model_citations_valid": bool(model_citations) and not invalid,
            "invalid_model_citations": invalid,
            "checked_model_citations": checked_model,
            "canonical_citations_valid": all(item.get("valid") for item in canonical_checks),
            "canonical_checks": canonical_checks,
        }

    def _pending_items(
        self,
        *,
        status: str,
        search: dict[str, Any] | None,
        evidence: list[dict[str, Any]],
        conflict: dict[str, Any] | None,
        failed_reads: dict[str, Any],
        search_failed: bool,
        tool_errors: list[dict[str, Any]],
        limit_reason: str | None,
    ) -> list[str]:
        items: list[str] = []
        if status == "needs_confirmation" and conflict:
            items.append(conflict["question"])
            items.append("在用户确认适用制度前，不能把任一取值当成最终答案。")
        if status == "insufficient_evidence":
            if not search or not search.get("candidates"):
                items.append("知识库没有匹配的 active 文档；请补充问题背景或确认知识范围。")
            else:
                items.append("已检索到文档，但没有读取到可引用的事实；请补充文档或换一种问法。")
        if status == "tool_failure":
            if search_failed:
                items.append("检索工具失败，无法继续查证。")
            if failed_reads:
                items.append(f"原文读取失败：{', '.join(sorted(failed_reads))}；已保留的检索结果不作为答案。")
            if not items and tool_errors:
                items.append(f"工具 {tool_errors[-1].get('tool')} 失败；需修复后重试。")
        if limit_reason:
            items.append("达到执行上限，未完成；不能无依据回答。")
        return list(dict.fromkeys(item for item in items if item))

    def _summary(
        self,
        *,
        status: str,
        canonical: dict[str, Any],
        search: dict[str, Any] | None,
        evidence: list[dict[str, Any]],
        conflict: dict[str, Any] | None,
        failed_reads: dict[str, Any],
        limit_reason: str | None,
        error_message: str | None,
    ) -> str:
        if status == "step_limit_reached":
            return "达到最大步数限制，未完成；没有给出无依据答案。"
        if status == "tool_limit_reached":
            return "达到最大工具调用次数，未完成；没有给出无依据答案。"
        if status == "agent_error":
            return f"Agent 执行异常：{error_message or '未知错误'}；没有给出无依据答案。"
        if status == "answered" and evidence:
            first = evidence[0]
            return f"已回答：{first['label']}为 {first['value']} {first.get('unit') or ''}（{first['doc_id']}），引用已校验。"
        if status == "needs_confirmation" and conflict:
            values = "、".join(
                f"{item['doc_id']} {item['value']}{item.get('unit') or ''}"
                for item in conflict["values"]
            )
            return f"未给出单一答案：{conflict['label']}存在冲突（{values}），已请求用户确认适用制度。"
        if status == "insufficient_evidence":
            if not search or not search.get("candidates"):
                return "无法回答：知识库没有匹配的 active 文档，未编造答案。"
            return "无法回答：检索到文档但未读取到可引用的事实。"
        if status == "tool_failure":
            if failed_reads:
                return f"未回答：读取 {', '.join(sorted(failed_reads))} 失败，无法核验引用。"
            return "未回答：检索工具失败，无法继续查证。"
        return "任务未完成：没有产生可验证的回答。"

    def _search_summary(self, search: dict[str, Any] | None) -> dict[str, Any] | None:
        if not search:
            return None
        return {
            "query": search.get("query"),
            "terms": search.get("terms"),
            "scope_hints": search.get("scope_hints"),
            "candidates": search.get("candidates"),
            "potential_conflicts": search.get("potential_conflicts"),
        }

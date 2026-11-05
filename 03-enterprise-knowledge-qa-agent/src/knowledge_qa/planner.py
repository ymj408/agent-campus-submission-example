"""Planner 协议与离线检索/冲突策略。

离线策略用于验证检索、原文读取、冲突、追问、上下文改写和工具失败；
不证明真实模型问答质量。
"""
from __future__ import annotations

import re
from typing import Any, Protocol

from .models import Action, QATask, StepRecord

SCOPES = ["总部", "华东", "华南", "华北", "全国"]
PRONOUNS = ("那", "呢", "这个", "上述", "它", "再问")


class Planner(Protocol):
    provider: str
    model: str

    def decide(self, task: QATask, history: list[StepRecord]) -> Action:
        ...


def _mem(history: list[StepRecord]) -> dict[str, Any]:
    memory: dict[str, Any] = {
        "search": None,
        "reads": {},
        "clarification": None,
        "errors": [],
        "query": None,
    }
    for record in history:
        if record.kind != "tool" or not record.observation:
            continue
        tool = record.tool
        observation = record.observation
        if not observation.get("ok"):
            memory["errors"].append({"tool": tool, "error": observation.get("error")})
            continue
        data = observation.get("data") or {}
        if tool == "search_documents":
            memory["search"] = data
            memory["query"] = data.get("query")
        elif tool == "read_document":
            memory["reads"][data.get("doc_id")] = data
        elif tool == "request_clarification":
            memory["clarification"] = data
    return memory


def _failure_count(history: list[StepRecord], tool: str) -> int:
    return sum(
        1
        for record in history
        if record.kind == "tool"
        and record.tool == tool
        and record.observation
        and not record.observation.get("ok")
    )


def rewrite_query(task: QATask) -> tuple[str, bool]:
    """离线上下文改写：只处理常见的范围切换/代词追问。"""
    question = task.question
    last = task.last_user_message
    if not last:
        return question, False
    new_scopes = [scope for scope in SCOPES if scope in question]
    old_scopes = [scope for scope in SCOPES if scope in last]
    if new_scopes and old_scopes:
        base = re.sub(r"(总部|华东|华南|华北|全国)区?", "", last).strip()
        return f"{new_scopes[0]} {base}", True
    if new_scopes and not old_scopes:
        return f"{new_scopes[0]} {last} {question}", True
    if any(pronoun in question for pronoun in PRONOUNS) and len(question) <= 12:
        return f"{last} {question}", True
    return question, False


def _citation_for(read: dict[str, Any], fact_key: str) -> dict[str, Any] | None:
    fact = (read.get("facts") or {}).get(fact_key)
    if not fact:
        return None
    section = next(
        (
            item
            for item in read.get("sections", [])
            if item.get("id") == fact.get("section_id")
        ),
        None,
    )
    if not section:
        return None
    return {
        "doc_id": read["doc_id"],
        "section_id": section["id"],
        "quote": section["text"],
    }


class MockPlanner:
    provider = "mock"
    model = "deterministic-retrieval-policy-v1"

    def decide(self, task: QATask, history: list[StepRecord]) -> Action:
        memory = _mem(history)
        search = memory["search"]
        reads = memory["reads"]

        if search is None:
            last = self._last_error(history, "search_documents")
            if last and not last.get("retryable"):
                return Action.finish("tool_failure", rationale="检索失败且不可重试。")
            if _failure_count(history, "search_documents") >= 2:
                return Action.finish("tool_failure", rationale="检索连续失败。")
            query, used = rewrite_query(task)
            return Action.tool_call(
                "search_documents",
                {
                    "query": query,
                    "top_k": 5,
                    "rationale": (
                        "结合上一轮问题改写范围后检索。"
                        if used
                        else "先用原文关键词检索 active 文档。"
                    ),
                },
                "检索制度/FAQ，优先确认是否存在同事实冲突。",
            )

        if memory["clarification"]:
            return Action.finish(
                "needs_confirmation",
                payload={
                    "answer": self._conflict_answer(memory["clarification"]),
                    "follow_up_question": memory["clarification"]["question"],
                },
                rationale="已创建待确认问题，不能替用户选择适用制度。",
            )

        candidates = list(search.get("candidates") or [])
        if not candidates:
            return Action.finish(
                "insufficient_evidence",
                summary="知识库没有匹配的 active 文档。",
                pending_items=["补充问题背景，或确认该主题是否在知识库范围内。"],
                rationale="没有检索到资料，不能无依据回答。",
            )

        # 每读一份文档就检查冲突；一旦发现，停止继续读取。
        conflicts: list[dict[str, Any]] = []
        for read in reads.values():
            for conflict in read.get("conflicts") or []:
                if conflict not in conflicts:
                    conflicts.append(conflict)
        if conflicts:
            conflict = conflicts[0]
            return Action.tool_call(
                "request_clarification",
                {
                    "question": conflict["question"],
                    "options": [
                        f"{item['doc_id']}（{('/'.join(item['scope_terms'])) or '未标注范围'}，"
                        f"{item['value']} {item.get('unit') or ''}）"
                        for item in conflict["values"]
                    ],
                    "rationale": "多个 active 文档给出不同事实，必须请求用户确认适用制度。",
                },
                "读取后发现冲突；先记录待确认问题，不做单一回答。",
            )

        unread = [item for item in candidates if item["doc_id"] not in reads]
        if unread:
            doc = unread[0]
            last = self._last_error(history, "read_document")
            if last and _failure_count(history, "read_document") >= 2:
                return Action.finish(
                    "tool_failure",
                    summary="原文读取失败，无法核验引用。",
                    rationale="读取原文连续失败，不能凭检索摘要回答。",
                )
            return Action.tool_call(
                "read_document",
                {
                    "doc_id": doc["doc_id"],
                    "rationale": "检索摘要不足以作为引用，读取原文并核验冲突。",
                },
                f"读取 {doc['doc_id']} 原文，检查事实和引用。",
            )

        # 唯一取值：构造带原文引用的回答；运行时还会再次校验。
        evidence: list[dict[str, Any]] = []
        for read in reads.values():
            if not read.get("is_active"):
                continue
            for key in (read.get("facts") or {}):
                citation = _citation_for(read, key)
                if citation:
                    evidence.append(
                        {
                            "fact_key": key,
                            "label": read["facts"][key].get("label"),
                            "value": read["facts"][key].get("value"),
                            "unit": read["facts"][key].get("unit"),
                            "citation": citation,
                            "read": read,
                        }
                    )
        if not evidence:
            return Action.finish(
                "insufficient_evidence",
                pending_items=["已检索到文档，但没有读取到可引用的事实。"],
                rationale="没有可引用事实，不能回答。",
            )
        fact_key = evidence[0]["fact_key"]
        same_fact = [item for item in evidence if item["fact_key"] == fact_key]
        first = same_fact[0]
        answer = (
            f"根据 {first['citation']['doc_id']}《{first['read']['title']}》"
            f"v{first['read']['version']}（生效 {first['read']['effective_date']}；"
            f"适用 {'/'.join(first['read']['scope_terms'])}），"
            f"{first['label']}为 {first['value']} {first.get('unit') or ''}。"
            f"原文：“{first['citation']['quote']}”"
        )
        return Action.finish(
            "answered",
            payload={
                "answer": answer,
                "citations": [item["citation"] for item in same_fact],
            },
            rationale="读取到的 active 文档对目标事实取值一致，基于原文回答。",
        )

    @staticmethod
    def _conflict_answer(clarification: dict[str, Any]) -> str:
        return (
            "当前知识库中的现行制度给出不同值，不能擅自选择其中一个。"
            f"{clarification['question']}"
        )

    @staticmethod
    def _last_error(history: list[StepRecord], tool: str) -> dict[str, Any] | None:
        for record in reversed(history):
            if record.kind == "tool" and record.tool == tool and record.observation:
                if not record.observation.get("ok"):
                    return record.observation.get("error") or {}
                return None
        return None

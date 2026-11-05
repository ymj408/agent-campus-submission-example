"""知识库检索、原文读取、冲突检测与追问工具。

约束：
- 搜索只返回 active 文档；superseded 文档不参与检索与冲突。
- 引用必须来自 read_document 返回的 sections 原文。
- 同一事实在多个 active 文档中取值不同，且没有范围优先级时，运行时收口为冲突。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

from .knowledge import KnowledgeBase
from .models import QATask

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]

RATIONALE_SCHEMA = {
    "type": "string",
    "description": "一句话说明为什么现在做这个动作，不要写思维链。",
}

SCOPE_TERMS = ["总部", "华东", "华南", "华北", "全国"]
KEYWORDS = [
    "报销",
    "差旅",
    "出差",
    "期限",
    "多久",
    "年假",
    "休假",
    "退款",
    "密码",
    "食堂",
    "产品",
    "制度",
    "政策",
]
SYNONYMS = {
    "出差": ["差旅"],
    "报销": ["差旅", "费用"],
    "期限": ["天", "截止"],
    "多久": ["期限", "天"],
    "年假": ["休假"],
    "退款": ["退货"],
    "签收": ["退款"],
}


def _schema(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    props = dict(properties)
    props["rationale"] = RATIONALE_SCHEMA
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


def tool_specs() -> list[dict[str, Any]]:
    return [
        _schema(
            "search_documents",
            "检索 active 制度/FAQ，返回文档 ID、版本、适用范围、匹配片段和潜在冲突；不返回过期版本。",
            {
                "query": {"type": "string", "description": "检索问题/改写后的查询"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            ["query"],
        ),
        _schema(
            "read_document",
            "读取文档/章节原文，返回精确 quote、事实和当前冲突；引用必须来自这里。",
            {
                "doc_id": {"type": "string", "description": "文档 ID，如 D01"},
                "section_id": {"type": "string", "description": "可选章节 ID"},
            },
            ["doc_id"],
        ),
        _schema(
            "request_clarification",
            "存在冲突或范围不明确时，创建待用户确认的问题；不代表已经回答。",
            {
                "question": {"type": "string", "description": "要问用户的具体问题"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 6,
                    "description": "可选适用制度/范围",
                },
            },
            ["question", "options"],
        ),
    ]


def _ok(tool: str, data: dict[str, Any], summary: str) -> dict[str, Any]:
    return {"ok": True, "tool": tool, "summary": summary, "data": data}


def _err(
    tool: str,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message, "retryable": retryable}
    if details:
        error["details"] = details
    return {"ok": False, "tool": tool, "summary": message, "error": error}


def _hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ToolRuntime:
    def __init__(
        self,
        task: QATask,
        kb: KnowledgeBase,
        *,
        fail_tools: dict[str, int] | None = None,
    ):
        self.task = task
        self.kb = kb
        self.fail_counts = {str(key): int(value) for key, value in (fail_tools or {}).items()}
        self.query: str | None = None
        self.search_result: dict[str, Any] | None = None
        self.search_failed = False
        self.reads: dict[str, dict[str, Any]] = {}
        self.failed_reads: dict[str, dict[str, Any]] = {}
        self.clarification: dict[str, Any] | None = None
        self.clarification_seq = 0
        self.tool_errors: list[dict[str, Any]] = []
        self._handlers: dict[str, ToolHandler] = {
            "search_documents": self._search_documents,
            "read_document": self._read_document,
            "request_clarification": self._request_clarification,
        }

    # ------------------------------------------------------------------ API
    def call(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        args = dict(arguments or {})
        args.pop("rationale", None)
        handler = self._handlers.get(name)
        if handler is None:
            return _err(name or "unknown", "unknown_tool", f"未知工具：{name}")
        try:
            result = handler(args)
        except (KeyError, TypeError, ValueError) as exc:
            result = _err(name, "invalid_arguments", f"工具参数不合法：{exc}")
        except LookupError as exc:
            result = _err(name, "not_found", str(exc))
        except Exception as exc:  # noqa: BLE001
            result = _err(name, "internal_tool_error", f"工具内部错误：{exc.__class__.__name__}")
        if not result.get("ok"):
            self.tool_errors.append(
                {
                    "tool": name,
                    "code": (result.get("error") or {}).get("code"),
                    "message": result.get("summary", ""),
                }
            )
        return result

    def snapshot(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "search_result": self.search_result,
            "search_failed": self.search_failed,
            "reads": self.reads,
            "failed_reads": self.failed_reads,
            "clarification": self.clarification,
            "tool_errors": self.tool_errors,
            "conflicts": self.conflicts(),
            "evidence": self.evidence(),
        }

    def _maybe_fail(self, tool: str, key: str) -> dict[str, Any] | None:
        remaining = int(self.fail_counts.get(key, 0))
        if remaining == 0:
            return None
        if remaining > 0:
            self.fail_counts[key] = remaining - 1
        return _err(
            tool,
            "tool_failed",
            f"注入的工具故障：{key} 执行失败",
            retryable=True,
            details={"failed_key": key},
        )

    # --------------------------------------------------------------- search
    @staticmethod
    def _terms(query: str) -> list[str]:
        terms: set[str] = set()
        for keyword in KEYWORDS:
            if keyword in query:
                terms.add(keyword)
        for source, expansions in SYNONYMS.items():
            if source in query:
                terms.update(expansions)
        for token in re.findall(r"[\u4e00-\u9fff]{2,6}", query):
            terms.add(token)
        return sorted(terms)

    def _search_documents(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query 不能为空")
        top_k = int(args.get("top_k", 5))
        if not 1 <= top_k <= 10:
            raise ValueError("top_k 必须在 1-10 之间")
        error = self._maybe_fail("search_documents", "search_documents")
        if error:
            self.search_failed = True
            return error

        terms = self._terms(query)
        scope_hints = [scope for scope in SCOPE_TERMS if scope in query]
        active_docs = {
            doc_id: doc
            for doc_id, doc in self.kb.documents.items()
            if doc.get("status") == "active"
        }
        allowed_ids = set(active_docs)
        if scope_hints:
            # 具体范围文档优先，但全国性制度仍可能适用；低分项由 score 阈值过滤。
            scoped = {
                doc_id
                for doc_id, doc in active_docs.items()
                if any(scope in doc.get("scope_terms", []) for scope in scope_hints)
            }
            nationwide = {
                doc_id
                for doc_id, doc in active_docs.items()
                if "全国" in doc.get("scope_terms", [])
            }
            allowed_ids = scoped | nationwide
        candidates: list[dict[str, Any]] = []
        for doc_id, doc in active_docs.items():
            if doc_id not in allowed_ids:
                continue
            title = str(doc.get("title") or "")
            summary = str(doc.get("summary") or "")
            faq = [str(item) for item in doc.get("faq_questions", [])]
            sections = doc.get("sections", [])
            facts = doc.get("facts") or {}
            matched: list[str] = []
            score = 0
            for term in terms:
                hit = False
                if term in title:
                    score += 3
                    hit = True
                if any(term in question for question in faq):
                    score += 3
                    hit = True
                if term in summary:
                    score += 2
                    hit = True
                if any(term in str(section.get("text", "")) for section in sections):
                    score += 1
                    hit = True
                if any(term in str(fact.get("label", "")) for fact in facts.values()):
                    score += 1
                    hit = True
                if hit:
                    matched.append(term)
            if score <= 0:
                continue
            candidates.append(
                {
                    "doc_id": doc_id,
                    "title": title,
                    "version": doc.get("version"),
                    "effective_date": doc.get("effective_date"),
                    "scope_terms": list(doc.get("scope_terms", [])),
                    "summary": summary,
                    "faq_questions": faq,
                    "score": score,
                    "matched_terms": matched,
                    "facts_preview": {
                        key: {
                            "label": fact.get("label"),
                            "value": fact.get("value"),
                            "unit": fact.get("unit"),
                        }
                        for key, fact in facts.items()
                    },
                }
            )
        candidates.sort(
            key=lambda item: (-int(item["score"]), str(item.get("effective_date", "")), item["doc_id"])
        )
        if candidates:
            best_score = int(candidates[0]["score"])
            candidates = [item for item in candidates if int(item["score"]) >= best_score - 1]
        candidates = candidates[:top_k]

        potential_conflicts: list[dict[str, Any]] = []
        by_fact: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            for key, fact in candidate["facts_preview"].items():
                by_fact.setdefault(key, []).append(
                    {
                        "doc_id": candidate["doc_id"],
                        "label": fact.get("label"),
                        "value": fact.get("value"),
                        "unit": fact.get("unit"),
                        "scope_terms": candidate["scope_terms"],
                    }
                )
        for key, items in by_fact.items():
            values = {json.dumps(item["value"], ensure_ascii=False) for item in items}
            if len(values) > 1:
                potential_conflicts.append(
                    {
                        "fact_key": key,
                        "label": items[0].get("label"),
                        "values": items,
                    }
                )

        data = {
            "query": query,
            "terms": terms,
            "scope_hints": scope_hints,
            "candidates": candidates,
            "count": len(candidates),
            "potential_conflicts": potential_conflicts,
        }
        self.query = query
        self.search_result = data
        self.search_failed = False
        if not candidates:
            summary = f"没有检索到与「{query}」匹配的 active 文档"
        elif potential_conflicts:
            summary = (
                f"检索到 {len(candidates)} 个 active 文档；"
                f"发现同一事实的候选冲突，需要读取原文核验"
            )
        else:
            summary = f"检索到 {len(candidates)} 个 active 文档，首条 {candidates[0]['doc_id']}"
        return _ok("search_documents", data, summary)

    # ----------------------------------------------------------------- read
    def _read_document(self, args: dict[str, Any]) -> dict[str, Any]:
        doc_id = str(args.get("doc_id") or "").strip()
        section_id = str(args.get("section_id") or "").strip() or None
        if not doc_id:
            raise ValueError("doc_id 不能为空")
        error = self._maybe_fail("read_document", "read_document")
        if error:
            self.failed_reads[doc_id] = error["error"]
            return error
        doc = self.kb.get(doc_id)
        if doc is None:
            raise LookupError(f"文档不存在：{doc_id}")
        sections = doc.get("sections", [])
        selected = None
        if section_id:
            selected = next((section for section in sections if section.get("id") == section_id), None)
            if selected is None:
                raise LookupError(f"章节不存在：{doc_id}/{section_id}")
        else:
            facts = doc.get("facts") or {}
            preferred = next(iter(facts.values()), {}).get("section_id")
            selected = next(
                (section for section in sections if section.get("id") == preferred),
                sections[0] if sections else None,
            )
        if selected is None:
            raise ValueError(f"文档没有可读章节：{doc_id}")

        record = {
            "doc_id": doc_id,
            "title": doc.get("title"),
            "version": doc.get("version"),
            "effective_date": doc.get("effective_date"),
            "status": doc.get("status"),
            "scope_terms": list(doc.get("scope_terms", [])),
            "summary": doc.get("summary"),
            "facts": doc.get("facts") or {},
            "sections": sections,
            "selected_section": selected,
            "quote": selected["text"],
            "is_active": doc.get("status") == "active",
        }
        self.reads[doc_id] = record
        self.failed_reads.pop(doc_id, None)
        data = dict(record)
        data["conflicts"] = self.conflicts()
        summary = (
            f"读取 {doc_id} v{doc.get('version')} 章节「{selected.get('title')}」；"
            f"quote 长度 {len(selected['text'])} 字符"
        )
        return _ok("read_document", data, summary)

    # --------------------------------------------------------- conflict/gaps
    def conflicts(self) -> list[dict[str, Any]]:
        active = [record for record in self.reads.values() if record.get("is_active")]
        by_fact: dict[str, list[dict[str, Any]]] = {}
        for record in active:
            for key, fact in record.get("facts", {}).items():
                section = next(
                    (
                        section
                        for section in record.get("sections", [])
                        if section.get("id") == fact.get("section_id")
                    ),
                    None,
                )
                by_fact.setdefault(key, []).append(
                    {
                        "doc_id": record["doc_id"],
                        "title": record.get("title"),
                        "version": record.get("version"),
                        "effective_date": record.get("effective_date"),
                        "scope_terms": list(record.get("scope_terms", [])),
                        "label": fact.get("label"),
                        "value": fact.get("value"),
                        "unit": fact.get("unit"),
                        "section_id": fact.get("section_id"),
                        "quote": section.get("text") if section else record.get("quote"),
                    }
                )
        scope_hints = (self.search_result or {}).get("scope_hints") or []
        conflicts: list[dict[str, Any]] = []
        for key, items in by_fact.items():
            if len({json.dumps(item["value"], ensure_ascii=False) for item in items}) <= 1:
                continue
            filtered = items
            if len(scope_hints) == 1:
                hint = scope_hints[0]
                scoped = [
                    item
                    for item in items
                    if hint in item["scope_terms"] or "全国" in item["scope_terms"]
                ]
                if scoped:
                    filtered = scoped
            if len({json.dumps(item["value"], ensure_ascii=False) for item in filtered}) <= 1:
                continue
            label = filtered[0].get("label") or key
            options = [
                f"{item['doc_id']}（{('/'.join(item['scope_terms'])) or '未标注范围'}，"
                f"{item['value']} {item.get('unit') or ''}）"
                for item in filtered
            ]
            question = (
                f"关于「{label}」，现行文档给出不同值。本次问题应适用以下哪份制度？"
                f"可选：{'；'.join(options)}"
            )
            conflicts.append(
                {
                    "fact_key": key,
                    "label": label,
                    "scope_hints": scope_hints,
                    "values": filtered,
                    "reason": "多份 active 文档对同一事实给出不同值，且未提供明确优先级",
                    "question": question,
                }
            )
        return conflicts

    def evidence(self) -> list[dict[str, Any]]:
        evidence: list[dict[str, Any]] = []
        for record in sorted(self.reads.values(), key=lambda item: item["doc_id"]):
            if not record.get("is_active"):
                continue
            for key, fact in record.get("facts", {}).items():
                section = next(
                    (
                        section
                        for section in record.get("sections", [])
                        if section.get("id") == fact.get("section_id")
                    ),
                    None,
                )
                evidence.append(
                    {
                        "doc_id": record["doc_id"],
                        "title": record.get("title"),
                        "version": record.get("version"),
                        "effective_date": record.get("effective_date"),
                        "scope_terms": list(record.get("scope_terms", [])),
                        "fact_key": key,
                        "label": fact.get("label"),
                        "value": fact.get("value"),
                        "unit": fact.get("unit"),
                        "section_id": fact.get("section_id"),
                        "section_title": section.get("title") if section else None,
                        "quote": section.get("text") if section else record.get("quote"),
                    }
                )
        return evidence

    def validate_citation(self, doc_id: str, quote: str, section_id: str | None = None) -> dict[str, Any]:
        record = self.reads.get(doc_id)
        if record is None:
            return {"valid": False, "reason": "文档未被读取"}
        if not record.get("is_active"):
            return {"valid": False, "reason": "文档已过期/被替代"}
        quote = str(quote or "").strip()
        if not quote:
            return {"valid": False, "reason": "引用为空"}
        for section in record.get("sections", []):
            if section_id and section.get("id") != section_id:
                continue
            if quote in str(section.get("text") or ""):
                return {
                    "valid": True,
                    "doc_id": doc_id,
                    "section_id": section.get("id"),
                    "quote": quote,
                }
        return {"valid": False, "reason": "引用不是已读取原文的精确子串"}

    # --------------------------------------------------------- clarification
    def _request_clarification(self, args: dict[str, Any]) -> dict[str, Any]:
        question = str(args.get("question") or "").strip()
        options = [str(item).strip() for item in args.get("options", []) if str(item).strip()]
        if not question:
            raise ValueError("question 不能为空")
        if not options:
            raise ValueError("options 至少需要一项")
        fingerprint = _hash({"question": question, "options": options})
        if self.clarification and self.clarification.get("fingerprint") == fingerprint:
            return _ok(
                "request_clarification",
                dict(self.clarification, deduplicated=True),
                "相同追问已存在，直接复用",
            )
        self.clarification_seq += 1
        data = {
            "clarification_id": f"CLAR-{self.clarification_seq:04d}",
            "fingerprint": fingerprint,
            "question": question,
            "options": options,
            "status": "pending_user_confirmation",
            "approved": False,
        }
        self.clarification = data
        return _ok(
            "request_clarification",
            data,
            f"已创建待确认问题 {data['clarification_id']}；当前仍不能给出单一答案",
        )

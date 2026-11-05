"""知识库加载与校验。"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


class KnowledgeBase:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir).resolve()
        path = self.data_dir / "documents.json"
        if not path.exists():
            raise FileNotFoundError(f"知识库文件不存在：{path}")
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        documents = payload.get("documents")
        if not isinstance(documents, dict) or not documents:
            raise ValueError("documents.json 必须包含非空 documents 对象")
        for doc_id, doc in documents.items():
            if not isinstance(doc, dict):
                raise ValueError(f"文档 {doc_id} 必须是对象")
            if str(doc.get("doc_id", doc_id)) != doc_id:
                raise ValueError(f"文档 id 与键不一致：{doc_id}")
            if doc.get("status") not in {"active", "superseded"}:
                raise ValueError(f"文档 {doc_id} 的 status 不合法")
            section_ids: set[str] = set()
            for section in doc.get("sections", []):
                section_id = str(section.get("id") or "")
                if not section_id or section_id in section_ids:
                    raise ValueError(f"文档 {doc_id} 的 section id 缺失或重复")
                if not str(section.get("text") or "").strip():
                    raise ValueError(f"文档 {doc_id} 的 section {section_id} 缺少正文")
                section_ids.add(section_id)
            for key, fact in (doc.get("facts") or {}).items():
                if fact.get("section_id") not in section_ids:
                    raise ValueError(f"文档 {doc_id} 的事实 {key} 引用了不存在的 section")
        self.payload = payload
        self._documents = {doc_id: copy.deepcopy(doc) for doc_id, doc in documents.items()}

    @property
    def documents(self) -> dict[str, dict[str, Any]]:
        return self._documents

    @property
    def active_documents(self) -> dict[str, dict[str, Any]]:
        return {
            doc_id: doc
            for doc_id, doc in self._documents.items()
            if doc.get("status") == "active"
        }

    def get(self, doc_id: str) -> dict[str, Any] | None:
        doc = self._documents.get(doc_id)
        return copy.deepcopy(doc) if doc else None

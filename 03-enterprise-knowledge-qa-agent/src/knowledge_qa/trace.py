"""JSONL Trace：动作、参数、观察摘要与一句决策依据。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import StepRecord


class TraceRecorder:
    def __init__(self, path: str | Path | None):
        self.path = Path(path).resolve() if path else None
        self._handle = None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("w", encoding="utf-8", newline="\n")

    def write(self, event: dict[str, Any]) -> None:
        if self._handle is None:
            return
        self._handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._handle.flush()

    def run_start(
        self,
        *,
        run_id: str,
        task: dict[str, Any],
        provider: str,
        model: str,
        limits: dict[str, int],
    ) -> None:
        self.write(
            {
                "event": "run_start",
                "run_id": run_id,
                "provider": provider,
                "model": model,
                "limits": limits,
                "task": task,
                "mode": "retrieval_qa_with_citations",
            }
        )

    def step(self, record: StepRecord) -> None:
        self.write({"event": "step", **record.trace_dict()})

    def limit(self, kind: str, steps_used: int, tool_calls_used: int) -> None:
        self.write(
            {
                "event": "limit",
                "kind": kind,
                "steps_used": steps_used,
                "tool_calls_used": tool_calls_used,
                "message": "达到执行上限，未完成",
            }
        )

    def run_end(self, result: dict[str, Any]) -> None:
        self.write(
            {
                "event": "run_end",
                "run_id": result.get("run_id"),
                "status": result.get("status"),
                "completed": result.get("completed"),
                "answer_type": result.get("answer_type"),
                "citation_count": len(result.get("citations") or []),
                "summary": result.get("summary"),
            }
        )

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

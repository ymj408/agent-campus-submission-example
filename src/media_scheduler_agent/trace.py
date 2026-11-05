"""JSONL Trace 记录器。

只记录动作、参数、工具返回摘要与一句决策依据，不记录模型隐藏思维过程。
"""
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
                "mode": "draft_only",
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
                "message": "达到执行上限，未完成最终收口",
            }
        )

    def run_end(self, result: dict[str, Any]) -> None:
        self.write(
            {
                "event": "run_end",
                "run_id": result.get("run_id"),
                "status": result.get("status"),
                "completed": result.get("completed"),
                "published": False,
                "summary": result.get("summary"),
                "draft_id": (result.get("draft") or {}).get("draft_id"),
                "draft_saved": (result.get("draft") or {}).get("saved") is True,
                "confirmation_id": (result.get("confirmation") or {}).get("confirmation_id"),
            }
        )

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "TraceRecorder":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

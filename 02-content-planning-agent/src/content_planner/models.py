"""共享数据结构。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from .timeutil import format_datetime


class TaskValidationError(ValueError):
    pass


@dataclass(frozen=True)
class PlanningTask:
    request_id: str
    theme: str
    budget: float | None
    deadline: str | None
    audience: str = ""
    channel: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        request_id = str(self.request_id or "").strip()
        theme = str(self.theme or "").strip()
        if not request_id:
            raise TaskValidationError("request_id 不能为空")
        if not theme:
            raise TaskValidationError("theme 不能为空")

        budget = self.budget
        if budget is not None and budget != "":
            if isinstance(budget, bool):
                raise TaskValidationError("budget 必须是数字或 null")
            try:
                budget = round(float(budget), 2)
            except (TypeError, ValueError) as exc:
                raise TaskValidationError(f"budget 必须是数字或 null：{budget!r}") from exc
            if budget < 0:
                raise TaskValidationError("budget 不能为负数")
        else:
            budget = None

        deadline = self.deadline
        deadline = format_datetime(deadline) if deadline not in (None, "") else None

        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "theme", theme)
        object.__setattr__(self, "budget", budget)
        object.__setattr__(self, "deadline", deadline)
        object.__setattr__(self, "audience", str(self.audience or "").strip())
        object.__setattr__(self, "channel", str(self.channel or "").strip())
        object.__setattr__(self, "note", str(self.note or ""))

    @property
    def missing_fields(self) -> list[str]:
        missing: list[str] = []
        if self.budget is None:
            missing.append("budget")
        if self.deadline is None:
            missing.append("deadline")
        return missing

    @property
    def fingerprint(self) -> str:
        payload = {
            "theme": self.theme,
            "budget": self.budget,
            "deadline": self.deadline,
            "audience": self.audience,
            "channel": self.channel,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanningTask":
        if not isinstance(data, dict):
            raise TaskValidationError("请求必须是 JSON 对象")
        missing = [name for name in ("request_id", "theme") if not str(data.get(name) or "").strip()]
        if missing:
            raise TaskValidationError(f"请求缺少字段：{', '.join(missing)}")
        return cls(
            request_id=data["request_id"],
            theme=data["theme"],
            budget=data.get("budget"),
            deadline=data.get("deadline"),
            audience=data.get("audience", ""),
            channel=data.get("channel", ""),
            note=data.get("note", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "theme": self.theme,
            "budget": self.budget,
            "deadline": self.deadline,
            "audience": self.audience,
            "channel": self.channel,
            "note": self.note,
            "fingerprint": self.fingerprint,
            "missing_fields": self.missing_fields,
        }


@dataclass(frozen=True)
class AgentLimits:
    max_steps: int = 16
    max_tool_calls: int = 14

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise TaskValidationError("max_steps 必须 >= 1")
        if self.max_tool_calls < 1:
            raise TaskValidationError("max_tool_calls 必须 >= 1")

    def to_dict(self) -> dict[str, int]:
        return {"max_steps": self.max_steps, "max_tool_calls": self.max_tool_calls}


@dataclass
class Action:
    kind: str  # tool | finish
    tool: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    finish_status: str | None = None
    finish_summary: str = ""
    pending_items: list[str] = field(default_factory=list)

    @classmethod
    def tool_call(cls, tool: str, arguments: dict[str, Any] | None = None, rationale: str = "") -> "Action":
        return cls(kind="tool", tool=tool, arguments=dict(arguments or {}), rationale=rationale)

    @classmethod
    def finish(
        cls,
        status: str,
        summary: str = "",
        pending_items: list[str] | None = None,
        rationale: str = "",
    ) -> "Action":
        return cls(
            kind="finish",
            rationale=rationale,
            finish_status=status,
            finish_summary=summary,
            pending_items=list(pending_items or []),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind, "rationale": self.rationale}
        if self.kind == "tool":
            payload.update({"tool": self.tool, "arguments": self.arguments})
        else:
            payload.update(
                {
                    "status": self.finish_status,
                    "summary": self.finish_summary,
                    "pending_items": self.pending_items,
                }
            )
        return payload


@dataclass
class StepRecord:
    step: int
    kind: str
    tool: str | None
    arguments: dict[str, Any]
    rationale: str
    observation: dict[str, Any] | None = None
    finish_claim: dict[str, Any] | None = None

    def trace_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "step": self.step,
            "action": self.kind,
            "decision_basis": self.rationale,
        }
        if self.kind == "tool":
            payload["tool"] = self.tool
            payload["arguments"] = self.arguments
            if self.observation is not None:
                payload["observation"] = self.observation
        else:
            payload["finish_claim"] = self.finish_claim
        return payload

    def public_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "step": self.step,
            "action": self.kind,
            "decision_basis": self.rationale,
        }
        if self.kind == "tool":
            payload["tool"] = self.tool
            payload["arguments"] = self.arguments
            if self.observation is not None:
                payload["ok"] = bool(self.observation.get("ok"))
                payload["summary"] = self.observation.get("summary", "")
                if not self.observation.get("ok"):
                    payload["error"] = self.observation.get("error")
        else:
            payload["finish_claim"] = self.finish_claim
        return payload

"""共享数据结构。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


class TaskValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Turn:
    role: str
    content: str

    def __post_init__(self) -> None:
        role = str(self.role or "").strip()
        content = str(self.content or "").strip()
        if role not in {"user", "assistant"}:
            raise TaskValidationError("history.role 只能是 user 或 assistant")
        if not content:
            raise TaskValidationError("history.content 不能为空")
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "content", content)

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(frozen=True)
class QATask:
    request_id: str
    question: str
    history: list[Turn] = field(default_factory=list)

    def __post_init__(self) -> None:
        request_id = str(self.request_id or "").strip()
        question = str(self.question or "").strip()
        if not request_id:
            raise TaskValidationError("request_id 不能为空")
        if not question:
            raise TaskValidationError("question 不能为空")
        history: list[Turn] = []
        for item in self.history:
            if isinstance(item, Turn):
                history.append(item)
            elif isinstance(item, dict):
                history.append(Turn(str(item.get("role") or ""), str(item.get("content") or "")))
            else:
                raise TaskValidationError("history 项必须是 {role, content}")
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "question", question)
        object.__setattr__(self, "history", history)

    @property
    def last_user_message(self) -> str:
        for turn in reversed(self.history):
            if turn.role == "user":
                return turn.content
        return ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QATask":
        if not isinstance(data, dict):
            raise TaskValidationError("请求必须是 JSON 对象")
        missing = [name for name in ("request_id", "question") if not str(data.get(name) or "").strip()]
        if missing:
            raise TaskValidationError(f"请求缺少字段：{', '.join(missing)}")
        history = data.get("history") or []
        if not isinstance(history, list):
            raise TaskValidationError("history 必须是数组")
        return cls(
            request_id=data["request_id"],
            question=data["question"],
            history=history,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "question": self.question,
            "history": [turn.to_dict() for turn in self.history],
        }


@dataclass(frozen=True)
class AgentLimits:
    max_steps: int = 10
    max_tool_calls: int = 8

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise TaskValidationError("max_steps 必须 >= 1")
        if self.max_tool_calls < 1:
            raise TaskValidationError("max_tool_calls 必须 >= 1")

    def to_dict(self) -> dict[str, int]:
        return {"max_steps": self.max_steps, "max_tool_calls": self.max_tool_calls}


@dataclass
class Action:
    kind: str
    tool: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    finish_status: str | None = None
    finish_summary: str = ""
    pending_items: list[str] = field(default_factory=list)
    finish_payload: dict[str, Any] = field(default_factory=dict)

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
        payload: dict[str, Any] | None = None,
    ) -> "Action":
        return cls(
            kind="finish",
            rationale=rationale,
            finish_status=status,
            finish_summary=summary,
            pending_items=list(pending_items or []),
            finish_payload=dict(payload or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "rationale": self.rationale,
            "payload": self.finish_payload,
        }
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

"""共享数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class TaskValidationError(ValueError):
    pass


@dataclass(frozen=True)
class VideoTask:
    request_id: str
    theme: str
    audience: str = ""
    platform: str = ""
    target_duration_seconds: int | None = None
    style: str = "简洁可信"
    product: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        request_id = str(self.request_id or "").strip()
        theme = str(self.theme or "").strip()
        if not request_id:
            raise TaskValidationError("request_id 不能为空")
        if not theme:
            raise TaskValidationError("theme 不能为空")
        target = self.target_duration_seconds
        if target not in (None, ""):
            if isinstance(target, bool):
                raise TaskValidationError("target_duration_seconds 必须是整数")
            try:
                target = int(target)
            except (TypeError, ValueError) as exc:
                raise TaskValidationError("target_duration_seconds 必须是整数") from exc
            if target <= 0:
                raise TaskValidationError("target_duration_seconds 必须大于 0")
        else:
            target = None
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "theme", theme)
        object.__setattr__(self, "audience", str(self.audience or "").strip())
        object.__setattr__(self, "platform", str(self.platform or "").strip())
        object.__setattr__(self, "target_duration_seconds", target)
        object.__setattr__(self, "style", str(self.style or "简洁可信").strip())
        object.__setattr__(self, "product", str(self.product or "").strip())
        object.__setattr__(self, "note", str(self.note or ""))

    @property
    def missing_fields(self) -> list[str]:
        missing: list[str] = []
        if not self.audience:
            missing.append("audience")
        if not self.platform:
            missing.append("platform")
        if self.target_duration_seconds is None:
            missing.append("target_duration_seconds")
        return missing

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VideoTask":
        if not isinstance(data, dict):
            raise TaskValidationError("请求必须是 JSON 对象")
        missing = [name for name in ("request_id", "theme") if not str(data.get(name) or "").strip()]
        if missing:
            raise TaskValidationError(f"请求缺少字段：{', '.join(missing)}")
        return cls(
            request_id=data["request_id"],
            theme=data["theme"],
            audience=data.get("audience", ""),
            platform=data.get("platform", ""),
            target_duration_seconds=data.get("target_duration_seconds"),
            style=data.get("style", "简洁可信"),
            product=data.get("product", ""),
            note=data.get("note", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "theme": self.theme,
            "audience": self.audience,
            "platform": self.platform,
            "target_duration_seconds": self.target_duration_seconds,
            "style": self.style,
            "product": self.product,
            "note": self.note,
            "missing_fields": self.missing_fields,
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

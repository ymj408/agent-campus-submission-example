"""共享的数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .timeutil import format_datetime, parse_datetime


class TaskValidationError(ValueError):
    """调用方输入不合法。"""


@dataclass(frozen=True)
class TaskRequest:
    request_id: str
    material_id: str
    requested_publish_at: str
    allow_time_change: bool = True
    note: str = ""

    def __post_init__(self) -> None:
        request_id = str(self.request_id or "").strip()
        material_id = str(self.material_id or "").strip()
        if not request_id:
            raise TaskValidationError("request_id 不能为空")
        if not material_id:
            raise TaskValidationError("material_id 不能为空")
        normalized = format_datetime(self.requested_publish_at)
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "material_id", material_id)
        object.__setattr__(self, "requested_publish_at", normalized)
        object.__setattr__(self, "allow_time_change", bool(self.allow_time_change))
        object.__setattr__(self, "note", str(self.note or ""))

    @property
    def requested_dt(self):
        return parse_datetime(self.requested_publish_at)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaskRequest":
        if not isinstance(data, dict):
            raise TaskValidationError("请求必须是 JSON 对象")
        missing = [
            name
            for name in ("request_id", "material_id", "requested_publish_at")
            if not str(data.get(name) or "").strip()
        ]
        if missing:
            raise TaskValidationError(f"请求缺少字段：{', '.join(missing)}")
        allow = data.get("allow_time_change", True)
        if isinstance(allow, str):
            allow = allow.strip().lower() not in {"false", "0", "no", "off"}
        return cls(
            request_id=data["request_id"],
            material_id=data["material_id"],
            requested_publish_at=data["requested_publish_at"],
            allow_time_change=allow,
            note=data.get("note", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "material_id": self.material_id,
            "requested_publish_at": self.requested_publish_at,
            "allow_time_change": self.allow_time_change,
            "note": self.note,
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
    """Planner 在单步内做出的动作；不是模型的完整思维过程。"""

    kind: str  # "tool" | "finish"
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
        """结果内的紧凑步骤记录；完整返回在 Trace 中。"""
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

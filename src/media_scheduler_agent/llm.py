"""OpenAI-compatible Chat Completions 适配器。

使用原生 function/tool calling。该模块不参与离线 Mock 运行；没有配置密钥时
CLI 会明确拒绝 --provider openai，而不是偷偷回退到 Mock。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from .models import Action, StepRecord, TaskRequest
from .prompts import SYSTEM_PROMPT
from .tools import tool_specs

FINISH_TOOL = {
    "type": "function",
    "function": {
        "name": "finish",
        "description": "结束本次运行并报告结果。最终事实仍由运行时根据工具结果校验。",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": [
                        "draft_saved",
                        "draft_saved_pending_confirmation",
                        "draft_saved_needs_review",
                        "blocked_missing_info",
                        "blocked_rule_risk",
                        "blocked_no_available_slot",
                        "blocked_time_change_not_allowed",
                        "blocked_material_not_found",
                        "save_failed",
                        "tool_failure",
                        "awaiting_user_confirmation",
                        "incomplete",
                    ],
                },
                "summary": {"type": "string", "description": "简短结果说明"},
                "pending_items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "仍需用户或运营确认的事项",
                },
                "rationale": {
                    "type": "string",
                    "description": "一句话决策依据，不要写思维链",
                },
            },
            "required": ["status", "rationale"],
            "additionalProperties": False,
        },
    },
}


class LLMError(RuntimeError):
    pass


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_chat_response(response: dict[str, Any]) -> Action:
    """把 Chat Completions 响应解析成一个动作，便于离线测试。"""
    try:
        message = response["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError("模型响应缺少 choices[0].message") from exc

    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        call = tool_calls[0]
        function = call.get("function") or {}
        name = str(function.get("name") or "")
        raw_args = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise LLMError(f"工具 {name} 的参数不是合法 JSON") from exc
        rationale = str(arguments.pop("rationale", "") or "")
        if name == "finish":
            status = str(arguments.get("status") or "")
            if not status:
                raise LLMError("finish 缺少 status")
            return Action.finish(
                status=status,
                summary=str(arguments.get("summary") or ""),
                pending_items=[str(item) for item in arguments.get("pending_items", [])],
                rationale=rationale,
            )
        return Action.tool_call(name, arguments, rationale)

    content = _strip_fences(str(message.get("content") or ""))
    if not content:
        raise LLMError("模型既没有返回 tool_calls，也没有返回 JSON 动作")
    # 允许模型用纯 JSON 兼容不支持原生 tool calling 的网关。
    if "{" in content and "}" in content:
        content = content[content.find("{") : content.rfind("}") + 1]
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMError("模型文本响应不是 JSON 动作") from exc
    rationale = str(payload.get("rationale") or "")
    if payload.get("action") == "finish":
        return Action.finish(
            status=str(payload.get("status") or ""),
            summary=str(payload.get("summary") or ""),
            pending_items=[str(item) for item in payload.get("pending_items", [])],
            rationale=rationale,
        )
    if payload.get("action") == "tool":
        return Action.tool_call(
            str(payload.get("tool") or ""),
            dict(payload.get("arguments") or {}),
            rationale,
        )
    raise LLMError("模型 JSON 动作缺少 action=tool|finish")


class OpenAICompatiblePlanner:
    provider = "openai"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float = 60.0,
    ):
        if not base_url:
            raise LLMError("AGENT_MODEL_BASE_URL 未配置")
        if not model:
            raise LLMError("AGENT_MODEL_NAME 未配置")
        if not api_key:
            raise LLMError("AGENT_MODEL_API_KEY 未配置")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def decide(self, task: TaskRequest, history: list[StepRecord]) -> Action:
        payload = {
            "model": self.model,
            "messages": self._messages(task, history),
            "tools": [*tool_specs(), FINISH_TOOL],
            "tool_choice": "auto",
            "temperature": 0.1,
            "max_tokens": 1200,
        }
        response = self._post(payload)
        return parse_chat_response(response)

    def _messages(self, task: TaskRequest, history: list[StepRecord]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "任务（JSON）：\n"
                    + json.dumps(task.to_dict(), ensure_ascii=False, indent=2)
                    + "\n\n请从第一步开始，一次只调用一个工具。"
                ),
            },
        ]
        for record in history:
            if record.kind != "tool":
                continue
            arguments = dict(record.arguments)
            if record.rationale:
                arguments["rationale"] = record.rationale
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call_{record.step}",
                            "type": "function",
                            "function": {
                                "name": record.tool,
                                "arguments": json.dumps(arguments, ensure_ascii=False),
                            },
                        }
                    ],
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": f"call_{record.step}",
                    "content": json.dumps(record.observation or {}, ensure_ascii=False),
                }
            )
        return messages

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise LLMError(f"模型 HTTP {exc.code}：{detail}") from exc
        except urllib.error.URLError as exc:
            raise LLMError(f"模型网络错误：{exc.reason}") from exc
        except TimeoutError as exc:
            raise LLMError("模型请求超时") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LLMError("模型响应不是合法 JSON") from exc


def planner_from_env() -> OpenAICompatiblePlanner:
    base_url = os.environ.get("AGENT_MODEL_BASE_URL", "https://api.deepseek.com/v1")
    model = os.environ.get("AGENT_MODEL_NAME", "deepseek-chat")
    key = os.environ.get("AGENT_MODEL_API_KEY", "")
    timeout = float(os.environ.get("AGENT_MODEL_TIMEOUT_SECONDS", "60"))
    return OpenAICompatiblePlanner(
        base_url=base_url,
        model=model,
        api_key=key,
        timeout_seconds=timeout,
    )

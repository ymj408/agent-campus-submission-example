from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from tests.support import kb, make_task

from script_agent.agent import ScriptAgent
from script_agent.llm import (
    LLMError,
    OpenAICompatiblePlanner,
    parse_chat_response,
    planner_from_env,
)
from script_agent.models import AgentLimits
from script_agent.planner import build_script
from script_agent.tools import ToolRuntime


def tool_response(name: str, arguments: dict, index: int) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": f"call_{index}",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments, ensure_ascii=False),
                            },
                        }
                    ],
                }
            }
        ]
    }


class FakeHandler(BaseHTTPRequestHandler):
    responses: list[dict] = []
    requests: list[dict] = []
    auth_headers: list[str] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        type(self).requests.append(payload)
        type(self).auth_headers.append(self.headers.get("Authorization", ""))
        index = len(type(self).requests) - 1
        response = type(self).responses[min(index, len(type(self).responses) - 1)]
        raw = json.dumps(response, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return


class LLMAdapterTest(unittest.TestCase):
    def test_native_tool_calling_loop(self) -> None:
        task = make_task()
        long_script = build_script(task, "long")
        short_script = build_script(task, "short")
        responses = [
            tool_response("get_brief", {"rationale": "读简报"}, 1),
            tool_response("search_materials", {"query": "通勤耳机", "rationale": "检索"}, 2),
            tool_response("estimate_duration", {"script": long_script, "rationale": "首稿"}, 3),
            tool_response("estimate_duration", {"script": short_script, "rationale": "压缩"}, 4),
            tool_response("check_rules", {"script": short_script, "rationale": "检查"}, 5),
            tool_response(
                "finish",
                {
                    "status": "script_ready",
                    "script": short_script,
                    "summary": "完成",
                    "rationale": "通过",
                },
                6,
            ),
        ]
        FakeHandler.responses = responses
        FakeHandler.requests = []
        FakeHandler.auth_headers = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            tools = ToolRuntime(task=task, kb=kb())
            planner = OpenAICompatiblePlanner(
                base_url=f"http://127.0.0.1:{port}/v1",
                model="fake-model",
                api_key="test-key",
                timeout_seconds=5,
            )
            agent = ScriptAgent(planner, tools, limits=AgentLimits(10, 8))
            result = agent.run(task)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

        self.assertEqual(result["status"], "script_ready")
        self.assertEqual(result["total_duration_seconds"], 28)
        self.assertEqual(len(FakeHandler.requests), 6)
        self.assertTrue(all(req.get("tools") for req in FakeHandler.requests))
        self.assertTrue(all(auth == "Bearer test-key" for auth in FakeHandler.auth_headers))

    def test_json_fallback_parser(self) -> None:
        action = parse_chat_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": '```json\n{"action":"finish","status":"duration_unmet","summary":"超时","rationale":"停止"}\n```'
                        }
                    }
                ]
            }
        )
        self.assertEqual(action.kind, "finish")
        self.assertEqual(action.finish_status, "duration_unmet")

    def test_missing_key_is_rejected(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AGENT_MODEL_BASE_URL": "https://example.invalid/v1",
                "AGENT_MODEL_NAME": "x",
                "AGENT_MODEL_API_KEY": "",
            },
            clear=False,
        ):
            with self.assertRaises(LLMError):
                planner_from_env()

    def test_http_error_is_wrapped(self) -> None:
        planner = OpenAICompatiblePlanner(
            base_url="http://127.0.0.1:1/v1",
            model="x",
            api_key="k",
            timeout_seconds=0.5,
        )
        with self.assertRaises(LLMError):
            planner.decide(make_task(), [])


if __name__ == "__main__":
    unittest.main()

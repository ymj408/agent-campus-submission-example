from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from tests.support import make_task, temp_store

from media_scheduler_agent.agent import SchedulerAgent
from media_scheduler_agent.llm import (
    LLMError,
    OpenAICompatiblePlanner,
    parse_chat_response,
    planner_from_env,
)
from media_scheduler_agent.models import AgentLimits
from media_scheduler_agent.tools import ToolRuntime


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
        responses = [
            tool_response("get_material", {"material_id": "M03", "rationale": "读素材"}, 1),
            tool_response("check_rules", {"material_id": "M03", "rationale": "查规则"}, 2),
            tool_response("query_slots", {"material_id": "M03", "rationale": "查时段"}, 3),
            tool_response(
                "save_schedule_draft",
                {
                    "material_id": "M03",
                    "proposed_publish_at": "2026-11-06T12:00:00+08:00",
                    "rationale": "保存草稿",
                },
                4,
            ),
            tool_response(
                "finish",
                {"status": "draft_saved", "summary": "完成", "rationale": "结束"},
                5,
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
            tmp, store, seed_data = temp_store()
            try:
                task = make_task("M03")
                tools = ToolRuntime(task=task, seed=seed_data, store=store)
                planner = OpenAICompatiblePlanner(
                    base_url=f"http://127.0.0.1:{port}/v1",
                    model="fake-model",
                    api_key="test-key",
                    timeout_seconds=5,
                )
                agent = SchedulerAgent(planner, tools, limits=AgentLimits(10, 8))
                result = agent.run(task)
            finally:
                tmp.cleanup()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

        self.assertEqual(result["status"], "draft_saved")
        self.assertTrue(result["completed"])
        self.assertFalse(result["published"])
        tools_called = [step.get("tool") for step in result["steps"] if step["action"] == "tool"]
        self.assertEqual(
            tools_called,
            ["get_material", "check_rules", "query_slots", "save_schedule_draft"],
        )
        self.assertEqual(len(FakeHandler.requests), 5)
        self.assertTrue(all(req.get("tools") for req in FakeHandler.requests))
        self.assertTrue(all("fake-model" == req.get("model") for req in FakeHandler.requests))
        self.assertTrue(all(auth == "Bearer test-key" for auth in FakeHandler.auth_headers))
        self.assertIn("M03", FakeHandler.requests[0]["messages"][1]["content"])

    def test_json_fallback_parser(self) -> None:
        action = parse_chat_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": '```json\n{"action":"finish","status":"blocked_rule_risk","summary":"阻断","rationale":"有风险"}\n```'
                        }
                    }
                ]
            }
        )
        self.assertEqual(action.kind, "finish")
        self.assertEqual(action.finish_status, "blocked_rule_risk")
        self.assertEqual(action.rationale, "有风险")

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
            planner.decide(make_task("M03"), [])


if __name__ == "__main__":
    unittest.main()

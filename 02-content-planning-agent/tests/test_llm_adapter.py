from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from tests.support import make_task, temp_store

from content_planner.agent import ContentPlanningAgent
from content_planner.llm import (
    LLMError,
    OpenAICompatiblePlanner,
    parse_chat_response,
    planner_from_env,
)
from content_planner.models import AgentLimits
from content_planner.tools import ToolRuntime


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
            tool_response("get_brief", {"rationale": "读任务"}, 1),
            tool_response("search_materials", {"query": "秋季通勤穿搭", "rationale": "查素材"}, 2),
            tool_response("list_plan_options", {"rationale": "列方案"}, 3),
            tool_response("estimate_plan", {"plan_id": "premium_video", "rationale": "先估高价方案"}, 4),
            tool_response("estimate_plan", {"plan_id": "lean_article", "rationale": "改估替代方案"}, 5),
            tool_response("generate_script", {"plan_id": "lean_article", "rationale": "生成脚本"}, 6),
            tool_response(
                "execute_step",
                {"plan_id": "lean_article", "step_id": "poster", "rationale": "海报"},
                7,
            ),
            tool_response(
                "execute_step",
                {"plan_id": "lean_article", "step_id": "review", "rationale": "审核"},
                8,
            ),
            tool_response(
                "execute_step",
                {"plan_id": "lean_article", "step_id": "distribution", "rationale": "分发"},
                9,
            ),
            tool_response("save_plan", {"plan_id": "lean_article", "rationale": "保存"}, 10),
            tool_response(
                "finish",
                {"status": "plan_saved", "summary": "完成", "rationale": "结束"},
                11,
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
                task = make_task(budget=10)
                tools = ToolRuntime(task=task, seed=seed_data, store=store)
                planner = OpenAICompatiblePlanner(
                    base_url=f"http://127.0.0.1:{port}/v1",
                    model="fake-model",
                    api_key="test-key",
                    timeout_seconds=5,
                )
                agent = ContentPlanningAgent(planner, tools, limits=AgentLimits(16, 14))
                result = agent.run(task)
            finally:
                tmp.cleanup()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

        self.assertEqual(result["status"], "plan_saved")
        self.assertTrue(result["saved"])
        self.assertEqual(result["budget"]["total"], 8)
        self.assertEqual(len(FakeHandler.requests), 11)
        self.assertTrue(all(req.get("tools") for req in FakeHandler.requests))
        self.assertTrue(all(req.get("model") == "fake-model" for req in FakeHandler.requests))
        self.assertTrue(all(auth == "Bearer test-key" for auth in FakeHandler.auth_headers))

    def test_json_fallback_parser(self) -> None:
        action = parse_chat_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": '```json\n{"action":"finish","status":"blocked_missing_fields","summary":"缺预算","rationale":"无法估算"}\n```'
                        }
                    }
                ]
            }
        )
        self.assertEqual(action.kind, "finish")
        self.assertEqual(action.finish_status, "blocked_missing_fields")

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

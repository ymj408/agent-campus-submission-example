from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from tests.support import kb, make_task

from knowledge_qa.agent import KnowledgeQAAgent
from knowledge_qa.llm import (
    LLMError,
    OpenAICompatiblePlanner,
    parse_chat_response,
    planner_from_env,
)
from knowledge_qa.models import AgentLimits
from knowledge_qa.tools import ToolRuntime


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
            tool_response(
                "search_documents",
                {"query": "总部差旅报销期限多久", "top_k": 5, "rationale": "检索"},
                1,
            ),
            tool_response("read_document", {"doc_id": "D01", "rationale": "读原文"}, 2),
            tool_response(
                "finish",
                {
                    "status": "answered",
                    "answer": "根据 D01，报销期限为 30 天。",
                    "citations": [
                        {
                            "doc_id": "D01",
                            "section_id": "S2",
                            "quote": "员工应在差旅结束后 30 个自然日内提交报销申请，逾期需说明原因。",
                        }
                    ],
                    "rationale": "依据原文回答",
                },
                3,
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
            task = make_task("总部差旅报销期限多久？")
            tools = ToolRuntime(task=task, kb=kb())
            planner = OpenAICompatiblePlanner(
                base_url=f"http://127.0.0.1:{port}/v1",
                model="fake-model",
                api_key="test-key",
                timeout_seconds=5,
            )
            agent = KnowledgeQAAgent(planner, tools, limits=AgentLimits(10, 8))
            result = agent.run(task)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["citations"][0]["doc_id"], "D01")
        self.assertEqual(len(FakeHandler.requests), 3)
        self.assertTrue(all(req.get("tools") for req in FakeHandler.requests))
        self.assertTrue(all(auth == "Bearer test-key" for auth in FakeHandler.auth_headers))

    def test_json_fallback_parser(self) -> None:
        action = parse_chat_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": '```json\n{"action":"finish","status":"insufficient_evidence","summary":"无资料","rationale":"停止"}\n```'
                        }
                    }
                ]
            }
        )
        self.assertEqual(action.kind, "finish")
        self.assertEqual(action.finish_status, "insufficient_evidence")

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
            planner.decide(make_task("问题"), [])


if __name__ == "__main__":
    unittest.main()

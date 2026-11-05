from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from script_agent.agent import ScriptAgent  # noqa: E402
from script_agent.knowledge import KnowledgeBase  # noqa: E402
from script_agent.models import AgentLimits, VideoTask  # noqa: E402
from script_agent.planner import MockPlanner, Planner  # noqa: E402
from script_agent.tools import ToolRuntime  # noqa: E402


class TestDir:
    def __init__(self, prefix: str = "svs-test-"):
        base = ROOT / ".runtime" / "tests"
        base.mkdir(parents=True, exist_ok=True)
        self.path = base / f"{prefix}{uuid.uuid4().hex[:10]}"
        self.path.mkdir()

    @property
    def name(self) -> str:
        return str(self.path)

    def cleanup(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def __enter__(self) -> str:
        return str(self.path)

    def __exit__(self, *args: Any) -> None:
        self.cleanup()


def kb() -> KnowledgeBase:
    return KnowledgeBase(ROOT / "data")


def make_task(
    *,
    theme: str = "通勤降噪耳机",
    audience: str = "通勤上班族",
    platform: str = "douyin",
    target: int | None = 30,
    style: str = "简洁可信",
    product: str = "耳机",
) -> VideoTask:
    return VideoTask(
        request_id="REQ-TEST",
        theme=theme,
        audience=audience,
        platform=platform,
        target_duration_seconds=target,
        style=style,
        product=product,
    )


def make_tools(
    task: VideoTask,
    kb_data: KnowledgeBase,
    *,
    fail_tools: dict[str, int] | None = None,
) -> ToolRuntime:
    return ToolRuntime(task=task, kb=kb_data, fail_tools=fail_tools or {})


def run_agent(
    task: VideoTask,
    *,
    kb_data: KnowledgeBase,
    planner: Planner | None = None,
    fail_tools: dict[str, int] | None = None,
    max_steps: int = 10,
    max_tool_calls: int = 8,
    trace_path: str | Path | None = None,
) -> dict[str, Any]:
    tools = make_tools(task, kb_data, fail_tools=fail_tools)
    agent = ScriptAgent(
        planner or MockPlanner(),
        tools,
        limits=AgentLimits(max_steps=max_steps, max_tool_calls=max_tool_calls),
        trace_path=trace_path,
    )
    return agent.run(task)


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]

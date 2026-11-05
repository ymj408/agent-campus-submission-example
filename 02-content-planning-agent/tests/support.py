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

from content_planner.agent import ContentPlanningAgent  # noqa: E402
from content_planner.models import AgentLimits, PlanningTask  # noqa: E402
from content_planner.planner import MockPlanner, Planner  # noqa: E402
from content_planner.repository import RuntimeStore, SeedData  # noqa: E402
from content_planner.tools import ToolRuntime  # noqa: E402

DEADLINE = "2027-12-31T18:00:00+08:00"


def seed() -> SeedData:
    return SeedData(ROOT / "data")


class TestDir:
    def __init__(self, prefix: str = "cpp-test-"):
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


def test_dir(prefix: str = "cpp-test-") -> TestDir:
    return TestDir(prefix)


def temp_store(seed_data: SeedData | None = None):
    tmp = test_dir()
    seed_data = seed_data or seed()
    store = RuntimeStore(Path(tmp.name) / "store", seed_data)
    store.initialize(reset=True)
    return tmp, store, seed_data


def make_task(
    *,
    budget: float | None = 10,
    deadline: str | None = DEADLINE,
    theme: str = "秋季通勤穿搭",
    request_id: str = "REQ-TEST",
) -> PlanningTask:
    return PlanningTask(
        request_id=request_id,
        theme=theme,
        budget=budget,
        deadline=deadline,
        audience="通勤女性",
        channel="xiaohongshu",
    )


def make_tools(
    task: PlanningTask,
    seed_data: SeedData,
    store: RuntimeStore,
    *,
    fail_steps: dict[str, int] | None = None,
    resume: bool = False,
) -> ToolRuntime:
    return ToolRuntime(
        task=task,
        seed=seed_data,
        store=store,
        fail_steps=fail_steps or {},
        resume=resume,
    )


def run_agent(
    task: PlanningTask,
    *,
    seed_data: SeedData,
    store: RuntimeStore,
    planner: Planner | None = None,
    fail_steps: dict[str, int] | None = None,
    resume: bool = False,
    max_steps: int = 16,
    max_tool_calls: int = 14,
    trace_path: str | Path | None = None,
) -> dict[str, Any]:
    tools = make_tools(task, seed_data, store, fail_steps=fail_steps, resume=resume)
    agent = ContentPlanningAgent(
        planner or MockPlanner(),
        tools,
        limits=AgentLimits(max_steps=max_steps, max_tool_calls=max_tool_calls),
        trace_path=trace_path,
    )
    return agent.run(task)


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]

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

from media_scheduler_agent.agent import SchedulerAgent  # noqa: E402
from media_scheduler_agent.models import AgentLimits, TaskRequest  # noqa: E402
from media_scheduler_agent.planner import MockPlanner, Planner  # noqa: E402
from media_scheduler_agent.repository import RuntimeStore, SeedData  # noqa: E402
from media_scheduler_agent.tools import ToolRuntime  # noqa: E402

MATERIAL_TIMES = {
    "M01": "2026-11-06T18:00:00+08:00",
    "M02": "2026-11-07T10:00:00+08:00",
    "M03": "2026-11-06T12:00:00+08:00",
    "M04": "2026-11-06T21:00:00+08:00",
    "M05": "2026-11-06T14:00:00+08:00",
    "M06": "2026-11-07T14:00:00+08:00",
}


def seed() -> SeedData:
    return SeedData(ROOT / "data")


class TestDir:
    """不使用 tempfile.mkdtemp：Windows 上 0o700 会生成沙箱无法穿透的 DACL。

    直接使用 Path.mkdir() 继承工作区权限，并在用完后尽力清理。
    """

    def __init__(self, prefix: str = "msa-test-"):
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


def test_dir(prefix: str = "msa-test-") -> TestDir:
    return TestDir(prefix)


def make_task(
    material_id: str,
    *,
    at: str | None = None,
    allow_time_change: bool = True,
    request_id: str | None = None,
) -> TaskRequest:
    return TaskRequest(
        request_id=request_id or f"REQ-{material_id}",
        material_id=material_id,
        requested_publish_at=at or MATERIAL_TIMES[material_id],
        allow_time_change=allow_time_change,
    )


def temp_store(
    seed_data: SeedData | None = None,
) -> tuple[tempfile.TemporaryDirectory, RuntimeStore, SeedData]:
    tmp = test_dir()
    seed_data = seed_data or seed()
    store = RuntimeStore(Path(tmp.name) / "store", seed_data)
    store.initialize(reset=True)
    return tmp, store, seed_data


def make_tools(
    task: TaskRequest,
    seed_data: SeedData,
    store: RuntimeStore,
    *,
    fail_save_times: int = 0,
) -> ToolRuntime:
    return ToolRuntime(task=task, seed=seed_data, store=store, fail_save_times=fail_save_times)


def run_agent(
    task: TaskRequest,
    *,
    seed_data: SeedData,
    store: RuntimeStore,
    planner: Planner | None = None,
    fail_save_times: int = 0,
    max_steps: int = 10,
    max_tool_calls: int = 8,
    trace_path: str | Path | None = None,
) -> dict[str, Any]:
    tools = make_tools(task, seed_data, store, fail_save_times=fail_save_times)
    agent = SchedulerAgent(
        planner or MockPlanner(),
        tools,
        limits=AgentLimits(max_steps=max_steps, max_tool_calls=max_tool_calls),
        trace_path=trace_path,
    )
    return agent.run(task)


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]

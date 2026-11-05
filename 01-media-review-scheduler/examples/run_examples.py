#!/usr/bin/env python3
"""生成 examples/ 下的实际结果与 Trace。

该脚本会重置 .runtime/examples/<case>，不会触碰 data/。
运行：
    python examples/run_examples.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from media_scheduler_agent.agent import SchedulerAgent  # noqa: E402
from media_scheduler_agent.models import AgentLimits, TaskRequest  # noqa: E402
from media_scheduler_agent.planner import MockPlanner  # noqa: E402
from media_scheduler_agent.repository import RuntimeStore, SeedData  # noqa: E402
from media_scheduler_agent.timeutil import CST  # noqa: E402
from media_scheduler_agent.tools import ToolRuntime  # noqa: E402


def rel(path: str | Path) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return Path(path).as_posix()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_case(case_path: Path, seed: SeedData) -> dict[str, Any]:
    case = json.loads(case_path.read_text(encoding="utf-8"))
    task = TaskRequest.from_dict(case["request"])
    options = case.get("run") or {}
    repeat = int(options.get("repeat", 1))
    max_steps = int(options.get("max_steps", 10))
    max_tool_calls = int(options.get("max_tool_calls", 8))
    fail_save = int(options.get("fail_save_times", 0))
    if fail_save == 99:
        fail_save = 10**9

    store_path = ROOT / ".runtime" / "examples" / case["id"]
    store = RuntimeStore(store_path, seed)
    store.initialize(reset=True)
    trace_paths: list[Path] = []
    runs: list[dict[str, Any]] = []

    for index in range(repeat):
        trace_path = ROOT / "examples" / "traces" / f"{case['id']}-run{index + 1}.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        if trace_path.exists():
            trace_path.unlink()
        tools = ToolRuntime(
            task=task,
            seed=seed,
            store=store,
            fail_save_times=fail_save,
        )
        agent = SchedulerAgent(
            MockPlanner(),
            tools,
            limits=AgentLimits(max_steps=max_steps, max_tool_calls=max_tool_calls),
            trace_path=trace_path,
        )
        result = agent.run(task)
        result["trace_path"] = rel(trace_path)
        result["scenario"] = {
            "id": case["id"],
            "title": case["title"],
            "covers": case.get("covers", []),
            "run": options,
        }
        runs.append(result)
        trace_paths.append(trace_path)

    summary = store.summary()
    summary["store"] = rel(summary["store"])
    expected = list(case.get("expect") or [])
    statuses = [run["status"] for run in runs]
    checks = {
        "status_match": statuses == expected,
        "published_false": all(run.get("published") is False for run in runs),
        "draft_published_false": all(
            not (run.get("draft") or {}).get("saved")
            or (run.get("draft") or {}).get("published") is False
            for run in runs
        ),
    }
    if repeat > 1 and all(run.get("draft", {}).get("saved") for run in runs):
        checks["duplicate_one_draft"] = summary["draft_count"] == 1
        checks["duplicate_same_id"] = runs[0]["draft"].get("draft_id") == runs[1]["draft"].get("draft_id")
        checks["duplicate_second_deduplicated"] = runs[1]["draft"].get("deduplicated") is True
    if case["id"].endswith("save-failure"):
        checks["no_draft_after_failure"] = summary["draft_count"] == 0
        checks["explicit_failure"] = runs[-1]["draft"].get("saved") is False

    output = {
        "scenario": {
            "id": case["id"],
            "title": case["title"],
            "covers": case.get("covers", []),
            "expect": expected,
            "source": rel(case_path),
        },
        "command": (
            f"python examples/run_examples.py  # 或 python run.py run --case {rel(case_path)} --reset"
        ),
        "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
        "provider": "mock",
        "boundary": "MOCK 只验证流程、边界与幂等；不证明真实模型决策质量。",
        "runs": runs,
        "store_summary": summary,
        "checks": checks,
        "checks_passed": all(checks.values()),
        "traces": [rel(path) for path in trace_paths],
    }
    out_path = ROOT / "examples" / "outputs" / f"{case['id']}.json"
    write_json(out_path, output)
    return {
        "id": case["id"],
        "title": case["title"],
        "covers": case.get("covers", []),
        "statuses": statuses,
        "draft_count": summary["draft_count"],
        "checks_passed": output["checks_passed"],
        "checks": checks,
        "output": rel(out_path),
        "traces": output["traces"],
    }


def main() -> int:
    seed = SeedData(ROOT / "data")
    cases = sorted((ROOT / "examples" / "cases").glob("*.json"))
    if not cases:
        print("没有找到案例", file=sys.stderr)
        return 1
    rows = [run_case(path, seed) for path in cases]
    all_passed = all(row["checks_passed"] for row in rows)
    index = {
        "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
        "provider": "mock",
        "boundary": "MOCK 只验证流程、边界与幂等；不证明真实模型决策质量。",
        "scenarios": rows,
        "totals": {
            "scenarios": len(rows),
            "runs": sum(len(row["statuses"]) for row in rows),
            "drafts": sum(row["draft_count"] for row in rows),
            "published": 0,
            "all_checks_passed": all_passed,
        },
    }
    write_json(ROOT / "examples" / "outputs" / "index.json", index)

    print(f"{'scenario':34} {'status':46} {'draft':>5}  checks")
    for row in rows:
        print(
            f"{row['id']:34} {','.join(row['statuses']):46} "
            f"{row['draft_count']:>5}  {'PASS' if row['checks_passed'] else 'FAIL'}"
        )
    print(f"\noutputs: {rel(ROOT / 'examples' / 'outputs')}")
    print(f"traces : {rel(ROOT / 'examples' / 'traces')}")
    print("published=0" if all_passed else "存在失败检查")
    return 0 if all_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

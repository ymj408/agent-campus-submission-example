#!/usr/bin/env python3
"""生成题 2 的实际输出与 Trace。

会重置 .runtime/examples/<case>，不修改 data/。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from content_planner.agent import ContentPlanningAgent  # noqa: E402
from content_planner.models import AgentLimits, PlanningTask  # noqa: E402
from content_planner.planner import MockPlanner  # noqa: E402
from content_planner.repository import RuntimeStore, SeedData  # noqa: E402
from content_planner.timeutil import CST  # noqa: E402
from content_planner.tools import ToolRuntime  # noqa: E402


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
    task = PlanningTask.from_dict(case["request"])
    runs_spec = case.get("runs") or [{"label": "run", "expect": case.get("expect")}]
    store_path = ROOT / ".runtime" / "examples" / case["id"]
    store = RuntimeStore(store_path, seed)
    store.initialize(reset=True)

    runs: list[dict[str, Any]] = []
    trace_paths: list[Path] = []
    plan_counts: list[int] = []
    for index, spec in enumerate(runs_spec):
        label = str(spec.get("label") or f"run{index + 1}")
        fail_steps = {str(key): int(value) for key, value in (spec.get("fail_steps") or {}).items()}
        if fail_steps.get("save") == 99:
            fail_steps["save"] = 10**9
        if fail_steps.get("poster") == 99:
            fail_steps["poster"] = 10**9
        if fail_steps.get("review") == 99:
            fail_steps["review"] = 10**9
        trace_path = ROOT / "examples" / "traces" / f"{case['id']}-{index + 1}-{label}.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        if trace_path.exists():
            trace_path.unlink()
        tools = ToolRuntime(
            task=task,
            seed=seed,
            store=store,
            fail_steps=fail_steps,
            resume=bool(spec.get("resume", False)),
        )
        agent = ContentPlanningAgent(
            MockPlanner(),
            tools,
            limits=AgentLimits(
                max_steps=int(spec.get("max_steps", 16)),
                max_tool_calls=int(spec.get("max_tool_calls", 14)),
            ),
            trace_path=trace_path,
        )
        result = agent.run(task)
        result["trace_path"] = rel(trace_path)
        result["scenario"] = {
            "id": case["id"],
            "title": case["title"],
            "covers": case.get("covers", []),
            "run_label": label,
        }
        runs.append(result)
        trace_paths.append(trace_path)
        plan_counts.append(store.summary()["plan_count"])

    summary = store.summary()
    summary["store"] = rel(summary["store"])
    expected = [str(spec.get("expect")) for spec in runs_spec]
    statuses = [run["status"] for run in runs]
    checks: dict[str, bool] = {
        "status_match": statuses == expected,
        "saved_flags_match": all(
            run["saved"] == (run["status"] in {"plan_saved", "plan_saved_deduplicated"})
            for run in runs
        ),
        "all_plan_files_valid": all(
            run["plan"] is None
            or not run["saved"]
            or (ROOT / ".runtime" / "examples" / case["id"] / "plans" / f"{run['plan']['plan_id']}.json").exists()
            for run in runs
        ),
    }

    if case["id"] == "01-normal-budget-replan":
        run = runs[0]
        checks["premium_was_over_budget"] = any(
            item.get("tool") == "estimate_plan" and "超预算 2" in item.get("summary", "")
            for item in run["step_records"]
        )
        checks["selected_lean"] = run["plan"]["template_id"] == "lean_article"
        checks["cost_is_8"] = run["budget"]["total"] == 8
        checks["all_steps_success"] = all(step["status"] == "succeeded" for step in run["steps"])
    elif case["id"] == "04-no-feasible-plan":
        checks["gap_is_3"] = runs[0]["budget"]["over_by"] == 3
        checks["no_plan_saved"] = runs[0]["saved"] is False and plan_counts[0] == 0
    elif case["id"] == "05-poster-failure-replan":
        run = runs[0]
        checks["fallback_no_poster"] = run["plan"]["template_id"] == "lean_no_poster"
        checks["poster_failed"] = any(item.get("step_id") == "poster" for item in run["failure_history"])
        checks["script_preserved"] = any(
            step["step_id"] == "script" and step["status"] == "succeeded" for step in run["steps"]
        )
    elif case["id"] == "06-review-failure-preserve":
        run = runs[0]
        by_id = {step["step_id"]: step for step in run["steps"]}
        checks["review_failed"] = by_id["review"]["status"] == "failed"
        checks["script_poster_preserved"] = (
            by_id["script"]["status"] == "succeeded" and by_id["poster"]["status"] == "succeeded"
        )
        checks["not_saved"] = run["saved"] is False and plan_counts[0] == 0
    elif case["id"] == "07-resume-failed-step":
        checks["first_failed"] = runs[0]["status"] == "incomplete_tool_failure"
        checks["resume_saved"] = runs[1]["status"] == "plan_saved"
        resume_tools = [item.get("tool") for item in runs[1]["step_records"]]
        checks["resume_skips_script"] = "generate_script" not in resume_tools
        checks["one_plan"] = plan_counts[-1] == 1
    elif case["id"] == "08-duplicate-execution":
        checks["one_plan"] = plan_counts[-1] == 1
        checks["second_deduplicated"] = runs[1]["status"] == "plan_saved_deduplicated"
        checks["same_plan_id"] = runs[0]["plan"]["plan_id"] == runs[1]["plan"]["plan_id"]
        checks["second_only_get_brief"] = len(
            [item for item in runs[1]["step_records"] if item.get("tool")]
        ) == 1
    elif case["id"] == "09-save-failure":
        run = runs[0]
        checks["no_plan"] = plan_counts[0] == 0
        checks["steps_succeeded"] = all(step["status"] == "succeeded" for step in run["steps"])
        checks["explicit_failure"] = run["saved"] is False
        checks["two_save_attempts"] = len(
            [item for item in run["step_records"] if item.get("tool") == "save_plan"]
        ) == 2
    elif case["id"] == "10-step-limit":
        checks["limit_status"] = runs[0]["status"] == "step_limit_reached"
        checks["not_completed"] = runs[0]["completed"] is False

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
        "boundary": "MOCK 只验证流程、预算、依赖、失败重规划与幂等；不证明真实模型决策质量。",
        "runs": runs,
        "store_summary": summary,
        "plan_counts_after_each_run": plan_counts,
        "checks": checks,
        "checks_passed": all(checks.values()),
        "traces": [rel(path) for path in trace_paths],
    }
    write_json(ROOT / "examples" / "outputs" / f"{case['id']}.json", output)
    return {
        "id": case["id"],
        "title": case["title"],
        "covers": case.get("covers", []),
        "statuses": statuses,
        "plan_count": summary["plan_count"],
        "checks_passed": output["checks_passed"],
        "checks": checks,
        "output": rel(ROOT / "examples" / "outputs" / f"{case['id']}.json"),
        "traces": output["traces"],
    }


def main() -> int:
    seed = SeedData(ROOT / "data")
    cases = sorted((ROOT / "examples" / "cases").glob("*.json"))
    if not cases:
        print("没有找到案例", file=sys.stderr)
        return 1
    rows = [run_case(path, seed) for path in cases]
    passed = all(row["checks_passed"] for row in rows)
    index = {
        "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
        "provider": "mock",
        "boundary": "MOCK 只验证流程、预算、依赖、失败重规划与幂等；不证明真实模型决策质量。",
        "scenarios": rows,
        "totals": {
            "scenarios": len(rows),
            "runs": sum(len(row["statuses"]) for row in rows),
            "saved_plans": sum(row["plan_count"] for row in rows),
            "all_checks_passed": passed,
        },
    }
    write_json(ROOT / "examples" / "outputs" / "index.json", index)
    print(f"{'scenario':34} {'status':44} {'plans':>5}  checks")
    for row in rows:
        print(
            f"{row['id']:34} {','.join(row['statuses']):44} "
            f"{row['plan_count']:>5}  {'PASS' if row['checks_passed'] else 'FAIL'}"
        )
    print(f"\noutputs: {rel(ROOT / 'examples' / 'outputs')}")
    print(f"traces : {rel(ROOT / 'examples' / 'traces')}")
    print(f"saved_plans={sum(row['plan_count'] for row in rows)}")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

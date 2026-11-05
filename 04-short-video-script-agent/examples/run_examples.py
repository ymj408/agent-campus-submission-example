#!/usr/bin/env python3
"""生成题 4 的实际脚本、检查结果与 Trace。"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from script_agent.agent import ScriptAgent  # noqa: E402
from script_agent.knowledge import KnowledgeBase  # noqa: E402
from script_agent.models import AgentLimits, VideoTask  # noqa: E402
from script_agent.planner import MockPlanner  # noqa: E402
from script_agent.timeutil import CST  # noqa: E402
from script_agent.tools import ToolRuntime  # noqa: E402


def rel(path: str | Path) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return Path(path).as_posix()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_case(case_path: Path, kb: KnowledgeBase) -> dict[str, Any]:
    case = json.loads(case_path.read_text(encoding="utf-8"))
    task = VideoTask.from_dict(case["request"])
    options = case.get("run") or {}
    fail_tools = {
        str(key): (10**9 if int(value) == 99 else int(value))
        for key, value in (options.get("fail_tools") or {}).items()
    }
    trace_path = ROOT / "examples" / "traces" / f"{case['id']}.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    if trace_path.exists():
        trace_path.unlink()
    tools = ToolRuntime(task=task, kb=kb, fail_tools=fail_tools)
    agent = ScriptAgent(
        MockPlanner(),
        tools,
        limits=AgentLimits(
            max_steps=int(options.get("max_steps", 10)),
            max_tool_calls=int(options.get("max_tool_calls", 8)),
        ),
        trace_path=trace_path,
    )
    result = agent.run(task)
    result["trace_path"] = rel(trace_path)
    result["scenario"] = {
        "id": case["id"],
        "title": case["title"],
        "covers": case.get("covers", []),
    }
    expected = str(options.get("expect") or "")
    checks: dict[str, bool] = {
        "status_match": result["status"] == expected,
        "no_forbidden_effect_claim": "效率提升 80%" not in str(result.get("script")),
    }
    if result["status"] == "script_ready":
        checks["script_ready"] = result["script"]["ready"] is True
        checks["has_storyboard"] = len(result["storyboard"]) > 0
        checks["has_p01"] = any(item["material_id"] == "P01" for item in result["sources"])
        checks["rule_pass"] = result["rule_check"]["decision"] == "pass"
        checks["within_target"] = result["total_duration_seconds"] <= result["target_duration_seconds"]
    if case["id"] == "01-normal-30s":
        checks["revisions_38_28"] = [item["total_seconds"] for item in result["revisions"]] == [38, 28]
        checks["total_28"] = result["total_duration_seconds"] == 28
    elif case["id"] == "03-no-material":
        checks["no_script"] = result["script"] is None
    elif case["id"] == "04-duration-unmet":
        checks["total_14"] = result["total_duration_seconds"] == 14
        checks["not_ready"] = result["script"]["ready"] is False
        checks["gap_4"] = result["duration_check"]["gap_seconds"] == 4
    elif case["id"] == "07-rule-risk-clean":
        checks["revisions_38_28_28"] = [
            item["total_seconds"] for item in result["revisions"]
        ] == [38, 28, 28]
    elif case["id"] == "08-target-20s":
        checks["revisions_38_28_14"] = [
            item["total_seconds"] for item in result["revisions"]
        ] == [38, 28, 14]
        checks["total_14"] = result["total_duration_seconds"] == 14
    elif case["id"] in {"05-search-failure", "06-check-failure"}:
        checks["not_ready"] = result["script"] is None or result["script"]["ready"] is False

    output = {
        "scenario": {
            "id": case["id"],
            "title": case["title"],
            "covers": case.get("covers", []),
            "expect": expected,
            "source": rel(case_path),
        },
        "command": f"python run.py run --case {rel(case_path)}",
        "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
        "provider": "mock",
        "boundary": "MOCK 只验证时长修正、素材事实、规则检查与失败处理；不证明真实模型创作质量。",
        "runs": [result],
        "checks": checks,
        "checks_passed": all(checks.values()),
        "traces": [rel(trace_path)],
    }
    write_json(ROOT / "examples" / "outputs" / f"{case['id']}.json", output)
    return {
        "id": case["id"],
        "title": case["title"],
        "status": result["status"],
        "total": result["total_duration_seconds"],
        "revisions": [item["total_seconds"] for item in result["revisions"]],
        "checks_passed": output["checks_passed"],
        "checks": checks,
        "output": rel(ROOT / "examples" / "outputs" / f"{case['id']}.json"),
        "trace": rel(trace_path),
    }


def main() -> int:
    kb = KnowledgeBase(ROOT / "data")
    cases = sorted((ROOT / "examples" / "cases").glob("*.json"))
    if not cases:
        print("没有找到案例", file=sys.stderr)
        return 1
    rows = [run_case(path, kb) for path in cases]
    passed = all(row["checks_passed"] for row in rows)
    index = {
        "generated_at": datetime.now(CST).isoformat(timespec="seconds"),
        "provider": "mock",
        "boundary": "MOCK 只验证时长修正、素材事实、规则检查与失败处理；不证明真实模型创作质量。",
        "scenarios": rows,
        "totals": {
            "scenarios": len(rows),
            "runs": len(rows),
            "script_ready": sum(1 for row in rows if row["status"] == "script_ready"),
            "blocked_missing_fields": sum(1 for row in rows if row["status"] == "blocked_missing_fields"),
            "insufficient_material": sum(1 for row in rows if row["status"] == "insufficient_material"),
            "duration_unmet": sum(1 for row in rows if row["status"] == "duration_unmet"),
            "tool_failure": sum(1 for row in rows if row["status"] == "tool_failure"),
            "all_checks_passed": passed,
        },
    }
    write_json(ROOT / "examples" / "outputs" / "index.json", index)
    print(f"{'scenario':30} {'status':24} {'total':>6} {'revisions':>14}  checks")
    for row in rows:
        print(
            f"{row['id']:30} {row['status']:24} {str(row['total']):>6} "
            f"{str(row['revisions']):>14}  {'PASS' if row['checks_passed'] else 'FAIL'}"
        )
    print(f"\noutputs: {rel(ROOT / 'examples' / 'outputs')}")
    print(f"traces : {rel(ROOT / 'examples' / 'traces')}")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

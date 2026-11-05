#!/usr/bin/env python3
"""生成题 3 的实际输出与 Trace。"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from knowledge_qa.agent import KnowledgeQAAgent  # noqa: E402
from knowledge_qa.knowledge import KnowledgeBase  # noqa: E402
from knowledge_qa.models import AgentLimits, QATask  # noqa: E402
from knowledge_qa.planner import MockPlanner  # noqa: E402
from knowledge_qa.timeutil import CST  # noqa: E402
from knowledge_qa.tools import ToolRuntime  # noqa: E402


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
    task = QATask.from_dict(case["request"])
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
    agent = KnowledgeQAAgent(
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
        "no_unsupported_answer": result["status"] in {"answered", "needs_confirmation"}
        or result["answer"] is None,
        "citation_checks_valid": result["citation_check"]["canonical_citations_valid"],
    }
    if result["status"] in {"answered", "needs_confirmation"}:
        checks["has_citations"] = bool(result["citations"])
        evidence_quotes = [item["quote"] for item in result["preserved_evidence"]]
        checks["answer_quotes_evidence"] = all(
            any(
                citation["quote"] in evidence_quote or evidence_quote in citation["quote"]
                for evidence_quote in evidence_quotes
            )
            for citation in result["citations"]
        )
    if case["id"] == "01-clear-answer":
        checks["answer_30"] = "30 天" in (result["answer"] or "")
        checks["cite_d01"] = [item["doc_id"] for item in result["citations"]] == ["D01"]
    elif case["id"] == "02-document-conflict":
        checks["is_conflict"] = result["answer_type"] == "conflict"
        checks["cites_both"] = {item["doc_id"] for item in result["citations"]} == {"D01", "D02"}
        checks["mentions_both_values"] = {
            str(item["value"]) for item in result["conflict"]["values"]
        } == {"30", "15"}
    elif case["id"] == "04-paraphrase-conflict":
        checks["retrieved_both"] = set(result["read_doc_ids"]) == {"D01", "D02"}
        checks["is_conflict"] = result["answer_type"] == "conflict"
    elif case["id"] == "05-context-followup":
        checks["history_used"] = result["history_used"]
        checks["query_has_headquarters"] = "总部" in (result["rewritten_query"] or "")
        checks["answer_30"] = "30 天" in (result["answer"] or "")
    elif case["id"] in {"06-search-failure", "07-read-failure"}:
        checks["no_answer"] = result["answer"] is None
    elif case["id"] == "08-clear-product-faq":
        checks["answer_7"] = "7 天" in (result["answer"] or "")
        checks["cite_d04"] = [item["doc_id"] for item in result["citations"]] == ["D04"]

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
        "boundary": "MOCK 只验证检索、引用、冲突、追问、上下文与工具失败；不证明真实模型问答质量。",
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
        "answer_type": result["answer_type"],
        "citation_count": len(result["citations"]),
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
        "boundary": "MOCK 只验证检索、引用、冲突、追问、上下文与工具失败；不证明真实模型问答质量。",
        "scenarios": rows,
        "totals": {
            "scenarios": len(rows),
            "runs": len(rows),
            "answered": sum(1 for row in rows if row["status"] == "answered"),
            "needs_confirmation": sum(1 for row in rows if row["status"] == "needs_confirmation"),
            "insufficient_evidence": sum(1 for row in rows if row["status"] == "insufficient_evidence"),
            "tool_failure": sum(1 for row in rows if row["status"] == "tool_failure"),
            "all_checks_passed": passed,
        },
    }
    write_json(ROOT / "examples" / "outputs" / "index.json", index)
    print(f"{'scenario':32} {'status':22} {'type':12} {'cites':>5}  checks")
    for row in rows:
        print(
            f"{row['id']:32} {row['status']:22} {row['answer_type']:12} "
            f"{row['citation_count']:>5}  {'PASS' if row['checks_passed'] else 'FAIL'}"
        )
    print(f"\noutputs: {rel(ROOT / 'examples' / 'outputs')}")
    print(f"traces : {rel(ROOT / 'examples' / 'traces')}")
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

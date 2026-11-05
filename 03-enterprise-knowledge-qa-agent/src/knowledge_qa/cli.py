"""企业知识问答 Agent CLI。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .agent import KnowledgeQAAgent
from .knowledge import KnowledgeBase
from .llm import LLMError, planner_from_env
from .models import AgentLimits, QATask, TaskValidationError
from .planner import MockPlanner
from .timeutil import CST
from .tools import ToolRuntime

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"


def _load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _parse_fail_tools(values: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in values:
        if "=" not in item:
            raise TaskValidationError(f"--fail-tool 格式应为 key=次数：{item!r}")
        key, count_text = item.split("=", 1)
        try:
            count = int(count_text)
        except ValueError as exc:
            raise TaskValidationError(f"失败次数不是整数：{item!r}") from exc
        if not key or count < 1:
            raise TaskValidationError(f"--fail-tool 不合法：{item!r}")
        result[key] = count
    return result


def _read_case(path: Path) -> tuple[QATask, dict[str, Any], dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskValidationError(f"无法读取 case：{path}：{exc}") from exc
    if not isinstance(payload, dict):
        raise TaskValidationError("case 必须是 JSON 对象")
    request_payload = dict(payload.get("request") or payload)
    request_payload.setdefault("request_id", f"REQ-{path.stem.upper()}")
    task = QATask.from_dict(request_payload)
    options = dict(payload.get("run") or {})
    scenario = {key: payload.get(key) for key in ("id", "title", "covers") if key in payload}
    return task, options, scenario


def _resolve_planner(provider: str):
    if provider == "mock":
        return MockPlanner()
    if provider == "openai":
        return planner_from_env()
    raise TaskValidationError(f"未知 provider：{provider}")


def _print_human(result: dict[str, Any]) -> None:
    task = result.get("task") or {}
    print(f"运行 ID : {result.get('run_id')}")
    print(f"问题    : {task.get('question')}")
    print(f"状态    : {result.get('status')} | 完成={result.get('completed')} | 可回答={result.get('answer_supported')}")
    if result.get("rewritten_query") and result.get("history_used"):
        print(f"改写查询: {result.get('rewritten_query')}（使用了对话历史）")
    print(f"回答    : {result.get('answer')}")
    citations = result.get("citations") or []
    if citations:
        print("引用    :")
        for item in citations:
            print(f"         - {item.get('doc_id')} {item.get('section_id')}: {item.get('quote')}")
    if result.get("conflict"):
        print("冲突    :")
        for item in result["conflict"].get("values", []):
            print(f"         - {item.get('doc_id')}: {item.get('value')}{item.get('unit')} | {item.get('quote')}")
    if result.get("missing_information"):
        print("资料缺口:")
        for item in result["missing_information"]:
            print(f"         - {item}")
    if result.get("follow_up_question"):
        print(f"追问    : {result.get('follow_up_question')}")
    if result.get("pending_items"):
        print("待办    :")
        for item in result["pending_items"]:
            print(f"         - {item}")
    usage = result.get("usage") or {}
    print(f"用量    : {usage.get('steps')} 步 / {usage.get('tool_calls')} 次工具调用")
    if result.get("trace_path"):
        print(f"Trace   : {result.get('trace_path')}")


def _cmd_run(args: argparse.Namespace) -> int:
    options: dict[str, Any] = {}
    scenario: dict[str, Any] = {}
    if args.case:
        case_path = Path(args.case).resolve()
        task, options, scenario = _read_case(case_path)
        case_id = str(scenario.get("id") or case_path.stem)
    else:
        if not args.question:
            print("错误：非 case 模式必须提供 --question", file=sys.stderr)
            return 2
        history: list[dict[str, str]] = []
        history_text = args.history
        if args.history_file:
            history_text = Path(args.history_file).read_text(encoding="utf-8")
        if history_text:
            try:
                history = json.loads(history_text)
            except json.JSONDecodeError as exc:
                raise TaskValidationError(f"--history 不是合法 JSON：{exc}") from exc
            if not isinstance(history, list):
                raise TaskValidationError("--history 必须是 JSON 数组")
        task = QATask(
            request_id=args.request_id or "REQ-CLI",
            question=args.question,
            history=history,
        )
        case_id = task.request_id

    provider = args.provider or options.get("provider") or os.environ.get("AGENT_PROVIDER", "mock")
    try:
        planner = _resolve_planner(provider)
    except (LLMError, TaskValidationError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    fail_tools = dict(options.get("fail_tools") or {})
    fail_tools.update(_parse_fail_tools(args.fail_tool or []))
    limits = AgentLimits(
        max_steps=args.max_steps or int(options.get("max_steps", 10)),
        max_tool_calls=args.max_tool_calls or int(options.get("max_tool_calls", 8)),
    )
    kb = KnowledgeBase(DATA_DIR)
    tools = ToolRuntime(
        task=task,
        kb=kb,
        fail_tools={str(key): int(value) for key, value in fail_tools.items()},
    )
    trace_path = (
        Path(args.trace).resolve()
        if args.trace
        else ROOT / ".runtime" / "traces" / f"{case_id}-{datetime.now(CST).strftime('%Y%m%d-%H%M%S')}.jsonl"
    )
    agent = KnowledgeQAAgent(planner, tools, limits=limits, trace_path=trace_path)
    result = agent.run(task)
    result["scenario"] = scenario or None
    if args.out:
        if args.out == "-":
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _write_json(Path(args.out).resolve(), result)
    if not args.quiet:
        _print_human(result)
    return 0 if result.get("terminal") else 3


def _cmd_list_docs(args: argparse.Namespace) -> int:
    kb = KnowledgeBase(DATA_DIR)
    for doc_id, doc in kb.documents.items():
        print(
            f"{doc_id}\t{doc.get('version')}\t{doc.get('status')}\t"
            f"{'/'.join(doc.get('scope_terms', []))}\t{doc.get('title')}"
        )
    return 0


def _cmd_show_doc(args: argparse.Namespace) -> int:
    kb = KnowledgeBase(DATA_DIR)
    doc = kb.get(args.doc_id)
    if doc is None:
        print(f"文档不存在：{args.doc_id}", file=sys.stderr)
        return 1
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    kb = KnowledgeBase(DATA_DIR)
    active = [doc for doc in kb.documents.values() if doc.get("status") == "active"]
    print(
        json.dumps(
            {
                "ok": True,
                "data_dir": str(kb.data_dir),
                "documents": len(kb.documents),
                "active": len(active),
                "superseded": len(kb.documents) - len(active),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="enterprise-knowledge-qa-agent",
        description="企业知识问答 Agent（引用原文、冲突追问、资料缺口）",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="运行一次问答")
    run.add_argument("--case", help="案例 JSON")
    run.add_argument("--question")
    run.add_argument("--request-id", default="REQ-CLI")
    run.add_argument("--history", help="JSON 数组：role/content")
    run.add_argument("--history-file", help="从文件读取对话历史 JSON")
    run.add_argument("--provider", choices=["mock", "openai"])
    run.add_argument("--fail-tool", action="append", default=[], help="注入失败，如 search_documents=99")
    run.add_argument("--max-steps", type=int)
    run.add_argument("--max-tool-calls", type=int)
    run.add_argument("--trace")
    run.add_argument("--out")
    run.add_argument("--quiet", action="store_true")
    run.set_defaults(func=_cmd_run)

    docs = sub.add_parser("list-docs", help="列出知识库文档")
    docs.set_defaults(func=_cmd_list_docs)

    show = sub.add_parser("show-doc", help="查看文档原文")
    show.add_argument("doc_id")
    show.set_defaults(func=_cmd_show_doc)

    verify = sub.add_parser("verify", help="校验知识库")
    verify.set_defaults(func=_cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    _load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (TaskValidationError, LLMError, FileNotFoundError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

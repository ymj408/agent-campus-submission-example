"""命令行入口。

常用：
  python run.py run --case examples/cases/02-conflict-alternative.json --reset
  python run.py run --material-id M03 --at 2026-11-06T12:00:00+08:00 --reset
  python run.py list
  python run.py show M01
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .agent import SchedulerAgent
from .llm import LLMError, planner_from_env
from .models import AgentLimits, TaskRequest, TaskValidationError
from .planner import MockPlanner
from .repository import RuntimeStore, SeedData
from .timeutil import CST
from .tools import ToolRuntime

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DEFAULT_STORE_ROOT = ROOT / ".runtime" / "cli"


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


def _read_case(path: Path) -> tuple[TaskRequest, dict[str, Any], dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskValidationError(f"无法读取 case：{path}：{exc}") from exc
    if not isinstance(payload, dict):
        raise TaskValidationError("case 必须是 JSON 对象")
    request_payload = payload.get("request") or payload
    request_payload = dict(request_payload)
    request_payload.setdefault("request_id", f"REQ-{path.stem.upper()}")
    request = TaskRequest.from_dict(request_payload)
    run_options = dict(payload.get("run") or {})
    scenario = {
        key: payload.get(key)
        for key in ("id", "title", "covers", "expect")
        if key in payload
    }
    return request, run_options, scenario


def _resolve_provider(name: str):
    if name == "mock":
        return MockPlanner()
    if name == "openai":
        return planner_from_env()
    raise TaskValidationError(f"未知 provider：{name}；可选 mock/openai")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _print_human(result: dict[str, Any]) -> None:
    task = result.get("task") or {}
    audit = result.get("audit") or {}
    draft = result.get("draft") or {}
    confirmation = result.get("confirmation") or {}
    print(f"运行 ID : {result.get('run_id')}")
    print(f"素材    : {task.get('material_id')} | 请求 {task.get('requested_publish_at')}")
    print(f"状态    : {result.get('status')} | 完成={result.get('completed')} | 已发布=否")
    if audit:
        print(f"审核    : {audit.get('decision')} | 规则版本 {audit.get('rule_version')}")
        for reason in audit.get("risk_reasons", [])[:4]:
            print(f"         - {reason}")
    if draft.get("saved"):
        print(
            f"草稿    : {draft.get('draft_id')} | {draft.get('proposed_publish_at')} | "
            f"{draft.get('status')} | published={draft.get('published')}"
        )
        print(f"草稿文件: {draft.get('path')}")
    else:
        error = draft.get("error") or {}
        print(f"草稿    : 未保存" + (f" | {error.get('code')}: {error.get('message')}" if error else ""))
    if confirmation:
        print(
            f"待确认  : {confirmation.get('confirmation_id')} | {confirmation.get('status')} | "
            f"用户已同意={confirmation.get('approved')}"
        )
    print(f"摘要    : {result.get('summary')}")
    pending = result.get("pending_items") or []
    if pending:
        print("待办    :")
        for item in pending:
            print(f"         - {item}")
    usage = result.get("usage") or {}
    print(f"用量    : {usage.get('steps')} 步 / {usage.get('tool_calls')} 次工具调用")
    if result.get("trace_path"):
        print(f"Trace   : {result.get('trace_path')}")


def _cmd_run(args: argparse.Namespace) -> int:
    case_options: dict[str, Any] = {}
    scenario: dict[str, Any] = {}
    if args.case:
        case_path = Path(args.case).resolve()
        request, case_options, scenario = _read_case(case_path)
        case_id = str(scenario.get("id") or case_path.stem)
    else:
        if not args.material_id or not args.at:
            print("错误：非 case 模式必须提供 --material-id 与 --at", file=sys.stderr)
            return 2
        request = TaskRequest(
            request_id=args.request_id or "REQ-CLI",
            material_id=args.material_id,
            requested_publish_at=args.at,
            allow_time_change=args.allow_time_change,
            note=args.note or "",
        )
        case_id = request.request_id

    provider_name = args.provider or case_options.get("provider") or os.environ.get("AGENT_PROVIDER", "mock")
    try:
        planner = _resolve_provider(provider_name)
    except (LLMError, TaskValidationError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    max_steps = args.max_steps or int(case_options.get("max_steps", 10))
    max_tool_calls = args.max_tool_calls or int(case_options.get("max_tool_calls", 8))
    limits = AgentLimits(max_steps=max_steps, max_tool_calls=max_tool_calls)
    fail_save = args.fail_save
    if fail_save is None:
        fail_save = int(case_options.get("fail_save_times", 0))
    if fail_save == 99:
        # 明确表达“每次都失败”，而不是只失败 99 次后恢复。
        fail_save = 10**9

    store_path = Path(args.store).resolve() if args.store else DEFAULT_STORE_ROOT / case_id
    seed = SeedData(DATA_DIR)
    store = RuntimeStore(store_path, seed)
    store.initialize(reset=args.reset)
    tools = ToolRuntime(
        task=request,
        seed=seed,
        store=store,
        fail_save_times=fail_save,
    )
    trace_path = (
        Path(args.trace).resolve()
        if args.trace
        else ROOT / ".runtime" / "traces" / f"{case_id}-{datetime.now(CST).strftime('%Y%m%d-%H%M%S')}.jsonl"
    )
    agent = SchedulerAgent(planner, tools, limits=limits, trace_path=trace_path)
    result = agent.run(request)
    result["scenario"] = scenario or None

    if args.out:
        if args.out == "-":
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            _write_json(Path(args.out).resolve(), result)
    if not args.quiet:
        _print_human(result)
    return 0 if result.get("terminal") else 3


def _cmd_list(args: argparse.Namespace) -> int:
    seed = SeedData(DATA_DIR)
    for material_id, material in seed.materials.items():
        print(
            f"{material_id}\t{material.get('title')}\t{material.get('platform')}\t"
            f"{material.get('publish_at')}\t{material.get('copyright')}"
        )
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    from .rules import RuleEngine

    seed = SeedData(DATA_DIR)
    material = seed.material(args.material_id)
    if material is None:
        print(f"素材不存在：{args.material_id}", file=sys.stderr)
        return 1
    requested = args.at or material.get("publish_at") or ""
    engine = RuleEngine(seed.rules, seed.rule_version)
    audit = engine.audit(material, requested)
    print(json.dumps({"material": material, "audit": audit.to_dict()}, ensure_ascii=False, indent=2))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    seed = SeedData(DATA_DIR)
    print(
        json.dumps(
            {
                "ok": True,
                "data_dir": str(seed.data_dir),
                "materials": len(seed.materials),
                "rules": len(seed.rules),
                "rule_version": seed.rule_version,
                "schedule_entries": len(seed.schedule_entries),
                "timezone": "Asia/Shanghai",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="media-scheduler-agent",
        description="素材审核与发布排期 Agent（仅保存草稿，不发布）",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="运行一次审核/排期任务")
    run.add_argument("--case", help="案例 JSON 文件")
    run.add_argument("--material-id", help="素材 ID（非 case 模式）")
    run.add_argument("--at", help="硬性要求时间，ISO 8601（非 case 模式）")
    run.add_argument("--request-id", default="REQ-CLI")
    run.add_argument("--note", default="")
    run.add_argument(
        "--allow-time-change",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否允许建议替代时段（默认允许，且仍需待确认）",
    )
    run.add_argument("--provider", choices=["mock", "openai"], help="默认读取 AGENT_PROVIDER 或 mock")
    run.add_argument("--store", help="运行状态目录")
    run.add_argument("--reset", action="store_true", help="清空并重建 store（仅限本项目创建的状态目录）")
    run.add_argument(
        "--fail-save",
        type=int,
        default=None,
        help="模拟草稿保存失败次数；99 表示每次失败",
    )
    run.add_argument("--max-steps", type=int, default=None)
    run.add_argument("--max-tool-calls", type=int, default=None)
    run.add_argument("--trace", help="Trace JSONL 路径")
    run.add_argument("--out", help="结果 JSON 路径；- 表示 stdout")
    run.add_argument("--quiet", action="store_true", help="不打印人类可读摘要")
    run.set_defaults(func=_cmd_run)

    show = sub.add_parser("show", help="查看素材与单次规则审核")
    show.add_argument("material_id")
    show.add_argument("--at", help="审核使用的时间；默认素材 publish_at")
    show.set_defaults(func=_cmd_show)

    ls = sub.add_parser("list", help="列出合成素材")
    ls.set_defaults(func=_cmd_list)

    verify = sub.add_parser("verify", help="校验数据文件可加载")
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

"""短视频脚本 Agent CLI。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .agent import ScriptAgent
from .knowledge import KnowledgeBase
from .llm import LLMError, planner_from_env
from .models import AgentLimits, TaskValidationError, VideoTask
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


def _read_case(path: Path) -> tuple[VideoTask, dict[str, Any], dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskValidationError(f"无法读取 case：{path}：{exc}") from exc
    if not isinstance(payload, dict):
        raise TaskValidationError("case 必须是 JSON 对象")
    request_payload = dict(payload.get("request") or payload)
    request_payload.setdefault("request_id", f"REQ-{path.stem.upper()}")
    task = VideoTask.from_dict(request_payload)
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
    script = result.get("script") or {}
    print(f"运行 ID : {result.get('run_id')}")
    print(f"主题    : {task.get('theme')} | 平台 {task.get('platform')} | 目标 {task.get('target_duration_seconds')} 秒")
    print(f"状态    : {result.get('status')} | 完成={result.get('completed')} | ready={script.get('ready')}")
    print(f"标题    : {result.get('title')}")
    print(f"总时长  : {result.get('total_duration_seconds')} 秒 | 目标 {result.get('target_duration_seconds')} 秒")
    revisions = result.get("revisions") or []
    if revisions:
        print("时长轨迹: " + " → ".join(f"{item.get('total_seconds')}s" for item in revisions))
    for scene in result.get("storyboard") or []:
        print(
            f"分镜 {scene.get('scene_id')}: {scene.get('seconds')}s | "
            f"{scene.get('visual')} | 旁白：{scene.get('voiceover')} | 字幕：{scene.get('subtitle')}"
        )
    sources = result.get("sources") or []
    if sources:
        print("素材来源:")
        for item in sources:
            print(f"         - {item.get('material_id')} {item.get('title')} | facts={item.get('facts')}")
    if result.get("risks"):
        print("风险    :")
        for item in result["risks"]:
            print(f"         - {item}")
    if result.get("pending_items"):
        print("待办    :")
        for item in result["pending_items"]:
            print(f"         - {item}")
    print(f"摘要    : {result.get('summary')}")
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
        if not args.theme:
            print("错误：非 case 模式必须提供 --theme", file=sys.stderr)
            return 2
        task = VideoTask(
            request_id=args.request_id or "REQ-CLI",
            theme=args.theme,
            audience=args.audience or "",
            platform=args.platform or "",
            target_duration_seconds=args.target_duration,
            style=args.style or "简洁可信",
            product=args.product or "",
            note=args.note or "",
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
    agent = ScriptAgent(planner, tools, limits=limits, trace_path=trace_path)
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


def _cmd_list_materials(args: argparse.Namespace) -> int:
    kb = KnowledgeBase(DATA_DIR)
    for material_id, material in kb.materials.items():
        print(
            f"{material_id}\t{material.get('usable')}\t{material.get('license')}\t"
            f"{material.get('title')}\tfacts={material.get('facts')}"
        )
    return 0


def _cmd_show_material(args: argparse.Namespace) -> int:
    kb = KnowledgeBase(DATA_DIR)
    material = kb.get(args.material_id)
    if material is None:
        print(f"素材不存在：{args.material_id}", file=sys.stderr)
        return 1
    print(json.dumps(material, ensure_ascii=False, indent=2))
    return 0


def _cmd_show_rules(args: argparse.Namespace) -> int:
    kb = KnowledgeBase(DATA_DIR)
    print(json.dumps(kb.rules, ensure_ascii=False, indent=2))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    kb = KnowledgeBase(DATA_DIR)
    print(
        json.dumps(
            {
                "ok": True,
                "data_dir": str(kb.data_dir),
                "materials": len(kb.materials),
                "usable": len(kb.usable_materials),
                "rule_version": kb.rules.get("version"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="short-video-script-agent",
        description="短视频脚本 Agent（时长修正、事实引用、规则检查）",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="运行一次脚本生成")
    run.add_argument("--case")
    run.add_argument("--theme")
    run.add_argument("--audience")
    run.add_argument("--platform")
    run.add_argument("--target-duration", type=int)
    run.add_argument("--style", default="简洁可信")
    run.add_argument("--product", default="")
    run.add_argument("--request-id", default="REQ-CLI")
    run.add_argument("--note", default="")
    run.add_argument("--provider", choices=["mock", "openai"])
    run.add_argument("--fail-tool", action="append", default=[], help="注入失败，如 check_rules=99")
    run.add_argument("--max-steps", type=int)
    run.add_argument("--max-tool-calls", type=int)
    run.add_argument("--trace")
    run.add_argument("--out")
    run.add_argument("--quiet", action="store_true")
    run.set_defaults(func=_cmd_run)

    materials = sub.add_parser("list-materials", help="列出素材")
    materials.set_defaults(func=_cmd_list_materials)

    show = sub.add_parser("show-material", help="查看素材")
    show.add_argument("material_id")
    show.set_defaults(func=_cmd_show_material)

    rules = sub.add_parser("show-rules", help="查看品牌规则")
    rules.set_defaults(func=_cmd_show_rules)

    verify = sub.add_parser("verify", help="校验素材与规则")
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

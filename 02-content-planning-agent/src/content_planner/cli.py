"""内容任务规划 Agent CLI。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .agent import ContentPlanningAgent
from .llm import LLMError, planner_from_env
from .models import AgentLimits, PlanningTask, TaskValidationError
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


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _parse_fail_steps(values: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in values:
        if "=" not in item:
            raise TaskValidationError(f"--fail-step 格式应为 key=次数：{item!r}")
        key, count_text = item.split("=", 1)
        try:
            count = int(count_text)
        except ValueError as exc:
            raise TaskValidationError(f"失败次数不是整数：{item!r}") from exc
        if not key or count < 1:
            raise TaskValidationError(f"--fail-step 不合法：{item!r}")
        result[key] = count
    return result


def _read_case(path: Path, run_index: int) -> tuple[PlanningTask, dict[str, Any], dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskValidationError(f"无法读取 case：{path}：{exc}") from exc
    if not isinstance(payload, dict):
        raise TaskValidationError("case 必须是 JSON 对象")
    request_payload = dict(payload.get("request") or payload)
    request_payload.setdefault("request_id", f"REQ-{path.stem.upper()}")
    task = PlanningTask.from_dict(request_payload)
    runs = payload.get("runs")
    if isinstance(runs, list) and runs:
        if not 0 <= run_index < len(runs):
            raise TaskValidationError(f"run_index 越界：{run_index}")
        options = dict(runs[run_index])
    else:
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
    plan = result.get("plan") or {}
    budget = result.get("budget") or {}
    print(f"运行 ID : {result.get('run_id')}")
    print(f"主题    : {task.get('theme')} | 预算 {task.get('budget')} | 截止 {task.get('deadline')}")
    print(
        f"状态    : {result.get('status')} | 完成={result.get('completed')} | "
        f"已保存方案={result.get('saved')}"
    )
    print(
        f"费用    : 合计 {budget.get('total')} / 预算 {budget.get('limit')} | "
        f"剩余 {budget.get('remaining')} | 超预算 {budget.get('over_by')}"
    )
    if plan:
        material_id = plan.get("material_id") or (plan.get("material") or {}).get("id")
        print(f"方案    : {plan.get('plan_id')} | {plan.get('label')} | 素材 {material_id}")
    if result.get("plan_path"):
        print(f"方案文件: {result.get('plan_path')}")
    steps = result.get("steps") or []
    if steps:
        print("步骤    :")
        for step in steps:
            print(
                f"         - {step.get('step_id'):12} {step.get('status'):10} "
                f"cost={step.get('cost')} artifact={(step.get('result') or {}).get('artifact_path')}"
            )
    print(f"摘要    : {result.get('summary')}")
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
        task, options, scenario = _read_case(case_path, args.run_index)
        case_id = str(scenario.get("id") or case_path.stem)
    else:
        if not args.theme:
            print("错误：非 case 模式必须提供 --theme", file=sys.stderr)
            return 2
        task = PlanningTask(
            request_id=args.request_id or "REQ-CLI",
            theme=args.theme,
            budget=args.budget,
            deadline=args.deadline,
            audience=args.audience or "",
            channel=args.channel or "",
            note=args.note or "",
        )
        case_id = task.request_id

    provider = args.provider or options.get("provider") or os.environ.get("AGENT_PROVIDER", "mock")
    try:
        planner = _resolve_planner(provider)
    except (LLMError, TaskValidationError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    fail_steps = dict(options.get("fail_steps") or {})
    fail_steps.update(_parse_fail_steps(args.fail_step or []))
    resume = bool(args.resume or options.get("resume", False))
    limits = AgentLimits(
        max_steps=args.max_steps or int(options.get("max_steps", 16)),
        max_tool_calls=args.max_tool_calls or int(options.get("max_tool_calls", 14)),
    )
    store_path = Path(args.store).resolve() if args.store else DEFAULT_STORE_ROOT / case_id
    seed = SeedData(DATA_DIR)
    store = RuntimeStore(store_path, seed)
    store.initialize(reset=args.reset)
    tools = ToolRuntime(
        task=task,
        seed=seed,
        store=store,
        fail_steps={str(key): int(value) for key, value in fail_steps.items()},
        resume=resume,
    )
    trace_path = (
        Path(args.trace).resolve()
        if args.trace
        else ROOT / ".runtime" / "traces" / f"{case_id}-{datetime.now(CST).strftime('%Y%m%d-%H%M%S')}.jsonl"
    )
    agent = ContentPlanningAgent(planner, tools, limits=limits, trace_path=trace_path)
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
    seed = SeedData(DATA_DIR)
    for material_id, material in seed.materials.items():
        print(
            f"{material_id}\t{material.get('title')}\t{material.get('type')}\t"
            f"cost={material.get('cost')}\tusable={material.get('usable')}"
        )
    return 0


def _cmd_list_plans(args: argparse.Namespace) -> int:
    seed = SeedData(DATA_DIR)
    for plan in seed.plans:
        print(
            f"{plan['id']}\t{plan['label']}\tmaterial={plan['material_id']}\t"
            f"total={seed.plan_total(plan['id'])}\tminutes={seed.plan_minutes(plan['id'])}"
        )
    return 0


def _cmd_show_plan(args: argparse.Namespace) -> int:
    seed = SeedData(DATA_DIR)
    plan = seed.plan(args.plan_id)
    if plan is None:
        print(f"方案不存在：{args.plan_id}", file=sys.stderr)
        return 1
    payload = {
        "plan": plan,
        "material": seed.material(plan["material_id"]),
        "total": seed.plan_total(args.plan_id),
        "minutes": seed.plan_minutes(args.plan_id),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    seed = SeedData(DATA_DIR)
    print(
        json.dumps(
            {
                "ok": True,
                "data_dir": str(seed.data_dir),
                "materials": len(seed.materials),
                "tools": len(seed.tools),
                "plans": len(seed.plans),
                "tool_cost_currency": seed.tools_doc.get("currency"),
                "timezone": "Asia/Shanghai",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="content-planning-agent",
        description="内容任务规划 Agent（预算/截止时间/失败重规划，仅保存方案）",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="运行一次内容规划")
    run.add_argument("--case", help="案例 JSON")
    run.add_argument("--run-index", type=int, default=0, help="案例 runs 数组中的序号")
    run.add_argument("--theme")
    run.add_argument("--budget", type=float)
    run.add_argument("--deadline")
    run.add_argument("--request-id", default="REQ-CLI")
    run.add_argument("--audience", default="")
    run.add_argument("--channel", default="")
    run.add_argument("--note", default="")
    run.add_argument("--provider", choices=["mock", "openai"])
    run.add_argument("--store")
    run.add_argument("--reset", action="store_true")
    run.add_argument("--resume", action="store_true", help="复用同一 store 的检查点续跑")
    run.add_argument("--fail-step", action="append", default=[], help="注入失败，如 review=99")
    run.add_argument("--max-steps", type=int)
    run.add_argument("--max-tool-calls", type=int)
    run.add_argument("--trace")
    run.add_argument("--out")
    run.add_argument("--quiet", action="store_true")
    run.set_defaults(func=_cmd_run)

    materials = sub.add_parser("list-materials", help="列出合成素材")
    materials.set_defaults(func=_cmd_list_materials)

    plans = sub.add_parser("list-plans", help="列出方案与成本/耗时")
    plans.set_defaults(func=_cmd_list_plans)

    show = sub.add_parser("show-plan", help="查看方案模板")
    show.add_argument("plan_id")
    show.set_defaults(func=_cmd_show_plan)

    verify = sub.add_parser("verify", help="校验数据文件")
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

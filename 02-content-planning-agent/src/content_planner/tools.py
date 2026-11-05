"""内容规划工具：检索、方案估算、脚本生成、步骤执行、方案保存。

关键边界：
- 预算超限是正常观察结果（ok=true, within_budget=false），不是伪造成功。
- 保存前重新检查预算、截止时间、依赖和全部步骤状态。
- 失败步骤被标记 failed，成功步骤保留在 step_results 中。
- 没有发布工具；save_plan 只写本地方案。
"""
from __future__ import annotations

import hashlib
import html
import json
from datetime import datetime
from typing import Any, Callable

from .models import PlanningTask
from .repository import RuntimeStore, SeedData
from .timeutil import CST, add_minutes, minutes_between, now_cst

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]

RATIONALE_SCHEMA = {
    "type": "string",
    "description": "一句话说明为什么现在做这个动作，不要写思维链。",
}


def _schema(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    props = dict(properties)
    props["rationale"] = RATIONALE_SCHEMA
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": props,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


def tool_specs() -> list[dict[str, Any]]:
    return [
        _schema(
            "get_brief",
            "读取活动主题、预算、截止时间，返回缺失字段、截止状态和已有方案。",
            {},
            [],
        ),
        _schema(
            "search_materials",
            "按主题/受众/渠道检索可用合成素材；授权过期素材会被排除。",
            {
                "query": {"type": "string", "description": "检索词；省略时使用主题+受众+渠道"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            [],
        ),
        _schema(
            "list_plan_options",
            "列出预置内容方案及其素材、总成本、总耗时；不执行步骤。",
            {},
            [],
        ),
        _schema(
            "estimate_plan",
            "估算指定方案的成本、剩余预算、完成时间与截止时间；超预算也是正常结果。",
            {"plan_id": {"type": "string", "description": "方案模板 ID"}},
            ["plan_id"],
        ),
        _schema(
            "generate_script",
            "为已估算且预算内的方案生成脚本；会写入 artifact，可失败。",
            {"plan_id": {"type": "string", "description": "已估算的方案 ID"}},
            ["plan_id"],
        ),
        _schema(
            "execute_step",
            "执行方案中的非脚本步骤（海报/剪辑/审核/分发），校验依赖并记录状态。",
            {
                "plan_id": {"type": "string", "description": "当前方案 ID"},
                "step_id": {"type": "string", "description": "要执行的步骤 ID"},
            },
            ["plan_id", "step_id"],
        ),
        _schema(
            "save_plan",
            "预算、截止时间和所有步骤成功后保存方案；重复请求幂等，不重复建方案。",
            {
                "plan_id": {"type": "string", "description": "要保存的方案 ID"},
                "note": {"type": "string", "description": "保存备注"},
            },
            ["plan_id"],
        ),
    ]


def _hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ok(tool: str, data: dict[str, Any], summary: str) -> dict[str, Any]:
    return {"ok": True, "tool": tool, "summary": summary, "data": data}


def _err(
    tool: str,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message, "retryable": retryable}
    if details:
        error["details"] = details
    return {"ok": False, "tool": tool, "summary": message, "error": error}


class ToolRuntime:
    def __init__(
        self,
        task: PlanningTask,
        seed: SeedData,
        store: RuntimeStore,
        *,
        fail_steps: dict[str, int] | None = None,
        resume: bool = False,
        now: datetime | None = None,
    ):
        self.task = task
        self.seed = seed
        self.store = store
        self.fail_counts = {str(key): int(value) for key, value in (fail_steps or {}).items()}
        self.resume = bool(resume)
        self.now = (now or now_cst()).astimezone(CST).replace(microsecond=0)

        self.brief: dict[str, Any] | None = None
        self.materials: dict[str, Any] | None = None
        self.options: list[dict[str, Any]] | None = None
        self.estimates: dict[str, dict[str, Any]] = {}
        self.current_plan: dict[str, Any] | None = None
        self.step_results: dict[str, dict[str, Any]] = {}
        self.failure_history: list[dict[str, Any]] = []
        self.saved_plan: dict[str, Any] | None = None
        self.save_error: dict[str, Any] | None = None
        self.save_attempts = 0
        self.last_save_deduplicated = False
        self.resumed_steps: list[str] = []
        self.tool_errors: list[dict[str, Any]] = []

        if self.resume:
            self._restore_checkpoint()

        self._handlers: dict[str, ToolHandler] = {
            "get_brief": self._get_brief,
            "search_materials": self._search_materials,
            "list_plan_options": self._list_plan_options,
            "estimate_plan": self._estimate_plan,
            "generate_script": self._generate_script,
            "execute_step": self._execute_step,
            "save_plan": self._save_plan,
        }

    # ------------------------------------------------------------------ API
    def call(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        args = dict(arguments or {})
        args.pop("rationale", None)
        handler = self._handlers.get(name)
        if handler is None:
            return _err(name or "unknown", "unknown_tool", f"未知工具：{name}")
        try:
            result = handler(args)
        except (KeyError, TypeError, ValueError) as exc:
            result = _err(name, "invalid_arguments", f"工具参数不合法：{exc}")
        except LookupError as exc:
            result = _err(name, "not_found", str(exc))
        except OSError as exc:
            result = _err(
                name,
                "storage_error",
                f"本地存储失败：{exc.__class__.__name__}",
                retryable=True,
            )
        except Exception as exc:  # noqa: BLE001
            result = _err(name, "internal_tool_error", f"工具内部错误：{exc.__class__.__name__}")
        if not result.get("ok"):
            self.tool_errors.append(
                {
                    "tool": name,
                    "code": (result.get("error") or {}).get("code"),
                    "message": result.get("summary", ""),
                }
            )
        return result

    def snapshot(self) -> dict[str, Any]:
        return {
            "brief": self.brief,
            "materials": self.materials,
            "options": self.options,
            "estimates": self.estimates,
            "current_plan": self.current_plan,
            "step_results": self.step_results,
            "failure_history": self.failure_history,
            "saved_plan": self.saved_plan,
            "save_error": self.save_error,
            "save_attempts": self.save_attempts,
            "last_save_deduplicated": self.last_save_deduplicated,
            "resumed_steps": self.resumed_steps,
            "tool_errors": self.tool_errors,
        }

    # -------------------------------------------------------------- storage
    def _restore_checkpoint(self) -> None:
        checkpoint = self.store.load_checkpoint(self.task.fingerprint)
        if not checkpoint or checkpoint.get("fingerprint") != self.task.fingerprint:
            return
        self.step_results = dict(checkpoint.get("step_results") or {})
        self.estimates = dict(checkpoint.get("estimates") or {})
        self.current_plan = checkpoint.get("current_plan")
        self.failure_history = list(checkpoint.get("failure_history") or [])
        self.saved_plan = checkpoint.get("saved_plan")
        self.resumed_steps = [
            step_id
            for step_id, result in self.step_results.items()
            if (result or {}).get("status") == "succeeded"
        ]

    def _checkpoint(self) -> None:
        try:
            self.store.save_checkpoint(
                self.task.fingerprint,
                {
                    "request_id": self.task.request_id,
                    "step_results": self.step_results,
                    "estimates": self.estimates,
                    "current_plan": self.current_plan,
                    "failure_history": self.failure_history,
                    "saved_plan": self.saved_plan,
                },
            )
        except OSError:
            # 检查点失败不应让已成功的工具结果消失；保存方案时仍会报错。
            pass

    def _maybe_fail(self, tool: str, key: str) -> dict[str, Any] | None:
        remaining = int(self.fail_counts.get(key, 0))
        if remaining == 0:
            return None
        if remaining > 0:
            self.fail_counts[key] = remaining - 1
        return _err(
            tool,
            "tool_failed",
            f"注入的工具故障：{key} 执行失败",
            retryable=True,
            details={"failed_key": key, "attempts_left": self.fail_counts.get(key)},
        )

    # --------------------------------------------------------------- tools
    def _get_brief(self, args: dict[str, Any]) -> dict[str, Any]:
        existing = self.store.find_saved_plan(self.task.fingerprint)
        if existing is not None:
            self.saved_plan = existing
            self.last_save_deduplicated = True
        deadline_minutes = None
        deadline_ok = None
        if self.task.deadline:
            deadline_minutes = minutes_between(self.task.deadline, self.now)
            deadline_ok = deadline_minutes >= 0
        data = {
            "request_id": self.task.request_id,
            "theme": self.task.theme,
            "budget": self.task.budget,
            "deadline": self.task.deadline,
            "audience": self.task.audience,
            "channel": self.task.channel,
            "missing_fields": self.task.missing_fields,
            "deadline_minutes_from_now": deadline_minutes,
            "deadline_ok": deadline_ok,
            "existing_plan": self._plan_summary(existing),
            "resumed_steps": self.resumed_steps,
            "resume_enabled": self.resume,
            "now": self.now.isoformat(timespec="seconds"),
        }
        self.brief = data
        if self.task.missing_fields:
            summary = f"任务信息不完整，缺少：{', '.join(self.task.missing_fields)}"
        elif existing is not None:
            summary = f"发现已保存方案 {existing.get('plan_id')}，可直接返回，避免重复执行"
        else:
            summary = f"读取任务：预算 {self.task.budget}，截止 {self.task.deadline}"
        return _ok("get_brief", data, summary)

    def _search_materials(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            query = " ".join(part for part in (self.task.theme, self.task.audience, self.task.channel) if part)
        limit = int(args.get("limit", 5))
        if not 1 <= limit <= 10:
            raise ValueError("limit 必须在 1-10 之间")
        error = self._maybe_fail("search_materials", "search_materials")
        if error:
            return error

        candidates: list[dict[str, Any]] = []
        excluded: list[str] = []
        for material_id, material in self.seed.materials.items():
            if not material.get("usable", False):
                excluded.append(material_id)
                continue
            tags = [str(tag) for tag in material.get("tags", [])]
            matched = [tag for tag in tags if tag and tag in query]
            if not matched:
                continue
            candidates.append(
                {
                    **material,
                    "matched_tags": matched,
                    "score": len(matched),
                }
            )
        candidates.sort(key=lambda item: (-int(item["score"]), float(item["cost"]), item["id"]))
        candidates = candidates[:limit]
        data = {
            "query": query,
            "candidates": candidates,
            "excluded_unusable": excluded,
            "count": len(candidates),
        }
        self.materials = data
        if candidates:
            summary = f"检索到 {len(candidates)} 个可用素材；首个为 {candidates[0]['id']}「{candidates[0]['title']}」"
        else:
            summary = "没有检索到可用素材，不能继续制定方案"
        return _ok("search_materials", data, summary)

    def _list_plan_options(self, args: dict[str, Any]) -> dict[str, Any]:
        options: list[dict[str, Any]] = []
        for plan in self.seed.plans:
            plan_id = str(plan["id"])
            total = self.seed.plan_total(plan_id)
            minutes = self.seed.plan_minutes(plan_id)
            budget = self.task.budget
            within = budget is not None and total <= budget
            options.append(
                {
                    "plan_id": plan_id,
                    "label": plan["label"],
                    "material_id": plan["material_id"],
                    "total": total,
                    "minutes": minutes,
                    "step_ids": [step["id"] for step in plan.get("steps", [])],
                    "within_budget": within,
                    "over_by": round(max(0.0, total - budget), 2) if budget is not None else None,
                    "budget": budget,
                }
            )
        self.options = options
        cheapest = min(options, key=lambda item: float(item["total"]))
        summary = (
            f"共 {len(options)} 个方案；最低成本 {cheapest['total']}（{cheapest['plan_id']}）"
        )
        return _ok("list_plan_options", {"options": options}, summary)

    def _build_plan_estimate(self, plan_id: str) -> dict[str, Any]:
        plan = self.seed.plan(plan_id)
        if plan is None:
            raise LookupError(f"方案不存在：{plan_id}")
        material = self.seed.material(str(plan["material_id"]))
        if material is None or not material.get("usable"):
            raise ValueError(f"方案 {plan_id} 的素材不可用")
        steps: list[dict[str, Any]] = []
        total = float(material.get("cost", 0))
        minutes = 0
        breakdown: list[dict[str, Any]] = [
            {
                "type": "material",
                "id": material["id"],
                "label": material["title"],
                "cost": float(material.get("cost", 0)),
            }
        ]
        for spec in plan.get("steps", []):
            tool = self.seed.tool(str(spec["tool"]))
            cost = float(tool["unit_cost"])
            step_minutes = int(tool["minutes"])
            total += cost
            minutes += step_minutes
            step = {
                "step_id": spec["id"],
                "label": spec["label"],
                "tool": spec["tool"],
                "depends_on": list(spec.get("depends_on", [])),
                "status": "pending",
                "cost": cost,
                "minutes": step_minutes,
            }
            cached = self.step_results.get(str(spec["id"]))
            if (
                cached
                and cached.get("status") == "succeeded"
                and cached.get("material_id") == material["id"]
            ):
                step.update(
                    {
                        "status": "succeeded",
                        "result": cached.get("result"),
                        "preserved": True,
                        "completed_at": cached.get("completed_at"),
                    }
                )
            steps.append(step)
            breakdown.append(
                {
                    "type": "tool",
                    "step_id": spec["id"],
                    "label": spec["label"],
                    "tool": spec["tool"],
                    "cost": cost,
                    "minutes": step_minutes,
                }
            )

        total = round(total, 2)
        budget = self.task.budget
        within_budget = budget is not None and total <= budget
        remaining = round(budget - total, 2) if budget is not None else None
        over_by = round(max(0.0, total - budget), 2) if budget is not None else None
        deadline_ok = None
        finish_by = None
        deadline_gap_minutes = None
        if self.task.deadline:
            finish_by = add_minutes(self.now, minutes).isoformat(timespec="seconds")
            deadline_gap_minutes = minutes_between(self.task.deadline, finish_by)
            deadline_ok = deadline_gap_minutes >= 0

        result = {
            "plan_id": plan_id,
            "label": plan["label"],
            "quality": plan.get("quality"),
            "material_id": material["id"],
            "material": material,
            "steps": steps,
            "total": total,
            "minutes": minutes,
            "budget": budget,
            "within_budget": within_budget,
            "remaining": remaining,
            "over_by": over_by,
            "deadline": self.task.deadline,
            "deadline_ok": deadline_ok,
            "finish_by": finish_by,
            "deadline_gap_minutes": deadline_gap_minutes,
            "breakdown": breakdown,
            "currency": self.seed.tools_doc.get("currency", "模拟单位"),
        }
        self.estimates[plan_id] = result
        self.current_plan = json.loads(json.dumps(result, ensure_ascii=False))
        self._checkpoint()
        return result

    def _estimate_plan(self, args: dict[str, Any]) -> dict[str, Any]:
        plan_id = str(args.get("plan_id") or "")
        result = self._build_plan_estimate(plan_id)
        if result["within_budget"]:
            summary = (
                f"方案 {plan_id} 估算 {result['total']}，预算 {result['budget']}，"
                f"剩余 {result['remaining']}"
            )
        else:
            summary = (
                f"方案 {plan_id} 估算 {result['total']}，超预算 {result['over_by']}"
            )
        if result["deadline_ok"] is False:
            summary += f"；截止时间超 {abs(result['deadline_gap_minutes'])} 分钟"
        return _ok("estimate_plan", result, summary)

    def _require_plan(self, plan_id: str) -> dict[str, Any]:
        if self.current_plan is None or self.current_plan.get("plan_id") != plan_id:
            raise ValueError("请先对当前方案调用 estimate_plan")
        if self.current_plan.get("within_budget") is not True:
            raise ValueError("当前方案超出预算，不能执行")
        if self.current_plan.get("deadline_ok") is False:
            raise ValueError("当前方案无法在截止时间前完成")
        if self.current_plan.get("deadline_ok") is None:
            raise ValueError("缺少截止时间，不能执行")
        return self.current_plan

    def _step(self, plan: dict[str, Any], step_id: str) -> dict[str, Any]:
        for step in plan.get("steps", []):
            if step.get("step_id") == step_id:
                return step
        raise LookupError(f"方案中没有步骤：{step_id}")

    def _resolve_failure(self, step_id: str) -> None:
        now = datetime.now(CST).isoformat(timespec="seconds")
        for item in reversed(self.failure_history):
            if item.get("step_id") == step_id and not item.get("resolved_at"):
                item["resolved_at"] = now
                item["resolved_status"] = "succeeded"

    def _mark_failed(self, plan: dict[str, Any], step: dict[str, Any], error: dict[str, Any]) -> None:
        step["status"] = "failed"
        step["error"] = error.get("error")
        step["completed_at"] = datetime.now(CST).isoformat(timespec="seconds")
        error.setdefault("error", {})["details"] = {
            "step_id": step.get("step_id"),
            "plan_id": plan.get("plan_id"),
            "preserved_steps": [
                item["step_id"]
                for item in plan.get("steps", [])
                if item.get("status") == "succeeded"
            ],
        }
        self.failure_history.append(
            {
                "plan_id": plan.get("plan_id"),
                "step_id": step.get("step_id"),
                "error": error.get("error"),
                "at": step["completed_at"],
                "preserved_steps": [
                    item["step_id"]
                    for item in plan.get("steps", [])
                    if item.get("status") == "succeeded"
                ],
            }
        )
        self._checkpoint()

    def _generate_script(self, args: dict[str, Any]) -> dict[str, Any]:
        plan_id = str(args.get("plan_id") or "")
        plan = self._require_plan(plan_id)
        step = self._step(plan, "script")
        if step.get("status") == "succeeded":
            cached = self.step_results.get("script") or {}
            return _ok(
                "generate_script",
                {
                    "deduplicated": True,
                    "step": step,
                    "plan": plan,
                    "result": cached.get("result"),
                    "artifact_path": (cached.get("result") or {}).get("artifact_path"),
                },
                "脚本步骤已成功，直接复用已有结果（续跑/重试）",
            )
        error = self._maybe_fail("generate_script", "script") or self._maybe_fail(
            "generate_script", "generate_script"
        )
        if error:
            self._mark_failed(plan, step, error)
            return error

        material = plan["material"]
        topic = self.task.theme
        audience = self.task.audience or "目标受众"
        channel = self.task.channel or "内容渠道"
        tags = "、".join(material.get("tags", []))
        script = (
            f"# {topic}｜{plan['label']}脚本\n\n"
            f"- 受众：{audience}\n"
            f"- 渠道：{channel}\n"
            f"- 素材：{material['id']} {material['title']}（{tags}）\n\n"
            f"## 开场（5 秒）\n用一个通勤场景切入：{topic} 到底怎么落地？\n\n"
            f"## 主体（3 个要点）\n"
            f"1. 结合素材「{material['title']}」展示第一处细节。\n"
            f"2. 给出一个可执行的做法，避免空泛描述。\n"
            f"3. 补充一个常见误区，并说明如何规避。\n\n"
            f"## 收尾（5 秒）\n回收主题，引导收藏；不承诺无法验证的效果。\n"
        )
        artifact = self.store.write_artifact(self.task.fingerprint, "script.md", script)
        result = {
            "script": script,
            "word_count": len(script),
            "artifact_path": artifact,
        }
        step.update(
            {
                "status": "succeeded",
                "result": result,
                "artifact_path": artifact,
                "completed_at": datetime.now(CST).isoformat(timespec="seconds"),
            }
        )
        self.step_results["script"] = {
            "step_id": "script",
            "status": "succeeded",
            "plan_id": plan_id,
            "material_id": material["id"],
            "cost": step["cost"],
            "minutes": step["minutes"],
            "result": result,
            "completed_at": step["completed_at"],
        }
        self._resolve_failure("script")
        self._checkpoint()
        return _ok(
            "generate_script",
            {
                "deduplicated": False,
                "step": step,
                "plan": plan,
                "result": result,
                "artifact_path": artifact,
            },
            f"脚本生成成功，{len(script)} 字符，已写入 {artifact}",
        )

    def _artifact_content(self, plan: dict[str, Any], step: dict[str, Any]) -> tuple[str, str]:
        step_id = str(step["step_id"])
        material = plan["material"]
        if step_id == "poster":
            svg = (
                '<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1440">'
                '<rect width="1080" height="1440" fill="#F5EFE6"/>'
                f'<text x="80" y="180" font-size="54" fill="#222">{html.escape(self.task.theme)}</text>'
                f'<text x="80" y="280" font-size="32" fill="#555">{html.escape(plan["label"])}</text>'
                f'<text x="80" y="360" font-size="26" fill="#777">{html.escape(material["title"])}</text>'
                "</svg>"
            )
            return "poster.svg", svg
        if step_id == "review":
            report = {
                "status": "approved_for_plan",
                "checks": ["预算内", "素材授权可用", "脚本无绝对化承诺", "步骤依赖完整"],
                "plan_id": plan["plan_id"],
            }
            return "review.json", json.dumps(report, ensure_ascii=False, indent=2)
        if step_id == "distribution":
            payload = {
                "plan_id": plan["plan_id"],
                "channel": self.task.channel or "未指定渠道",
                "finish_by": plan.get("finish_by"),
                "status": "scheduled_in_plan_only",
                "published": False,
            }
            return "distribution.json", json.dumps(payload, ensure_ascii=False, indent=2)
        if step_id == "video_edit":
            payload = {
                "plan_id": plan["plan_id"],
                "source_material": material["id"],
                "status": "edit_manifest_ready",
                "note": "合成演示：只生成剪辑清单，不生成真实视频",
            }
            return "video_edit.json", json.dumps(payload, ensure_ascii=False, indent=2)
        payload = {"plan_id": plan["plan_id"], "step_id": step_id, "status": "done"}
        return f"{step_id}.json", json.dumps(payload, ensure_ascii=False, indent=2)

    def _execute_step(self, args: dict[str, Any]) -> dict[str, Any]:
        plan_id = str(args.get("plan_id") or "")
        step_id = str(args.get("step_id") or "")
        plan = self._require_plan(plan_id)
        if step_id == "script":
            return _err(
                "execute_step",
                "wrong_tool",
                "script 步骤请使用 generate_script 工具",
            )
        step = self._step(plan, step_id)
        if step.get("status") == "succeeded":
            cached = self.step_results.get(step_id) or {}
            return _ok(
                "execute_step",
                {
                    "deduplicated": True,
                    "step": step,
                    "plan": plan,
                    "result": cached.get("result"),
                    "artifact_path": (cached.get("result") or {}).get("artifact_path"),
                },
                f"步骤 {step_id} 已成功，直接复用",
            )
        for dependency in step.get("depends_on", []):
            dep_step = self._step(plan, dependency)
            if dep_step.get("status") != "succeeded":
                return _err(
                    "execute_step",
                    "dependency_not_met",
                    f"步骤 {step_id} 依赖 {dependency}，但该依赖尚未成功",
                    details={"dependency": dependency, "status": dep_step.get("status")},
                )

        error = self._maybe_fail("execute_step", step_id) or self._maybe_fail(
            "execute_step", str(step.get("tool"))
        )
        if error:
            self._mark_failed(plan, step, error)
            return error

        filename, content = self._artifact_content(plan, step)
        artifact = self.store.write_artifact(self.task.fingerprint, filename, content)
        result = {
            "artifact_path": artifact,
            "label": step["label"],
            "step_id": step_id,
        }
        step.update(
            {
                "status": "succeeded",
                "result": result,
                "artifact_path": artifact,
                "completed_at": datetime.now(CST).isoformat(timespec="seconds"),
            }
        )
        self.step_results[step_id] = {
            "step_id": step_id,
            "status": "succeeded",
            "plan_id": plan_id,
            "material_id": plan["material_id"],
            "cost": step["cost"],
            "minutes": step["minutes"],
            "result": result,
            "completed_at": step["completed_at"],
        }
        self._resolve_failure(step_id)
        self._checkpoint()
        return _ok(
            "execute_step",
            {
                "deduplicated": False,
                "step": step,
                "plan": plan,
                "result": result,
                "artifact_path": artifact,
            },
            f"步骤 {step_id}（{step['label']}）执行成功，产物 {artifact}",
        )

    def _plan_summary(self, plan: dict[str, Any] | None) -> dict[str, Any] | None:
        if not plan:
            return None
        return {
            "plan_id": plan.get("plan_id"),
            "template_id": plan.get("template_id"),
            "total": (plan.get("estimate") or {}).get("total"),
            "status": plan.get("status"),
            "saved": plan.get("saved"),
        }

    def _save_plan(self, args: dict[str, Any]) -> dict[str, Any]:
        plan_id = str(args.get("plan_id") or "")
        plan = self._require_plan(plan_id)
        incomplete = [step for step in plan["steps"] if step.get("status") != "succeeded"]
        if incomplete:
            return _err(
                "save_plan",
                "steps_incomplete",
                "仍有步骤未成功，拒绝保存方案",
                details={
                    "incomplete_steps": [
                        {"step_id": step["step_id"], "status": step["status"]}
                        for step in incomplete
                    ]
                },
            )

        existing = self.store.find_saved_plan(self.task.fingerprint)
        if existing is not None:
            self.saved_plan = existing
            self.last_save_deduplicated = True
            return _ok(
                "save_plan",
                {
                    "saved": True,
                    "deduplicated": True,
                    "plan": existing,
                    "plan_path": f"plans/{existing['plan_id']}.json",
                },
                f"相同任务已有方案 {existing['plan_id']}，未重复保存",
            )

        error = self._maybe_fail("save_plan", "save") or self._maybe_fail("save_plan", "save_plan")
        if error:
            self.save_attempts += 1
            self.save_error = error["error"]
            self._checkpoint()
            return error

        idempotency_key = _hash(
            {"fingerprint": self.task.fingerprint, "plan_id": plan_id}
        )
        with self.store.locked():
            state = self.store.load_state()
            existing_id = state.get("plans_index", {}).get(idempotency_key)
            if existing_id:
                existing_plan = self.store.read_plan(str(existing_id))
                if existing_plan:
                    self.saved_plan = existing_plan
                    self.last_save_deduplicated = True
                    return _ok(
                        "save_plan",
                        {
                            "saved": True,
                            "deduplicated": True,
                            "plan": existing_plan,
                            "plan_path": f"plans/{existing_id}.json",
                        },
                        f"方案 {existing_id} 已存在，幂等返回",
                    )
            sequence = self.store.next_sequence(state)
            new_plan_id = f"PLAN-{sequence:04d}"
            payload = {
                "schema_version": 1,
                "plan_id": new_plan_id,
                "request_id": self.task.request_id,
                "fingerprint": self.task.fingerprint,
                "status": "saved",
                "saved": True,
                "template_id": plan_id,
                "label": plan["label"],
                "material_id": plan["material_id"],
                "request": self.task.to_dict(),
                "material": plan["material"],
                "estimate": plan,
                "steps": plan["steps"],
                "failure_history": self.failure_history,
                "note": str(args.get("note") or ""),
                "saved_at": datetime.now(CST).isoformat(timespec="seconds"),
            }
            path = None
            try:
                path = self.store.write_plan(payload)
                state.setdefault("plans_index", {})[idempotency_key] = new_plan_id
                state.setdefault("request_index", {})[self.task.fingerprint] = new_plan_id
                self.store.save_state(state)
            except OSError as exc:
                if path is not None:
                    try:
                        path.unlink()
                    except OSError:
                        pass
                self.save_attempts += 1
                error = _err(
                    "save_plan",
                    "save_failed",
                    f"方案写入失败：{exc.__class__.__name__}，未保存",
                    retryable=True,
                )
                self.save_error = error["error"]
                self._checkpoint()
                return error

        self.save_attempts += 1
        self.save_error = None
        self.last_save_deduplicated = False
        self.saved_plan = payload
        self._checkpoint()
        return _ok(
            "save_plan",
            {
                "saved": True,
                "deduplicated": False,
                "plan": payload,
                "plan_path": f"plans/{new_plan_id}.json",
            },
            f"方案 {new_plan_id} 已保存，总成本 {plan['total']}，预算 {plan['budget']}",
        )

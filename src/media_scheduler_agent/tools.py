"""Agent 可调用的业务工具。

关键安全边界：
- 工具集里没有 publish 接口；save_schedule_draft 只写入 drafts/ 与索引。
- 草稿保存会重新读取素材、重新审核、重新查冲突，不能信任模型参数。
- 请求时间来自 TaskRequest，不由模型参数覆盖。
- 不同时段必须持有待确认请求，且永远不能被工具标记为用户已同意。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Callable

from .models import TaskRequest
from .repository import RuntimeStore, SeedData
from .rules import RuleEngine
from .timeutil import (
    CST,
    day_window,
    format_datetime,
    minutes_between,
    parse_datetime,
    parse_hhmm,
    within_window,
)

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]

REQUIRED_RATIONALE = {
    "type": "string",
    "description": "一句话说明为什么现在执行这个动作，不要写思维链或内心推理。",
}

REASON_LABELS = {
    "slot_deviation": "时段偏离待确认",
    "missing_information": "素材信息不足",
    "review_warning": "规则风险待复核",
    "other": "其他待确认",
}


def _tool_schema(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
) -> dict[str, Any]:
    props = dict(properties)
    props["rationale"] = REQUIRED_RATIONALE
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
        _tool_schema(
            "get_material",
            "读取任务素材的元数据，并返回缺失的必填字段。",
            {"material_id": {"type": "string", "description": "素材 ID"}},
            ["material_id"],
        ),
        _tool_schema(
            "lookup_material_source",
            "信息缺失时补查制作系统/工单；只回填来源中非空字段，不伪造字段。",
            {"material_id": {"type": "string", "description": "素材 ID"}},
            ["material_id"],
        ),
        _tool_schema(
            "check_rules",
            "按规则库审核素材，返回 pass / needs_review / blocked、风险原因和缺失字段。",
            {"material_id": {"type": "string", "description": "素材 ID"}},
            ["material_id"],
        ),
        _tool_schema(
            "query_slots",
            "查询某素材请求时间是否可用，并返回同平台最近的可用替代时段。只读，不占位。",
            {
                "material_id": {"type": "string", "description": "素材 ID"},
                "requested_publish_at": {
                    "type": "string",
                    "description": "要查询的 ISO 8601 时间；省略时使用任务要求时间",
                },
                "limit": {
                    "type": "integer",
                    "description": "最多返回几个替代时段（1-10）",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            ["material_id"],
        ),
        _tool_schema(
            "request_confirmation",
            "创建待用户确认项（如改时段、补资料）；只记录 pending，不代表用户已同意，也不能发布。",
            {
                "material_id": {"type": "string", "description": "素材 ID"},
                "reason": {
                    "type": "string",
                    "enum": list(REASON_LABELS),
                    "description": "待确认类型",
                },
                "proposed_publish_at": {
                    "type": "string",
                    "description": "建议时段；信息补查类可省略",
                },
                "questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 5,
                    "description": "要向用户确认的具体问题",
                },
                "risk_summary": {"type": "string", "description": "风险或冲突的简要说明"},
            },
            ["material_id", "reason", "questions"],
        ),
        _tool_schema(
            "save_schedule_draft",
            "仅保存排期草稿（不发布）。保存前会重新审核、重新查冲突并校验待确认请求；相同请求幂等。",
            {
                "material_id": {"type": "string", "description": "素材 ID"},
                "proposed_publish_at": {
                    "type": "string",
                    "description": "建议排期时间；若与任务硬性要求不同，必须提供 confirmation_id",
                },
                "confirmation_id": {
                    "type": "string",
                    "description": "request_confirmation 返回的待确认 ID；未获用户同意时只能是 pending",
                },
                "note": {"type": "string", "description": "给审核人的草稿备注"},
            },
            ["material_id", "proposed_publish_at"],
        ),
    ]


def _canonical_hash(payload: dict[str, Any]) -> str:
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
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if details:
        error["details"] = details
    return {"ok": False, "tool": tool, "summary": message, "error": error}


class ToolRuntime:
    def __init__(
        self,
        task: TaskRequest,
        seed: SeedData,
        store: RuntimeStore,
        *,
        fail_save_times: int = 0,
    ):
        self.task = task
        self.seed = seed
        self.store = store
        self.engine = RuleEngine(seed.rules, seed.rule_version)
        self.fail_save_times = max(0, int(fail_save_times))
        window = seed.rules_doc.get("publishing_window", {})
        self.window_start = parse_hhmm(str(window.get("start", "09:00")))
        self.window_end = parse_hhmm(str(window.get("end", "21:00")))

        self.material: dict[str, Any] | None = None
        self.lookup_result: dict[str, Any] | None = None
        self.audit: dict[str, Any] | None = None
        self.slots: dict[str, Any] | None = None
        self.confirmation: dict[str, Any] | None = None
        self.draft: dict[str, Any] | None = None
        self.save_error: dict[str, Any] | None = None
        self.save_attempts = 0
        self.last_save_deduplicated = False

        self._handlers: dict[str, ToolHandler] = {
            "get_material": self._get_material,
            "lookup_material_source": self._lookup_material_source,
            "check_rules": self._check_rules,
            "query_slots": self._query_slots,
            "request_confirmation": self._request_confirmation,
            "save_schedule_draft": self._save_schedule_draft,
        }

    # ------------------------------------------------------------------ API
    def call(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        args = dict(arguments or {})
        args.pop("rationale", None)  # 仅用于 Trace，不进入业务逻辑。
        handler = self._handlers.get(name)
        if handler is None:
            return _err(name or "unknown", "unknown_tool", f"未知工具：{name}")
        try:
            return handler(args)
        except (KeyError, TypeError, ValueError) as exc:
            return _err(name, "invalid_arguments", f"工具参数不合法：{exc}")
        except LookupError as exc:
            return _err(name, "material_not_found", str(exc))
        except OSError as exc:
            return _err(
                name,
                "storage_error",
                f"本地存储失败：{exc.__class__.__name__}",
                retryable=True,
            )
        except Exception as exc:  # noqa: BLE001 - 返回可观察失败，不让进程崩溃
            return _err(
                name,
                "internal_tool_error",
                f"工具内部错误：{exc.__class__.__name__}",
                retryable=False,
            )

    def snapshot(self) -> dict[str, Any]:
        return {
            "material": self.material,
            "lookup": self.lookup_result,
            "audit": self.audit,
            "slots": self.slots,
            "confirmation": self.confirmation,
            "draft": self.draft,
            "save_error": self.save_error,
            "save_attempts": self.save_attempts,
            "last_save_deduplicated": self.last_save_deduplicated,
        }

    # -------------------------------------------------------------- helpers
    def _required_fields(self) -> list[dict[str, Any]]:
        for rule in self.seed.rules:
            if rule.get("type") == "required_fields":
                return list(rule.get("fields", []))
        return []

    def _missing_fields(self, material: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        for item in self._required_fields():
            name = item["name"]
            if name == "publish_at":
                continue
            value = material.get(name)
            if value is None or (isinstance(value, str) and not value.strip()):
                missing.append(name)
        return missing

    def _require_task_material(self, material_id: str) -> dict[str, Any]:
        if str(material_id) != self.task.material_id:
            raise ValueError(
                f"工具只允许操作任务绑定素材 {self.task.material_id}，收到 {material_id!r}"
            )
        material = self.store.get_material(self.task.material_id)
        if material is None:
            raise LookupError(f"素材不存在：{self.task.material_id}")
        return material

    def _slot_probe(self, platform: str, at: datetime) -> dict[str, Any]:
        at_text = format_datetime(at)
        schedule = self.store.schedule_conflict(platform, at_text)
        if schedule:
            return {"status": "occupied", "conflict": schedule}
        draft = self.store.draft_conflict(
            platform,
            at_text,
            ignore_material_id=self.task.material_id,
        )
        if draft:
            return {"status": "occupied", "conflict": draft}
        return {"status": "available"}

    def _confirmation_from_state(self, confirmation_id: str) -> dict[str, Any] | None:
        state = self.store.load_state()
        confirmation = state.get("confirmations", {}).get(confirmation_id)
        if confirmation is None:
            return None
        return dict(confirmation)

    # --------------------------------------------------------------- tools
    def _get_material(self, args: dict[str, Any]) -> dict[str, Any]:
        material = self._require_task_material(str(args.get("material_id", "")))
        missing = self._missing_fields(material)
        data = dict(material)
        data["missing_fields"] = missing
        data["source_notes_available"] = self.seed.source_note(self.task.material_id) is not None
        self.material = data
        if missing:
            summary = f"读取素材 {self.task.material_id}；缺失字段：{', '.join(missing)}"
        else:
            summary = f"读取素材 {self.task.material_id}；必填字段完整"
        return _ok("get_material", data, summary)

    def _lookup_material_source(self, args: dict[str, Any]) -> dict[str, Any]:
        material = self._require_task_material(str(args.get("material_id", "")))
        self.material = material
        note = self.seed.source_note(self.task.material_id)
        if note is None:
            data = {
                "found": False,
                "source": None,
                "resolved_fields": [],
                "still_missing": self._missing_fields(material),
                "note": "未找到可用的补充来源",
            }
            self.lookup_result = data
            return _ok(
                "lookup_material_source",
                data,
                f"补查素材 {self.task.material_id}：没有找到补充来源",
            )

        fields = note.get("fields") or {}
        resolved = {
            key: value
            for key, value in fields.items()
            if value not in (None, "") and (self.material or {}).get(key) in (None, "")
        }
        merged = self.store.apply_material_fields(self.task.material_id, resolved)
        missing = self._missing_fields(merged)
        data = {
            "found": True,
            "source": note.get("source"),
            "confidence": note.get("confidence"),
            "note": note.get("note"),
            "resolved_fields": resolved,
            "still_missing": missing,
            "material": merged,
        }
        self.material = merged
        self.lookup_result = data
        if resolved:
            summary = (
                f"补查成功（{note.get('source')}）：回填 {', '.join(resolved)}；"
                f"仍缺 {', '.join(missing) if missing else '无'}"
            )
        else:
            summary = f"补查完成（{note.get('source')}），没有新的非空字段；仍缺 {', '.join(missing)}"
        return _ok("lookup_material_source", data, summary)

    def _check_rules(self, args: dict[str, Any]) -> dict[str, Any]:
        material = self._require_task_material(str(args.get("material_id", "")))
        audit = self.engine.audit(material, self.task.requested_publish_at)
        self.material = material
        self.audit = audit.to_dict()
        if audit.decision == "blocked":
            summary = f"规则检查阻断：{audit.risk_reasons[0] if audit.risk_reasons else '存在阻断项'}"
        elif audit.decision == "needs_review":
            summary = f"规则检查需人工复核：{audit.risk_reasons[0] if audit.risk_reasons else '存在警告'}"
        else:
            summary = "规则检查通过，未发现风险"
        return _ok("check_rules", self.audit, summary)

    def _query_slots(self, args: dict[str, Any]) -> dict[str, Any]:
        material = self._require_task_material(str(args.get("material_id", "")))
        requested_text = str(args.get("requested_publish_at") or self.task.requested_publish_at)
        requested = parse_datetime(requested_text, field="requested_publish_at")
        limit = int(args.get("limit", 3))
        if not 1 <= limit <= 10:
            raise ValueError("limit 必须在 1-10 之间")
        day = requested.date().isoformat()

        timeline: list[dict[str, Any]] = []
        for point in day_window(day, self.window_start, self.window_end):
            probe = self._slot_probe(str(material["platform"]), point)
            timeline.append(
                {
                    "at": format_datetime(point),
                    "label": point.strftime("%H:%M"),
                    **probe,
                    "delta_minutes": minutes_between(point, requested),
                }
            )

        requested_probe = self._slot_probe(str(material["platform"]), requested)
        if not within_window(requested, self.window_start, self.window_end):
            requested_probe = {
                "status": "outside_window",
                "conflict": None,
                "message": "请求时间不在 09:00-21:00 发布窗口内",
            }
        if not any(item["at"] == format_datetime(requested) for item in timeline):
            timeline.append(
                {
                    "at": format_datetime(requested),
                    "label": requested.strftime("%H:%M"),
                    **requested_probe,
                    "delta_minutes": 0,
                }
            )
            timeline.sort(key=lambda item: item["at"])

        available = [
            item
            for item in timeline
            if item["status"] == "available"
            and item["at"] != format_datetime(requested)
            and abs(int(item["delta_minutes"])) <= 360
        ]
        # 冲突时优先建议“之后”的最近时段（参考实例 18:00 -> 19:00），
        # 没有更晚可用时段时才回退到更早时段。
        available.sort(
            key=lambda item: (
                0 if int(item["delta_minutes"]) > 0 else 1,
                abs(int(item["delta_minutes"])),
                item["at"],
            )
        )
        alternatives = [
            {
                "at": item["at"],
                "label": item["label"],
                "delta_minutes": item["delta_minutes"],
            }
            for item in available[:limit]
        ]
        occupied = [
            {
                "at": item["at"],
                "label": item["label"],
                "conflict": item.get("conflict"),
            }
            for item in timeline
            if item["status"] == "occupied"
        ]
        data = {
            "platform": material["platform"],
            "date": day,
            "requested_publish_at": format_datetime(requested),
            "requested": {
                "at": format_datetime(requested),
                "label": requested.strftime("%H:%M"),
                **requested_probe,
            },
            "alternatives": alternatives,
            "occupied_count": len(occupied),
            "occupied_sample": occupied[:5],
            "timezone": "Asia/Shanghai",
            "policy_window": {
                "start": self.window_start.strftime("%H:%M"),
                "end": self.window_end.strftime("%H:%M"),
            },
        }
        self.material = material
        self.slots = data
        req_status = data["requested"]["status"]
        if req_status == "available":
            summary = f"{requested.strftime('%H:%M')} 可用；最近替代时段：{alternatives[0]['label'] if alternatives else '无'}"
        elif req_status == "occupied":
            conflict = data["requested"].get("conflict") or {}
            owner = conflict.get("title") or conflict.get("material_id") or "已有排期/草稿"
            first = alternatives[0]["label"] if alternatives else "无"
            summary = f"{requested.strftime('%H:%M')} 已被「{owner}」占用；最近可用 {first}"
        else:
            summary = f"{requested.strftime('%H:%M')} 不在发布窗口内；最近可用 {alternatives[0]['label'] if alternatives else '无'}"
        return _ok("query_slots", data, summary)

    def _request_confirmation(self, args: dict[str, Any]) -> dict[str, Any]:
        self._require_task_material(str(args.get("material_id", "")))
        reason = str(args.get("reason", ""))
        if reason not in REASON_LABELS:
            raise ValueError(f"不支持的待确认类型：{reason}")
        questions = [str(item).strip() for item in args.get("questions", [])]
        questions = [item for item in questions if item]
        if not questions or len(questions) > 5:
            raise ValueError("questions 必须包含 1-5 条具体问题")
        proposed_raw = args.get("proposed_publish_at")
        proposed = format_datetime(proposed_raw) if proposed_raw else None
        if reason == "slot_deviation":
            if proposed is None:
                raise ValueError("slot_deviation 必须提供 proposed_publish_at")
            if proposed == self.task.requested_publish_at:
                raise ValueError("建议时段与硬性要求相同，无需创建时段偏离确认")
            if not self.task.allow_time_change:
                return _err(
                    "request_confirmation",
                    "time_change_not_allowed",
                    "任务明确不允许修改发布时间，不能创建改时段确认",
                )

        fingerprint = _canonical_hash(
            {
                "material_id": self.task.material_id,
                "requested_publish_at": self.task.requested_publish_at,
                "proposed_publish_at": proposed,
                "reason": reason,
                "questions": questions,
            }
        )
        with self.store.locked():
            state = self.store.load_state()
            confirmations = state.setdefault("confirmations", {})
            index = state.setdefault("confirmation_index", {})
            existing_id = index.get(fingerprint)
            if existing_id and existing_id in confirmations:
                confirmation = dict(confirmations[existing_id])
                deduplicated = True
            else:
                seq = self.store.next_sequence(state)
                confirmation_id = f"CONF-{seq:04d}"
                confirmation = {
                    "confirmation_id": confirmation_id,
                    "material_id": self.task.material_id,
                    "requested_publish_at": self.task.requested_publish_at,
                    "proposed_publish_at": proposed,
                    "reason": reason,
                    "reason_label": REASON_LABELS[reason],
                    "questions": questions,
                    "risk_summary": str(args.get("risk_summary") or ""),
                    "status": "pending_user_confirmation",
                    "approved": False,
                    "created_at": datetime.now(CST).isoformat(timespec="seconds"),
                }
                confirmations[confirmation_id] = confirmation
                index[fingerprint] = confirmation_id
                state["confirmations"] = confirmations
                state["confirmation_index"] = index
                self.store.save_state(state)
                deduplicated = False

        self.confirmation = confirmation
        result = dict(confirmation)
        result["deduplicated"] = deduplicated
        summary = (
            f"创建待确认项 {confirmation['confirmation_id']}（{confirmation['reason_label']}）；"
            "当前为 pending，未获得用户同意"
        )
        return _ok("request_confirmation", result, summary)

    def _save_schedule_draft(self, args: dict[str, Any]) -> dict[str, Any]:
        material = self._require_task_material(str(args.get("material_id", "")))
        proposed_text = str(args.get("proposed_publish_at") or "")
        proposed = parse_datetime(proposed_text, field="proposed_publish_at")
        proposed_iso = format_datetime(proposed)
        platform = str(material.get("platform") or "")
        if not within_window(proposed, self.window_start, self.window_end):
            return _err(
                "save_schedule_draft",
                "outside_publishing_window",
                "建议时段不在 09:00-21:00 发布窗口内，未保存草稿",
            )

        audit = self.engine.audit(material, self.task.requested_publish_at)
        self.material = material
        self.audit = audit.to_dict()
        if audit.decision == "blocked":
            return _err(
                "save_schedule_draft",
                "review_blocked",
                "规则审核未通过，拒绝保存草稿",
                details={"audit": audit.to_dict()},
            )

        deviation = proposed_iso != self.task.requested_publish_at
        confirmation: dict[str, Any] | None = None
        confirmation_id = args.get("confirmation_id")
        if deviation:
            if not self.task.allow_time_change:
                return _err(
                    "save_schedule_draft",
                    "time_change_not_allowed",
                    "任务明确不允许修改发布时间，拒绝保存不同时段的草稿",
                )
            if not confirmation_id:
                return _err(
                    "save_schedule_draft",
                    "confirmation_required",
                    "建议时段与硬性要求不同，必须先生成待确认项，未保存草稿",
                )
            confirmation = self._confirmation_from_state(str(confirmation_id))
            valid = (
                confirmation is not None
                and confirmation.get("material_id") == self.task.material_id
                and confirmation.get("requested_publish_at") == self.task.requested_publish_at
                and confirmation.get("proposed_publish_at") == proposed_iso
                and confirmation.get("status") == "pending_user_confirmation"
                and confirmation.get("approved") is False
            )
            if not valid:
                return _err(
                    "save_schedule_draft",
                    "confirmation_invalid",
                    "待确认项不存在、与建议时段不匹配或并非 pending，拒绝保存草稿",
                )

        idempotency_key = _canonical_hash(
            {
                "material_id": self.task.material_id,
                "requested_publish_at": self.task.requested_publish_at,
                "proposed_publish_at": proposed_iso,
            }
        )

        with self.store.locked():
            state = self.store.load_state()
            index = state.setdefault("draft_index", {})
            existing_id = index.get(idempotency_key)
            if existing_id:
                existing = self.store.read_draft(str(existing_id))
                if existing is None:
                    return _err(
                        "save_schedule_draft",
                        "draft_index_corrupt",
                        f"索引指向的草稿不存在：{existing_id}",
                        retryable=False,
                    )
                self.draft = existing
                self.last_save_deduplicated = True
                return _ok(
                    "save_schedule_draft",
                    {
                        "saved": True,
                        "deduplicated": True,
                        "draft": existing,
                        "draft_path": f"drafts/{existing['draft_id']}.json",
                    },
                    f"相同请求已存在草稿 {existing['draft_id']}，未重复创建",
                )

            if self.fail_save_times > 0:
                self.fail_save_times -= 1
                self.save_attempts += 1
                error = _err(
                    "save_schedule_draft",
                    "save_failed",
                    "模拟存储故障：草稿写入失败，未保存",
                    retryable=True,
                )
                self.save_error = error["error"]
                return error

            for draft in self.store.iter_drafts():
                if draft.get("material_id") == self.task.material_id:
                    return _err(
                        "save_schedule_draft",
                        "draft_exists",
                        f"该素材已有另一份草稿 {draft.get('draft_id')}，未重复创建",
                        details={"draft_id": draft.get("draft_id")},
                    )

            conflict = self.store.schedule_conflict(platform, proposed_iso)
            if conflict is None:
                conflict = self.store.draft_conflict(
                    platform,
                    proposed_iso,
                    ignore_material_id=self.task.material_id,
                )
            if conflict is not None:
                return _err(
                    "save_schedule_draft",
                    "slot_conflict",
                    f"建议时段 {proposed.strftime('%H:%M')} 已被占用，未保存草稿",
                    details={"conflict": conflict},
                )

            seq = self.store.next_sequence(state)
            draft_id = f"DRAFT-{seq:04d}"
            draft_status = "draft_ready"
            if audit.decision == "needs_review":
                draft_status = "draft_needs_review"
            elif deviation:
                draft_status = "draft_pending_confirmation"

            pending_items: list[str] = []
            if audit.needs_review:
                pending_items.extend(audit.review_items)
            if deviation and confirmation:
                pending_items.extend(confirmation.get("questions", []))

            draft = {
                "schema_version": 1,
                "draft_id": draft_id,
                "idempotency_key": idempotency_key,
                "status": draft_status,
                "published": False,
                "material_id": self.task.material_id,
                "title": material.get("title"),
                "platform": platform,
                "duration_minutes": material.get("duration_minutes"),
                "requested_publish_at": self.task.requested_publish_at,
                "proposed_publish_at": proposed_iso,
                "time_deviation": deviation,
                "confirmation_id": confirmation.get("confirmation_id") if confirmation else None,
                "confirmation_status": (
                    "pending_user_confirmation" if confirmation else "not_required"
                ),
                "audit": audit.to_dict(),
                "pending_items": pending_items,
                "note": str(args.get("note") or ""),
                "created_at": datetime.now(CST).isoformat(timespec="seconds"),
                "created_by": "media-scheduler-agent",
            }
            path = None
            try:
                path = self.store.write_draft(draft)
                state["draft_index"][idempotency_key] = draft_id
                self.store.save_state(state)
            except OSError as exc:
                if path is not None:
                    try:
                        path.unlink()
                    except OSError:
                        pass
                self.save_attempts += 1
                error = _err(
                    "save_schedule_draft",
                    "save_failed",
                    f"草稿写入失败：{exc.__class__.__name__}，未保存",
                    retryable=True,
                )
                self.save_error = error["error"]
                return error

        self.save_attempts += 1
        self.save_error = None
        self.last_save_deduplicated = False
        self.draft = draft
        if draft_status == "draft_pending_confirmation":
            summary = (
                f"已保存草稿 {draft_id}（未发布）：建议 {proposed.strftime('%H:%M')}，"
                f"原要求 {self.task.requested_publish_at[11:16]}，等待用户确认"
            )
        elif draft_status == "draft_needs_review":
            summary = f"已保存草稿 {draft_id}（未发布），需人工复核后再进入后续流程"
        else:
            summary = f"已保存草稿 {draft_id}（未发布）"
        return _ok(
            "save_schedule_draft",
            {
                "saved": True,
                "deduplicated": False,
                "draft": draft,
                "draft_path": f"drafts/{draft_id}.json",
            },
            summary,
        )

"""素材检索、时长估算与品牌规则检查。

关键边界：
- 总时长由工具从分镜 seconds 计算，模型自报的 total 不进入最终结果。
- claims 必须能在同一素材 facts 中找到，否则阻断。
- 无效果数据时，“效率提升 80%”既命中禁用词，也没有事实支持。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable

from .knowledge import KnowledgeBase
from .models import VideoTask
from .timeutil import CST

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]

RATIONALE_SCHEMA = {
    "type": "string",
    "description": "一句话说明为什么现在做这个动作，不要写思维链。",
}

SCENE_SCHEMA = {
    "type": "object",
    "properties": {
        "scene_id": {"type": "integer"},
        "visual": {"type": "string"},
        "voiceover": {"type": "string"},
        "subtitle": {"type": "string"},
        "seconds": {"type": "number"},
        "material_ids": {"type": "array", "items": {"type": "string"}},
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "material_id": {"type": "string"},
                },
                "required": ["text", "material_id"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["scene_id", "visual", "voiceover", "subtitle", "seconds"],
    "additionalProperties": False,
}

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "platform": {"type": "string"},
        "audience": {"type": "string"},
        "style": {"type": "string"},
        "target_duration_seconds": {"type": "integer"},
        "draft_version": {"type": "string"},
        "scenes": {"type": "array", "items": SCENE_SCHEMA},
    },
    "required": ["title", "platform", "audience", "target_duration_seconds", "scenes"],
    "additionalProperties": False,
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
        _schema("get_brief", "读取任务关键字段，返回缺失项。", {}, []),
        _schema(
            "search_materials",
            "检索可用产品素材，返回素材 ID、facts、授权和匹配片段；不返回过期素材。",
            {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            ["query"],
        ),
        _schema(
            "estimate_duration",
            "按分镜 seconds 估算总时长，并校验语速；返回是否超过目标时长。",
            {"script": SCRIPT_SCHEMA},
            ["script"],
        ),
        _schema(
            "check_rules",
            "检查必填字段、平台时长、分镜语速、素材授权、claims 事实支持和禁用表述。",
            {"script": SCRIPT_SCHEMA},
            ["script"],
        ),
    ]


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


def count_speech_chars(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", text or ""))


class ToolRuntime:
    def __init__(
        self,
        task: VideoTask,
        kb: KnowledgeBase,
        *,
        fail_tools: dict[str, int] | None = None,
    ):
        self.task = task
        self.kb = kb
        self.fail_counts = {str(key): int(value) for key, value in (fail_tools or {}).items()}
        self.brief: dict[str, Any] | None = None
        self.search_result: dict[str, Any] | None = None
        self.search_failed = False
        self.estimate_failed = False
        self.check_failed = False
        self.last_script: dict[str, Any] | None = None
        self.last_estimate: dict[str, Any] | None = None
        self.estimates: list[dict[str, Any]] = []
        self.rule_check: dict[str, Any] | None = None
        self.tool_errors: list[dict[str, Any]] = []
        self._handlers: dict[str, ToolHandler] = {
            "get_brief": self._get_brief,
            "search_materials": self._search_materials,
            "estimate_duration": self._estimate_duration,
            "check_rules": self._check_rules,
        }

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
            "search_result": self.search_result,
            "search_failed": self.search_failed,
            "estimate_failed": self.estimate_failed,
            "check_failed": self.check_failed,
            "last_script": self.last_script,
            "last_estimate": self.last_estimate,
            "estimates": self.estimates,
            "rule_check": self.rule_check,
            "tool_errors": self.tool_errors,
        }

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
            details={"failed_key": key},
        )

    # --------------------------------------------------------------- brief
    def _get_brief(self, args: dict[str, Any]) -> dict[str, Any]:
        data = dict(self.task.to_dict())
        self.brief = data
        if data["missing_fields"]:
            summary = "任务缺少关键信息：" + ", ".join(data["missing_fields"])
        else:
            summary = (
                f"读取任务：{data['theme']} / {data['platform']} / "
                f"目标 {data['target_duration_seconds']} 秒"
            )
        return _ok("get_brief", data, summary)

    # --------------------------------------------------------------- search
    def _search_materials(self, args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query 不能为空")
        limit = int(args.get("limit", 5))
        if not 1 <= limit <= 10:
            raise ValueError("limit 必须在 1-10 之间")
        error = self._maybe_fail("search_materials", "search_materials")
        if error:
            self.search_failed = True
            return error
        terms = {term for term in re.findall(r"[\u4e00-\u9fff]{2,6}|[A-Za-z0-9]+", query)}
        for keyword in ("耳机", "通勤", "字幕", "产品", "外观", "配件"):
            if keyword in query:
                terms.add(keyword)
        candidates: list[dict[str, Any]] = []
        for material_id, material in self.kb.usable_materials.items():
            text = " ".join(
                [
                    str(material.get("title", "")),
                    str(material.get("summary", "")),
                    " ".join(str(tag) for tag in material.get("tags", [])),
                    " ".join(str(fact) for fact in material.get("facts", [])),
                ]
            )
            matched = [term for term in terms if term in text]
            score = 0
            for term in matched:
                score += 3 if term in str(material.get("title", "")) else 1
                score += 2 if term in str(material.get("summary", "")) else 0
            if score <= 0:
                continue
            candidates.append(
                {
                    "material_id": material_id,
                    "title": material.get("title"),
                    "facts": list(material.get("facts", [])),
                    "license": material.get("license"),
                    "duration_seconds": material.get("duration_seconds"),
                    "tags": list(material.get("tags", [])),
                    "summary": material.get("summary"),
                    "score": score,
                    "matched_terms": matched,
                }
            )
        candidates.sort(key=lambda item: (-int(item["score"]), item["material_id"]))
        candidates = candidates[:limit]
        data = {"query": query, "candidates": candidates, "count": len(candidates)}
        self.search_result = data
        self.search_failed = False
        if candidates:
            summary = f"检索到 {len(candidates)} 个可用素材；首选 {candidates[0]['material_id']}"
        else:
            summary = f"没有检索到与「{query}」匹配的可用素材"
        return _ok("search_materials", data, summary)

    # ------------------------------------------------------------- duration
    def _duration(self, script: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(script, dict):
            raise ValueError("script 必须是对象")
        scenes = script.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            raise ValueError("script.scenes 不能为空")
        speech_rules = self.kb.rules.get("speech_rate", {})
        min_rate = float(speech_rules.get("min_chars_per_second", 2.0))
        max_rate = float(speech_rules.get("max_chars_per_second", 6.0))
        scene_estimates: list[dict[str, Any]] = []
        total = 0.0
        for index, scene in enumerate(scenes, start=1):
            if not isinstance(scene, dict):
                raise ValueError(f"第 {index} 个分镜必须是对象")
            voiceover = str(scene.get("voiceover") or "")
            chars = count_speech_chars(voiceover)
            seconds = scene.get("seconds")
            if seconds in (None, ""):
                seconds = max(3.0, round(1.5 + chars / 4.5, 1))
            try:
                seconds = round(float(seconds), 1)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"第 {index} 个分镜的 seconds 不是数字") from exc
            if seconds <= 0:
                raise ValueError(f"第 {index} 个分镜的 seconds 必须大于 0")
            rate = round(chars / seconds, 2) if seconds else 0
            pacing_ok = chars == 0 or min_rate <= rate <= max_rate
            total += seconds
            scene_estimates.append(
                {
                    "scene_id": scene.get("scene_id", index),
                    "seconds": seconds,
                    "speech_chars": chars,
                    "chars_per_second": rate,
                    "pacing_ok": pacing_ok,
                }
            )
        total = round(total, 1)
        target = self.task.target_duration_seconds
        within = target is not None and total <= target
        gap = round(total - target, 1) if target is not None else None
        return {
            "total_seconds": total,
            "target_seconds": target,
            "within_target": within,
            "gap_seconds": gap,
            "scene_estimates": scene_estimates,
        }

    def _estimate_duration(self, args: dict[str, Any]) -> dict[str, Any]:
        script = args.get("script")
        error = self._maybe_fail("estimate_duration", "estimate_duration")
        if error:
            self.estimate_failed = True
            return error
        duration = self._duration(script)
        self.last_script = script
        self.last_estimate = {**duration, "script": script, "draft_version": script.get("draft_version")}
        self.estimates.append(self.last_estimate)
        self.estimate_failed = False
        if duration["within_target"]:
            summary = (
                f"估算 {duration['total_seconds']} 秒，目标 {duration['target_seconds']} 秒，"
                f"剩余 {-duration['gap_seconds']} 秒"
            )
        else:
            summary = (
                f"估算 {duration['total_seconds']} 秒，超过目标 "
                f"{duration['gap_seconds']} 秒"
            )
        return _ok("estimate_duration", self.last_estimate, summary)

    # ---------------------------------------------------------------- rules
    def _check_rules(self, args: dict[str, Any]) -> dict[str, Any]:
        script = args.get("script")
        error = self._maybe_fail("check_rules", "check_rules")
        if error:
            self.check_failed = True
            return error
        if not isinstance(script, dict):
            raise ValueError("script 必须是对象")
        duration = self._duration(script)
        violations: list[dict[str, Any]] = []
        required = list(self.kb.rules.get("required_fields", []))
        for field in required:
            value = script.get(field)
            if value in (None, "", []):
                violations.append(
                    {
                        "rule_id": "R001",
                        "code": "missing_field",
                        "severity": "blocking",
                        "field": field,
                        "message": f"脚本缺少必填字段：{field}",
                    }
                )
        platform = str(script.get("platform") or self.task.platform)
        target = duration["target_seconds"]
        platform_range = self.kb.platform_range(platform)
        if platform_range and target is not None:
            if not (platform_range["min"] <= target <= platform_range["max"]):
                violations.append(
                    {
                        "rule_id": "R002",
                        "code": "target_out_of_range",
                        "severity": "blocking",
                        "field": "target_duration_seconds",
                        "message": (
                            f"{platform} 目标时长 {target} 秒不在 "
                            f"{platform_range['min']}-{platform_range['max']} 秒范围内"
                        ),
                    }
                )
        if target is not None and duration["total_seconds"] > target:
            violations.append(
                {
                    "rule_id": "R003",
                    "code": "duration_exceeded",
                    "severity": "blocking",
                    "field": "duration",
                    "message": (
                        f"估算 {duration['total_seconds']} 秒，超过目标 {target} 秒 "
                        f"{duration['gap_seconds']} 秒"
                    ),
                }
            )
        for estimate in duration["scene_estimates"]:
            if not estimate["pacing_ok"]:
                violations.append(
                    {
                        "rule_id": "R004",
                        "code": "pacing_invalid",
                        "severity": "blocking",
                        "field": f"scene[{estimate['scene_id']}]",
                        "message": (
                            f"分镜 {estimate['scene_id']} 语速 "
                            f"{estimate['chars_per_second']} 字/秒，不在可读范围"
                        ),
                    }
                )

        forbidden = [str(item) for item in self.kb.rules.get("forbidden_phrases", [])]
        for scene in script.get("scenes", []):
            scene_id = scene.get("scene_id")
            for field in ("voiceover", "subtitle"):
                text = str(scene.get(field) or "")
                for phrase in forbidden:
                    if phrase and phrase in text:
                        violations.append(
                            {
                                "rule_id": "R005",
                                "code": "forbidden_phrase",
                                "severity": "blocking",
                                "field": f"scene[{scene_id}].{field}",
                                "message": f"命中禁用表述：{phrase}",
                                "evidence": phrase,
                            }
                        )
            if self.kb.rules.get("required_subtitles") and not str(scene.get("subtitle") or "").strip():
                violations.append(
                    {
                        "rule_id": "R006",
                        "code": "missing_subtitle",
                        "severity": "blocking",
                        "field": f"scene[{scene_id}].subtitle",
                        "message": "每个分镜必须有字幕",
                    }
                )
            material_ids = [str(item) for item in scene.get("material_ids", [])]
            for material_id in material_ids:
                material = self.kb.get(material_id)
                if material is None or not material.get("usable"):
                    violations.append(
                        {
                            "rule_id": "R007",
                            "code": "unusable_material",
                            "severity": "blocking",
                            "field": f"scene[{scene_id}].material_ids",
                            "message": f"素材 {material_id} 不存在或不可用",
                        }
                    )
            for claim in scene.get("claims", []):
                text = str(claim.get("text") or "")
                material_id = str(claim.get("material_id") or "")
                material = self.kb.get(material_id)
                supported = material is not None and text in [
                    str(fact) for fact in material.get("facts", [])
                ]
                if not supported:
                    violations.append(
                        {
                            "rule_id": "R008",
                            "code": "unsupported_claim",
                            "severity": "blocking",
                            "field": f"scene[{scene_id}].claims",
                            "message": f"claim「{text}」没有素材 {material_id} 的事实支持",
                            "evidence": text,
                        }
                    )

        # 去重
        seen: set[tuple[str, str, str]] = set()
        unique: list[dict[str, Any]] = []
        for item in violations:
            key = (item["rule_id"], item["field"], item["message"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)

        decision = "blocked" if unique else "pass"
        data = {
            "decision": decision,
            "rule_version": self.kb.rules.get("version"),
            "violations": unique,
            "duration": duration,
            "script": script,
            "checked_at": datetime.now(CST).isoformat(timespec="seconds"),
        }
        self.check_failed = False
        self.rule_check = data
        self.last_script = script
        if decision == "pass":
            summary = f"规则检查通过；总时长 {duration['total_seconds']} 秒，claims 均有素材支持"
        else:
            summary = f"规则检查阻断：{unique[0]['message']}"
        return _ok("check_rules", data, summary)

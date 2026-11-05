"""Planner 协议与离线脚本修正策略。

离线策略：38 秒首稿 → 28 秒压缩稿 → 规则检查；必要时清理无依据 claim。
"""
from __future__ import annotations

from typing import Any, Protocol

from .models import Action, StepRecord, VideoTask


class Planner(Protocol):
    provider: str
    model: str

    def decide(self, task: VideoTask, history: list[StepRecord]) -> Action:
        ...


def _mem(history: list[StepRecord]) -> dict[str, Any]:
    memory: dict[str, Any] = {
        "brief": None,
        "search": None,
        "estimates": [],
        "estimate": None,
        "rule": None,
        "errors": [],
    }
    for record in history:
        if record.kind != "tool" or not record.observation:
            continue
        tool = record.tool
        observation = record.observation
        if not observation.get("ok"):
            memory["errors"].append({"tool": tool, "error": observation.get("error")})
            continue
        data = observation.get("data") or {}
        if tool == "get_brief":
            memory["brief"] = data
        elif tool == "search_materials":
            memory["search"] = data
        elif tool == "estimate_duration":
            memory["estimate"] = data
            memory["estimates"].append(data)
        elif tool == "check_rules":
            memory["rule"] = data
    return memory


def _failures(history: list[StepRecord], tool: str) -> int:
    return sum(
        1
        for record in history
        if record.kind == "tool"
        and record.tool == tool
        and record.observation
        and not record.observation.get("ok")
    )


def _scene(
    scene_id: int,
    visual: str,
    voiceover: str,
    subtitle: str,
    seconds: float,
    *,
    claims: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "scene_id": scene_id,
        "visual": visual,
        "voiceover": voiceover,
        "subtitle": subtitle,
        "seconds": seconds,
        "material_ids": ["P01"],
        "claims": claims or [],
    }


def build_script(
    task: VideoTask,
    variant: str,
    *,
    include_unsupported_claim: bool = False,
) -> dict[str, Any]:
    """构造长/短/迷你版本；variant 控制 38/28/14 秒。"""
    target = task.target_duration_seconds or 30
    title = f"{task.theme}｜{target} 秒字幕功能演示"
    support = [{"text": "支持字幕", "material_id": "P01"}]
    if variant == "long":
        scenes = [
            _scene(
                1,
                "通勤地铁内，手持耳机，字幕浮层出现。",
                "通勤路上戴上耳机，地铁噪音很大，打开字幕就能看懂关键信息。",
                "通勤路上，打开字幕也能看懂关键信息。",
                10,
                claims=support,
            ),
            _scene(
                2,
                "耳机外观与佩戴细节特写。",
                "镜头展示耳机外观和佩戴细节，字幕同步说明当前画面。",
                "耳机外观与佩戴细节特写。",
                10,
            ),
            _scene(
                3,
                "耳机放回充电盒，设置界面特写。",
                "把耳机放回充电盒，画面提示字幕功能可以在设置中开启。",
                "字幕功能可在设置中开启。",
                9,
                claims=support,
            ),
            _scene(
                4,
                "产品与字幕界面同框，收尾。",
                "结尾提醒观众按需开启字幕，并以实际使用为准。",
                "按需开启字幕，实际使用为准。",
                9,
            ),
        ]
    elif variant in {"short", "short_clean"}:
        scenes = [
            _scene(
                1,
                "通勤地铁内，字幕浮层出现。",
                "通勤路上打开字幕，地铁再吵也能看懂关键信息。",
                "通勤打开字幕，地铁再吵也能看懂。",
                7,
                claims=support,
            ),
            _scene(
                2,
                "耳机外观与佩戴细节特写。",
                "耳机外观与佩戴细节特写，字幕同步说明。",
                "耳机外观与佩戴细节。",
                7,
            ),
            _scene(
                3,
                "设置界面特写。",
                "在设置中开启字幕，画面展示操作路径。",
                "设置中可开启字幕。",
                7,
                claims=support,
            ),
            _scene(
                4,
                "产品与字幕界面同框。",
                "按需开启字幕，请以实际使用为准。",
                "按需开启，实际为准。",
                7,
            ),
        ]
    elif variant == "mini":
        scenes = [
            _scene(
                1,
                "通勤地铁内，字幕浮层出现。",
                "通勤打开字幕，地铁也看得清。",
                "通勤打开字幕。",
                4,
                claims=support,
            ),
            _scene(
                2,
                "耳机外观特写。",
                "外观与佩戴细节特写。",
                "外观与佩戴细节。",
                4,
            ),
            _scene(
                3,
                "设置界面特写。",
                "设置中开启字幕。",
                "设置中开启字幕。",
                3,
                claims=support,
            ),
            _scene(
                4,
                "产品收尾。",
                "按需开启，实际为准。",
                "按需开启，实际为准。",
                3,
            ),
        ]
    else:
        raise ValueError(f"未知脚本版本：{variant}")

    if include_unsupported_claim:
        # 故意加入无素材事实支持的效果结论；规则检查应阻断。
        scenes[2]["voiceover"] += "效率提升 80%，通勤更高效。"
        scenes[2]["subtitle"] += "效率提升 80%。"
        scenes[2]["claims"].append({"text": "效率提升 80%", "material_id": "P01"})

    return {
        "title": title,
        "platform": task.platform,
        "audience": task.audience,
        "style": task.style,
        "target_duration_seconds": target,
        "draft_version": variant,
        "scenes": scenes,
    }


class MockPlanner:
    provider = "mock"
    model = "deterministic-script-revision-policy-v1"

    def decide(self, task: VideoTask, history: list[StepRecord]) -> Action:
        memory = _mem(history)
        brief = memory["brief"]
        search = memory["search"]
        estimate = memory["estimate"]
        rule = memory["rule"]

        if brief is None:
            return Action.tool_call(
                "get_brief",
                {"rationale": "先确认主题、受众、平台、目标时长是否齐全。"},
                "读取任务简报，确认关键字段。",
            )

        if brief.get("missing_fields"):
            return Action.finish(
                "blocked_missing_fields",
                pending_items=["补充：" + "、".join(brief["missing_fields"])],
                rationale="关键信息缺失，不能编造受众或时长。",
            )

        if search is None:
            last = self._last_error(history, "search_materials")
            if last and not last.get("retryable"):
                return Action.finish("tool_failure", rationale="素材检索失败且不可重试。")
            if _failures(history, "search_materials") >= 2:
                return Action.finish("tool_failure", rationale="素材检索连续失败。")
            return Action.tool_call(
                "search_materials",
                {
                    "query": " ".join(part for part in (task.theme, task.product) if part),
                    "limit": 5,
                    "rationale": "检索可用产品素材，优先找有事实依据的片段。",
                },
                "检索产品素材，确认可用来源。",
            )

        if not search.get("candidates"):
            return Action.finish(
                "insufficient_material",
                pending_items=["补充产品素材，或调整主题后重试。"],
                rationale="没有可用素材，不能生成有事实依据的脚本。",
            )

        if _failures(history, "estimate_duration") >= 2:
            return Action.finish("tool_failure", rationale="时长估算连续失败，无法确认脚本时长。")
        if _failures(history, "check_rules") >= 2:
            return Action.finish("tool_failure", rationale="规则检查连续失败，无法确认脚本合规。")

        # 参考实例固定使用 P01（只包含“支持字幕”事实）。
        # 时长修正：38 → 28 → 14；仍超时则检查并明确失败。
        if estimate is None:
            script = build_script(task, "long")
            return Action.tool_call(
                "estimate_duration",
                {
                    "script": script,
                    "rationale": "先生成长版首稿，用工具估算总时长。",
                },
                "生成首稿并估算时长。",
            )

        last_script = estimate.get("script") or {}
        version = last_script.get("draft_version") or "long"
        if not estimate.get("within_target"):
            if version == "long":
                script = build_script(
                    task,
                    "short",
                    include_unsupported_claim=("夸张" in task.style or "强营销" in task.style),
                )
                return Action.tool_call(
                    "estimate_duration",
                    {
                        "script": script,
                        "rationale": "首稿超出目标时长，压缩到 28 秒后重新估算。",
                    },
                    "首稿超时，压缩脚本。",
                )
            if version == "short":
                script = build_script(task, "mini")
                return Action.tool_call(
                    "estimate_duration",
                    {
                        "script": script,
                        "rationale": "28 秒仍超时，继续压缩到 14 秒版本。",
                    },
                    "继续压缩脚本。",
                )
            if rule is None or rule.get("script") != last_script:
                return Action.tool_call(
                    "check_rules",
                    {
                        "script": last_script,
                        "rationale": "已到最短版本仍超时，检查规则并报告无法满足。",
                    },
                    "检查最终候选，确认无法满足时长。",
                )
            return Action.finish(
                "duration_unmet",
                summary=(
                    f"最短版本 {estimate['total_seconds']} 秒仍超过目标 "
                    f"{estimate['target_seconds']} 秒"
                ),
                pending_items=["放宽目标时长，或补充可删减的素材/分镜。"],
                rationale="多次压缩后仍超时，明确失败，不虚报满足时长。",
                payload={"script": last_script, "duration": estimate},
            )

        # 时长通过后检查规则。
        if rule is None or rule.get("script") != last_script:
            return Action.tool_call(
                "check_rules",
                {
                    "script": last_script,
                    "rationale": "时长已满足，检查 claims、禁用词、素材授权和语速。",
                },
                "时长通过，执行品牌规则检查。",
            )

        if rule.get("decision") == "blocked":
            codes = {item.get("code") for item in rule.get("violations", [])}
            if "unsupported_claim" in codes or "forbidden_phrase" in codes:
                if version in {"long", "short"}:
                    clean = build_script(task, "short_clean", include_unsupported_claim=False)
                    return Action.tool_call(
                        "estimate_duration",
                        {
                            "script": clean,
                            "rationale": "规则检查发现无依据效果 claim，删除后重新估算和检查。",
                        },
                        "删除无依据 claim，重新走时长与规则检查。",
                    )
                return Action.finish(
                    "rule_blocked",
                    summary="规则检查阻断：无依据效果 claim 或禁用表述。",
                    rationale="无法在保留事实依据的前提下通过规则检查。",
                    payload={"script": last_script, "rule": rule},
                )
            if "duration_exceeded" in codes:
                return Action.finish(
                    "duration_unmet",
                    summary="规则检查确认总时长超过目标。",
                    pending_items=["放宽目标时长，或减少分镜内容。"],
                    rationale="时长不满足，明确失败。",
                    payload={"script": last_script, "rule": rule},
                )
            return Action.finish(
                "rule_blocked",
                summary="规则检查阻断。",
                pending_items=[item["message"] for item in rule.get("violations", [])],
                rationale="规则不通过，不能输出 ready 脚本。",
                payload={"script": last_script, "rule": rule},
            )

        return Action.finish(
            "script_ready",
            payload={
                "script": last_script,
                "duration": estimate,
                "rule": rule,
            },
            rationale="时长与规则检查均通过，输出可编辑脚本。",
        )

    @staticmethod
    def _last_error(history: list[StepRecord], tool: str) -> dict[str, Any] | None:
        for record in reversed(history):
            if record.kind == "tool" and record.tool == tool and record.observation:
                if not record.observation.get("ok"):
                    return record.observation.get("error") or {}
                return None
        return None

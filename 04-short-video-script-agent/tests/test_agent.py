from __future__ import annotations

import unittest
from pathlib import Path

from tests.support import TestDir, kb, make_task, read_jsonl, run_agent

from script_agent.models import Action, StepRecord, VideoTask
from script_agent.planner import build_script


class FalseReadyPlanner:
    provider = "test"
    model = "test-false-ready"

    def decide(self, task: VideoTask, history: list[StepRecord]) -> Action:
        n = len(history)
        if n == 0:
            return Action.tool_call("get_brief", {}, "读简报")
        if n == 1:
            return Action.tool_call("search_materials", {"query": "通勤耳机"}, "查素材")
        if n == 2:
            script = build_script(task, "short", include_unsupported_claim=True)
            return Action.tool_call("estimate_duration", {"script": script}, "估算")
        if n == 3:
            script = build_script(task, "short", include_unsupported_claim=True)
            return Action.tool_call("check_rules", {"script": script}, "检查")
        return Action.finish(
            "script_ready",
            payload={"script": build_script(task, "short", include_unsupported_claim=True)},
            rationale="故意谎报 ready",
        )


class DurationLiePlanner:
    provider = "test"
    model = "test-duration-lie"

    def decide(self, task: VideoTask, history: list[StepRecord]) -> Action:
        n = len(history)
        if n == 0:
            return Action.tool_call("get_brief", {}, "读简报")
        if n == 1:
            return Action.tool_call("search_materials", {"query": "通勤耳机"}, "查素材")
        if n == 2:
            return Action.tool_call("estimate_duration", {"script": build_script(task, "long")}, "估算")
        return Action.finish(
            "script_ready",
            payload={"script": build_script(task, "short")},
            rationale="故意谎报 28 秒",
        )


class AgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.kb = kb()

    def _run(self, task, **kwargs):
        return run_agent(task, kb_data=self.kb, **kwargs)

    def test_normal_38_to_28(self) -> None:
        result = self._run(make_task())
        self.assertEqual(result["status"], "script_ready")
        self.assertTrue(result["completed"])
        self.assertEqual(result["total_duration_seconds"], 28)
        self.assertEqual([item["total_seconds"] for item in result["revisions"]], [38, 28])
        self.assertEqual(result["rule_check"]["decision"], "pass")
        self.assertEqual(result["sources"][0]["material_id"], "P01")
        self.assertNotIn("效率提升 80%", str(result["script"]))
        self.assertEqual(result["script"]["ready"], True)
        self.assertEqual(len(result["storyboard"]), 4)

    def test_missing_critical_info(self) -> None:
        result = self._run(make_task(audience="", platform="", target=None))
        self.assertEqual(result["status"], "blocked_missing_fields")
        self.assertIsNone(result["script"])
        self.assertIn("audience", result["task"]["missing_fields"])
        self.assertIn("target_duration_seconds", result["task"]["missing_fields"])

    def test_no_material(self) -> None:
        result = self._run(make_task(theme="宠物猫粮", product=""))
        self.assertEqual(result["status"], "insufficient_material")
        self.assertIsNone(result["script"])
        self.assertEqual(result["sources"], [])

    def test_duration_unmet(self) -> None:
        result = self._run(make_task(target=10))
        self.assertEqual(result["status"], "duration_unmet")
        self.assertFalse(result["completed"])
        self.assertEqual(result["total_duration_seconds"], 14)
        self.assertEqual([item["total_seconds"] for item in result["revisions"]], [38, 28, 14])
        self.assertFalse(result["script"]["ready"])
        self.assertTrue(any("仍超过目标" in item for item in result["pending_items"]))

    def test_rule_risk_is_cleaned(self) -> None:
        result = self._run(make_task(style="夸张营销"))
        self.assertEqual(result["status"], "script_ready")
        self.assertEqual(result["total_duration_seconds"], 28)
        self.assertEqual([item["total_seconds"] for item in result["revisions"]], [38, 28, 28])
        self.assertNotIn("效率提升 80%", str(result["script"]))
        self.assertEqual(result["rule_check"]["decision"], "pass")

    def test_search_failure(self) -> None:
        result = self._run(make_task(), fail_tools={"search_materials": 10**9})
        self.assertEqual(result["status"], "tool_failure")
        self.assertIsNone(result["script"])
        self.assertFalse(result["completed"])

    def test_estimate_failure(self) -> None:
        result = self._run(make_task(), fail_tools={"estimate_duration": 10**9})
        self.assertEqual(result["status"], "tool_failure")
        self.assertIsNone(result["script"])

    def test_check_failure(self) -> None:
        result = self._run(make_task(), fail_tools={"check_rules": 10**9})
        self.assertEqual(result["status"], "tool_failure")
        self.assertFalse(result["script"]["ready"])

    def test_runtime_rejects_false_ready_on_rule_violation(self) -> None:
        result = self._run(make_task(), planner=FalseReadyPlanner())
        self.assertEqual(result["model_claim"]["status"], "script_ready")
        self.assertEqual(result["status"], "rule_blocked")
        self.assertFalse(result["script"]["ready"])
        self.assertTrue(any("效率提升 80%" in item for item in result["risks"]))

    def test_runtime_rejects_false_duration_claim(self) -> None:
        result = self._run(make_task(), planner=DurationLiePlanner())
        self.assertEqual(result["model_claim"]["status"], "script_ready")
        self.assertEqual(result["status"], "duration_unmet")
        self.assertEqual(result["total_duration_seconds"], 38)
        self.assertFalse(result["script"]["ready"])

    def test_target_change_changes_revision_path(self) -> None:
        result = self._run(make_task(target=20))
        self.assertEqual(result["status"], "script_ready")
        self.assertEqual([item["total_seconds"] for item in result["revisions"]], [38, 28, 14])
        self.assertEqual(result["total_duration_seconds"], 14)
        self.assertTrue(result["script"]["ready"])

    def test_step_limit(self) -> None:
        result = self._run(make_task(), max_steps=2)
        self.assertEqual(result["status"], "step_limit_reached")
        self.assertFalse(result["completed"])
        self.assertIsNone(result["script"])

    def test_trace_contains_checks(self) -> None:
        with TestDir("svs-trace-") as tmp:
            trace = Path(tmp) / "trace.jsonl"
            result = self._run(make_task(), trace_path=trace)
            events = read_jsonl(trace)
            steps = [event for event in events if event.get("event") == "step"]
            self.assertTrue(any(event.get("tool") == "estimate_duration" for event in steps))
            self.assertTrue(any(event.get("tool") == "check_rules" for event in steps))
            self.assertEqual(events[-1]["status"], "script_ready")


if __name__ == "__main__":
    unittest.main()

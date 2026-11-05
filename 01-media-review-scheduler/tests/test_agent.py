from __future__ import annotations

import unittest
from pathlib import Path

from tests.support import make_task, read_jsonl, run_agent, temp_store

from media_scheduler_agent.models import Action, StepRecord, TaskRequest


class SaveThenLiePlanner:
    """先经历持久保存失败，再谎报 draft_saved；用于验证运行时不以模型自报为准。"""

    provider = "test"
    model = "test-liar"

    def decide(self, task: TaskRequest, history: list[StepRecord]) -> Action:
        n = len(history)
        if n == 0:
            return Action.tool_call("get_material", {"material_id": task.material_id}, "读素材")
        if n == 1:
            return Action.tool_call("check_rules", {"material_id": task.material_id}, "查规则")
        if n == 2:
            return Action.tool_call("query_slots", {"material_id": task.material_id}, "查时段")
        if n in (3, 4):
            return Action.tool_call(
                "save_schedule_draft",
                {
                    "material_id": task.material_id,
                    "proposed_publish_at": "2026-11-06T14:00:00+08:00",
                },
                "保存草稿",
            )
        return Action.finish("draft_saved", "我声称保存成功", rationale="谎报用于测试")


class AgentPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp, self.store, self.seed = temp_store()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, task, **kwargs):
        return run_agent(task, seed_data=self.seed, store=self.store, **kwargs)

    def test_normal_path(self) -> None:
        result = self._run(make_task("M03"))
        self.assertEqual(result["status"], "draft_saved")
        self.assertTrue(result["completed"])
        self.assertFalse(result["published"])
        self.assertFalse(result["draft"]["published"])
        tools = [step.get("tool") for step in result["steps"] if step["action"] == "tool"]
        self.assertEqual(
            tools,
            ["get_material", "check_rules", "query_slots", "save_schedule_draft"],
        )
        self.assertNotIn("已发布", result["summary"])
        self.assertEqual(self.store.summary()["draft_count"], 1)

    def test_conflict_path_requests_confirmation_and_keeps_requested_time(self) -> None:
        result = self._run(make_task("M01"))
        self.assertEqual(result["status"], "draft_saved_pending_confirmation")
        self.assertTrue(result["completed"])
        self.assertFalse(result["published"])
        self.assertEqual(result["draft"]["requested_publish_at"], "2026-11-06T18:00:00+08:00")
        self.assertEqual(result["draft"]["proposed_publish_at"], "2026-11-06T19:00:00+08:00")
        self.assertEqual(result["confirmation"]["status"], "pending_user_confirmation")
        self.assertFalse(result["confirmation"]["approved"])
        self.assertIn("等待用户确认", result["summary"])

    def test_duplicate_submission_produces_one_draft(self) -> None:
        task = make_task("M01")
        first = self._run(task)
        second = self._run(task)
        self.assertEqual(first["draft"]["draft_id"], second["draft"]["draft_id"])
        self.assertFalse(first["draft"]["deduplicated"])
        self.assertTrue(second["draft"]["deduplicated"])
        self.assertEqual(self.store.summary()["draft_count"], 1)
        self.assertEqual(first["confirmation"]["confirmation_id"], second["confirmation"]["confirmation_id"])

    def test_missing_information_path_looks_up_then_blocks(self) -> None:
        result = self._run(make_task("M02"))
        self.assertEqual(result["status"], "blocked_missing_info")
        self.assertFalse(result["completed"])
        self.assertFalse(result["published"])
        self.assertFalse(result["draft"]["saved"])
        tools = [step.get("tool") for step in result["steps"] if step["action"] == "tool"]
        self.assertIn("lookup_material_source", tools)
        self.assertIn("request_confirmation", tools)
        self.assertNotIn("save_schedule_draft", tools)
        self.assertIn("duration_minutes", result["audit"]["missing_fields"])
        self.assertNotIn("copyright", result["audit"]["missing_fields"])
        self.assertTrue(any("时长" in item for item in result["pending_items"]))

    def test_blocking_rule_path_does_not_schedule(self) -> None:
        result = self._run(make_task("M04"))
        self.assertEqual(result["status"], "blocked_rule_risk")
        self.assertFalse(result["completed"])
        self.assertFalse(result["draft"]["saved"])
        self.assertEqual(self.store.summary()["draft_count"], 0)
        self.assertTrue(any("R004" in item for item in result["pending_items"]))

    def test_warning_path_saves_review_draft(self) -> None:
        result = self._run(make_task("M06"))
        self.assertEqual(result["status"], "draft_saved_needs_review")
        self.assertTrue(result["completed"])
        self.assertEqual(result["draft"]["status"], "draft_needs_review")
        self.assertTrue(result["draft"]["published"] is False)
        self.assertTrue(any("R005" in item for item in result["pending_items"]))

    def test_persistent_save_failure_is_explicit(self) -> None:
        result = self._run(make_task("M05"), fail_save_times=10**9)
        self.assertEqual(result["status"], "save_failed")
        self.assertFalse(result["completed"])
        self.assertFalse(result["published"])
        self.assertFalse(result["draft"]["saved"])
        self.assertEqual(result["draft"]["error"]["code"], "save_failed")
        attempts = [
            step
            for step in result["steps"]
            if step.get("tool") == "save_schedule_draft"
        ]
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(step["ok"] is False for step in attempts))
        self.assertEqual(self.store.summary()["draft_count"], 0)

    def test_hard_time_requirement_blocks_instead_of_substituting(self) -> None:
        result = self._run(make_task("M01", allow_time_change=False))
        self.assertEqual(result["status"], "blocked_time_change_not_allowed")
        self.assertFalse(result["completed"])
        self.assertFalse(result["draft"]["saved"])
        self.assertEqual(self.store.summary()["draft_count"], 0)

    def test_step_limit_is_explicit_and_traced(self) -> None:
        trace = Path(self.tmp.name) / "step-limit.jsonl"
        result = self._run(make_task("M03"), max_steps=2, max_tool_calls=8, trace_path=trace)
        self.assertEqual(result["status"], "step_limit_reached")
        self.assertFalse(result["completed"])
        self.assertFalse(result["terminal"])
        self.assertEqual(result["limit"]["kind"], "step_limit")
        events = read_jsonl(trace)
        self.assertTrue(any(event.get("event") == "limit" for event in events))
        self.assertEqual(events[-1]["event"], "run_end")
        self.assertFalse(events[-1]["published"])

    def test_tool_limit_is_explicit(self) -> None:
        result = self._run(make_task("M03"), max_steps=10, max_tool_calls=2)
        self.assertEqual(result["status"], "tool_limit_reached")
        self.assertFalse(result["completed"])
        self.assertEqual(result["limit"]["kind"], "tool_limit")
        self.assertEqual(result["usage"]["tool_calls"], 2)

    def test_runtime_rejects_model_false_success_claim(self) -> None:
        result = self._run(
            make_task("M05"),
            planner=SaveThenLiePlanner(),
            fail_save_times=10**9,
        )
        self.assertEqual(result["finish_claim"]["status"], "draft_saved")
        self.assertEqual(result["status"], "save_failed")
        self.assertFalse(result["completed"])
        self.assertFalse(result["draft"]["saved"])
        self.assertFalse(result["published"])

    def test_trace_contains_observation_and_decision_basis(self) -> None:
        trace = Path(self.tmp.name) / "normal.jsonl"
        result = self._run(make_task("M03"), trace_path=trace)
        self.assertEqual(result["status"], "draft_saved")
        events = read_jsonl(trace)
        steps = [event for event in events if event.get("event") == "step"]
        self.assertGreaterEqual(len(steps), 4)
        self.assertTrue(all("decision_basis" in event for event in steps))
        self.assertTrue(any((event.get("observation") or {}).get("ok") for event in steps))
        self.assertEqual(events[0]["mode"], "draft_only")


if __name__ == "__main__":
    unittest.main()

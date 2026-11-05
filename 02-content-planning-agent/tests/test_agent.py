from __future__ import annotations

import unittest
from pathlib import Path

from tests.support import (
    make_task,
    read_jsonl,
    run_agent,
    temp_store,
)

from content_planner.models import Action, PlanningTask, StepRecord


class SaveThenLiePlanner:
    provider = "test"
    model = "test-liar"

    def decide(self, task: PlanningTask, history: list[StepRecord]) -> Action:
        n = len(history)
        if n == 0:
            return Action.tool_call("get_brief", {}, "读任务")
        if n == 1:
            return Action.tool_call("search_materials", {"query": task.theme}, "查素材")
        if n == 2:
            return Action.tool_call("list_plan_options", {}, "列方案")
        if n == 3:
            return Action.tool_call("estimate_plan", {"plan_id": "premium_video"}, "估高价方案")
        if n == 4:
            return Action.tool_call("estimate_plan", {"plan_id": "lean_article"}, "估替代方案")
        if n == 5:
            return Action.tool_call("generate_script", {"plan_id": "lean_article"}, "生成脚本")
        if n in (6, 7, 8):
            step = {6: "poster", 7: "review", 8: "distribution"}[n]
            return Action.tool_call(
                "execute_step",
                {"plan_id": "lean_article", "step_id": step},
                f"执行 {step}",
            )
        if n in (9, 10):
            return Action.tool_call("save_plan", {"plan_id": "lean_article"}, "保存")
        return Action.finish("plan_saved", "我声称已保存", rationale="谎报用于测试")


class AgentPathTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp, self.store, self.seed = temp_store()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, task, **kwargs):
        return run_agent(task, seed_data=self.seed, store=self.store, **kwargs)

    def test_normal_budget_replan(self) -> None:
        result = self._run(make_task(budget=10))
        self.assertEqual(result["status"], "plan_saved")
        self.assertTrue(result["completed"])
        self.assertTrue(result["saved"])
        self.assertEqual(result["budget"]["total"], 8)
        self.assertEqual(result["budget"]["remaining"], 2)
        self.assertEqual(result["plan"]["template_id"], "lean_article")
        self.assertEqual(
            [step["status"] for step in result["steps"]],
            ["succeeded", "succeeded", "succeeded", "succeeded"],
        )
        tools = [item.get("tool") for item in result["step_records"]]
        self.assertEqual(tools.count("estimate_plan"), 2)
        self.assertIn("save_plan", tools)

    def test_missing_budget_blocks(self) -> None:
        result = self._run(make_task(budget=None))
        self.assertEqual(result["status"], "blocked_missing_fields")
        self.assertFalse(result["completed"])
        self.assertFalse(result["saved"])
        self.assertIn("budget", result["task"]["missing_fields"])
        self.assertIsNone(result["plan"])

    def test_missing_deadline_blocks(self) -> None:
        result = self._run(make_task(deadline=None))
        self.assertEqual(result["status"], "blocked_missing_fields")
        self.assertIn("deadline", result["task"]["missing_fields"])
        self.assertFalse(result["saved"])

    def test_no_feasible_plan_reports_gap(self) -> None:
        result = self._run(make_task(budget=3))
        self.assertEqual(result["status"], "incomplete_no_feasible_plan")
        self.assertFalse(result["completed"])
        self.assertFalse(result["saved"])
        self.assertEqual(result["budget"]["over_by"], 3)
        self.assertTrue(any("缺口 3" in item for item in result["pending_items"]))

    def test_poster_failure_replans_and_preserves_script(self) -> None:
        result = self._run(make_task(budget=10), fail_steps={"poster": 10**9})
        self.assertEqual(result["status"], "plan_saved")
        self.assertEqual(result["plan"]["template_id"], "lean_no_poster")
        self.assertEqual(result["budget"]["total"], 7)
        self.assertEqual(result["failure_history"][-1]["step_id"], "poster")
        self.assertTrue(
            any(step["step_id"] == "script" and step["status"] == "succeeded" for step in result["steps"])
        )
        self.assertFalse(any(step["step_id"] == "poster" for step in result["steps"]))
        self.assertFalse(result["pending_items"])

    def test_review_failure_preserves_successes_and_does_not_save(self) -> None:
        result = self._run(make_task(budget=10), fail_steps={"review": 10**9})
        self.assertEqual(result["status"], "incomplete_tool_failure")
        self.assertFalse(result["completed"])
        self.assertFalse(result["saved"])
        statuses = {step["step_id"]: step["status"] for step in result["steps"]}
        self.assertEqual(statuses["script"], "succeeded")
        self.assertEqual(statuses["poster"], "succeeded")
        self.assertEqual(statuses["review"], "failed")
        self.assertNotEqual(statuses["distribution"], "succeeded")
        self.assertEqual(self.store.summary()["plan_count"], 0)

    def test_resume_after_failure_skips_succeeded_steps(self) -> None:
        first = self._run(make_task(budget=10), fail_steps={"review": 10**9})
        self.assertEqual(first["status"], "incomplete_tool_failure")
        second = self._run(make_task(budget=10), resume=True)
        self.assertEqual(second["status"], "plan_saved")
        tools = [item.get("tool") for item in second["step_records"]]
        self.assertNotIn("generate_script", tools)
        self.assertEqual(second["plan"]["template_id"], "lean_article")
        self.assertEqual(second["budget"]["total"], 8)
        self.assertEqual(second["failure_history"][-1]["step_id"], "review")
        self.assertIn("resolved_at", second["failure_history"][-1])

    def test_duplicate_execution_returns_one_plan(self) -> None:
        first = self._run(make_task(budget=10))
        second = self._run(make_task(budget=10))
        self.assertEqual(first["status"], "plan_saved")
        self.assertEqual(second["status"], "plan_saved_deduplicated")
        self.assertTrue(second["saved"])
        self.assertEqual(first["plan"]["plan_id"], second["plan"]["plan_id"])
        self.assertEqual(self.store.summary()["plan_count"], 1)
        self.assertEqual(len([item for item in second["step_records"] if item.get("tool")]), 1)

    def test_save_failure_preserves_steps_but_no_plan(self) -> None:
        result = self._run(make_task(budget=10), fail_steps={"save": 10**9})
        self.assertEqual(result["status"], "save_failed")
        self.assertFalse(result["saved"])
        self.assertTrue(all(step["status"] == "succeeded" for step in result["steps"]))
        self.assertEqual(self.store.summary()["plan_count"], 0)
        self.assertEqual(len([item for item in result["step_records"] if item.get("tool") == "save_plan"]), 2)

    def test_step_limit_is_explicit_and_traced(self) -> None:
        trace = Path(self.tmp.name) / "limit.jsonl"
        result = self._run(make_task(budget=10), max_steps=3, trace_path=trace)
        self.assertEqual(result["status"], "step_limit_reached")
        self.assertFalse(result["completed"])
        self.assertFalse(result["terminal"])
        events = read_jsonl(trace)
        self.assertTrue(any(event.get("event") == "limit" for event in events))
        self.assertEqual(events[-1]["event"], "run_end")

    def test_tool_limit_is_explicit(self) -> None:
        result = self._run(make_task(budget=10), max_tool_calls=2)
        self.assertEqual(result["status"], "tool_limit_reached")
        self.assertFalse(result["completed"])
        self.assertEqual(result["usage"]["tool_calls"], 2)

    def test_runtime_rejects_false_save_claim(self) -> None:
        result = self._run(
            make_task(budget=10),
            planner=SaveThenLiePlanner(),
            fail_steps={"save": 10**9},
        )
        self.assertEqual(result["finish_claim"]["status"], "plan_saved")
        self.assertEqual(result["status"], "save_failed")
        self.assertFalse(result["saved"])
        self.assertFalse(result["completed"])

    def test_trace_has_decision_basis_and_artifacts(self) -> None:
        trace = Path(self.tmp.name) / "normal.jsonl"
        result = self._run(make_task(budget=10), trace_path=trace)
        events = read_jsonl(trace)
        steps = [event for event in events if event.get("event") == "step"]
        self.assertTrue(all("decision_basis" in event for event in steps))
        self.assertTrue(any((event.get("observation") or {}).get("data", {}).get("artifact_path") for event in steps))
        self.assertEqual(events[-1]["saved"], True)


if __name__ == "__main__":
    unittest.main()

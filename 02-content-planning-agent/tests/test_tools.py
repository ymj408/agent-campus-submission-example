from __future__ import annotations

import unittest
from pathlib import Path

from tests.support import make_task, make_tools, seed, temp_store


class ToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp, self.store, self.seed = temp_store()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _selected_lean(self, tools):
        self.assertTrue(tools.call("get_brief", {})["ok"])
        self.assertTrue(tools.call("search_materials", {"query": "秋季通勤穿搭"})["ok"])
        self.assertTrue(tools.call("list_plan_options", {})["ok"])
        estimate = tools.call("estimate_plan", {"plan_id": "lean_article"})
        self.assertTrue(estimate["ok"], estimate)
        return estimate["data"]

    def test_brief_missing_budget_and_deadline(self) -> None:
        task = make_task(budget=None, deadline=None)
        tools = make_tools(task, self.seed, self.store)
        data = tools.call("get_brief", {})["data"]
        self.assertEqual(data["missing_fields"], ["budget", "deadline"])
        self.assertIsNone(data["deadline_ok"])

    def test_search_excludes_expired_material(self) -> None:
        task = make_task()
        tools = make_tools(task, self.seed, self.store)
        data = tools.call("search_materials", {"query": "秋季通勤穿搭"})["data"]
        ids = {item["id"] for item in data["candidates"]}
        self.assertIn("MAT-01", ids)
        self.assertNotIn("MAT-04", ids)
        self.assertIn("MAT-04", data["excluded_unusable"])
        self.assertTrue(all(item["usable"] for item in data["candidates"]))

    def test_plan_costs_match_reference(self) -> None:
        self.assertEqual(self.seed.plan_total("premium_video"), 12)
        self.assertEqual(self.seed.plan_total("lean_article"), 8)
        self.assertEqual(self.seed.plan_total("lean_no_poster"), 7)
        self.assertEqual(self.seed.plan_total("organic_text"), 6)

    def test_estimate_premium_is_over_budget(self) -> None:
        task = make_task(budget=10)
        tools = make_tools(task, self.seed, self.store)
        data = tools.call("estimate_plan", {"plan_id": "premium_video"})["data"]
        self.assertEqual(data["total"], 12)
        self.assertFalse(data["within_budget"])
        self.assertEqual(data["over_by"], 2)

    def test_estimate_lean_is_within_budget(self) -> None:
        task = make_task(budget=10)
        tools = make_tools(task, self.seed, self.store)
        data = tools.call("estimate_plan", {"plan_id": "lean_article"})["data"]
        self.assertEqual(data["total"], 8)
        self.assertTrue(data["within_budget"])
        self.assertEqual(data["remaining"], 2)
        self.assertTrue(data["deadline_ok"])

    def test_dependency_is_enforced(self) -> None:
        task = make_task()
        tools = make_tools(task, self.seed, self.store)
        tools.call("estimate_plan", {"plan_id": "lean_article"})
        result = tools.call("execute_step", {"plan_id": "lean_article", "step_id": "poster"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "dependency_not_met")

    def test_script_success_writes_artifact(self) -> None:
        task = make_task()
        tools = make_tools(task, self.seed, self.store)
        tools.call("estimate_plan", {"plan_id": "lean_article"})
        result = tools.call("generate_script", {"plan_id": "lean_article"})
        self.assertTrue(result["ok"], result)
        artifact = result["data"]["artifact_path"]
        self.assertTrue((self.store.root / artifact).exists())
        self.assertGreater(result["data"]["result"]["word_count"], 20)

    def test_poster_failure_preserves_script_and_fallback(self) -> None:
        task = make_task()
        tools = make_tools(task, self.seed, self.store, fail_steps={"poster": 10**9})
        self._selected_lean(tools)
        self.assertTrue(tools.call("generate_script", {"plan_id": "lean_article"})["ok"])
        failed = tools.call("execute_step", {"plan_id": "lean_article", "step_id": "poster"})
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["error"]["code"], "tool_failed")
        self.assertTrue(tools.snapshot()["step_results"]["script"]["status"] == "succeeded")
        self.assertEqual(tools.snapshot()["failure_history"][-1]["step_id"], "poster")

        fallback = tools.call("estimate_plan", {"plan_id": "lean_no_poster"})
        self.assertTrue(fallback["ok"], fallback)
        steps = {step["step_id"]: step for step in fallback["data"]["steps"]}
        self.assertEqual(steps["script"]["status"], "succeeded")
        self.assertTrue(steps["script"]["preserved"])
        self.assertEqual(tools.call("execute_step", {"plan_id": "lean_no_poster", "step_id": "review"})["ok"], True)
        self.assertEqual(
            tools.call("execute_step", {"plan_id": "lean_no_poster", "step_id": "distribution"})["ok"],
            True,
        )
        saved = tools.call("save_plan", {"plan_id": "lean_no_poster"})
        self.assertTrue(saved["ok"], saved)
        self.assertTrue(saved["data"]["plan"]["saved"])
        self.assertEqual(saved["data"]["plan"]["estimate"]["total"], 7)

    def test_save_requires_all_steps_succeeded(self) -> None:
        task = make_task()
        tools = make_tools(task, self.seed, self.store)
        tools.call("estimate_plan", {"plan_id": "lean_article"})
        result = tools.call("save_plan", {"plan_id": "lean_article"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "steps_incomplete")

    def test_save_is_idempotent(self) -> None:
        task = make_task()
        tools = make_tools(task, self.seed, self.store)
        self._selected_lean(tools)
        tools.call("generate_script", {"plan_id": "lean_article"})
        for step in ("poster", "review", "distribution"):
            self.assertTrue(tools.call("execute_step", {"plan_id": "lean_article", "step_id": step})["ok"])
        first = tools.call("save_plan", {"plan_id": "lean_article"})
        second = tools.call("save_plan", {"plan_id": "lean_article"})
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)
        self.assertTrue(second["data"]["deduplicated"])
        self.assertEqual(first["data"]["plan"]["plan_id"], second["data"]["plan"]["plan_id"])
        self.assertEqual(self.store.summary()["plan_count"], 1)

    def test_save_failure_is_explicit_and_no_plan(self) -> None:
        task = make_task()
        tools = make_tools(task, self.seed, self.store, fail_steps={"save": 10**9})
        self._selected_lean(tools)
        tools.call("generate_script", {"plan_id": "lean_article"})
        for step in ("poster", "review", "distribution"):
            tools.call("execute_step", {"plan_id": "lean_article", "step_id": step})
        for _ in range(2):
            result = tools.call("save_plan", {"plan_id": "lean_article"})
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "tool_failed")
        self.assertEqual(self.store.summary()["plan_count"], 0)

    def test_resume_skips_succeeded_steps(self) -> None:
        task = make_task()
        tools = make_tools(task, self.seed, self.store, fail_steps={"review": 10**9})
        self._selected_lean(tools)
        tools.call("generate_script", {"plan_id": "lean_article"})
        tools.call("execute_step", {"plan_id": "lean_article", "step_id": "poster"})
        failed = tools.call("execute_step", {"plan_id": "lean_article", "step_id": "review"})
        self.assertFalse(failed["ok"])
        self.assertEqual(len(list((self.store.root / "runs").glob("*.json"))), 1)

        resumed = make_tools(task, self.seed, self.store, resume=True)
        self.assertIn("script", resumed.resumed_steps)
        self.assertIn("poster", resumed.resumed_steps)
        estimate = resumed.call("estimate_plan", {"plan_id": "lean_article"})["data"]
        steps = {step["step_id"]: step for step in estimate["steps"]}
        self.assertEqual(steps["script"]["status"], "succeeded")
        self.assertEqual(steps["poster"]["status"], "succeeded")
        self.assertTrue(resumed.call("execute_step", {"plan_id": "lean_article", "step_id": "review"})["ok"])
        self.assertTrue(resumed.call("execute_step", {"plan_id": "lean_article", "step_id": "distribution"})["ok"])
        self.assertTrue(resumed.call("save_plan", {"plan_id": "lean_article"})["ok"])

    def test_artifact_paths_are_relative_to_store(self) -> None:
        task = make_task()
        tools = make_tools(task, self.seed, self.store)
        tools.call("estimate_plan", {"plan_id": "lean_article"})
        result = tools.call("generate_script", {"plan_id": "lean_article"})
        artifact = result["data"]["artifact_path"]
        self.assertTrue(artifact.startswith("artifacts/"))
        self.assertFalse(Path(artifact).is_absolute())


if __name__ == "__main__":
    unittest.main()

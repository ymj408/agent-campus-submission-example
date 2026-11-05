from __future__ import annotations

import json
import unittest

from tests.support import ROOT


class ExamplesArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.outputs = ROOT / "examples" / "outputs"
        cls.traces = ROOT / "examples" / "traces"
        cls.index = json.loads((cls.outputs / "index.json").read_text(encoding="utf-8"))

    def test_all_scenarios_passed(self) -> None:
        self.assertTrue(self.index["totals"]["all_checks_passed"])
        self.assertEqual(self.index["totals"]["scenarios"], 10)
        self.assertEqual(self.index["totals"]["runs"], 12)
        self.assertEqual(self.index["totals"]["saved_plans"], 4)
        for scenario in self.index["scenarios"]:
            self.assertTrue(scenario["checks_passed"], scenario["id"])
            self.assertTrue((ROOT / scenario["output"]).exists())

    def test_required_cases_present(self) -> None:
        ids = {item["id"] for item in self.index["scenarios"]}
        for required in (
            "01-normal-budget-replan",
            "02-missing-budget",
            "03-missing-deadline",
            "04-no-feasible-plan",
            "05-poster-failure-replan",
            "06-review-failure-preserve",
            "07-resume-failed-step",
            "08-duplicate-execution",
            "09-save-failure",
            "10-step-limit",
        ):
            self.assertIn(required, ids)

    def test_reference_replan_output(self) -> None:
        data = json.loads((self.outputs / "01-normal-budget-replan.json").read_text(encoding="utf-8"))
        run = data["runs"][0]
        self.assertEqual(run["status"], "plan_saved")
        self.assertEqual(run["budget"]["total"], 8)
        self.assertEqual(run["budget"]["remaining"], 2)
        self.assertEqual(run["plan"]["template_id"], "lean_article")
        self.assertTrue(data["checks"]["premium_was_over_budget"])

    def test_failure_preserves_and_resume(self) -> None:
        review = json.loads(
            (self.outputs / "06-review-failure-preserve.json").read_text(encoding="utf-8")
        )["runs"][0]
        statuses = {step["step_id"]: step["status"] for step in review["steps"]}
        self.assertEqual(statuses["review"], "failed")
        self.assertEqual(statuses["script"], "succeeded")
        self.assertEqual(statuses["poster"], "succeeded")
        self.assertFalse(review["saved"])

        resume = json.loads(
            (self.outputs / "07-resume-failed-step.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(resume["runs"]), 2)
        self.assertEqual(resume["runs"][1]["status"], "plan_saved")
        self.assertTrue(resume["checks"]["resume_skips_script"])

    def test_duplicate_has_one_plan(self) -> None:
        data = json.loads(
            (self.outputs / "08-duplicate-execution.json").read_text(encoding="utf-8")
        )
        self.assertEqual(data["store_summary"]["plan_count"], 1)
        self.assertEqual(data["runs"][1]["status"], "plan_saved_deduplicated")
        self.assertTrue(data["checks"]["second_only_get_brief"])

    def test_traces_exist_and_limit(self) -> None:
        for scenario in self.index["scenarios"]:
            for trace in scenario["traces"]:
                self.assertTrue((ROOT / trace).exists(), trace)
        text = (self.traces / "10-step-limit-1-limit.jsonl").read_text(encoding="utf-8")
        self.assertIn('"event": "limit"', text)
        self.assertIn('"saved": false', text)


if __name__ == "__main__":
    unittest.main()

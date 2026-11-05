from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.support import ROOT


class ExamplesArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.outputs = ROOT / "examples" / "outputs"
        cls.traces = ROOT / "examples" / "traces"
        cls.index = json.loads((cls.outputs / "index.json").read_text(encoding="utf-8"))

    def test_all_scenarios_passed_checks(self) -> None:
        self.assertTrue(self.index["totals"]["all_checks_passed"])
        self.assertEqual(self.index["totals"]["scenarios"], 8)
        self.assertEqual(self.index["totals"]["published"], 0)
        for scenario in self.index["scenarios"]:
            self.assertTrue(scenario["checks_passed"], scenario["id"])
            self.assertTrue((ROOT / scenario["output"]).exists())

    def test_required_acceptance_cases_are_present(self) -> None:
        ids = {item["id"] for item in self.index["scenarios"]}
        self.assertIn("01-normal-available", ids)
        self.assertIn("02-conflict-alternative", ids)
        self.assertIn("03-missing-information", ids)
        self.assertIn("04-rule-risk-block", ids)
        self.assertIn("06-save-failure", ids)
        self.assertIn("07-hard-time-no-change", ids)
        self.assertIn("08-step-limit", ids)

    def test_duplicate_example_has_two_runs_and_one_draft(self) -> None:
        payload = json.loads(
            (self.outputs / "02-conflict-alternative.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(payload["runs"]), 2)
        self.assertEqual(payload["store_summary"]["draft_count"], 1)
        self.assertTrue(payload["runs"][1]["draft"]["deduplicated"])
        self.assertFalse(payload["runs"][0]["published"])
        self.assertFalse(payload["runs"][1]["published"])

    def test_traces_exist_and_have_limit_boundary(self) -> None:
        for scenario in self.index["scenarios"]:
            for trace in scenario["traces"]:
                self.assertTrue((ROOT / trace).exists(), trace)
        limit_trace = (self.traces / "08-step-limit-run1.jsonl").read_text(encoding="utf-8")
        self.assertIn('"event": "limit"', limit_trace)
        self.assertIn('"published": false', limit_trace)

    def test_save_failure_example_has_no_draft(self) -> None:
        payload = json.loads(
            (self.outputs / "06-save-failure.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["runs"][0]["status"], "save_failed")
        self.assertFalse(payload["runs"][0]["draft"]["saved"])
        self.assertEqual(payload["store_summary"]["draft_count"], 0)


if __name__ == "__main__":
    unittest.main()

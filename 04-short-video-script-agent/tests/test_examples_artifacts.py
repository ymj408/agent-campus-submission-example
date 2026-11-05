from __future__ import annotations

import json
import unittest

from tests.support import ROOT


class ExamplesArtifactTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.outputs = ROOT / "examples" / "outputs"
        cls.index = json.loads((cls.outputs / "index.json").read_text(encoding="utf-8"))

    def test_totals(self) -> None:
        self.assertTrue(self.index["totals"]["all_checks_passed"])
        self.assertEqual(self.index["totals"]["scenarios"], 8)
        self.assertEqual(self.index["totals"]["script_ready"], 3)
        self.assertEqual(self.index["totals"]["blocked_missing_fields"], 1)
        self.assertEqual(self.index["totals"]["insufficient_material"], 1)
        self.assertEqual(self.index["totals"]["duration_unmet"], 1)
        self.assertEqual(self.index["totals"]["tool_failure"], 2)

    def test_required_cases_present(self) -> None:
        ids = {item["id"] for item in self.index["scenarios"]}
        for required in (
            "01-normal-30s",
            "02-missing-info",
            "03-no-material",
            "04-duration-unmet",
            "05-search-failure",
            "06-check-failure",
            "07-rule-risk-clean",
        ):
            self.assertIn(required, ids)

    def test_reference_38_to_28(self) -> None:
        data = json.loads((self.outputs / "01-normal-30s.json").read_text(encoding="utf-8"))
        run = data["runs"][0]
        self.assertEqual(run["status"], "script_ready")
        self.assertEqual([item["total_seconds"] for item in run["revisions"]], [38, 28])
        self.assertEqual(run["total_duration_seconds"], 28)
        self.assertEqual(run["sources"][0]["material_id"], "P01")
        self.assertEqual(run["sources"][0]["facts"], ["支持字幕"])
        self.assertNotIn("效率提升 80%", str(run["script"]))
        self.assertTrue(run["script"]["ready"])

    def test_duration_unmet_is_explicit(self) -> None:
        data = json.loads((self.outputs / "04-duration-unmet.json").read_text(encoding="utf-8"))
        run = data["runs"][0]
        self.assertEqual(run["status"], "duration_unmet")
        self.assertEqual(run["total_duration_seconds"], 14)
        self.assertEqual(run["duration_check"]["gap_seconds"], 4)
        self.assertFalse(run["script"]["ready"])

    def test_rule_risk_clean_final(self) -> None:
        data = json.loads((self.outputs / "07-rule-risk-clean.json").read_text(encoding="utf-8"))
        run = data["runs"][0]
        self.assertEqual(run["status"], "script_ready")
        self.assertEqual([item["total_seconds"] for item in run["revisions"]], [38, 28, 28])
        self.assertNotIn("效率提升 80%", str(run["script"]))
        self.assertEqual(run["rule_check"]["decision"], "pass")

    def test_target_change_changes_revision(self) -> None:
        data = json.loads((self.outputs / "08-target-20s.json").read_text(encoding="utf-8"))
        run = data["runs"][0]
        self.assertEqual([item["total_seconds"] for item in run["revisions"]], [38, 28, 14])
        self.assertEqual(run["total_duration_seconds"], 14)

    def test_traces_exist(self) -> None:
        for scenario in self.index["scenarios"]:
            self.assertTrue((ROOT / scenario["trace"]).exists(), scenario["trace"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from tests.support import ROOT, seed

from media_scheduler_agent.rules import RuleEngine


class RuleEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.seed = seed()
        cls.engine = RuleEngine(cls.seed.rules, cls.seed.rule_version)

    def audit(self, material_id: str, at: str):
        material = self.seed.material(material_id)
        assert material is not None
        return self.engine.audit(material, at)

    def test_normal_material_passes(self) -> None:
        result = self.audit("M03", "2026-11-06T12:00:00+08:00")
        self.assertEqual(result.decision, "pass")
        self.assertEqual(result.violations, [])
        self.assertEqual(result.missing_fields, [])

    def test_missing_fields_are_blocking_and_explained(self) -> None:
        result = self.audit("M02", "2026-11-07T10:00:00+08:00")
        self.assertEqual(result.decision, "blocked")
        self.assertIn("duration_minutes", result.missing_fields)
        self.assertIn("copyright", result.missing_fields)
        self.assertTrue(any("时长" in reason for reason in result.risk_reasons))

    def test_forbidden_medical_claim_blocks(self) -> None:
        result = self.audit("M04", "2026-11-06T21:00:00+08:00")
        self.assertEqual(result.decision, "blocked")
        self.assertTrue(any(item.rule_id == "R004" for item in result.violations))
        self.assertTrue(any("根治" in reason or "无需就医" in reason for reason in result.risk_reasons))

    def test_sensitive_topic_warns_but_does_not_block(self) -> None:
        result = self.audit("M06", "2026-11-07T14:00:00+08:00")
        self.assertEqual(result.decision, "needs_review")
        self.assertTrue(result.needs_review)
        self.assertFalse(result.has_blocking)
        self.assertTrue(any(item.rule_id == "R005" for item in result.violations))

    def test_invalid_copyright_value_blocks(self) -> None:
        material = dict(self.seed.material("M03"))
        material["copyright"] = "unknown"
        result = self.engine.audit(material, "2026-11-06T12:00:00+08:00")
        self.assertEqual(result.decision, "blocked")
        self.assertTrue(any(item.rule_id == "R002" for item in result.violations))

    def test_rule_version_is_exposed(self) -> None:
        result = self.audit("M03", "2026-11-06T12:00:00+08:00")
        self.assertEqual(result.rule_version, "2026.11-demo")
        self.assertTrue(result.checked_at)


if __name__ == "__main__":
    unittest.main()

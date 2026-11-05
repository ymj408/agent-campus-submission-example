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

    def test_totals(self) -> None:
        self.assertTrue(self.index["totals"]["all_checks_passed"])
        self.assertEqual(self.index["totals"]["scenarios"], 8)
        self.assertEqual(self.index["totals"]["runs"], 8)
        self.assertEqual(self.index["totals"]["answered"], 3)
        self.assertEqual(self.index["totals"]["needs_confirmation"], 2)
        self.assertEqual(self.index["totals"]["insufficient_evidence"], 1)
        self.assertEqual(self.index["totals"]["tool_failure"], 2)

    def test_required_cases_present(self) -> None:
        ids = {item["id"] for item in self.index["scenarios"]}
        for required in (
            "01-clear-answer",
            "02-document-conflict",
            "03-out-of-scope",
            "04-paraphrase-conflict",
            "05-context-followup",
            "06-search-failure",
            "07-read-failure",
        ):
            self.assertIn(required, ids)

    def test_clear_answer_cites_original(self) -> None:
        data = json.loads((self.outputs / "01-clear-answer.json").read_text(encoding="utf-8"))
        run = data["runs"][0]
        self.assertEqual(run["status"], "answered")
        self.assertEqual(run["citations"][0]["doc_id"], "D01")
        self.assertIn("30 个自然日", run["citations"][0]["quote"])
        self.assertTrue(run["citation_check"]["canonical_citations_valid"])

    def test_conflict_has_both_citations_and_question(self) -> None:
        data = json.loads((self.outputs / "02-document-conflict.json").read_text(encoding="utf-8"))
        run = data["runs"][0]
        self.assertEqual(run["status"], "needs_confirmation")
        self.assertEqual(run["answer_type"], "conflict")
        self.assertEqual({item["doc_id"] for item in run["citations"]}, {"D01", "D02"})
        self.assertIn("哪份制度", run["follow_up_question"])
        self.assertFalse(run["answer_supported"])

    def test_context_rewrite(self) -> None:
        data = json.loads((self.outputs / "05-context-followup.json").read_text(encoding="utf-8"))
        run = data["runs"][0]
        self.assertTrue(run["history_used"])
        self.assertIn("总部", run["rewritten_query"])
        self.assertIn("30 天", run["answer"])

    def test_tool_failure_preserves_retrieval(self) -> None:
        data = json.loads((self.outputs / "07-read-failure.json").read_text(encoding="utf-8"))
        run = data["runs"][0]
        self.assertEqual(run["status"], "tool_failure")
        self.assertIsNone(run["answer"])
        self.assertEqual(run["retrieved_doc_ids"], ["D01"])
        self.assertEqual(run["read_doc_ids"], [])

    def test_traces_exist(self) -> None:
        for scenario in self.index["scenarios"]:
            self.assertTrue((ROOT / scenario["trace"]).exists(), scenario["trace"])


if __name__ == "__main__":
    unittest.main()

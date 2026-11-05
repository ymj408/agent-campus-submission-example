from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from tests.support import ROOT, TestDir, kb, make_task, read_jsonl, run_agent
from knowledge_qa.knowledge import KnowledgeBase
from knowledge_qa.models import Action, QATask, StepRecord


class ConflictLiePlanner:
    provider = "test"
    model = "test-conflict-liar"

    def decide(self, task: QATask, history: list[StepRecord]) -> Action:
        n = len(history)
        if n == 0:
            return Action.tool_call("search_documents", {"query": "报销期限多久"}, "检索")
        if n == 1:
            return Action.tool_call("read_document", {"doc_id": "D01"}, "读 D01")
        if n == 2:
            return Action.tool_call("read_document", {"doc_id": "D02"}, "读 D02")
        return Action.finish(
            "answered",
            payload={
                "answer": "报销期限是 15 天。",
                "citations": [
                    {
                        "doc_id": "D02",
                        "section_id": "S2",
                        "quote": "华南区员工应在差旅结束后 15 个自然日内提交报销申请，逾期需说明原因。",
                    }
                ],
            },
            rationale="故意只选一个值，验证运行时冲突收口。",
        )


class InvalidCitationPlanner:
    provider = "test"
    model = "test-invalid-citation"

    def decide(self, task: QATask, history: list[StepRecord]) -> Action:
        n = len(history)
        if n == 0:
            return Action.tool_call(
                "search_documents", {"query": "总部差旅报销期限多久"}, "检索"
            )
        if n == 1:
            return Action.tool_call("read_document", {"doc_id": "D01"}, "读原文")
        return Action.finish(
            "answered",
            payload={
                "answer": "报销期限是 30 天。",
                "citations": [{"doc_id": "D01", "quote": "这是一段不存在的伪造引用"}],
            },
            rationale="故意提交错误引用，验证运行时重建引用。",
        )


class NoEvidencePlanner:
    provider = "test"
    model = "test-no-evidence"

    def decide(self, task: QATask, history: list[StepRecord]) -> Action:
        return Action.finish(
            "answered",
            payload={"answer": "42 天", "citations": []},
            rationale="故意无证据回答。",
        )


class AgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.kb = kb()

    def _run(self, question: str, **kwargs):
        task = make_task(question, history=kwargs.pop("history", None))
        return run_agent(task, kb_data=self.kb, **kwargs)

    def test_clear_answer_with_citation(self) -> None:
        result = self._run("总部差旅报销期限多久？")
        self.assertEqual(result["status"], "answered")
        self.assertTrue(result["completed"])
        self.assertIn("30 天", result["answer"])
        self.assertEqual([item["doc_id"] for item in result["citations"]], ["D01"])
        self.assertEqual(result["citations"][0]["section_id"], "S2")
        self.assertTrue(result["citation_check"]["canonical_citations_valid"])
        self.assertTrue(result["citation_check"]["model_citations_valid"])

    def test_conflict_never_answers_single_value(self) -> None:
        result = self._run("报销期限多久？")
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertEqual(result["answer_type"], "conflict")
        self.assertFalse(result["completed"])
        self.assertFalse(result["answer_supported"])
        self.assertEqual({item["doc_id"] for item in result["citations"]}, {"D01", "D02"})
        self.assertEqual(
            {item["doc_id"]: item["value"] for item in result["conflict"]["values"]},
            {"D01": 30, "D02": 15},
        )
        self.assertIn("不能擅自选择", result["answer"])
        self.assertIn("哪份制度", result["follow_up_question"])

    def test_out_of_scope_returns_gap(self) -> None:
        result = self._run("公司食堂今天吃什么？")
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertIsNone(result["answer"])
        self.assertEqual(result["citations"], [])
        self.assertTrue(any("知识库" in item for item in result["missing_information"]))

    def test_paraphrase_retrieves_and_detects_conflict(self) -> None:
        result = self._run("出差回来多久内要报销？")
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertEqual(set(result["read_doc_ids"]), {"D01", "D02"})
        self.assertEqual(len(result["citations"]), 2)

    def test_context_followup_rewrites_query(self) -> None:
        result = self._run(
            "那总部呢？",
            history=[
                {"role": "user", "content": "华南区差旅报销期限多久？"},
                {"role": "assistant", "content": "根据 D02，报销期限为 15 天。"},
            ],
        )
        self.assertEqual(result["status"], "answered")
        self.assertTrue(result["history_used"])
        self.assertIn("总部", result["rewritten_query"])
        self.assertIn("30 天", result["answer"])
        self.assertEqual(result["read_doc_ids"], ["D01"])

    def test_search_failure_has_no_answer(self) -> None:
        result = self._run("报销期限多久？", fail_tools={"search_documents": 10**9})
        self.assertEqual(result["status"], "tool_failure")
        self.assertIsNone(result["answer"])
        self.assertFalse(result["completed"])
        self.assertEqual(result["retrieved_doc_ids"], [])

    def test_read_failure_preserves_retrieval_only(self) -> None:
        result = self._run("总部差旅报销期限多久？", fail_tools={"read_document": 10**9})
        self.assertEqual(result["status"], "tool_failure")
        self.assertIsNone(result["answer"])
        self.assertEqual(result["retrieved_doc_ids"], ["D01"])
        self.assertEqual(result["read_doc_ids"], [])
        self.assertTrue(any("读取" in item for item in result["missing_information"]))

    def test_runtime_overrides_conflict_lie(self) -> None:
        result = self._run("报销期限多久？", planner=ConflictLiePlanner())
        self.assertEqual(result["model_claim"]["payload"]["answer"], "报销期限是 15 天。")
        self.assertEqual(result["status"], "needs_confirmation")
        self.assertEqual(result["answer_type"], "conflict")
        self.assertNotEqual(result["answer"], "报销期限是 15 天。")
        self.assertEqual(len(result["citations"]), 2)

    def test_runtime_rebuilds_invalid_citation(self) -> None:
        result = self._run("总部差旅报销期限多久？", planner=InvalidCitationPlanner())
        self.assertEqual(result["status"], "answered")
        self.assertFalse(result["citation_check"]["model_citations_valid"])
        self.assertTrue(result["citation_check"]["canonical_citations_valid"])
        self.assertTrue(all("S2" == item["section_id"] for item in result["citations"]))
        self.assertIn("30 天", result["answer"])

    def test_no_evidence_lie_is_rejected(self) -> None:
        result = self._run("报销期限多久？", planner=NoEvidencePlanner())
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertIsNone(result["answer"])
        self.assertFalse(result["completed"])

    def test_step_limit_is_explicit(self) -> None:
        result = self._run("报销期限多久？", max_steps=1)
        self.assertEqual(result["status"], "step_limit_reached")
        self.assertFalse(result["completed"])
        self.assertIsNone(result["answer"])

    def test_trace_contains_observation_and_citation(self) -> None:
        with TestDir("kqa-trace-") as tmp:
            trace = Path(tmp) / "trace.jsonl"
            result = self._run("总部差旅报销期限多久？", trace_path=trace)
            events = read_jsonl(trace)
            steps = [event for event in events if event.get("event") == "step"]
            self.assertTrue(all("decision_basis" in event for event in steps))
            self.assertEqual(events[-1]["status"], "answered")
            self.assertEqual(events[-1]["citation_count"], 1)

    def test_replacing_document_changes_answer(self) -> None:
        with TestDir("kqa-data-") as tmp:
            data_dir = Path(tmp) / "data"
            shutil.copytree(ROOT / "data", data_dir)
            path = data_dir / "documents.json"
            payload = json.loads(path.read_text(encoding="utf-8"))
            doc = payload["documents"]["D01"]
            doc["facts"]["reimbursement_deadline_days"]["value"] = 45
            for section in doc["sections"]:
                section["text"] = section["text"].replace("30 个自然日", "45 个自然日")
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            changed_kb = KnowledgeBase(data_dir)
            result = run_agent(
                make_task("总部差旅报销期限多久？"),
                kb_data=changed_kb,
            )
            self.assertEqual(result["status"], "answered")
            self.assertIn("45 天", result["answer"])
            self.assertIn("45 个自然日", result["citations"][0]["quote"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from tests.support import kb, make_task, make_tools

from knowledge_qa.planner import rewrite_query


class ToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.kb = kb()

    def test_search_finds_conflicting_documents(self) -> None:
        task = make_task("报销期限多久？")
        tools = make_tools(task, self.kb)
        result = tools.call("search_documents", {"query": task.question})
        self.assertTrue(result["ok"], result)
        ids = [item["doc_id"] for item in result["data"]["candidates"]]
        self.assertEqual(ids[:2], ["D01", "D02"])
        self.assertTrue(result["data"]["potential_conflicts"])
        self.assertNotIn("D06", ids)

    def test_scope_query_only_returns_applicable_document(self) -> None:
        task = make_task("总部差旅报销期限多久？")
        tools = make_tools(task, self.kb)
        data = tools.call("search_documents", {"query": task.question})["data"]
        self.assertEqual([item["doc_id"] for item in data["candidates"]], ["D01"])
        self.assertEqual(data["scope_hints"], ["总部"])
        self.assertEqual(data["potential_conflicts"], [])

    def test_superseded_document_is_not_searchable(self) -> None:
        task = make_task("旧版差旅报销 45 天还有效吗？")
        tools = make_tools(task, self.kb)
        data = tools.call("search_documents", {"query": task.question})["data"]
        self.assertNotIn("D06", [item["doc_id"] for item in data["candidates"]])

    def test_paraphrase_retrieves_reimbursement_documents(self) -> None:
        task = make_task("出差回来多久内要报销？")
        tools = make_tools(task, self.kb)
        data = tools.call("search_documents", {"query": task.question})["data"]
        ids = [item["doc_id"] for item in data["candidates"]]
        self.assertIn("D01", ids)
        self.assertIn("D02", ids)

    def test_out_of_scope_has_no_candidates(self) -> None:
        task = make_task("公司食堂今天吃什么？")
        tools = make_tools(task, self.kb)
        data = tools.call("search_documents", {"query": task.question})["data"]
        self.assertEqual(data["candidates"], [])

    def test_read_document_returns_exact_quote_and_facts(self) -> None:
        task = make_task("总部报销期限")
        tools = make_tools(task, self.kb)
        tools.call("search_documents", {"query": task.question})
        result = tools.call("read_document", {"doc_id": "D01"})
        self.assertTrue(result["ok"], result)
        data = result["data"]
        self.assertEqual(data["facts"]["reimbursement_deadline_days"]["value"], 30)
        section = next(item for item in data["sections"] if item["id"] == "S2")
        self.assertIn("30 个自然日", section["text"])
        self.assertEqual(data["quote"], section["text"])

    def test_conflict_after_reading_both_documents(self) -> None:
        task = make_task("报销期限多久？")
        tools = make_tools(task, self.kb)
        tools.call("search_documents", {"query": task.question})
        first = tools.call("read_document", {"doc_id": "D01"})
        self.assertEqual(first["data"]["conflicts"], [])
        second = tools.call("read_document", {"doc_id": "D02"})
        conflicts = second["data"]["conflicts"]
        self.assertEqual(len(conflicts), 1)
        values = {item["doc_id"]: item["value"] for item in conflicts[0]["values"]}
        self.assertEqual(values, {"D01": 30, "D02": 15})
        self.assertIn("30 个自然日", conflicts[0]["values"][0]["quote"])

    def test_scope_resolves_conflict_when_both_were_read(self) -> None:
        task = make_task("总部差旅报销期限多久？")
        tools = make_tools(task, self.kb)
        tools.call("search_documents", {"query": task.question})
        tools.call("read_document", {"doc_id": "D01"})
        tools.call("read_document", {"doc_id": "D02"})
        self.assertEqual(tools.conflicts(), [])

    def test_citation_validation(self) -> None:
        task = make_task("总部报销期限")
        tools = make_tools(task, self.kb)
        tools.call("search_documents", {"query": task.question})
        read = tools.call("read_document", {"doc_id": "D01"})["data"]
        quote = next(item for item in read["sections"] if item["id"] == "S2")["text"]
        self.assertTrue(tools.validate_citation("D01", quote)["valid"])
        self.assertFalse(tools.validate_citation("D01", "伪造的原文")["valid"])
        self.assertFalse(tools.validate_citation("D02", quote)["valid"])

    def test_read_failure_is_visible(self) -> None:
        task = make_task("总部报销期限")
        tools = make_tools(task, self.kb, fail_tools={"read_document": 10**9})
        tools.call("search_documents", {"query": task.question})
        result = tools.call("read_document", {"doc_id": "D01"})
        self.assertFalse(result["ok"])
        self.assertIn("D01", tools.failed_reads)
        self.assertEqual(tools.snapshot()["evidence"], [])

    def test_clarification_is_deduplicated(self) -> None:
        task = make_task("报销期限")
        tools = make_tools(task, self.kb)
        args = {
            "question": "适用 D01 还是 D02？",
            "options": ["D01（总部/华东，30 天）", "D02（华南，15 天）"],
        }
        first = tools.call("request_clarification", args)
        second = tools.call("request_clarification", args)
        self.assertTrue(first["ok"])
        self.assertEqual(first["data"]["clarification_id"], second["data"]["clarification_id"])
        self.assertTrue(second["data"]["deduplicated"])

    def test_history_rewrite_changes_scope(self) -> None:
        task = make_task(
            "那总部呢？",
            history=[
                {"role": "user", "content": "华南区差旅报销期限多久？"},
                {"role": "assistant", "content": "根据 D02，报销期限为 15 天。"},
            ],
        )
        query, used = rewrite_query(task)
        self.assertTrue(used)
        self.assertIn("总部", query)
        self.assertNotIn("华南", query)
        self.assertIn("报销", query)


if __name__ == "__main__":
    unittest.main()

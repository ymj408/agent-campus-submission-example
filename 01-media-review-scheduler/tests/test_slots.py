from __future__ import annotations

import unittest

from tests.support import make_task, make_tools, temp_store

from media_scheduler_agent.timeutil import CST, parse_datetime


class SlotQueryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp, self.store, self.seed = temp_store()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_conflict_offers_next_hour_not_earlier(self) -> None:
        task = make_task("M01")
        tools = make_tools(task, self.seed, self.store)
        self.assertTrue(tools.call("get_material", {"material_id": "M01"})["ok"])
        self.assertTrue(tools.call("check_rules", {"material_id": "M01"})["ok"])
        result = tools.call("query_slots", {"material_id": "M01", "limit": 3})
        self.assertTrue(result["ok"])
        data = result["data"]
        self.assertEqual(data["requested"]["status"], "occupied")
        self.assertEqual(data["requested"]["conflict"]["material_id"], "M99")
        self.assertEqual(data["alternatives"][0]["at"], "2026-11-06T19:00:00+08:00")
        self.assertEqual(data["alternatives"][0]["label"], "19:00")
        self.assertEqual(data["timezone"], "Asia/Shanghai")

    def test_available_request_time(self) -> None:
        task = make_task("M03")
        tools = make_tools(task, self.seed, self.store)
        tools.call("get_material", {"material_id": "M03"})
        tools.call("check_rules", {"material_id": "M03"})
        data = tools.call("query_slots", {"material_id": "M03"})["data"]
        self.assertEqual(data["requested"]["status"], "available")
        self.assertEqual(data["requested"]["at"], "2026-11-06T12:00:00+08:00")

    def test_own_draft_is_ignored_for_duplicate_query(self) -> None:
        task = make_task("M01")
        tools = make_tools(task, self.seed, self.store)
        tools.call("get_material", {"material_id": "M01"})
        tools.call("check_rules", {"material_id": "M01"})
        tools.call("query_slots", {"material_id": "M01"})
        confirmation = tools.call(
            "request_confirmation",
            {
                "material_id": "M01",
                "reason": "slot_deviation",
                "proposed_publish_at": "2026-11-06T19:00:00+08:00",
                "questions": ["是否改到 19:00？"],
            },
        )
        save = tools.call(
            "save_schedule_draft",
            {
                "material_id": "M01",
                "proposed_publish_at": "2026-11-06T19:00:00+08:00",
                "confirmation_id": confirmation["data"]["confirmation_id"],
            },
        )
        self.assertTrue(save["ok"])

        # 同一任务再次查询时，自己的草稿不应把 19:00 变成“被他人占用”。
        again = tools.call("query_slots", {"material_id": "M01"})
        self.assertEqual(again["data"]["alternatives"][0]["at"], "2026-11-06T19:00:00+08:00")

    def test_naive_time_is_assumed_shanghai(self) -> None:
        parsed = parse_datetime("2026-11-06T18:00:00")
        self.assertEqual(parsed.tzinfo, CST)
        self.assertEqual(parsed.isoformat(), "2026-11-06T18:00:00+08:00")

    def test_missing_material_is_reported(self) -> None:
        task = make_task("NOPE", at="2026-11-06T12:00:00+08:00")
        tools = make_tools(task, self.seed, self.store)
        result = tools.call("get_material", {"material_id": "NOPE"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "material_not_found")


if __name__ == "__main__":
    unittest.main()

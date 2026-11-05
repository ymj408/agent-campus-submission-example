from __future__ import annotations

import json
import unittest

from tests.support import make_task, make_tools, seed, temp_store

from media_scheduler_agent.tools import tool_specs


class SaveDraftTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp, self.store, self.seed = temp_store()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _prepare(self, material_id: str, *, allow_time_change: bool = True):
        task = make_task(material_id, allow_time_change=allow_time_change)
        tools = make_tools(task, self.seed, self.store)
        tools.call("get_material", {"material_id": material_id})
        tools.call("check_rules", {"material_id": material_id})
        tools.call("query_slots", {"material_id": material_id})
        return task, tools

    def _draft_files(self) -> list[str]:
        return sorted(path.name for path in (self.store.root / "drafts").glob("*.json"))

    def test_exact_slot_saves_one_unpublished_draft_and_keeps_schedule(self) -> None:
        before = json.dumps(self.store.load_state()["schedule"], sort_keys=True)
        _, tools = self._prepare("M03")
        result = tools.call(
            "save_schedule_draft",
            {"material_id": "M03", "proposed_publish_at": "2026-11-06T12:00:00+08:00"},
        )
        self.assertTrue(result["ok"], result)
        draft = result["data"]["draft"]
        self.assertEqual(draft["status"], "draft_ready")
        self.assertFalse(draft["published"])
        self.assertFalse(draft["time_deviation"])
        self.assertEqual(len(self._draft_files()), 1)
        after = json.dumps(self.store.load_state()["schedule"], sort_keys=True)
        self.assertEqual(before, after)
        self.assertEqual(self.store.summary()["published_count"], 0)

    def test_same_request_is_idempotent(self) -> None:
        _, tools = self._prepare("M03")
        first = tools.call(
            "save_schedule_draft",
            {"material_id": "M03", "proposed_publish_at": "2026-11-06T12:00:00+08:00"},
        )
        second = tools.call(
            "save_schedule_draft",
            {"material_id": "M03", "proposed_publish_at": "2026-11-06T12:00:00+08:00"},
        )
        self.assertTrue(second["ok"])
        self.assertTrue(second["data"]["deduplicated"])
        self.assertEqual(first["data"]["draft"]["draft_id"], second["data"]["draft"]["draft_id"])
        self.assertEqual(len(self._draft_files()), 1)

    def test_deviation_without_confirmation_is_rejected(self) -> None:
        _, tools = self._prepare("M01")
        result = tools.call(
            "save_schedule_draft",
            {"material_id": "M01", "proposed_publish_at": "2026-11-06T19:00:00+08:00"},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "confirmation_required")
        self.assertEqual(len(self._draft_files()), 0)

    def test_deviation_with_pending_confirmation_is_saved_as_pending(self) -> None:
        _, tools = self._prepare("M01")
        confirmation = tools.call(
            "request_confirmation",
            {
                "material_id": "M01",
                "reason": "slot_deviation",
                "proposed_publish_at": "2026-11-06T19:00:00+08:00",
                "questions": ["是否同意改到 19:00？"],
            },
        )
        self.assertTrue(confirmation["ok"], confirmation)
        self.assertFalse(confirmation["data"]["approved"])
        result = tools.call(
            "save_schedule_draft",
            {
                "material_id": "M01",
                "proposed_publish_at": "2026-11-06T19:00:00+08:00",
                "confirmation_id": confirmation["data"]["confirmation_id"],
            },
        )
        self.assertTrue(result["ok"], result)
        draft = result["data"]["draft"]
        self.assertEqual(draft["status"], "draft_pending_confirmation")
        self.assertEqual(draft["requested_publish_at"], "2026-11-06T18:00:00+08:00")
        self.assertEqual(draft["proposed_publish_at"], "2026-11-06T19:00:00+08:00")
        self.assertEqual(draft["confirmation_status"], "pending_user_confirmation")
        self.assertFalse(draft["published"])

    def test_confirmed_claim_is_not_accepted_from_model(self) -> None:
        _, tools = self._prepare("M01")
        result = tools.call(
            "save_schedule_draft",
            {
                "material_id": "M01",
                "proposed_publish_at": "2026-11-06T19:00:00+08:00",
                "confirmation_id": "CONF-9999",
            },
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "confirmation_invalid")

    def test_hard_time_requirement_cannot_be_changed(self) -> None:
        _, tools = self._prepare("M01", allow_time_change=False)
        confirmation = tools.call(
            "request_confirmation",
            {
                "material_id": "M01",
                "reason": "slot_deviation",
                "proposed_publish_at": "2026-11-06T19:00:00+08:00",
                "questions": ["是否改到 19:00？"],
            },
        )
        self.assertFalse(confirmation["ok"])
        self.assertEqual(confirmation["error"]["code"], "time_change_not_allowed")
        result = tools.call(
            "save_schedule_draft",
            {"material_id": "M01", "proposed_publish_at": "2026-11-06T19:00:00+08:00"},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "time_change_not_allowed")
        self.assertEqual(len(self._draft_files()), 0)

    def test_conflicting_slot_is_rejected_at_save(self) -> None:
        _, tools = self._prepare("M01")
        result = tools.call(
            "save_schedule_draft",
            {"material_id": "M01", "proposed_publish_at": "2026-11-06T18:00:00+08:00"},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "slot_conflict")
        self.assertEqual(len(self._draft_files()), 0)

    def test_blocking_risk_is_rejected_at_save(self) -> None:
        _, tools = self._prepare("M04")
        result = tools.call(
            "save_schedule_draft",
            {"material_id": "M04", "proposed_publish_at": "2026-11-06T21:00:00+08:00"},
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "review_blocked")
        self.assertEqual(len(self._draft_files()), 0)

    def test_persistent_save_failure_returns_failure_and_no_draft(self) -> None:
        task = make_task("M05")
        tools = make_tools(task, self.seed, self.store, fail_save_times=10**9)
        tools.call("get_material", {"material_id": "M05"})
        tools.call("check_rules", {"material_id": "M05"})
        tools.call("query_slots", {"material_id": "M05"})
        for _ in range(2):
            result = tools.call(
                "save_schedule_draft",
                {"material_id": "M05", "proposed_publish_at": "2026-11-06T14:00:00+08:00"},
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "save_failed")
            self.assertTrue(result["error"]["retryable"])
        self.assertEqual(len(self._draft_files()), 0)
        self.assertEqual(self.store.summary()["draft_count"], 0)

    def test_transient_save_failure_can_retry(self) -> None:
        task = make_task("M05")
        tools = make_tools(task, self.seed, self.store, fail_save_times=1)
        tools.call("get_material", {"material_id": "M05"})
        tools.call("check_rules", {"material_id": "M05"})
        tools.call("query_slots", {"material_id": "M05"})
        first = tools.call(
            "save_schedule_draft",
            {"material_id": "M05", "proposed_publish_at": "2026-11-06T14:00:00+08:00"},
        )
        second = tools.call(
            "save_schedule_draft",
            {"material_id": "M05", "proposed_publish_at": "2026-11-06T14:00:00+08:00"},
        )
        self.assertFalse(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual(len(self._draft_files()), 1)

    def test_tool_set_has_no_publish_endpoint(self) -> None:
        names = {spec["function"]["name"] for spec in tool_specs()}
        self.assertNotIn("publish", names)
        self.assertNotIn("publish_schedule", names)
        self.assertEqual(
            names,
            {
                "get_material",
                "lookup_material_source",
                "check_rules",
                "query_slots",
                "request_confirmation",
                "save_schedule_draft",
            },
        )


if __name__ == "__main__":
    unittest.main()

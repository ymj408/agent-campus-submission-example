from __future__ import annotations

import unittest

from tests.support import kb, make_task, make_tools

from script_agent.planner import build_script


class ToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.kb = kb()

    def test_brief_missing_fields(self) -> None:
        task = make_task(audience="", platform="", target=None)
        tools = make_tools(task, self.kb)
        data = tools.call("get_brief", {})["data"]
        self.assertEqual(
            data["missing_fields"],
            ["audience", "platform", "target_duration_seconds"],
        )

    def test_search_excludes_unusable_material(self) -> None:
        task = make_task()
        tools = make_tools(task, self.kb)
        data = tools.call("search_materials", {"query": "通勤耳机 产品 素材"})["data"]
        ids = [item["material_id"] for item in data["candidates"]]
        self.assertIn("P01", ids)
        self.assertNotIn("P04", ids)
        self.assertEqual(data["candidates"][0]["facts"], ["支持字幕"])

    def test_no_material_query(self) -> None:
        task = make_task(theme="宠物猫粮")
        tools = make_tools(task, self.kb)
        data = tools.call("search_materials", {"query": "宠物猫粮"})["data"]
        self.assertEqual(data["candidates"], [])

    def test_long_script_is_38_seconds(self) -> None:
        task = make_task()
        tools = make_tools(task, self.kb)
        script = build_script(task, "long")
        data = tools.call("estimate_duration", {"script": script})["data"]
        self.assertEqual(data["total_seconds"], 38)
        self.assertFalse(data["within_target"])
        self.assertEqual(data["gap_seconds"], 8)

    def test_short_script_is_28_seconds(self) -> None:
        task = make_task()
        tools = make_tools(task, self.kb)
        script = build_script(task, "short")
        data = tools.call("estimate_duration", {"script": script})["data"]
        self.assertEqual(data["total_seconds"], 28)
        self.assertTrue(data["within_target"])

    def test_short_script_passes_rules(self) -> None:
        task = make_task()
        tools = make_tools(task, self.kb)
        script = build_script(task, "short")
        data = tools.call("check_rules", {"script": script})["data"]
        self.assertEqual(data["decision"], "pass", data["violations"])
        self.assertEqual(data["duration"]["total_seconds"], 28)

    def test_long_script_fails_duration_rule(self) -> None:
        task = make_task()
        tools = make_tools(task, self.kb)
        script = build_script(task, "long")
        data = tools.call("check_rules", {"script": script})["data"]
        codes = {item["code"] for item in data["violations"]}
        self.assertIn("duration_exceeded", codes)

    def test_unsupported_effect_claim_is_blocked(self) -> None:
        task = make_task(style="夸张营销")
        tools = make_tools(task, self.kb)
        script = build_script(task, "short", include_unsupported_claim=True)
        data = tools.call("check_rules", {"script": script})["data"]
        codes = {item["code"] for item in data["violations"]}
        self.assertIn("unsupported_claim", codes)
        self.assertIn("forbidden_phrase", codes)
        self.assertTrue(any("效率提升 80%" in item["message"] for item in data["violations"]))

    def test_duration_violation_for_mini_at_target_10(self) -> None:
        task = make_task(target=10)
        tools = make_tools(task, self.kb)
        script = build_script(task, "mini")
        data = tools.call("check_rules", {"script": script})["data"]
        codes = {item["code"] for item in data["violations"]}
        self.assertIn("duration_exceeded", codes)
        self.assertEqual(data["duration"]["total_seconds"], 14)

    def test_pacing_violation(self) -> None:
        task = make_task()
        tools = make_tools(task, self.kb)
        script = build_script(task, "short")
        script["scenes"][0]["voiceover"] = "太短"
        data = tools.call("check_rules", {"script": script})["data"]
        codes = {item["code"] for item in data["violations"]}
        self.assertIn("pacing_invalid", codes)

    def test_tool_failure_is_visible(self) -> None:
        task = make_task()
        tools = make_tools(task, self.kb, fail_tools={"estimate_duration": 10**9})
        script = build_script(task, "long")
        result = tools.call("estimate_duration", {"script": script})
        self.assertFalse(result["ok"])
        self.assertTrue(tools.estimate_failed)


if __name__ == "__main__":
    unittest.main()

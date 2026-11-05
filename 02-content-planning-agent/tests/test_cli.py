from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support import ROOT, test_dir


class CliTest(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        return subprocess.run(
            [sys.executable, "run.py", *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=60,
        )

    def test_verify(self) -> None:
        proc = self.run_cli("verify")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["materials"], 5)
        self.assertEqual(data["plans"], 4)

    def test_case_run_writes_json(self) -> None:
        with test_dir("cpp-cli-") as tmp:
            out = Path(tmp) / "result.json"
            proc = self.run_cli(
                "run",
                "--case",
                "examples/cases/01-normal-budget-replan.json",
                "--run-index",
                "0",
                "--store",
                str(Path(tmp) / "store"),
                "--reset",
                "--out",
                str(out),
                "--quiet",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "plan_saved")
            self.assertEqual(result["budget"]["total"], 8)
            self.assertTrue(result["saved"])

    def test_missing_budget_human_output(self) -> None:
        with test_dir("cpp-cli-") as tmp:
            proc = self.run_cli(
                "run",
                "--case",
                "examples/cases/02-missing-budget.json",
                "--store",
                str(Path(tmp) / "store"),
                "--reset",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("blocked_missing_fields", proc.stdout)
            self.assertIn("已保存方案=False", proc.stdout)
            self.assertIn("缺少 budget", proc.stdout)

    def test_openai_without_key_is_clear_error(self) -> None:
        with test_dir("cpp-cli-") as tmp:
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            env["AGENT_MODEL_API_KEY"] = ""
            proc = subprocess.run(
                [
                    sys.executable,
                    "run.py",
                    "run",
                    "--case",
                    "examples/cases/01-normal-budget-replan.json",
                    "--store",
                    str(Path(tmp) / "store"),
                    "--reset",
                    "--provider",
                    "openai",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                timeout=60,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn("AGENT_MODEL_API_KEY", proc.stderr)


if __name__ == "__main__":
    unittest.main()

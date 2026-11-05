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
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["materials"], 6)

    def test_case_run_writes_json(self) -> None:
        with test_dir("msa-cli-") as tmp:
            out = Path(tmp) / "result.json"
            store = Path(tmp) / "store"
            trace = Path(tmp) / "trace.jsonl"
            proc = self.run_cli(
                "run",
                "--case",
                "examples/cases/01-normal-available.json",
                "--store",
                str(store),
                "--reset",
                "--trace",
                str(trace),
                "--out",
                str(out),
                "--quiet",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "draft_saved")
            self.assertFalse(result["published"])
            self.assertTrue(trace.exists())

    def test_human_output_never_claims_published(self) -> None:
        with test_dir("msa-cli-") as tmp:
            proc = self.run_cli(
                "run",
                "--case",
                "examples/cases/02-conflict-alternative.json",
                "--store",
                str(Path(tmp) / "store"),
                "--reset",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("已发布=否", proc.stdout)
            self.assertNotIn("已发布=是", proc.stdout)
            self.assertIn("等待用户确认", proc.stdout)

    def test_openai_provider_without_key_is_clear_error(self) -> None:
        with test_dir("msa-cli-") as tmp:
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            env["AGENT_MODEL_API_KEY"] = ""
            proc = subprocess.run(
                [
                    sys.executable,
                    "run.py",
                    "run",
                    "--case",
                    "examples/cases/01-normal-available.json",
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

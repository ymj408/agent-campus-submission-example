from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

from tests.support import ROOT, TestDir


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
        self.assertEqual(data["documents"], 6)
        self.assertEqual(data["active"], 5)
        self.assertEqual(data["superseded"], 1)

    def test_case_run_writes_json(self) -> None:
        with TestDir("kqa-cli-") as tmp:
            out = Path(tmp) / "result.json"
            proc = self.run_cli(
                "run",
                "--case",
                "examples/cases/01-clear-answer.json",
                "--out",
                str(out),
                "--quiet",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "answered")
            self.assertIn("30 天", result["answer"])
            self.assertTrue(result["citation_check"]["canonical_citations_valid"])

    def test_conflict_human_output(self) -> None:
        proc = self.run_cli(
            "run",
            "--case",
            "examples/cases/02-document-conflict.json",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("needs_confirmation", proc.stdout)
        self.assertIn("D01", proc.stdout)
        self.assertIn("D02", proc.stdout)
        self.assertIn("不能擅自选择", proc.stdout)

    def test_openai_without_key_is_clear_error(self) -> None:
        with TestDir("kqa-cli-") as tmp:
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            env["AGENT_MODEL_API_KEY"] = ""
            proc = subprocess.run(
                [
                    sys.executable,
                    "run.py",
                    "run",
                    "--question",
                    "总部报销期限多久？",
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

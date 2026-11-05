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
        self.assertEqual(data["materials"], 5)
        self.assertEqual(data["usable"], 4)

    def test_case_run_writes_json(self) -> None:
        with TestDir("svs-cli-") as tmp:
            out = Path(tmp) / "result.json"
            proc = self.run_cli(
                "run",
                "--case",
                "examples/cases/01-normal-30s.json",
                "--out",
                str(out),
                "--quiet",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            result = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "script_ready")
            self.assertEqual(result["total_duration_seconds"], 28)
            self.assertEqual([item["total_seconds"] for item in result["revisions"]], [38, 28])

    def test_duration_unmet_human_output(self) -> None:
        proc = self.run_cli(
            "run",
            "--case",
            "examples/cases/04-duration-unmet.json",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("duration_unmet", proc.stdout)
        self.assertIn("14.0 秒", proc.stdout)
        self.assertIn("未输出 ready 脚本", proc.stdout)

    def test_openai_without_key(self) -> None:
        with TestDir("svs-cli-") as tmp:
            env = dict(os.environ)
            env["PYTHONIOENCODING"] = "utf-8"
            env["AGENT_MODEL_API_KEY"] = ""
            proc = subprocess.run(
                [
                    sys.executable,
                    "run.py",
                    "run",
                    "--theme",
                    "通勤降噪耳机",
                    "--audience",
                    "通勤上班族",
                    "--platform",
                    "douyin",
                    "--target-duration",
                    "30",
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

#!/usr/bin/env python3
"""运行全部测试，并写入 examples/test-results.txt。"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from content_planner.timeutil import CST  # noqa: E402


def main() -> int:
    output = ROOT / "examples" / "test-results.txt"
    output.parent.mkdir(parents=True, exist_ok=True)
    started = datetime.now(CST)
    suite = unittest.TestLoader().discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("command: python scripts/run_tests.py\n")
        handle.write(f"started_at: {started.isoformat(timespec='seconds')}\n")
        handle.write("python: " + sys.version.replace("\n", " ") + "\n")
        handle.write("=" * 78 + "\n")
        result = unittest.TextTestRunner(stream=handle, verbosity=2).run(suite)
        handle.write("=" * 78 + "\n")
        handle.write(f"success: {result.wasSuccessful()}\n")
        handle.write(f"tests_run: {result.testsRun}\n")
        handle.write(f"failures: {len(result.failures)}\n")
        handle.write(f"errors: {len(result.errors)}\n")
        handle.write(f"skipped: {len(result.skipped)}\n")
        handle.write(f"finished_at: {datetime.now(CST).isoformat(timespec='seconds')}\n")
    summary = {
        "command": "python scripts/run_tests.py",
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now(CST).isoformat(timespec="seconds"),
        "success": result.wasSuccessful(),
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "result_file": "examples/test-results.txt",
    }
    (ROOT / "examples" / "test-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"tests={result.testsRun} failures={len(result.failures)} "
        f"errors={len(result.errors)} success={result.wasSuccessful()}"
    )
    print(f"result: {output.relative_to(ROOT).as_posix()}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())

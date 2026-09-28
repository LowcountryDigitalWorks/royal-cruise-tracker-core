#!/usr/bin/env python3
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "public_runner.py"


class PublicRunnerTests(unittest.TestCase):
    def run_child(self, exit_code: int):
        sentinel = "PRIVATE_" + "CABIN_91827_PRICE_1234"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "child.py"
            public_summary = root / "public-summary.md"
            child.write_text(
                "import os,sys\n"
                f"sentinel={sentinel!r}\n"
                "print(sentinel)\n"
                "print(sentinel, file=sys.stderr)\n"
                "summary=os.environ.get('GITHUB_STEP_SUMMARY')\n"
                "open(summary,'a',encoding='utf-8').write(sentinel+'\\n') if summary else None\n"
                + ("print('HTTP Error 403 Access Denied')\n" if exit_code else "")
                + f"raise SystemExit({exit_code})\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["GITHUB_STEP_SUMMARY"] = str(public_summary)
            proc = subprocess.run(
                [sys.executable, str(RUNNER), "--", sys.executable, str(child)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                check=False,
            )
            public_text = public_summary.read_text(encoding="utf-8") if public_summary.exists() else ""
            return sentinel, proc, public_text

    def test_success_exposes_only_generic_status(self):
        sentinel, proc, public_summary = self.run_child(0)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("Royal tracker: SUCCESS", proc.stdout)
        self.assertNotIn(sentinel, proc.stdout)
        self.assertNotIn(sentinel, public_summary)

    def test_failure_exposes_only_generic_class(self):
        sentinel, proc, public_summary = self.run_child(7)
        self.assertEqual(proc.returncode, 7)
        self.assertIn("class=royal_http_403", proc.stdout)
        self.assertNotIn(sentinel, proc.stdout)
        self.assertNotIn(sentinel, public_summary)


if __name__ == "__main__":
    unittest.main()

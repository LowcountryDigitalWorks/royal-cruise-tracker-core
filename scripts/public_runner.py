#!/usr/bin/env python3
"""Run the provider-capable tracker without exposing child output in public Actions."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from classify_tracker_failure import classify


def run_public(command: list[str]) -> int:
    if not command:
        print("::error::Royal tracker failed; class=runner_configuration_error")
        return 2
    try:
        with tempfile.TemporaryDirectory(prefix="royal-public-runner-") as tmp:
            root = Path(tmp)
            log_path = root / "child.log"
            summary_path = root / "child-summary.md"
            child_env = os.environ.copy()
            child_env["GITHUB_STEP_SUMMARY"] = str(summary_path)
            child_env["PUBLIC_ACTIONS_LOGS"] = "true"
            with log_path.open("w", encoding="utf-8", errors="replace") as handle:
                proc = subprocess.run(command, stdout=handle, stderr=subprocess.STDOUT, text=True, env=child_env, check=False)
            if proc.returncode:
                private_log = log_path.read_text(encoding="utf-8", errors="replace")
                print(f"::error::Royal tracker failed; class={classify(private_log)}")
                return proc.returncode if proc.returncode > 0 else 1
            print("Royal tracker: SUCCESS")
            return 0
    except Exception:
        print("::error::Royal tracker failed; class=runner_internal_error")
        return 2


def main(argv: list[str]) -> int:
    args = list(argv)
    if args and args[0] == "--":
        args = args[1:]
    return run_public(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

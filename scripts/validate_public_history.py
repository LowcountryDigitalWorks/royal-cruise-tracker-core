#!/usr/bin/env python3
"""Scan every commit reachable from HEAD for public/private boundary markers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import PurePosixPath

from validate_public_boundary import TEXT_SUFFIXES, VALIDATOR_PATHS, scan_text


def git(*args: str) -> str:
    proc = subprocess.run(["git", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", check=False)
    if proc.returncode:
        raise RuntimeError("git history inspection failed")
    return proc.stdout


def main() -> int:
    errors: list[str] = []
    commits = [line.strip() for line in git("rev-list", "--reverse", "HEAD").splitlines() if line.strip()]
    for commit in commits:
        paths = [line for line in git("ls-tree", "-r", "--name-only", commit).splitlines() if line]
        for rel in paths:
            if rel in VALIDATOR_PATHS or PurePosixPath(rel).suffix.lower() not in TEXT_SUFFIXES:
                continue
            proc = subprocess.run(["git", "show", f"{commit}:{rel}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
            if proc.returncode:
                continue
            text = proc.stdout.decode("utf-8-sig", errors="replace")
            for error in scan_text(rel, text):
                errors.append(f"{commit[:12]}: {error}")
    if errors:
        for error in sorted(set(errors)):
            print(f"ERROR: {error}")
        return 1
    print(f"Public-history boundary validation: PASS ({len(commits)} reachable commit(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())

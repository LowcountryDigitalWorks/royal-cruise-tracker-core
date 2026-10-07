#!/usr/bin/env python3
"""Scan every commit reachable from HEAD for public/private boundary markers."""

from __future__ import annotations

import subprocess
import sys

from validate_public_boundary import VALIDATOR_PATHS, decode_tracked_text, scan_path, scan_text


def git_text(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode:
        raise RuntimeError("git history inspection failed")
    return proc.stdout


def git_bytes(*args: str) -> bytes:
    proc = subprocess.run(["git", *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode:
        raise RuntimeError("git history inspection failed")
    return proc.stdout


def main() -> int:
    errors: list[str] = []
    commits = [line.strip() for line in git_text("rev-list", "--reverse", "HEAD").splitlines() if line.strip()]
    for commit in commits:
        raw_paths = git_bytes("ls-tree", "-r", "-z", "--name-only", commit)
        paths = [item.decode("utf-8", errors="surrogateescape") for item in raw_paths.split(b"\0") if item]
        for rel in paths:
            for error in scan_path(rel):
                errors.append(f"{commit[:12]}: {error}")
            if rel in VALIDATOR_PATHS:
                continue
            proc = subprocess.run(["git", "show", f"{commit}:{rel}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
            if proc.returncode:
                continue
            text = decode_tracked_text(proc.stdout)
            if text is None:
                continue
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

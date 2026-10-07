#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
MARKER = "LDW" + "01"


class PublicBoundaryTraversalTests(unittest.TestCase):
    def git(self, root: Path, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode:
            self.fail(f"git {' '.join(args)} failed: {proc.stderr}")
        return proc.stdout.strip()

    def make_repo(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "scripts").mkdir(parents=True)
        (root / "config").mkdir(parents=True)
        shutil.copy2(ROOT / "scripts" / "validate_public_boundary.py", root / "scripts" / "validate_public_boundary.py")
        shutil.copy2(ROOT / "scripts" / "validate_public_history.py", root / "scripts" / "validate_public_history.py")
        shutil.copy2(ROOT / "config" / "demo-profile.json", root / "config" / "demo-profile.json")
        self.git(root, "init", "-q")
        self.git(root, "config", "user.email", "tests@example.com")
        self.git(root, "config", "user.name", "Synthetic Test")
        self.git(root, "add", "scripts", "config")
        self.git(root, "commit", "-qm", "safe synthetic baseline")
        return temp, root

    def run_validator(self, root: Path, script: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(root / "scripts" / script)],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def test_current_tree_traversal_rejects_required_text_formats_and_paths(self):
        cases = {
            "HTML": ("public/index.html", f"<p>{MARKER}</p>"),
            "CSS": ("public/site.css", f".demo::after {{ content: '{MARKER}'; }}"),
            "SVG": ("public/icon.svg", f"<svg><text>{MARKER}</text></svg>"),
            "ES module": ("public/app.mjs", f"export const synthetic = '{MARKER}';"),
            "tracked path": (f"fixtures/{MARKER}/safe.txt", "harmless synthetic content"),
        }
        for label, (relative_path, content) in cases.items():
            with self.subTest(label=label):
                temp, root = self.make_repo()
                try:
                    file = root / relative_path
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_text(content, encoding="utf-8")
                    self.git(root, "add", relative_path)
                    proc = self.run_validator(root, "validate_public_boundary.py")
                    self.assertEqual(proc.returncode, 1, proc.stdout)
                    self.assertIn(relative_path, proc.stdout)
                    self.assertIn("internal device label", proc.stdout)
                finally:
                    temp.cleanup()

    def test_reachable_history_rejects_deleted_forbidden_path_and_identifies_originating_commit(self):
        temp, root = self.make_repo()
        try:
            relative_path = f"history/{MARKER}/synthetic.html"
            file = root / relative_path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("harmless synthetic content", encoding="utf-8")
            self.git(root, "add", relative_path)
            self.git(root, "commit", "-qm", "add synthetic forbidden path sentinel")
            originating_commit = self.git(root, "rev-parse", "HEAD")

            self.git(root, "rm", "-q", relative_path)
            self.git(root, "commit", "-qm", "remove synthetic forbidden path sentinel")

            current = self.run_validator(root, "validate_public_boundary.py")
            self.assertEqual(current.returncode, 0, current.stdout)

            history = self.run_validator(root, "validate_public_history.py")
            self.assertEqual(history.returncode, 1, history.stdout)
            self.assertIn(originating_commit[:12], history.stdout)
            self.assertIn(relative_path, history.stdout)
            self.assertIn("internal device label", history.stdout)
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()

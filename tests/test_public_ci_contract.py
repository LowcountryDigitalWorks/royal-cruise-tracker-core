#!/usr/bin/env python3
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")


class PublicCiContractTests(unittest.TestCase):
    def test_pr_ci_is_secretless_and_read_only(self):
        self.assertIn("pull_request:", CI)
        self.assertNotIn("pull_request_target", CI)
        self.assertIn("contents: read", CI)
        self.assertNotIn("secrets.", CI)

    def test_full_history_is_available_to_boundary_scans(self):
        self.assertIn("fetch-depth: 0", CI)
        self.assertIn("scripts/validate_public_history.py", CI)

    def test_pinned_actionlint_is_required(self):
        self.assertIn("v1.7.12", CI)
        self.assertIn("8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8", CI)
        self.assertIn('"$dest/actionlint" -color', CI)

    def test_pinned_betterleaks_head_history_scan_is_required(self):
        self.assertIn("v1.8.1", CI)
        self.assertIn("efa407244e1ea8e35f582b8a42becdeac08bdead04f68eb752adda722d583c2a", CI)
        self.assertIn('--log-opts="HEAD"', CI)
        self.assertIn("--redact", CI)
        self.assertNotIn("--validation", CI)


if __name__ == "__main__":
    unittest.main()

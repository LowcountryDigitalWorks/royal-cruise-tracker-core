#!/usr/bin/env python3
from datetime import datetime, timezone
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from royal_check_due import decide

class CloudflareSchedulerDueGateTests(unittest.TestCase):
    def test_automatic_wake_skips_satisfied_slot(self):
        state = decide(
            datetime(2030, 1, 2, 19, 0, tzinfo=timezone.utc),
            datetime(2030, 1, 2, 18, 6, tzinfo=timezone.utc),
            "workflow_dispatch",
            scheduler_wake=True,
        )
        self.assertFalse(state.due)

    def test_automatic_wake_recovers_unsatisfied_slot(self):
        state = decide(
            datetime(2030, 1, 2, 19, 0, tzinfo=timezone.utc),
            datetime(2030, 1, 2, 6, 6, tzinfo=timezone.utc),
            "workflow_dispatch",
            scheduler_wake=True,
        )
        self.assertTrue(state.due)
        self.assertEqual(state.reason, "target_window_overdue")

    def test_manual_dispatch_still_bypasses_gate(self):
        state = decide(
            datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc),
            None,
            "workflow_dispatch",
            scheduler_wake=False,
        )
        self.assertTrue(state.due)
        self.assertEqual(state.reason, "manual_dispatch")

if __name__ == "__main__":
    unittest.main()

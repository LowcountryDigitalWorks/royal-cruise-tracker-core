#!/usr/bin/env python3
from datetime import datetime, timezone
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from royal_check_due import (
    TARGET_HOURS_UTC,
    TARGET_MINUTE,
    decide,
    most_recent_target,
    next_target,
    parse_timestamp,
)

class RoyalCheckDueTests(unittest.TestCase):
    def test_demo_schedule_is_runtime_configured(self):
        self.assertEqual(TARGET_HOURS_UTC, (6, 18))
        self.assertEqual(TARGET_MINUTE, 5)

    def test_target_selection_uses_configured_slots(self):
        now = datetime(2030, 1, 2, 19, 0, tzinfo=timezone.utc)
        self.assertEqual(most_recent_target(now), datetime(2030, 1, 2, 18, 5, tzinfo=timezone.utc))
        self.assertEqual(next_target(now), datetime(2030, 1, 3, 6, 5, tzinfo=timezone.utc))

    def test_latest_evidence_after_target_satisfies_window(self):
        state = decide(
            datetime(2030, 1, 2, 19, 0, tzinfo=timezone.utc),
            datetime(2030, 1, 2, 18, 6, tzinfo=timezone.utc),
            "schedule",
        )
        self.assertFalse(state.due)
        self.assertEqual(state.reason, "target_window_already_satisfied")

    def test_missed_window_is_recovered(self):
        state = decide(
            datetime(2030, 1, 2, 19, 0, tzinfo=timezone.utc),
            datetime(2030, 1, 2, 6, 6, tzinfo=timezone.utc),
            "schedule",
        )
        self.assertTrue(state.due)
        self.assertEqual(state.target_at, datetime(2030, 1, 2, 18, 5, tzinfo=timezone.utc))

    def test_manual_dispatch_bypasses_gate(self):
        state = decide(
            datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc),
            None,
            "workflow_dispatch",
        )
        self.assertTrue(state.due)
        self.assertEqual(state.reason, "manual_dispatch")

    def test_timestamp_parser_accepts_z_and_offsets(self):
        expected = datetime(2030, 1, 2, 18, 6, tzinfo=timezone.utc)
        self.assertEqual(parse_timestamp("2030-01-02T18:06:00Z"), expected)
        self.assertEqual(parse_timestamp("2030-01-02T13:06:00-05:00"), expected)
        self.assertIsNone(parse_timestamp("not-a-time"))

if __name__ == "__main__":
    unittest.main()

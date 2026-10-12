#!/usr/bin/env python3
"""Focused regressions for ALERT-001B bridge diagnostic and framing hardening."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from notification_intent_bridge import (  # noqa: E402
    INTENT_CONTRACT_VERSION,
    NotificationIntentBridgeError,
    invoke_intent_cli,
)


class NotificationIntentBridgeHardeningTests(unittest.TestCase):
    def test_cli_diagnostics_never_echo_caller_controlled_action_or_version(self):
        cli_path = ROOT / "scripts" / "notification_intent_cli.mjs"
        version_sentinel = "SENSITIVE_VERSION_SENTINEL_12345"
        action_sentinel = "SENSITIVE_ACTION_SENTINEL_67890"

        bad_version = subprocess.run(
            ["node", str(cli_path)],
            input=json.dumps(
                {
                    "contract_version": version_sentinel,
                    "action": "safeIntentPayload",
                }
            ).encode("utf-8"),
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(bad_version.returncode, 0)
        self.assertNotIn(version_sentinel, bad_version.stderr.decode("utf-8", errors="replace"))

        bad_action = subprocess.run(
            ["node", str(cli_path)],
            input=json.dumps(
                {
                    "contract_version": INTENT_CONTRACT_VERSION,
                    "action": action_sentinel,
                }
            ).encode("utf-8"),
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(bad_action.returncode, 0)
        self.assertNotIn(action_sentinel, bad_action.stderr.decode("utf-8", errors="replace"))

    def test_bridge_does_not_propagate_child_stderr(self):
        sentinel = "SENSITIVE_CHILD_STDERR_SENTINEL"
        completed = subprocess.CompletedProcess(
            args=["node"],
            returncode=9,
            stdout=b"",
            stderr=sentinel.encode("utf-8"),
        )
        with mock.patch("notification_intent_bridge.subprocess.run", return_value=completed):
            with self.assertRaises(NotificationIntentBridgeError) as ctx:
                invoke_intent_cli("deriveIntents", {})
        self.assertIn("failed closed", str(ctx.exception))
        self.assertNotIn(sentinel, str(ctx.exception))

    def test_bridge_rejects_extra_blank_or_nonblank_stdout(self):
        response = json.dumps(
            {
                "contract_version": INTENT_CONTRACT_VERSION,
                "action": "deriveIntents",
                "status": "ok",
                "result": [],
            },
            separators=(",", ":"),
        ).encode("utf-8")

        bad_outputs = [
            b"\n" + response + b"\n",
            response + b"\n\n",
            response + b"\nEXTRA\n",
            response,
        ]

        for stdout in bad_outputs:
            with self.subTest(stdout=stdout):
                completed = subprocess.CompletedProcess(
                    args=["node"],
                    returncode=0,
                    stdout=stdout,
                    stderr=b"",
                )
                with mock.patch("notification_intent_bridge.subprocess.run", return_value=completed):
                    with self.assertRaises(NotificationIntentBridgeError) as ctx:
                        invoke_intent_cli("deriveIntents", {})
                self.assertIn("stdout framing", str(ctx.exception).lower())

    def test_bridge_accepts_exact_single_json_line(self):
        response = json.dumps(
            {
                "contract_version": INTENT_CONTRACT_VERSION,
                "action": "deriveIntents",
                "status": "ok",
                "result": [],
            },
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        completed = subprocess.CompletedProcess(
            args=["node"],
            returncode=0,
            stdout=response,
            stderr=b"",
        )
        with mock.patch("notification_intent_bridge.subprocess.run", return_value=completed):
            self.assertEqual(invoke_intent_cli("deriveIntents", {}), [])


if __name__ == "__main__":
    unittest.main()

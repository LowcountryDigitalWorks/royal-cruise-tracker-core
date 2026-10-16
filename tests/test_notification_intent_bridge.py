#!/usr/bin/env python3
"""Deterministic python bridge tests for accepted notification-intent semantics."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from notification_intent_bridge import (
    INTENT_CONTRACT_VERSION,
    NotificationIntentBridgeError,
    derive_intents,
    group_intents,
    invoke_intent_cli,
    map_observation_to_semantic_state,
    record_delivery_attempt,
    reconcile_intents,
    safe_intent_lifecycle_record,
    safe_intent_payload,
)


def sample_state(**overrides) -> dict:
    defaults = {
        "subject_key": "subject:synthetic-demo",
        "group_key": "group:synthetic-sailing",
        "comparison_scope_key": "scope:usd-total-demo",
        "observed_at": "2030-01-02T12:00:00Z",
        "freshness": "fresh",
        "comparable": True,
        "current_value": 100.0,
        "historical_low": 95.0,
        "target_state": "above",
        "availability_state": "available",
        "availability_continuity": True,
        "evidence_label": "synthetic evidence",
        "actionability_state": "verify",
        "policy_state": "configured",
    }
    defaults.update(overrides)
    return map_observation_to_semantic_state(**defaults)


class NotificationIntentBridgeTests(unittest.TestCase):
    def test_first_observation_baseline_emits_no_intent(self):
        st = sample_state(current_value=80.0)
        res = derive_intents(None, st, {"meaningful_drop": {"enabled": True, "absolute_threshold": 5}})
        self.assertEqual(res, [])

    def test_bootstrap_target_policy_on_and_off(self):
        st = sample_state(target_state="reached", target_generation="g1", current_value=90.0)
        off = derive_intents(None, st, {"target": {"enabled": True, "bootstrap": False}})
        self.assertEqual(off, [])

        on = derive_intents(None, st, {"target": {"enabled": True, "bootstrap": True}})
        self.assertEqual(len(on), 1)
        self.assertEqual(on[0]["event_type"], "target_hit")
        self.assertEqual(on[0]["transition"], "baseline->reached")

    def test_target_steady_rearm_rehit_generation_identity(self):
        prev = sample_state(target_state="reached", target_generation="g1", current_value=90.0)
        curr = sample_state(target_state="reached", target_generation="g1", current_value=90.0)
        self.assertEqual(derive_intents(prev, curr, {"target": {"enabled": True}}), [])

        prev_above = sample_state(target_state="above", current_value=110.0)
        curr_g1 = sample_state(target_state="reached", target_generation="gen-1", current_value=95.0)
        curr_g2 = sample_state(target_state="reached", target_generation="gen-2", current_value=95.0)
        hit1 = derive_intents(prev_above, curr_g1, {"target": {"enabled": True}})[0]
        hit2 = derive_intents(prev_above, curr_g2, {"target": {"enabled": True}})[0]
        self.assertNotEqual(hit1["intent_id"], hit2["intent_id"])

    def test_meaningful_drop_absolute_threshold(self):
        prev = sample_state(current_value=100.0)
        curr = sample_state(current_value=90.0, price_generation="p90")
        res = derive_intents(prev, curr, {"meaningful_drop": {"enabled": True, "absolute_threshold": 5, "combine": "ANY"}})
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["event_type"], "meaningful_drop")
        self.assertEqual(res[0]["absolute_delta"], 10.0)

    def test_meaningful_drop_percent_threshold(self):
        prev = sample_state(current_value=200.0)
        curr = sample_state(current_value=180.0, price_generation="p180")
        res = derive_intents(prev, curr, {"meaningful_drop": {"enabled": True, "percent_threshold": 9, "combine": "ANY"}})
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["percent_delta"], 10.0)

    def test_explicit_any_and_all_threshold_behavior(self):
        prev = sample_state(current_value=100.0)
        curr10 = sample_state(current_value=90.0, price_generation="p90")
        policy_all = {"meaningful_drop": {"enabled": True, "absolute_threshold": 5, "percent_threshold": 20, "combine": "ALL"}}
        fail = derive_intents(prev, curr10, policy_all)
        self.assertEqual(fail, [])

        curr25 = sample_state(current_value=75.0, price_generation="p75")
        pass_res = derive_intents(prev, curr25, policy_all)
        self.assertEqual(len(pass_res), 1)

    def test_stale_incomplete_failed_noncomparable_suppression(self):
        prev = sample_state(current_value=100.0)
        for freshness in ["stale", "incomplete", "failed"]:
            curr = sample_state(current_value=80.0, freshness=freshness, price_generation="p80")
            res = derive_intents(prev, curr, {"meaningful_drop": {"enabled": True, "absolute_threshold": 5}})
            self.assertEqual(res, [], f"Expected suppression for freshness '{freshness}'")

        non_comp = sample_state(current_value=80.0, comparable=False, price_generation="p80")
        self.assertEqual(derive_intents(prev, non_comp, {"meaningful_drop": {"enabled": True, "absolute_threshold": 5}}), [])

    def test_scope_mismatch_suppression(self):
        prev = sample_state(current_value=100.0, comparison_scope_key="scope:usd-total")
        curr = sample_state(current_value=80.0, comparison_scope_key="scope:usd-cabin", price_generation="p80")
        res = derive_intents(prev, curr, {"meaningful_drop": {"enabled": True, "absolute_threshold": 5}})
        self.assertEqual(res, [])

    def test_new_low_semantics(self):
        prev = sample_state(current_value=100.0, historical_low=90.0)
        curr = sample_state(current_value=85.0, price_generation="p85")
        res = derive_intents(prev, curr, {"new_low": {"enabled": True}})
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["event_type"], "new_low")

    def test_contiguous_restock(self):
        prev = sample_state(availability_state="unavailable", freshness="fresh")
        curr = sample_state(availability_state="available", freshness="fresh", availability_generation="r1")
        res = derive_intents(prev, curr, {"availability": {"enabled": True}})
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["event_type"], "availability_restored")

    def test_failure_gap_invalid_previous_availability_suppression(self):
        prev = sample_state(availability_state="unavailable", freshness="failed")
        curr = sample_state(availability_state="available", freshness="fresh", availability_generation="r1")
        res = derive_intents(prev, curr, {"availability": {"enabled": True}})
        self.assertEqual(res, [])

        prev_disc = sample_state(availability_state="unavailable", freshness="fresh")
        curr_disc = sample_state(availability_state="available", freshness="fresh", availability_generation="r1", availability_continuity=False)
        self.assertEqual(derive_intents(prev_disc, curr_disc, {"availability": {"enabled": True}}), [])

    def test_repeated_failure_health_threshold(self):
        prev = sample_state(freshness="failed", consecutive_failures=2)
        curr = sample_state(freshness="failed", consecutive_failures=3, health_generation="h1")
        res = derive_intents(prev, curr, {"health": {"enabled": True, "failure_threshold": 3}})
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["event_type"], "observation_failed")

    def test_deterministic_fingerprint_equality(self):
        c1 = safe_intent_payload({"subject_key": "subject:x", "event_type": "target_hit", "semantic_generation": "g1"})
        c2 = safe_intent_payload({"semantic_generation": "g1", "subject_key": "subject:x", "event_type": "target_hit"})
        self.assertEqual(c1["intent_id"], c2["intent_id"])

    def test_deterministic_grouping_order(self):
        target = safe_intent_payload({"subject_key": "subject:x", "event_type": "target_hit", "semantic_generation": "g1", "priority_class": "urgent"})
        drop = safe_intent_payload({"subject_key": "subject:x", "event_type": "meaningful_drop", "semantic_generation": "p90", "priority_class": "high"})
        g1 = group_intents([drop, target])
        g2 = group_intents([target, drop])
        self.assertEqual(g1, g2)
        self.assertEqual(g1[0]["intent_ids"], [target["intent_id"], drop["intent_id"]])

    def test_forged_or_mismatched_intent_id_rejected(self):
        with self.assertRaises(NotificationIntentBridgeError) as ctx:
            safe_intent_payload({
                "intent_id": "ni1_forged_id",
                "subject_key": "subject:x",
                "event_type": "target_hit",
                "semantic_generation": "g1",
            })
        self.assertIn("Node CLI execution failed closed", str(ctx.exception))

    def test_private_forbidden_field_input_rejected(self):
        for key in ["reservation_id", "booking_id", "apprise_url", "passenger_name", "secret_token"]:
            with self.assertRaises(NotificationIntentBridgeError):
                safe_intent_payload({
                    "subject_key": "subject:x",
                    "event_type": "target_hit",
                    "semantic_generation": "g1",
                    key: "sensitive_value",
                })

    def test_malformed_json_input_hard_fails(self):
        cli_path = ROOT / "scripts" / "notification_intent_cli.mjs"
        proc = subprocess.run(
            ["node", str(cli_path)],
            input=b"{invalid_json",
            capture_output=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Malformed JSON input", proc.stderr.decode("utf-8"))

    def test_oversized_input_hard_fails_before_execution(self):
        oversized = b"a" * (512 * 1024 + 100)
        cli_path = ROOT / "scripts" / "notification_intent_cli.mjs"
        proc = subprocess.run(
            ["node", str(cli_path)],
            input=oversized,
            capture_output=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Input size limit exceeded", proc.stderr.decode("utf-8"))

    def test_node_subprocess_timeout_or_nonzero_fails_closed(self):
        with self.assertRaises(NotificationIntentBridgeError) as ctx:
            invoke_intent_cli("deriveIntents", {}, timeout=0.0001)
        self.assertIn("timed out", str(ctx.exception).lower())

    def test_unexpected_stdout_framing_fails_closed(self):
        # Pass unknown action to trigger error in Node CLI
        with self.assertRaises(NotificationIntentBridgeError) as ctx:
            invoke_intent_cli("unknownAction", {})
        self.assertIn("execution failed closed", str(ctx.exception))

    def test_contract_version_mismatch_fails_closed(self):
        cli_path = ROOT / "scripts" / "notification_intent_cli.mjs"
        proc = subprocess.run(
            ["node", str(cli_path)],
            input=json.dumps({"contract_version": "alert-intent/v0", "action": "safeIntentPayload"}).encode("utf-8"),
            capture_output=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Contract version mismatch", proc.stderr.decode("utf-8"))

    def test_stdout_contains_json_only_and_never_diagnostics_or_echo(self):
        payload = {"intent": {"subject_key": "subject:x", "event_type": "target_hit", "semantic_generation": "g1"}}
        cli_path = ROOT / "scripts" / "notification_intent_cli.mjs"
        input_bytes = json.dumps({"contract_version": INTENT_CONTRACT_VERSION, "action": "safeIntentPayload", **payload}).encode("utf-8")
        proc = subprocess.run(["node", str(cli_path)], input=input_bytes, capture_output=True)
        self.assertEqual(proc.returncode, 0)
        stdout_str = proc.stdout.decode("utf-8")
        self.assertTrue(stdout_str.startswith("{") and stdout_str.endswith("}\n"))
        self.assertEqual(proc.stderr, b"")

    def test_bridge_output_contains_no_transport_state(self):
        res = safe_intent_payload({"subject_key": "subject:x", "event_type": "target_hit", "semantic_generation": "g1"})
        for forbidden in ["pending", "delivered", "retry_count", "last_attempt_at", "delivered_at", "transport_state"]:
            self.assertNotIn(forbidden, res)

    def test_repeated_identical_input_produces_byte_equivalent_canonical_json(self):
        payload = {"contract_version": INTENT_CONTRACT_VERSION, "action": "safeIntentPayload", "intent": {"subject_key": "subject:x", "event_type": "target_hit", "semantic_generation": "g1"}}
        cli_path = ROOT / "scripts" / "notification_intent_cli.mjs"
        input_bytes = json.dumps(payload).encode("utf-8")
        proc1 = subprocess.run(["node", str(cli_path)], input=input_bytes, capture_output=True)
        proc2 = subprocess.run(["node", str(cli_path)], input=input_bytes, capture_output=True)
        self.assertEqual(proc1.stdout, proc2.stdout)

    def test_reconcile_intents_lifecycle_and_retry(self):
        p1 = sample_state(current_value=100.0)
        c1 = sample_state(current_value=90.0, price_generation="p90", observed_at="2030-01-02T12:00:00Z")
        policy = {"meaningful_drop": {"enabled": True, "absolute_threshold": 5, "combine": "ANY"}}

        reconciled1 = reconcile_intents([], c1, policy, previous_state=p1)
        self.assertEqual(len(reconciled1), 1)
        self.assertEqual(reconciled1[0]["delivery_state"], "pending")

        failed_attempt = record_delivery_attempt(reconciled1, {"status": "failed", "timestamp": "2030-01-02T12:01:00Z"})
        self.assertEqual(failed_attempt[0]["delivery_state"], "failed")
        self.assertEqual(failed_attempt[0]["attempt_count"], 1)

        c2 = sample_state(current_value=90.0, price_generation="p90", observed_at="2030-01-02T13:00:00Z")
        retry_reconciled = reconcile_intents(failed_attempt, c2, policy, previous_state=c1)
        self.assertEqual(len(retry_reconciled), 1)
        self.assertEqual(retry_reconciled[0]["delivery_state"], "failed")

        delivered = record_delivery_attempt(retry_reconciled, {"status": "delivered", "timestamp": "2030-01-02T13:01:00Z"})
        self.assertEqual(delivered[0]["delivery_state"], "delivered")
        self.assertEqual(delivered[0]["delivered_at"], "2030-01-02T13:01:00Z")

    def test_reconcile_intents_supersedes_when_price_rises(self):
        p1 = sample_state(current_value=100.0)
        c1 = sample_state(current_value=90.0, price_generation="p90", observed_at="2030-01-02T12:00:00Z")
        policy = {"meaningful_drop": {"enabled": True, "absolute_threshold": 5, "combine": "ANY"}}

        reconciled1 = reconcile_intents([], c1, policy, previous_state=p1)
        failed_attempt = record_delivery_attempt(reconciled1, {"status": "failed", "timestamp": "2030-01-02T12:01:00Z"})

        c2 = sample_state(current_value=110.0, price_generation="p110", observed_at="2030-01-02T13:00:00Z")
        superseded = reconcile_intents(failed_attempt, c2, policy, timestamp="2030-01-02T13:00:00Z", previous_state=c1)

        self.assertEqual(len(superseded), 1)
        self.assertEqual(superseded[0]["delivery_state"], "superseded")
        self.assertEqual(superseded[0]["superseded_reason"], "price_increased")

    def test_safe_intent_lifecycle_record_and_forbidden_fields(self):
        valid = safe_intent_lifecycle_record({"subject_key": "subject:x", "event_type": "target_hit", "semantic_generation": "g1"})
        self.assertEqual(valid["delivery_state"], "pending")
        self.assertEqual(valid["attempt_count"], 0)

        with self.assertRaises(NotificationIntentBridgeError):
            safe_intent_lifecycle_record({"subject_key": "subject:x", "event_type": "target_hit", "semantic_generation": "g1", "apprise_url": "https://forbidden.com"})


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
WORKFLOW = (ROOT / ".github" / "workflows" / "price-check.yml").read_text(encoding="utf-8")
WAKE = (ROOT / "cloudflare" / "scheduler-wake.js").read_text(encoding="utf-8")


def provider_block() -> str:
    start = WORKFLOW.index("- name: Run enabled profile through public-safe boundary")
    return WORKFLOW[start:]


def telemetry_block() -> str:
    start = WORKFLOW.index("- name: Record Cloudflare scheduler dispatch timing")
    end = WORKFLOW.index("- name: Record skipped scheduler wake")
    return WORKFLOW[start:end]


def run_node(source: str) -> dict:
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed ({result.returncode}): {result.stderr}\n{result.stdout}")
    return json.loads(result.stdout.strip())


def persist_module():
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    with mock.patch.dict(os.environ, {"ROYAL_RUNTIME_MODE": "demo"}, clear=False):
        return importlib.import_module("persist_schedule_health")


class Sec001AContractTests(unittest.TestCase):
    def test_provider_process_receives_only_primary_profile_secret_family(self):
        block = provider_block()
        for required in (
            "PRIMARY_RCCL_USERNAME",
            "PRIMARY_RCCL_PASSWORD",
            "PRIMARY_RCCL_RESERVATION_ID",
            "PRIMARY_RCCL_PAID_PRICE",
            "PRIMARY_APPRISE_URL",
            "PRIMARY_MINIMUM_SAVING_ALERT",
            "PRIMARY_WATCHLIST_JSON",
        ):
            self.assertIn(required, block)
        for forbidden in (
            "SECONDARY_ENABLED",
            "SECONDARY_RCCL_USERNAME",
            "SECONDARY_RCCL_PASSWORD",
            "SECONDARY_RCCL_RESERVATION_ID",
            "SECONDARY_RCCL_PAID_PRICE",
            "SECONDARY_APPRISE_URL",
            "SECONDARY_MINIMUM_SAVING_ALERT",
            "SECONDARY_WATCHLIST_JSON",
        ):
            self.assertNotIn(forbidden, block)

    def test_workflow_maps_scheduler_verification_to_dedicated_proof_secret(self):
        block = telemetry_block()
        self.assertIn("ROYAL_TARGET_PROOF_KEY: ${{ secrets.SCHEDULER_TARGET_PROOF_KEY }}", block)
        self.assertNotIn("ACTIONS_DISPATCH_TOKEN", block)
        self.assertIn("SCHEDULER_TARGET_PROOF_KEY", WAKE)
        self.assertIn("Authorization: `Bearer ${token}`", WAKE)
        self.assertIn("schedulerTargetProof(target, proofKey)", WAKE)
        self.assertNotIn("schedulerTargetProof(target, token)", WAKE)

    def test_worker_rejects_scheduled_dispatch_without_bearer_token(self):
        result = run_node("""
            import { dispatchScheduledRoyalWake } from './cloudflare/scheduler-wake.js';
            const cron = ['5', '6', '*', '*', '*'].join(' ');
            const controller = { cron, scheduledTime: 1893456000000 };
            const env = {
              ROYAL_WAKE_CRON: cron,
              GITHUB_ACTIONS_DISPATCH_URL: 'https://api.github.com/repos/example/demo/actions/workflows/price-check.yml/dispatches',
              SCHEDULER_TARGET_PROOF_KEY: 'p',
            };
            let error = '';
            try {
              await dispatchScheduledRoyalWake(controller, env, async () => ({ status: 204 }));
            } catch (caught) {
              error = String(caught?.message || caught);
            }
            console.log(JSON.stringify({ error }));
        """)
        self.assertEqual(result["error"], "scheduler_wake_secret_missing")

    def test_missing_proof_key_never_falls_back_to_bearer_for_hmac(self):
        result = run_node("""
            import { dispatchScheduledRoyalWake } from './cloudflare/scheduler-wake.js';
            const cron = ['5', '6', '*', '*', '*'].join(' ');
            const controller = { cron, scheduledTime: 1893456000000 };
            const env = {
              ROYAL_WAKE_CRON: cron,
              GITHUB_ACTIONS_DISPATCH_URL: 'https://api.github.com/repos/example/demo/actions/workflows/price-check.yml/dispatches',
              GITHUB_ACTIONS_DISPATCH_TOKEN: 'b',
            };
            let captured = null;
            await dispatchScheduledRoyalWake(controller, env, async (url, options) => {
              captured = { url, headers: options.headers, body: JSON.parse(options.body) };
              return { status: 204 };
            });
            console.log(JSON.stringify(captured));
        """)
        self.assertEqual(result["headers"]["Authorization"], "Bearer b")
        self.assertEqual(result["body"]["inputs"]["scheduler_target_proof"], "")
        self.assertTrue(result["body"]["inputs"]["scheduler_target_at"].endswith("Z"))

    def test_worker_signs_exact_target_only_with_dedicated_proof_key(self):
        result = run_node("""
            import { dispatchScheduledRoyalWake, schedulerTargetProof } from './cloudflare/scheduler-wake.js';
            const cron = ['5', '6', '*', '*', '*'].join(' ');
            const controller = { cron, scheduledTime: 1893456000000 };
            const bearer = 'b';
            const proofKey = 'p';
            const env = {
              ROYAL_WAKE_CRON: cron,
              GITHUB_ACTIONS_DISPATCH_URL: 'https://api.github.com/repos/example/demo/actions/workflows/price-check.yml/dispatches',
              GITHUB_ACTIONS_DISPATCH_TOKEN: bearer,
              SCHEDULER_TARGET_PROOF_KEY: proofKey,
            };
            let captured = null;
            await dispatchScheduledRoyalWake(controller, env, async (url, options) => {
              captured = { url, headers: options.headers, body: JSON.parse(options.body) };
              return { status: 204 };
            });
            const target = captured.body.inputs.scheduler_target_at;
            console.log(JSON.stringify({
              captured,
              expected: await schedulerTargetProof(target, proofKey),
              bearerProof: await schedulerTargetProof(target, bearer),
            }));
        """)
        supplied = result["captured"]["body"]["inputs"]["scheduler_target_proof"]
        self.assertEqual(result["captured"]["headers"]["Authorization"], "Bearer b")
        self.assertEqual(supplied, result["expected"])
        self.assertNotEqual(supplied, result["bearerProof"])

    def test_distinct_wrong_key_fails_and_correct_proof_verifies(self):
        persist = persist_module()
        target = "2030-01-01T00:00:00.000Z"
        bearer_proof = persist.target_proof(target, "b")
        proof = persist.target_proof(target, "p")
        self.assertFalse(persist.verified_exact_target(target, bearer_proof, "p"))
        self.assertTrue(persist.verified_exact_target(target, proof, "p"))

    def test_missing_or_invalid_proof_selects_fallback_without_provider_failure(self):
        persist = persist_module()
        exact = "2030-01-01T00:00:00+00:00"
        fallback = "2030-01-01T00:05:00+00:00"
        for supplied in ("", "x", persist.target_proof(exact, "w")):
            selected, source, verified = persist.select_target(
                exact,
                supplied,
                "p",
                fallback,
            )
            self.assertEqual(selected, fallback)
            self.assertEqual(source, persist.FALLBACK_TARGET_SOURCE)
            self.assertFalse(verified)

    def test_correct_proof_selects_authenticated_exact_target(self):
        persist = persist_module()
        exact = "2030-01-01T00:00:00+00:00"
        fallback = "2030-01-01T00:05:00+00:00"
        proof = persist.target_proof(exact, "p")
        selected, source, verified = persist.select_target(exact, proof, "p", fallback)
        self.assertEqual(selected, exact)
        self.assertEqual(source, persist.EXACT_TARGET_SOURCE)
        self.assertTrue(verified)

    def test_disabled_secondary_profile_needs_no_secondary_secret_values(self):
        config = {
            "sailing": {
                "id": "TEST-SEC-001A",
                "ship_name": "Test Ship",
                "ship_code": "TS",
                "sail_date": "2031-01-01",
            },
            "watch_specs": [
                {
                    "name": "Test Product",
                    "prefix": "test",
                    "product": "TEST1",
                    "age_field": "adult",
                    "audience": [],
                }
            ],
            "profiles": [
                {
                    "label": "primary",
                    "username_env": "PRIMARY_RCCL_USERNAME",
                    "password_env": "PRIMARY_RCCL_PASSWORD",
                    "traveler_roles": {},
                    "enabled": True,
                },
                {
                    "label": "secondary",
                    "username_env": "SECONDARY_RCCL_USERNAME",
                    "password_env": "SECONDARY_RCCL_PASSWORD",
                    "reservation_id_env": "SECONDARY_RCCL_RESERVATION_ID",
                    "paid_price_env": "SECONDARY_RCCL_PAID_PRICE",
                    "apprise_url_env": "SECONDARY_APPRISE_URL",
                    "threshold_env": "SECONDARY_MINIMUM_SAVING_ALERT",
                    "watchlist_env": "SECONDARY_WATCHLIST_JSON",
                    "enabled_env": "SECONDARY_ENABLED",
                    "traveler_roles": {},
                },
            ],
        }
        env = os.environ.copy()
        env.update({
            "ROYAL_RUNTIME_MODE": "production",
            "ROYAL_PROFILE_JSON": json.dumps(config),
            "PRIMARY_RCCL_USERNAME": "u",
            "PRIMARY_RCCL_PASSWORD": "p",
            "SECONDARY_ENABLED": "false",
        })
        for key in (
            "SECONDARY_RCCL_USERNAME",
            "SECONDARY_RCCL_PASSWORD",
            "SECONDARY_RCCL_RESERVATION_ID",
            "SECONDARY_RCCL_PAID_PRICE",
            "SECONDARY_APPRISE_URL",
            "SECONDARY_MINIMUM_SAVING_ALERT",
            "SECONDARY_WATCHLIST_JSON",
        ):
            env.pop(key, None)
        script = """
import json
import sys
sys.path.insert(0, 'scripts')
from run_tracker import load_profiles
profiles = load_profiles()
print(json.dumps([{'label': p.label, 'username': p.username} for p in profiles]))
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        loaded = json.loads(result.stdout.strip())
        self.assertEqual(loaded, [{"label": "primary", "username": "u"}])


if __name__ == "__main__":
    unittest.main()

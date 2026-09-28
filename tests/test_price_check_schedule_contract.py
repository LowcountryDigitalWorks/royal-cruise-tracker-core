from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "price-check.yml").read_text(encoding="utf-8")
DUE = (ROOT / "scripts" / "royal_check_due.py").read_text(encoding="utf-8")
PERSIST = (ROOT / "scripts" / "persist_schedule_health.py").read_text(encoding="utf-8")
WAKE = (ROOT / "cloudflare" / "scheduler-wake.js").read_text(encoding="utf-8")

class PriceCheckScheduleContractTests(unittest.TestCase):
    def test_github_has_no_automatic_schedule(self):
        self.assertNotIn("schedule:", WORKFLOW)
        self.assertNotIn("cron:", WORKFLOW)
        self.assertIn("workflow_dispatch:", WORKFLOW)

    def test_workflow_uses_protected_runtime_configuration(self):
        self.assertIn("ROYAL_RUNTIME_MODE: production", WORKFLOW)
        self.assertIn("Require protected production runtime profile", WORKFLOW)
        self.assertIn("ROYAL_PROFILE_JSON:", WORKFLOW)
        self.assertIn("secrets.CLOUDFLARE_ACCOUNT_ID", WORKFLOW)
        self.assertIn("secrets.CLOUDFLARE_D1_DATABASE_ID", WORKFLOW)
        self.assertIn("PRIMARY_RCCL_USERNAME", WORKFLOW)
        self.assertIn("SECONDARY_RCCL_USERNAME", WORKFLOW)

    def test_sparse_checkout_contains_public_core_dependencies(self):
        self.assertIn("cloudflare/scheduler-wake.js", WORKFLOW)
        self.assertIn("config/demo-profile.json", WORKFLOW)
        self.assertIn("scripts/runtime_profile.py", WORKFLOW)
        self.assertIn("scripts/royal_check_due.py", WORKFLOW)

    def test_due_gate_runs_before_docker_or_royal(self):
        preflight = WORKFLOW.index("python3 scripts/runtime_profile.py")
        gate = WORKFLOW.index("python3 scripts/royal_check_due.py")
        docker = WORKFLOW.index('docker pull "$UPSTREAM_IMAGE"')
        royal = WORKFLOW.index("python3 scripts/public_runner.py -- python3 scripts/run_tracker_adaptive_v4.py")
        self.assertLess(preflight, gate)
        self.assertLess(gate, docker)
        self.assertLess(gate, royal)

    def test_public_workflow_suppresses_provider_child_output(self):
        self.assertIn("scripts/public_runner.py", WORKFLOW)
        self.assertNotIn('2>&1 | tee "$tracker_log"', WORKFLOW)
        self.assertIn("PUBLIC_ACTIONS_LOGS: 'true'", WORKFLOW)
        self.assertNotIn("Selected: ", WORKFLOW)
        self.assertNotIn("Comparable cabin fare observed", WORKFLOW)

    def test_schedule_is_loaded_from_runtime_profile(self):
        self.assertIn("load_runtime_config", DUE)
        self.assertIn('schedule", {}', DUE)
        self.assertIn("_SCHEDULE", DUE)

    def test_scheduler_target_is_runtime_configured(self):
        self.assertIn("GITHUB_ACTIONS_DISPATCH_URL", WAKE)
        self.assertIn("ROYAL_WAKE_CRON", WAKE)
        self.assertIn("expectedCron", WAKE)

    def test_scheduler_telemetry_receives_required_runtime_profile(self):
        start = WORKFLOW.index("- name: Record Cloudflare scheduler dispatch timing")
        end = WORKFLOW.index("- name: Record skipped scheduler wake")
        block = WORKFLOW[start:end]
        self.assertIn("ROYAL_RUNTIME_MODE: production", block)
        self.assertIn("ROYAL_PROFILE_JSON: ${{ secrets.ROYAL_PROFILE_JSON }}", block)
        self.assertIn("SECONDARY_ENABLED: ${{ vars.SECONDARY_ENABLED || 'false' }}", block)

    def test_scheduler_telemetry_stays_signed_and_fail_closed(self):
        self.assertIn("schedulerTargetProof", WAKE)
        self.assertIn("scheduler_target_proof", WAKE)
        self.assertIn("verified_exact_target", PERSIST)
        self.assertIn("hmac.compare_digest", PERSIST)
        self.assertNotIn("RCCL_", PERSIST)

if __name__ == "__main__":
    unittest.main()

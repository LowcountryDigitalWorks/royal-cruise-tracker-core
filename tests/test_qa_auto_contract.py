from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
HARNESS = (ROOT / "scripts" / "browser-smoke.mjs").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github" / "workflows" / "browser-smoke.yml").read_text(encoding="utf-8")


class QaAutoContractTests(unittest.TestCase):
    def test_harness_uses_node_builtins_and_no_browser_test_package(self):
        imports = re.findall(r'from\s+["\']([^"\']+)["\']', HARNESS)
        self.assertTrue(imports)
        self.assertTrue(all(value.startswith("node:") for value in imports), imports)
        for forbidden in ("playwright", "puppeteer", "selenium", "webdriverio", "cypress"):
            self.assertNotIn(forbidden, HARNESS.lower())
            self.assertNotIn(forbidden, WORKFLOW.lower())
        self.assertNotIn("npm install", WORKFLOW.lower())
        self.assertNotIn("npx ", WORKFLOW.lower())

    def test_workflow_is_secretless_read_only_and_not_required_by_ruleset_code(self):
        self.assertIn("contents: read", WORKFLOW)
        self.assertIn("persist-credentials: false", WORKFLOW)
        self.assertIn("pull_request:", WORKFLOW)
        self.assertNotIn("pull_request_target", WORKFLOW)
        self.assertNotIn("secrets.", WORKFLOW)
        self.assertNotIn("upload-artifact", WORKFLOW)
        self.assertNotIn("repository_dispatch", WORKFLOW)
        self.assertNotIn("schedule:", WORKFLOW)

    def test_workflow_uses_existing_ubuntu_chrome_and_direct_node_entrypoint(self):
        self.assertIn("runs-on: ubuntu-24.04", WORKFLOW)
        self.assertIn("google-chrome --version", WORKFLOW)
        self.assertIn("node --check scripts/browser-smoke.mjs", WORKFLOW)
        self.assertIn("node scripts/browser-smoke.mjs", WORKFLOW)

    def test_harness_binds_localhost_uses_ephemeral_ports_and_temporary_profile(self):
        self.assertIn('server.listen(0, "127.0.0.1"', HARNESS)
        self.assertIn('"--remote-debugging-address=127.0.0.1"', HARNESS)
        self.assertIn('"--remote-debugging-port=0"', HARNESS)
        self.assertIn("DevToolsActivePort", HARNESS)
        self.assertIn("mkdtemp", HARNESS)
        self.assertIn("--user-data-dir=", HARNESS)
        self.assertIn("rm(profileDir", HARNESS)
        self.assertNotIn("--no-sandbox", HARNESS)

    def test_static_server_is_allowlisted_to_prototype_and_src_only(self):
        self.assertIn('pathname.startsWith("/prototype/")', HARNESS)
        self.assertIn('pathname.startsWith("/src/")', HARNESS)
        self.assertIn("safeRepositoryPath", HARNESS)
        self.assertIn("Content-Security-Policy", HARNESS)
        self.assertNotIn("createReadStream(ROOT", HARNESS)

    def test_required_viewports_states_and_rendered_assertions_are_encoded(self):
        for width in (320, 375, 768, 1440):
            self.assertRegex(HARNESS, rf"\b{width}\b")
        for scenario in (
            "case-01-zero",
            "case-03-multi",
            "case-05-drop",
            "case-10-target-rearm",
            "case-11-unavailable",
            "case-11-not-open",
            "case-12-failure-gap",
            "case-13-stale",
            "case-14-failed",
            "case-15-incomplete",
            "case-16-noncomparable",
            "case-20-below-range",
        ):
            self.assertIn(scenario, HARNESS)
        self.assertIn("scrollWidth <= snapshot.clientWidth", HARNESS)
        self.assertIn("cardScrollWidth <= snapshot.cardClientWidth", HARNESS)
        self.assertIn("tableScrollWidth > snapshot.tableClientWidth", HARNESS)
        self.assertIn("runLongContentStress", HARNESS)

    def test_keyboard_accessibility_scale_and_network_guards_are_encoded(self):
        self.assertIn('"Input.dispatchKeyEvent"', HARNESS)
        self.assertIn("scenario-select", HARNESS)
        self.assertIn("comparison-details", HARNESS)
        self.assertIn('"Accessibility.getFullAXTree"', HARNESS)
        self.assertIn('role(node) === "main"', HARNESS)
        self.assertIn('role(node) === "combobox"', HARNESS)
        self.assertIn('role(node) === "table"', HARNESS)
        self.assertIn('"Emulation.setPageScaleFactor"', HARNESS)
        self.assertIn("not a human browser-zoom conformance claim", HARNESS)
        self.assertIn('"Network.requestWillBeSent"', HARNESS)
        self.assertIn("externalRequests.length === 0", HARNESS)

    def test_browser_launch_and_runtime_failures_are_hard_failures(self):
        self.assertIn("Chrome executable not found", HARNESS)
        self.assertIn("Chrome exited before DevTools became available", HARNESS)
        self.assertIn("Runtime.exceptionThrown", HARNESS)
        self.assertIn("process.exitCode = 1", HARNESS)
        self.assertNotIn("browser smoke: skipped", HARNESS.lower())

    def test_no_screenshot_or_retained_artifact_path_is_present(self):
        self.assertNotIn("Page.captureScreenshot", HARNESS)
        self.assertNotIn("screenshot", WORKFLOW.lower())
        self.assertNotIn("artifact", WORKFLOW.lower())


if __name__ == "__main__":
    unittest.main()

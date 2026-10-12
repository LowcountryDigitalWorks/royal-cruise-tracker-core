import subprocess
import sys
import unittest

class BrowserSmokeContractTests(unittest.TestCase):
    def test_browser_smoke_script_syntax(self):
        """Verify scripts/browser-smoke.mjs passes node syntax check."""
        res = subprocess.run(
            [sys.executable, "-c", "import shutil; exit(0 if shutil.which('node') else 1)"]
        )
        if res.returncode != 0:
            self.skipTest("node binary not available")

        proc = subprocess.run(
            ["node", "--check", "scripts/browser-smoke.mjs"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"scripts/browser-smoke.mjs failed syntax check: {proc.stderr}",
        )

    def test_browser_smoke_execution(self):
        """Verify scripts/browser-smoke.mjs executes successfully in Chrome environment."""
        res = subprocess.run(
            [sys.executable, "-c", "import shutil; exit(0 if (shutil.which('google-chrome') or shutil.which('chromium')) else 1)"]
        )
        if res.returncode != 0:
            self.skipTest("google-chrome or chromium binary not available")

        proc = subprocess.run(
            ["node", "scripts/browser-smoke.mjs"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"scripts/browser-smoke.mjs failed execution:\nStdout:\n{proc.stdout}\nStderr:\n{proc.stderr}",
        )
        self.assertIn("All CDP browser smoke harness assertions PASSED", proc.stdout)

if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_public_boundary import scan_text


class PublicBoundaryTests(unittest.TestCase):
    def test_example_email_is_allowed(self):
        self.assertEqual(scan_text("sample.txt", "ops@example.com"), [])

    def test_operational_domain_is_rejected(self):
        value = "service@" + "lowcountrydigitalworks" + ".com"
        self.assertTrue(any("email" in item or "hostname" in item for item in scan_text("sample.txt", value)))

    def test_uuid_like_identifier_is_rejected(self):
        value = "12345678" + "-1234-1234-1234-" + "123456789abc"
        self.assertTrue(any("UUID" in item for item in scan_text("sample.txt", value)))

    def test_internal_device_label_is_rejected(self):
        value = "LDW" + "01"
        self.assertTrue(any("device" in item for item in scan_text("sample.txt", value)))

    def test_private_repo_reference_is_rejected(self):
        value = "LowcountryDigitalWorks/" + "royal-cruise-tracker"
        self.assertTrue(any("private production repository" in item for item in scan_text("sample.txt", value)))


if __name__ == "__main__":
    unittest.main()

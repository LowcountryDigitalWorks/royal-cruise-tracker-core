#!/usr/bin/env python3
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from validate_public_boundary import decode_tracked_text, scan_path, scan_text


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

    def test_html_private_literal_is_rejected(self):
        value = "<p>" + "LDW" + "01" + "</p>"
        self.assertTrue(any("device" in item for item in scan_text("index.html", value)))

    def test_css_private_literal_is_rejected(self):
        value = ".contact::after { content: '" + "843" + "-555-1212'; }"
        self.assertTrue(any("phone" in item for item in scan_text("site.css", value)))

    def test_svg_private_literal_is_rejected(self):
        value = "<svg><text>" + "LDW" + "01" + "</text></svg>"
        self.assertTrue(any("device" in item for item in scan_text("icon.svg", value)))

    def test_module_private_literal_is_rejected(self):
        value = "export const host = '" + "portal.lowcountrydigitalworks" + ".com';"
        self.assertTrue(any("hostname" in item for item in scan_text("app.mjs", value)))

    def test_private_literal_in_path_is_rejected(self):
        path = "fixtures/" + "LDW" + "01" + "/synthetic.html"
        self.assertTrue(any("device" in item for item in scan_path(path)))

    def test_text_detection_is_extension_agnostic(self):
        self.assertEqual(decode_tracked_text(b"<html>synthetic</html>"), "<html>synthetic</html>")
        self.assertEqual(decode_tracked_text(b"plain text without an extension"), "plain text without an extension")

    def test_binary_bytes_are_not_treated_as_safe_text(self):
        self.assertIsNone(decode_tracked_text(b"\x89PNG\x00\x01"))
        self.assertIsNone(decode_tracked_text(b"\xff\xfe\xfd"))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
import os
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from runtime_profile import load_runtime_config

class RuntimeProfileTests(unittest.TestCase):
    def test_committed_fallback_is_synthetic(self):
        config = load_runtime_config()
        self.assertTrue(config["sailing"]["id"].startswith("DEMO-"))
        self.assertTrue(config["sailing"]["ship_name"].startswith("Example"))
        self.assertEqual(config["profiles"][0]["label"], "primary")

    def test_inline_profile_overrides_demo(self):
        old = os.environ.get("ROYAL_PROFILE_JSON")
        try:
            os.environ["ROYAL_PROFILE_JSON"] = (
                '{"sailing":{"id":"TEST-1","ship_name":"Test Ship","ship_code":"TS",'
                '"sail_date":"2031-01-01"},"watch_specs":[{"name":"Test Product",'
                '"prefix":"test","product":"TEST1","age_field":"adult","audience":[]}],'
                '"profiles":[{"label":"primary","username_env":"PRIMARY_RCCL_USERNAME",'
                '"password_env":"PRIMARY_RCCL_PASSWORD","traveler_roles":{}}]}'
            )
            self.assertEqual(load_runtime_config()["sailing"]["id"], "TEST-1")
        finally:
            if old is None:
                os.environ.pop("ROYAL_PROFILE_JSON", None)
            else:
                os.environ["ROYAL_PROFILE_JSON"] = old

if __name__ == "__main__":
    unittest.main()

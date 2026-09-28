#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from runtime_profile import DEFAULT_DEMO, load_runtime_config


def profile_json(profile_count=1):
    profiles = []
    for index in range(profile_count):
        profiles.append({
            "label": f"profile-{index + 1}",
            "username_env": "PRIMARY_RCCL_USERNAME",
            "password_env": "PRIMARY_RCCL_PASSWORD",
            "traveler_roles": {},
            "enabled": True,
        })
    return json.dumps({
        "sailing": {"id": "TEST-1", "ship_name": "Test Ship", "ship_code": "TS", "sail_date": "2031-01-01"},
        "watch_specs": [{"name": "Test Product", "prefix": "test", "product": "TEST1", "age_field": "adult", "audience": []}],
        "profiles": profiles,
    })


class RuntimeProfileTests(unittest.TestCase):
    def setUp(self):
        self.old = {key: os.environ.get(key) for key in ("ROYAL_RUNTIME_MODE", "ROYAL_PROFILE_JSON", "ROYAL_PROFILE_PATH", "SECONDARY_ENABLED")}
        for key in self.old:
            os.environ.pop(key, None)

    def tearDown(self):
        for key, value in self.old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_runtime_mode_is_required(self):
        with self.assertRaises(RuntimeError):
            load_runtime_config()

    def test_demo_mode_uses_committed_synthetic_profile(self):
        config = load_runtime_config("demo")
        self.assertTrue(config["sailing"]["id"].startswith("DEMO-"))
        self.assertTrue(config["sailing"]["ship_name"].startswith("Example"))

    def test_demo_mode_allows_inline_test_profile(self):
        os.environ["ROYAL_PROFILE_JSON"] = profile_json()
        self.assertEqual(load_runtime_config("demo")["sailing"]["id"], "TEST-1")

    def test_production_requires_explicit_protected_profile(self):
        with self.assertRaises(RuntimeError):
            load_runtime_config("production")

    def test_production_rejects_committed_demo_path(self):
        os.environ["ROYAL_PROFILE_PATH"] = str(DEFAULT_DEMO)
        with self.assertRaises(RuntimeError):
            load_runtime_config("production")

    def test_production_accepts_explicit_inline_profile(self):
        os.environ["ROYAL_PROFILE_JSON"] = profile_json()
        self.assertEqual(load_runtime_config("production")["sailing"]["id"], "TEST-1")

    def test_foundation_production_rejects_two_enabled_profiles(self):
        os.environ["ROYAL_PROFILE_JSON"] = profile_json(profile_count=2)
        with self.assertRaises(ValueError):
            load_runtime_config("production")


if __name__ == "__main__":
    unittest.main()

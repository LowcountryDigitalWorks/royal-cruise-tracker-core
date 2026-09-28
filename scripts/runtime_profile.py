#!/usr/bin/env python3
"""Load private Royal runtime configuration without committing household data."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_ENV = "ROYAL_PROFILE_JSON"
CONFIG_PATH_ENV = "ROYAL_PROFILE_PATH"
DEFAULT_DEMO = Path(__file__).resolve().parents[1] / "config" / "demo-profile.json"


def _read_config_text() -> str:
    inline = os.environ.get(CONFIG_ENV, "").strip()
    if inline:
        return inline

    configured = os.environ.get(CONFIG_PATH_ENV, "").strip()
    path = Path(configured) if configured else DEFAULT_DEMO
    return path.read_text(encoding="utf-8")


def _need_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _need_string(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must be a non-empty string")
    return text


def load_runtime_config() -> dict[str, Any]:
    """Return validated provider-neutral runtime configuration.

    Production should pass ROYAL_PROFILE_JSON as a protected secret/runtime value.
    The committed demo profile exists only for deterministic public CI and examples.
    """
    data = json.loads(_read_config_text())
    root = _need_object(data, "root")
    sailing = _need_object(root.get("sailing"), "sailing")
    profiles = root.get("profiles")
    watch_specs = root.get("watch_specs")

    _need_string(sailing.get("id"), "sailing.id")
    _need_string(sailing.get("ship_name"), "sailing.ship_name")
    _need_string(sailing.get("ship_code"), "sailing.ship_code")
    _need_string(sailing.get("sail_date"), "sailing.sail_date")

    if not isinstance(profiles, list) or not profiles:
        raise ValueError("profiles must be a non-empty array")
    if not isinstance(watch_specs, list) or not watch_specs:
        raise ValueError("watch_specs must be a non-empty array")

    for index, profile in enumerate(profiles):
        item = _need_object(profile, f"profiles[{index}]")
        _need_string(item.get("label"), f"profiles[{index}].label")
        _need_string(item.get("username_env"), f"profiles[{index}].username_env")
        _need_string(item.get("password_env"), f"profiles[{index}].password_env")
        traveler_roles = item.get("traveler_roles", {})
        if not isinstance(traveler_roles, dict):
            raise ValueError(f"profiles[{index}].traveler_roles must be an object")

    for index, spec in enumerate(watch_specs):
        item = _need_object(spec, f"watch_specs[{index}]")
        for key in ("name", "prefix", "product", "age_field"):
            _need_string(item.get(key), f"watch_specs[{index}].{key}")
        if not isinstance(item.get("audience", []), list):
            raise ValueError(f"watch_specs[{index}].audience must be an array")

    return root

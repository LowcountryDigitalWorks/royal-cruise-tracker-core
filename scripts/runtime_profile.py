#!/usr/bin/env python3
"""Load Royal runtime configuration without committing deployment data."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CONFIG_ENV = "ROYAL_PROFILE_JSON"
CONFIG_PATH_ENV = "ROYAL_PROFILE_PATH"
RUNTIME_MODE_ENV = "ROYAL_RUNTIME_MODE"
DEFAULT_DEMO = Path(__file__).resolve().parents[1] / "config" / "demo-profile.json"
VALID_RUNTIME_MODES = {"production", "demo"}


def _runtime_mode(explicit: str | None = None) -> str:
    mode = str(explicit or os.environ.get(RUNTIME_MODE_ENV, "")).strip().lower()
    if mode not in VALID_RUNTIME_MODES:
        raise RuntimeError("ROYAL_RUNTIME_MODE must be explicitly set to 'production' or 'demo'")
    return mode


def _read_config_text(mode: str) -> str:
    inline = os.environ.get(CONFIG_ENV, "").strip()
    configured = os.environ.get(CONFIG_PATH_ENV, "").strip()
    if mode == "production":
        if inline:
            return inline
        if configured:
            path = Path(configured)
            if path.resolve() == DEFAULT_DEMO.resolve():
                raise RuntimeError("production mode may not use the committed demo profile")
            return path.read_text(encoding="utf-8")
        raise RuntimeError("production mode requires ROYAL_PROFILE_JSON or an explicit non-demo ROYAL_PROFILE_PATH")
    if inline:
        return inline
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


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _profile_enabled(item: dict[str, Any]) -> bool:
    enabled_env = str(item.get("enabled_env") or "").strip()
    if enabled_env:
        return _truthy(os.environ.get(enabled_env))
    return bool(item.get("enabled", True))


def load_runtime_config(mode: str | None = None) -> dict[str, Any]:
    runtime_mode = _runtime_mode(mode)
    data = json.loads(_read_config_text(runtime_mode))
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

    enabled_profiles = 0
    for index, profile in enumerate(profiles):
        item = _need_object(profile, f"profiles[{index}]")
        _need_string(item.get("label"), f"profiles[{index}].label")
        _need_string(item.get("username_env"), f"profiles[{index}].username_env")
        _need_string(item.get("password_env"), f"profiles[{index}].password_env")
        traveler_roles = item.get("traveler_roles", {})
        if not isinstance(traveler_roles, dict):
            raise ValueError(f"profiles[{index}].traveler_roles must be an object")
        if _profile_enabled(item):
            enabled_profiles += 1

    if runtime_mode == "production" and enabled_profiles != 1:
        raise ValueError("FOUNDATION-001 production mode requires exactly one enabled profile")

    for index, spec in enumerate(watch_specs):
        item = _need_object(spec, f"watch_specs[{index}]")
        for key in ("name", "prefix", "product", "age_field"):
            _need_string(item.get(key), f"watch_specs[{index}].{key}")
        if not isinstance(item.get("audience", []), list):
            raise ValueError(f"watch_specs[{index}].audience must be an array")

    return root


def main() -> int:
    mode = _runtime_mode()
    load_runtime_config(mode)
    print(f"Royal runtime profile: {mode} configuration valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

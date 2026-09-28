#!/usr/bin/env python3
"""Validate that committed public-core fixtures/config remain synthetic."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
TEXT_SUFFIXES = {".md", ".py", ".js", ".mjs", ".json", ".yml", ".yaml", ".toml", ".sql", ".txt"}

EMAIL = re.compile(r"\b[A-Z0-9._%+.-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b", re.I)
PHONE = re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)")
PROFILE_ENV = re.compile(r"\b([A-Z][A-Z0-9]*)_RCCL_(USERNAME|PASSWORD|RESERVATION_ID|PAID_PRICE)\b")
ALLOWED_EMAIL_DOMAINS = {"example.com", "lowcountrydigitalworks.com"}
ALLOWED_PROFILE_PREFIXES = {"PRIMARY", "SECONDARY"}


def text_files():
    for file in ROOT.rglob("*"):
        if not file.is_file() or file.resolve() == SELF or ".git" in file.parts:
            continue
        if file.suffix.lower() in TEXT_SUFFIXES:
            yield file


def main() -> int:
    errors: list[str] = []
    demo = json.loads((ROOT / "config" / "demo-profile.json").read_text(encoding="utf-8-sig"))

    sailing = demo.get("sailing", {})
    if not str(sailing.get("id", "")).startswith("DEMO-"):
        errors.append("demo sailing id is not synthetic")
    if not str(sailing.get("ship_name", "")).startswith("Example"):
        errors.append("demo ship name is not synthetic")

    for profile in demo.get("profiles", []):
        if not str(profile.get("display_name", "")).startswith("Demo"):
            errors.append("demo profile display name is not synthetic")
        if not str(profile.get("cabin", "")).startswith("DEMO"):
            errors.append("demo cabin is not synthetic")
        if any(not str(name).startswith("Demo") for name in profile.get("traveler_roles", {})):
            errors.append("demo traveler identity is not synthetic")

    for spec in demo.get("watch_specs", []):
        if not str(spec.get("product", "")).startswith("DEMO"):
            errors.append("demo product code is not synthetic")

    for file in text_files():
        text = file.read_text(encoding="utf-8-sig", errors="replace")
        rel = file.relative_to(ROOT).as_posix()

        for match in EMAIL.finditer(text):
            if match.group(1).lower() not in ALLOWED_EMAIL_DOMAINS:
                errors.append(f"{rel}: non-allowlisted email literal")

        if PHONE.search(text):
            errors.append(f"{rel}: phone-like literal")

        if "account_id =" in text or "database_id =" in text:
            errors.append(f"{rel}: committed infrastructure identifier assignment")

        for match in PROFILE_ENV.finditer(text):
            if match.group(1) not in ALLOWED_PROFILE_PREFIXES:
                errors.append(f"{rel}: non-generic Royal environment prefix")

    if errors:
        for error in sorted(set(errors)):
            print(f"ERROR: {error}")
        return 1

    print("Public-boundary validation: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

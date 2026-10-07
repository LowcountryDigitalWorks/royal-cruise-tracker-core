#!/usr/bin/env python3
"""Validate that tracked public-core source remains synthetic/public-safe."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATHS = {"scripts/validate_public_boundary.py", "scripts/validate_public_history.py"}

EMAIL = re.compile(r"\b[A-Z0-9._%+.-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b", re.I)
PHONE = re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)")
UUID_LIKE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
HEX32 = re.compile(r"(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", re.I)
INTERNAL_DEVICE = re.compile(r"\bLDW0[0-9]\b", re.I)
LDW_HOSTNAME = re.compile(r"\b(?:[A-Z0-9-]+\.)*lowcountrydigitalworks\.com\b", re.I)
PRIVATE_REPO = re.compile(r"\bLowcountryDigitalWorks/royal-cruise-tracker(?!-core)(?:\b|/)", re.I)
CRON_LITERAL = re.compile(r"['\"](?:[0-9*/,-]+\s+){4}[0-9*/,-]+['\"]")
PROFILE_ENV = re.compile(r"\b([A-Z][A-Z0-9]*)_RCCL_(USERNAME|PASSWORD|RESERVATION_ID|PAID_PRICE)\b")
ALLOWED_EMAIL_DOMAINS = {"example.com"}
ALLOWED_PROFILE_PREFIXES = {"PRIMARY", "SECONDARY"}


def git_tracked_paths() -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        raise RuntimeError("git tracked-file inspection failed")
    return [item.decode("utf-8", errors="surrogateescape") for item in proc.stdout.split(b"\0") if item]


def decode_tracked_text(data: bytes) -> str | None:
    """Return UTF-8 text for scanable tracked content; binary/opaque bytes return None."""
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        return None


def scan_text(rel: str, text: str) -> list[str]:
    errors: list[str] = []
    for match in EMAIL.finditer(text):
        if match.group(1).lower() not in ALLOWED_EMAIL_DOMAINS:
            errors.append(f"{rel}: non-allowlisted email literal")
    if PHONE.search(text):
        errors.append(f"{rel}: phone-like literal")
    if UUID_LIKE.search(text):
        errors.append(f"{rel}: UUID-like deployment/device identifier")
    if HEX32.search(text):
        errors.append(f"{rel}: 32-hex infrastructure-like identifier")
    if INTERNAL_DEVICE.search(text):
        errors.append(f"{rel}: internal device label")
    if LDW_HOSTNAME.search(text):
        errors.append(f"{rel}: LDW operational hostname/domain literal")
    if PRIVATE_REPO.search(text):
        errors.append(f"{rel}: private production repository reference")
    if CRON_LITERAL.search(text):
        errors.append(f"{rel}: committed cron-like literal")
    if "account_id =" in text or "database_id =" in text:
        errors.append(f"{rel}: committed infrastructure identifier assignment")
    for match in PROFILE_ENV.finditer(text):
        if match.group(1) not in ALLOWED_PROFILE_PREFIXES:
            errors.append(f"{rel}: non-generic Royal environment prefix")
    return errors


def scan_path(rel: str) -> list[str]:
    return scan_text(f"path:{rel}", rel)


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

    for rel in git_tracked_paths():
        errors.extend(scan_path(rel))
        if rel in VALIDATOR_PATHS:
            continue
        file = ROOT / rel
        if not file.is_file():
            continue
        text = decode_tracked_text(file.read_bytes())
        if text is not None:
            errors.extend(scan_text(rel, text))

    if errors:
        for error in sorted(set(errors)):
            print(f"ERROR: {error}")
        return 1
    print("Public-boundary validation: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

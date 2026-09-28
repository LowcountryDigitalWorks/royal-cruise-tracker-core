#!/usr/bin/env python3
"""Persist sanitized Cloudflare Cron -> GitHub dispatch timing to D1.

This helper makes no Royal request and is fail-open: scheduler telemetry can never
block the Royal monitor. Persistence is explicit and applies only to the automatic
Cloudflare scheduler wake. An exact target is trusted only when its HMAC proves it
came from the Worker that holds the existing GitHub dispatch secret.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime

import run_tracker as base
from schedule_health import AUTOMATIC_EVENT, DISPLAY_TIMEZONE, assess

EXACT_TARGET_SOURCE = "cloudflare_scheduled_time"
FALLBACK_TARGET_SOURCE = "due_gate_fallback"
TARGET_PROOF_DOMAIN = b"royal-cruise-scheduler-v1\0"
EXACT_PROOF_NOTE = "proof=hmac-sha256-v1"


def enabled() -> bool:
    return base.truthy(os.environ.get("SCHEDULE_HEALTH_PERSIST"))


def automatic_wake() -> bool:
    return base.truthy(os.environ.get("ROYAL_SCHEDULER_WAKE"))


def target_proof(target_at: str, key: str) -> str:
    target = str(target_at or "").strip()
    secret = str(key or "").strip()
    if not target or not secret:
        return ""
    return hmac.new(secret.encode("utf-8"), TARGET_PROOF_DOMAIN + target.encode("utf-8"), hashlib.sha256).hexdigest()


def verified_exact_target(target_at: str, proof: str, key: str) -> bool:
    expected = target_proof(target_at, key)
    supplied = str(proof or "").strip().lower()
    return bool(expected and len(supplied) == 64 and hmac.compare_digest(expected, supplied))


def normalize_health(value: str) -> str:
    return value.strip().lower().replace(" ", "_")


def idempotency_key(event_name: str, expected_at: str | None) -> str:
    # One immutable timing observation per Cloudflare target. Duplicate workflow
    # dispatches for the same target do not create additional rows.
    material = f"{event_name}\0{expected_at or ''}"
    return "scheduler:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def queries_for_result(result, target_source: str = FALLBACK_TARGET_SOURCE) -> list[dict]:
    if result.event_name != AUTOMATIC_EVENT or result.expected_at is None:
        return []
    if target_source not in {EXACT_TARGET_SOURCE, FALLBACK_TARGET_SOURCE}:
        raise ValueError("unexpected scheduler target source")
    observed_at = result.observed_at.isoformat()
    expected_at = result.expected_at.isoformat()
    health = normalize_health(result.health)
    provenance = f"target_source={target_source}; "
    if target_source == EXACT_TARGET_SOURCE:
        # Persist a versioned proof marker only after the HMAC has already been
        # verified in main(). This lets future readers distinguish authenticated
        # exact evidence from older rows that used the same target-source label.
        provenance += f"{EXACT_PROOF_NOTE}; "
    note = provenance + "sanitized Cloudflare Cron to GitHub workflow timing; measured before Royal traffic"
    return [
        {
            "sql": """
            CREATE TABLE IF NOT EXISTS scheduler_observations (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              event_name TEXT NOT NULL,
              expected_at TEXT,
              observed_at TEXT NOT NULL,
              delay_minutes REAL CHECK (delay_minutes IS NULL OR delay_minutes >= 0),
              health TEXT NOT NULL CHECK (health IN ('on_time','delayed','severely_delayed','very_late','unscheduled')),
              source TEXT NOT NULL DEFAULT 'github_actions' CHECK (source='github_actions'),
              idempotency_key TEXT NOT NULL UNIQUE,
              notes TEXT
            )
            """,
            "params": [],
        },
        {
            "sql": "CREATE INDEX IF NOT EXISTS idx_scheduler_observations_time ON scheduler_observations(observed_at DESC,id DESC)",
            "params": [],
        },
        {
            "sql": """
            CREATE TRIGGER IF NOT EXISTS scheduler_observations_no_update
            BEFORE UPDATE ON scheduler_observations
            BEGIN
              SELECT RAISE(ABORT, 'scheduler_observations is append-only');
            END
            """,
            "params": [],
        },
        {
            "sql": """
            CREATE TRIGGER IF NOT EXISTS scheduler_observations_no_delete
            BEFORE DELETE ON scheduler_observations
            BEGIN
              SELECT RAISE(ABORT, 'scheduler_observations is append-only');
            END
            """,
            "params": [],
        },
        {
            "sql": "INSERT OR IGNORE INTO scheduler_observations(event_name,expected_at,observed_at,delay_minutes,health,source,idempotency_key,notes) VALUES(?,?,?,?,?,'github_actions',?,?)",
            "params": [
                result.event_name,
                expected_at,
                observed_at,
                result.delay_minutes,
                health,
                idempotency_key(result.event_name, expected_at),
                note,
            ],
        },
    ]


def main() -> int:
    if not enabled():
        print("Scheduler health D1 persistence: disabled")
        return 0
    if not automatic_wake():
        print("Scheduler health D1 persistence: skipped non-automatic dispatch")
        return 0

    exact_candidate = os.environ.get("ROYAL_EXACT_TARGET_AT", "").strip()
    proof = os.environ.get("ROYAL_TARGET_PROOF", "")
    proof_key = os.environ.get("ROYAL_TARGET_PROOF_KEY", "")
    fallback_target = os.environ.get("ROYAL_FALLBACK_TARGET_AT", "").strip()
    exact = verified_exact_target(exact_candidate, proof, proof_key)
    if exact:
        selected_target = exact_candidate
        target_source = EXACT_TARGET_SOURCE
    else:
        selected_target = fallback_target
        target_source = FALLBACK_TARGET_SOURCE
        if exact_candidate:
            print("::warning::Scheduler exact-target proof unavailable or invalid; using due-gate fallback telemetry")

    event_name = os.environ.get("GITHUB_EVENT_NAME", "unknown")
    run_attempt = int(os.environ.get("GITHUB_RUN_ATTEMPT", "1") or "1")
    result = assess(
        event_name,
        datetime.now(DISPLAY_TIMEZONE),
        run_attempt,
        scheduler_wake=True,
        target_at=selected_target,
    )
    queries = queries_for_result(result, target_source)
    if not queries:
        print("::warning::Scheduler health persistence skipped: automatic target unavailable or rerun")
        return 0
    try:
        base.d1_request(queries)
    except Exception:
        # Scheduler telemetry must never block the Royal monitor. Do not echo the
        # underlying D1 exception: the shared client can include a remote response
        # body containing query context, which does not belong in Actions logs.
        print("::warning::Scheduler health persistence skipped: D1 telemetry write failed; remote error details suppressed")
        return 0
    print(
        f"Scheduler health D1 persistence: PASS ({normalize_health(result.health)}; "
        f"delay={result.delay_minutes if result.delay_minutes is not None else 'n/a'}; "
        f"target_source={target_source})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

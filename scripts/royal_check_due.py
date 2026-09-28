#!/usr/bin/env python3
"""Gate automatic wakes to runtime-configured Royal check windows.

Automatic scheduling is external to this repository. This helper preserves the
runtime-configured Royal traffic windows without committing production timing:

* identify the most recent intended Royal slot;
* read the latest persisted authenticated Royal observation from D1;
* run Royal only when that slot has not yet been satisfied;
* manual workflow_dispatch runs bypass the gate;
* a D1-read failure fails closed before Docker/Royal traffic.

Cloudflare workflow_dispatch wakes set ROYAL_SCHEDULER_WAKE=true and remain gated. Human manual dispatches still bypass the gate.

The script never prints booking IDs, reservation IDs, credentials or raw Royal data.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runtime_profile import load_runtime_config

_RUNTIME = load_runtime_config()
_SCHEDULE = dict(_RUNTIME.get("schedule", {}))
TARGET_HOURS_UTC = tuple(int(value) for value in _SCHEDULE.get("hours_utc", [6, 18]))
TARGET_MINUTE = int(_SCHEDULE.get("minute", 5))


@dataclass(frozen=True)
class DueState:
    due: bool
    reason: str
    target_at: datetime | None
    latest_at: datetime | None
    next_target_at: datetime | None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc)


def target_slots_near(now: datetime) -> list[datetime]:
    utc = _aware_utc(now)
    slots: list[datetime] = []
    for offset in (-1, 0, 1):
        day = (utc + timedelta(days=offset)).date()
        for hour in TARGET_HOURS_UTC:
            slots.append(datetime(day.year, day.month, day.day, hour, TARGET_MINUTE, tzinfo=timezone.utc))
    return sorted(slots)


def most_recent_target(now: datetime) -> datetime:
    utc = _aware_utc(now)
    eligible = [slot for slot in target_slots_near(utc) if slot <= utc]
    if not eligible:
        raise RuntimeError("No target slot found")
    return max(eligible)


def next_target(now: datetime) -> datetime:
    utc = _aware_utc(now)
    future = [slot for slot in target_slots_near(utc) if slot > utc]
    if not future:
        raise RuntimeError("No future target slot found")
    return min(future)


def parse_timestamp(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def decide(now: datetime, latest_at: datetime | None, event_name: str = "schedule", scheduler_wake: bool = False) -> DueState:
    utc = _aware_utc(now)
    target = most_recent_target(utc)
    upcoming = next_target(utc)
    automated_wake = event_name == "schedule" or scheduler_wake
    if not automated_wake:
        return DueState(True, "manual_dispatch", target, latest_at, upcoming)
    if latest_at is None:
        return DueState(True, "no_persisted_royal_evidence", target, None, upcoming)
    latest = _aware_utc(latest_at)
    if latest >= target:
        return DueState(False, "target_window_already_satisfied", target, latest, upcoming)
    return DueState(True, "target_window_overdue", target, latest, upcoming)


def _d1_query(sql: str) -> list[dict]:
    token = os.environ.get("CLOUDFLARE_D1_TOKEN", "")
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    database = os.environ.get("CLOUDFLARE_D1_DATABASE_ID", "")
    if not token or not account or not database:
        raise RuntimeError("D1 gate credentials are not configured")
    endpoint = f"https://api.cloudflare.com/client/v4/accounts/{account}/d1/database/{database}/query"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps({"sql": sql}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"D1 gate HTTP {exc.code}") from exc
    if not payload.get("success"):
        raise RuntimeError("D1 gate query failed")
    result = payload.get("result") or []
    if any(not item.get("success", False) for item in result):
        raise RuntimeError("D1 gate statement failed")
    return [row for item in result for row in (item.get("results") or [])]


def latest_authenticated_observation() -> datetime | None:
    values: list[datetime] = []
    queries = (
        "SELECT MAX(observed_at) AS observed_at FROM price_snapshots WHERE source='authenticated_royal'",
        "SELECT MAX(observed_at) AS observed_at FROM product_availability WHERE source='authenticated_royal_watch'",
    )
    for sql in queries:
        try:
            rows = _d1_query(sql)
        except RuntimeError as exc:
            # product_availability is an additive table and may not exist in a fresh fixture;
            # price snapshots are the authoritative minimum gate. Missing optional table is
            # tolerated only when the price query has already produced evidence.
            if values and "product_availability" in sql:
                continue
            raise exc
        if rows:
            parsed = parse_timestamp(rows[0].get("observed_at"))
            if parsed:
                values.append(parsed)
    return max(values) if values else None


def _fmt(value: datetime | None) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if value else "none"


def _public_actions_logs() -> bool:
    return os.environ.get("PUBLIC_ACTIONS_LOGS", "").strip().lower() in {"1", "true", "yes", "on"}


def emit(state: DueState) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"due={'true' if state.due else 'false'}\n")
            handle.write(f"reason={state.reason}\n")
            handle.write(f"target_at={_fmt(state.target_at)}\n")
            handle.write(f"latest_at={_fmt(state.latest_at)}\n")
            handle.write(f"next_target_at={_fmt(state.next_target_at)}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write("## Royal target-window gate\n\n")
            handle.write(f"- Royal traffic due: **{'YES' if state.due else 'NO'}**\n")
            handle.write(f"- Reason: `{state.reason}`\n")
            if not _public_actions_logs():
                handle.write(f"- Most recent intended Royal slot: `{_fmt(state.target_at)}`\n")
                handle.write(f"- Latest persisted authenticated Royal evidence: `{_fmt(state.latest_at)}`\n")
                handle.write(f"- Next intended Royal slot: `{_fmt(state.next_target_at)}`\n")
            handle.write("- Automatic scheduler wake-ups do not increase Royal traffic; only an unsatisfied target slot may proceed.\n\n")


def main() -> int:
    event_name = os.environ.get("GITHUB_EVENT_NAME", "unknown")
    scheduler_wake = os.environ.get("ROYAL_SCHEDULER_WAKE", "").strip().lower() in {"1", "true", "yes", "on"}
    now = datetime.now(timezone.utc)
    try:
        automated_wake = event_name == "schedule" or scheduler_wake
        latest = latest_authenticated_observation() if automated_wake else None
        state = decide(now, latest, event_name, scheduler_wake=scheduler_wake)
    except Exception as exc:
        print(f"::error::Royal due gate failed closed before Royal traffic: {type(exc).__name__}")
        return 2
    emit(state)
    if _public_actions_logs():
        print(f"Royal due gate: {'DUE' if state.due else 'SKIP'}; reason={state.reason}")
    else:
        print(
            f"Royal due gate: {'DUE' if state.due else 'SKIP'}; reason={state.reason}; "
            f"target={_fmt(state.target_at)}; latest={_fmt(state.latest_at)}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

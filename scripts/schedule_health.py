#!/usr/bin/env python3
"""Measure Cloudflare Cron -> GitHub dispatch timing without making a Royal request.

Production automation may be clocked by an external scheduler using protected
runtime-configured target windows. The scheduler dispatches the GitHub Actions
workflow, where ``royal_check_due.py`` independently decides
whether Royal traffic is necessary. The persistence layer supplies this classifier
with the HMAC-authenticated Cloudflare ``ScheduledController.scheduledTime`` when
that proof verifies; otherwise it can supply the independent D1 due-gate target as
explicit fallback telemetry. This helper only measures the supplied target against
the observed GitHub workflow time and never decides Royal eligibility itself.

Human workflow_dispatch runs and reruns are not scheduler evidence.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from runtime_profile import load_runtime_config

DISPLAY_TIMEZONE = ZoneInfo(str(load_runtime_config().get("local_timezone", "UTC")))
AUTOMATIC_EVENT = "cloudflare_cron_dispatch"


@dataclass(frozen=True)
class ScheduleHealth:
    event_name: str
    observed_at: datetime
    expected_at: datetime | None
    delay_minutes: float | None
    health: str


def parse_target(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw or raw.lower() == "none":
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


def classify_delay(minutes: float) -> str:
    if minutes < 5:
        return "ON TIME"
    if minutes < 30:
        return "DELAYED"
    if minutes < 120:
        return "SEVERELY DELAYED"
    return "VERY LATE"


def assess(
    event_name: str,
    observed_at: datetime,
    run_attempt: int = 1,
    *,
    scheduler_wake: bool = False,
    target_at: str | datetime | None = None,
) -> ScheduleHealth:
    """Classify one GitHub workflow start against its supplied Cloudflare target.

    Only the explicit scheduler-wake input emitted by the Cloudflare Worker turns a
    workflow_dispatch run into scheduler evidence. Target provenance is established
    by the caller: ``persist_schedule_health.py`` prefers a verified signed
    Cloudflare scheduledTime and otherwise uses the D1 due-gate fallback. This
    module never authenticates, reconstructs or invents a second schedule.
    """
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    if int(run_attempt or 1) > 1:
        return ScheduleHealth(event_name, observed_at, None, None, "MANUAL RERUN")
    if not scheduler_wake:
        return ScheduleHealth(event_name, observed_at, None, None, "UNSCHEDULED")
    expected = parse_target(target_at)
    if expected is None:
        return ScheduleHealth(AUTOMATIC_EVENT, observed_at, None, None, "UNSCHEDULED")
    delay = max(0.0, (observed_at.astimezone(timezone.utc) - expected).total_seconds() / 60.0)
    return ScheduleHealth(AUTOMATIC_EVENT, observed_at, expected, delay, classify_delay(delay))


def fmt(dt: datetime | None) -> str:
    return dt.astimezone(DISPLAY_TIMEZONE).strftime("%Y-%m-%d %I:%M:%S %p %Z") if dt else "n/a"


def append_summary(result: ScheduleHealth) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = ["## Cloudflare automatic wake health", "", f"- Evidence type: `{result.event_name}`"]
    if result.expected_at is None:
        reason = "manual re-run; not scheduler evidence" if result.health == "MANUAL RERUN" else "manual/non-scheduler dispatch"
        lines.extend([
            f"- Observed GitHub job time: `{fmt(result.observed_at)}`",
            f"- Dispatch delay: `n/a` ({reason})",
            f"- Health: **{result.health}**",
        ])
    else:
        lines.extend([
            f"- Cloudflare target: `{fmt(result.expected_at)}`",
            f"- Observed GitHub job time: `{fmt(result.observed_at)}`",
            f"- Cloudflare-to-GitHub delay: `{result.delay_minutes:.1f} minutes`",
            f"- Health: **{result.health}**",
            "- This measures scheduler/dispatch timing only. Royal traffic remains independently gated by D1.",
        ])
    lines.append("")
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def append_outputs(result: ScheduleHealth) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as handle:
        handle.write(f"health={result.health.lower().replace(' ', '_')}\n")
        handle.write(f"observed_at={result.observed_at.isoformat()}\n")
        if result.expected_at is not None:
            handle.write(f"expected_at={result.expected_at.isoformat()}\n")
            handle.write(f"delay_minutes={result.delay_minutes:.1f}\n")


def main() -> None:
    event_name = os.environ.get("GITHUB_EVENT_NAME", "unknown")
    run_attempt = int(os.environ.get("GITHUB_RUN_ATTEMPT", "1") or "1")
    scheduler_wake = os.environ.get("ROYAL_SCHEDULER_WAKE", "").strip().lower() in {"1", "true", "yes", "on"}
    observed = datetime.now(DISPLAY_TIMEZONE)
    result = assess(
        event_name,
        observed,
        run_attempt,
        scheduler_wake=scheduler_wake,
        target_at=os.environ.get("ROYAL_TARGET_AT"),
    )
    append_summary(result)
    append_outputs(result)
    if result.expected_at is None:
        print(f"Automatic wake health: {result.health} ({event_name}; attempt {run_attempt})")
    else:
        print(
            f"Automatic wake health: {result.health}; target {fmt(result.expected_at)}, "
            f"observed {fmt(result.observed_at)}, delay {result.delay_minutes:.1f} minutes"
        )


if __name__ == "__main__":
    main()

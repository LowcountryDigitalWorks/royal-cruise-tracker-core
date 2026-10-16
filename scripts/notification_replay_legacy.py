#!/usr/bin/env python3
"""Legacy-delivery suppression for ALERT-REL-001 retained-evidence replay."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_EVENT_ALIASES = {
    "fare": {"fare", "new_low"},
    "watch_new_low": {"watch_new_low", "new_low"},
    "purchase_reprice": {"purchase_reprice", "meaningful_drop"},
    "availability_return": {"availability_return", "availability_restored"},
}


def _time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def suppress_already_delivered_legacy(
    candidates: list[dict[str, Any]],
    profile: Any,
    base: Any,
) -> list[dict[str, Any]]:
    """Drop replay candidates already represented by legacy delivery history.

    The new outbox is empty at first rollout. Without this migration guard, a recent
    pre-outbox transition that was successfully sent by the legacy runtime could be
    reconstructed and interrupted again. Legacy evidence is used only to suppress
    replay, never to create or authorize a notification.
    """
    if not candidates:
        return []
    result = base.d1_request([{
        "sql": """
        SELECT product_id,event_type,MAX(sent_at) AS latest_sent_at
          FROM notification_events
         WHERE household_id=? AND sailing_id=? AND product_id IS NOT NULL
         GROUP BY product_id,event_type
        """,
        "params": [profile.label, base.SAILING_ID],
    }])
    rows = (result[0].get("results") or []) if result else []
    latest = {
        (str(row.get("product_id") or ""), str(row.get("event_type") or "")): row.get("latest_sent_at")
        for row in rows
        if row.get("product_id") and row.get("event_type")
    }

    kept: list[dict[str, Any]] = []
    for candidate in candidates:
        alert = candidate.get("alert") or {}
        kind = str(alert.get("kind") or "")
        product_id = str(alert.get("product_id") or "")
        observed = _time(candidate.get("observed_at"))
        delivered = False
        for event_type in _EVENT_ALIASES.get(kind, {kind}):
            sent_raw = latest.get((product_id, event_type))
            if sent_raw is None:
                continue
            sent = _time(sent_raw)
            # A malformed matching legacy timestamp is conservative evidence that
            # delivery history cannot be safely disproven; suppress replay rather
            # than risk duplicate user interruption.
            if sent is None or observed is None or sent >= observed:
                delivered = True
                break
        if not delivered:
            kept.append(candidate)
    return kept

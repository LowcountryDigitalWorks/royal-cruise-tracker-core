#!/usr/bin/env python3
"""Adaptive v3 wrapper: approximately once-daily sailing promotion catalog capture.

Extends adaptive_v2 without changing its alert, purchase, or Royal booking behavior.
The first scheduled check after the last successful promotion-catalog capture is at
least 20 hours old enables Royal's sailing-specific promotion catalog. In normal
operation that is the 00:17 Eastern run; if Royal/GitHub misses that window, a later
scheduled run can recover without waiting another full day. A manual proof may force
capture explicitly.

The upstream checker already prints authoritative start/end dates for sailing promos;
this wrapper parses the sanitized output and keeps a small active/history catalog in
D1. Promotion catalog rows are context/urgency only. Product/traveler price snapshots
remain the authority for purchase/reprice decisions.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import run_tracker as base
import run_tracker_adaptive as adaptive
import run_tracker_adaptive_v2  # noqa: F401 - imports/installs the proven v2 monkeypatches

PROMO_LINE = re.compile(
    r"^\s*\[PROMO\]\s+(?P<label>.+?)\s+"
    r"\(Valid\s+(?P<start>\d{4}-\d{2}-\d{2})?\s+to\s+"
    r"(?P<end>\d{4}-\d{2}-\d{2})?\)\s*$",
    re.I,
)
PROMO_CAPTURE_INTERVAL_HOURS = 20.0

_original_build_config = adaptive.build_config
_original_parse_history = base.parse_history
_original_persist_history = base.persist_history
_capture_requested: dict[str, bool] = {}


def promotion_catalog_due(profile: base.Profile, state: adaptive.HistoryState) -> bool:
    """Return whether this run should refresh the Royal sailing promo catalog."""
    if profile.label != "primary":
        return False
    if base.truthy(base.env("PROMOTION_CATALOG_FORCE")):
        return True

    # If the normal adaptive-state read already failed, do not add optional Royal promo
    # calls except in the preferred midnight window. Normal price monitoring stays first.
    if not state.available:
        return datetime.now(ZoneInfo(base.LOCAL_TIMEZONE)).hour == 0

    try:
        result = base.d1_request([{
            "sql": "SELECT MAX(last_seen_at) AS last_seen_at FROM promotion_catalog WHERE sailing_id=? AND active=1",
            "params": [base.SAILING_ID],
        }])
        rows = (result[0].get("results") or []) if result else []
        last_seen = rows[0].get("last_seen_at") if rows else None
    except Exception:
        # The core D1 state read just succeeded. At rollout this normally means the
        # optional table does not exist yet, so treat it as a first-capture due state.
        return True

    if not last_seen:
        return True
    return adaptive.hours_since(last_seen) >= PROMO_CAPTURE_INTERVAL_HOURS


def build_config(
    profile: base.Profile,
    state: adaptive.HistoryState,
) -> tuple[dict[str, Any], list[dict[str, Any]], str, list[base.WatchSpec]]:
    cfg, watchlist, mode, selected = _original_build_config(profile, state)
    capture = promotion_catalog_due(profile, state)
    _capture_requested[profile.label] = capture
    cfg["showPromos"] = capture
    if capture:
        mode = f"{mode}; sailing promo catalog"
    return cfg, watchlist, mode, selected


def parse_promotion_catalog(lines: list[str]) -> list[dict[str, str | None]]:
    """Parse upstream sailing promo lines, deduplicating duplicate linked-booking output."""
    found: dict[tuple[str, str | None, str | None], dict[str, str | None]] = {}
    for line in lines:
        match = PROMO_LINE.search(line)
        if not match:
            continue
        label = " ".join(match.group("label").split()).strip()
        if not label:
            continue
        start = match.group("start") or None
        end = match.group("end") or None
        found[(label, start, end)] = {"label": label, "starts_on": start, "ends_on": end}
    return list(found.values())


def parse_history(profile: base.Profile, watchlist: list[dict[str, Any]], lines: list[str]) -> dict[str, Any]:
    snapshot = _original_parse_history(profile, watchlist, lines)
    requested = _capture_requested.get(profile.label, False)
    snapshot["promotion_catalog_requested"] = requested
    snapshot["promotion_catalog"] = parse_promotion_catalog(lines) if requested else []
    return snapshot


def persist_promotion_catalog(
    profile: base.Profile,
    promotions: list[dict[str, str | None]],
    observed_at: str,
) -> int:
    """Upsert current sailing promo windows. Empty captures never deactivate prior state."""
    create_table = """
    CREATE TABLE IF NOT EXISTS promotion_catalog (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      sailing_id TEXT NOT NULL REFERENCES sailings(id),
      label TEXT NOT NULL,
      starts_on TEXT,
      ends_on TEXT,
      first_seen_at TEXT NOT NULL,
      last_seen_at TEXT NOT NULL,
      active INTEGER NOT NULL DEFAULT 1,
      source TEXT NOT NULL DEFAULT 'authenticated_royal_promo_catalog',
      UNIQUE(sailing_id, label, starts_on, ends_on)
    )
    """
    create_index = """
    CREATE INDEX IF NOT EXISTS idx_promotion_catalog_sailing_active_end
      ON promotion_catalog(sailing_id, active, ends_on)
    """
    queries: list[dict[str, Any]] = [
        {"sql": create_table, "params": []},
        {"sql": create_index, "params": []},
    ]
    if promotions:
        # Only mark older rows inactive when Royal actually returned a non-empty catalog.
        # A transient promo API failure must not make yesterday's catalog disappear.
        queries.append({
            "sql": "UPDATE promotion_catalog SET active=0 WHERE sailing_id=?",
            "params": [base.SAILING_ID],
        })
        for promo in promotions:
            queries.append({
                "sql": """
                INSERT INTO promotion_catalog(
                  sailing_id,label,starts_on,ends_on,first_seen_at,last_seen_at,active,source
                ) VALUES(?,?,?,?,?,?,1,'authenticated_royal_promo_catalog')
                ON CONFLICT(sailing_id,label,starts_on,ends_on) DO UPDATE SET
                  last_seen_at=excluded.last_seen_at,
                  active=1,
                  source=excluded.source
                """,
                "params": [
                    base.SAILING_ID,
                    promo["label"],
                    promo.get("starts_on"),
                    promo.get("ends_on"),
                    observed_at,
                    observed_at,
                ],
            })
    base.d1_request(queries)
    return len(promotions)


def persist_history(profile: base.Profile, snapshot: dict[str, Any]) -> int:
    # Normal history goes first so the sailing FK is guaranteed to exist.
    count = _original_persist_history(profile, snapshot)
    if snapshot.get("promotion_catalog_requested"):
        promotions = snapshot.get("promotion_catalog") or []
        try:
            written = persist_promotion_catalog(profile, promotions, snapshot["observed_at"])
            if written:
                print(f"{profile.label} Royal promotion catalog: PASS ({written} active promo windows observed)")
            else:
                print("::warning::Royal promotion catalog was requested but no promo windows were parsed; prior catalog state retained")
        except Exception as exc:
            # Price/history success is more important than optional promo context.
            print(f"::warning::Royal promotion catalog persistence failed for {profile.label}: {exc}")
    return count


adaptive.build_config = build_config
base.parse_history = parse_history
base.persist_history = persist_history

if __name__ == "__main__":
    sys.exit(adaptive.main())

#!/usr/bin/env python3
"""Bootstrap-safe adaptive alert wrapper.

Extends the proven adaptive tracker so a currently-good product can send one
initial target-hit alert even when its first personalized D1 observation was
already below the configured deal threshold. Subsequent alerts continue to use
exact-sailing new historical lows/reprice savings.
"""
from __future__ import annotations

import sys
from typing import Any

import run_tracker as base
import run_tracker_adaptive as adaptive

_original_history_state = adaptive.history_state
_original_build_alerts = adaptive.build_alerts
_original_consolidate_alerts = adaptive.consolidate_alerts


def history_state(profile: base.Profile) -> adaptive.HistoryState:
    state = _original_history_state(profile)
    state.notified_products = set()  # type: ignore[attr-defined]
    state.notification_history_available = False  # type: ignore[attr-defined]
    if not state.available:
        return state
    try:
        rows = base.d1_request([{
            "sql": "SELECT DISTINCT product_id FROM notification_events WHERE household_id=? AND sailing_id=? AND product_id IS NOT NULL",
            "params": [profile.label, base.SAILING_ID],
        }])
        state.notified_products = {  # type: ignore[attr-defined]
            row["product_id"] for row in (rows[0].get("results") or []) if row.get("product_id")
        }
        state.notification_history_available = True  # type: ignore[attr-defined]
    except Exception as exc:
        print(f"::warning::Could not load notification history for {profile.label}; initial target-hit alerts disabled: {exc}")
    return state


def build_alerts(profile: base.Profile, snapshot: dict[str, Any], purchases: list[dict[str, Any]], state: adaptive.HistoryState) -> list[dict[str, Any]]:
    alerts = _original_build_alerts(profile, snapshot, purchases, state)
    if not state.available or not getattr(state, "notification_history_available", False):
        return alerts

    notified_products = getattr(state, "notified_products", set())
    existing = {(item.get("product_id"), item.get("role")) for item in alerts}
    for row in snapshot["products"]:
        spec = base.SPEC_BY_ID.get(row["product_id"])
        if not spec or spec.alert_below is None:
            continue
        role = row["traveler_role"]
        if role not in spec.audience or spec.product_id in notified_products:
            continue
        if (spec.product_id, role) in existing:
            continue
        if row["current"] > spec.alert_below + adaptive.EPSILON:
            continue
        alerts.append({
            "kind": "watch_initial_good",
            "product_id": spec.product_id,
            "spec": spec,
            "role": role,
            "current": row["current"],
            "target": spec.alert_below,
            "savings": 0.0,
            "promotion": row.get("promotion"),
        })
    return alerts


def consolidate_alerts(profile: base.Profile, alerts: list[dict[str, Any]]) -> tuple[str, str]:
    initial = [item for item in alerts if item["kind"] == "watch_initial_good"]
    standard = [item for item in alerts if item["kind"] != "watch_initial_good"]

    if standard:
        title, body = _original_consolidate_alerts(profile, standard)
        lines = body.splitlines()
        if lines and lines[-1].startswith("No purchase"):
            lines.pop()
    else:
        title = f"Royal deal alert — {base.SHIP_NAME} {base.SAIL_DATE}"
        lines = [f"{base.SHIP_NAME} · {base.SAIL_DATE} · {base.NIGHTS} nights", f"Cabin {profile.cabin}", ""]

    grouped: dict[tuple[str, float, float, str | None], list[dict[str, Any]]] = {}
    for item in initial:
        key = (item["product_id"], float(item["current"]), float(item["target"]), item.get("promotion"))
        grouped.setdefault(key, []).append(item)

    for (product_id, current, target, promo), rows in grouped.items():
        spec = rows[0]["spec"]
        roles = ", ".join(base.ROLE_LABELS.get(row["role"], row["role"]) for row in rows)
        lines += [
            f"GOOD CURRENT PRICE — {spec.name}: ${current:.2f} ({roles})",
            f"At or below configured deal target: ${target:.2f}",
            base.royal_product_url(profile, spec),
        ]
        if promo:
            lines.append(f"Promo: {promo}")
        lines.append("")

    lines.append("No purchase, cancellation, or rebooking was performed automatically.")
    return title, "\n".join(lines)


adaptive.history_state = history_state
adaptive.build_alerts = build_alerts
adaptive.consolidate_alerts = consolidate_alerts

if __name__ == "__main__":
    sys.exit(adaptive.main())

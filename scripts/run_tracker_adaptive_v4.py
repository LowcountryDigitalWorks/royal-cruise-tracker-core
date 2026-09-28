#!/usr/bin/env python3
"""Adaptive v4 wrapper: product availability/restock intelligence.

Builds on adaptive_v3 and keeps price monitoring authoritative. Royal's upstream
watch output uses the deliberately conservative phrase "not available or already
booked" when it cannot price a watch item. This wrapper records that state without
pretending it always means sold out, raises recently unavailable products inside the
existing six-product adaptive budget, and emits one AVAILABLE AGAIN alert when a
later authenticated watch returns a price.

A manually verified Royal UI SOLD OUT state may also be seeded in D1. Manual evidence
is never treated as a price observation and never changes purchase/reprice logic.
"""
from __future__ import annotations

import re
import sys
from typing import Any

import run_tracker as base
import run_tracker_adaptive as adaptive
import run_tracker_adaptive_v3  # noqa: F401 - installs v2 + v3 behavior first

UNAVAILABLE = re.compile(
    r"^\s*(?P<title>.+?)\s+not available or already booked for\s+(?P<passenger>.+?)\s*$",
    re.I,
)
UNAVAILABLE_STATUSES = {"unavailable_or_already_booked", "sold_out"}

_original_history_state = adaptive.history_state
_original_choose_specs = adaptive.choose_specs
_original_build_alerts = adaptive.build_alerts
_original_consolidate_alerts = adaptive.consolidate_alerts
_original_parse_history = base.parse_history
_original_persist_history = base.persist_history


def history_state(profile: base.Profile) -> adaptive.HistoryState:
    state = _original_history_state(profile)
    state.availability_previous = {}  # type: ignore[attr-defined]
    state.availability_history_available = False  # type: ignore[attr-defined]
    if not state.available:
        return state
    try:
        rows = base.d1_request([{
            "sql": """
            WITH ranked AS (
              SELECT product_id,COALESCE(passenger_class,'all') AS passenger_class,
                     status,observed_at,source,
                     ROW_NUMBER() OVER (
                       PARTITION BY product_id,COALESCE(passenger_class,'all')
                       ORDER BY id DESC
                     ) AS rn
                FROM product_availability
               WHERE household_id=? AND sailing_id=? AND booking_id=?
            )
            SELECT product_id,passenger_class,status,observed_at,source
              FROM ranked WHERE rn=1
            """,
            "params": [profile.label, base.SAILING_ID, base.booking_id(profile)],
        }])
        state.availability_previous = {  # type: ignore[attr-defined]
            (row["product_id"], row.get("passenger_class") or "all"): row
            for row in ((rows[0].get("results") or []) if rows else [])
        }
        state.availability_history_available = True  # type: ignore[attr-defined]
    except Exception:
        # Rollout-safe: missing optional table or a transient D1 read never changes
        # normal Royal price monitoring.
        pass
    return state


def choose_specs(profile: base.Profile, state: adaptive.HistoryState) -> tuple[list[base.WatchSpec], str]:
    selected, mode = _original_choose_specs(profile, state)
    if mode.startswith("manual group") or not getattr(state, "availability_history_available", False):
        return selected, mode

    previous = getattr(state, "availability_previous", {})
    unavailable_ids = {
        product_id
        for (product_id, _role), row in previous.items()
        if row.get("status") in UNAVAILABLE_STATUSES and product_id not in state.recent_purchases
    }
    forced = [base.SPEC_BY_ID[pid] for pid in unavailable_ids if pid in base.SPEC_BY_ID]
    if not forced:
        return selected, mode

    # Preserve the same request budget. Recently unavailable products move to the
    # front; lower-priority selected products yield until availability recovers.
    ordered: list[base.WatchSpec] = []
    for spec in sorted(forced, key=lambda item: adaptive.policy_for(item).base_priority, reverse=True):
        if spec not in ordered:
            ordered.append(spec)
    for spec in selected:
        if spec not in ordered:
            ordered.append(spec)
    ordered = ordered[: adaptive.MAX_WATCH_SPECS]
    return ordered, f"{mode}; {len(forced)} unavailable product variant(s) prioritized for restock"


def _selected_specs(watchlist: list[dict[str, Any]]) -> list[base.WatchSpec]:
    return [base.spec_from_watch_item(item) for item in watchlist]


def _best_unavailable_spec(title: str, role: str, specs: list[base.WatchSpec]) -> base.WatchSpec | None:
    candidates = [spec for spec in specs if not spec.audience or role in spec.audience]
    return base.match_watch_spec(title, candidates)

def parse_history(profile: base.Profile, watchlist: list[dict[str, Any]], lines: list[str]) -> dict[str, Any]:
    snapshot = _original_parse_history(profile, watchlist, lines)
    specs = _selected_specs(watchlist)
    availability: dict[tuple[str, str], dict[str, Any]] = {}

    for line in lines:
        match = UNAVAILABLE.search(line)
        if not match:
            continue
        passenger = match.group("passenger").strip()
        role = base.traveler_role(profile, passenger)
        spec = _best_unavailable_spec(match.group("title").strip(), role, specs)
        if not spec:
            continue
        availability[(spec.product_id, role)] = {
            "product_id": spec.product_id,
            "traveler_role": role,
            "status": "unavailable_or_already_booked",
            "source": "authenticated_royal_watch",
            "raw_status": "Royal upstream: not available or already booked",
        }

    # A parsed price is authoritative evidence that the item is currently available
    # to that traveler. It overrides any duplicate unavailable line in the same run.
    for row in snapshot.get("products") or []:
        key = (row["product_id"], row["traveler_role"])
        availability[key] = {
            "product_id": row["product_id"],
            "traveler_role": row["traveler_role"],
            "status": "available",
            "source": "authenticated_royal_watch",
            "raw_status": "Royal returned current watch price",
            "current": row.get("current"),
            "promotion": row.get("promotion"),
        }

    snapshot["availability"] = list(availability.values())
    return snapshot


def persist_availability(profile: base.Profile, snapshot: dict[str, Any]) -> int:
    rows = snapshot.get("availability") or []
    if not rows:
        return 0
    observed_at = snapshot["observed_at"]
    queries: list[dict[str, Any]] = [
        {
            "sql": """
            CREATE TABLE IF NOT EXISTS product_availability (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              household_id TEXT NOT NULL REFERENCES households(id),
              sailing_id TEXT NOT NULL REFERENCES sailings(id),
              booking_id TEXT REFERENCES bookings(id),
              product_id TEXT NOT NULL REFERENCES products(id),
              passenger_class TEXT,
              status TEXT NOT NULL,
              observed_at TEXT NOT NULL,
              source TEXT NOT NULL,
              raw_status TEXT,
              UNIQUE(household_id,sailing_id,booking_id,product_id,passenger_class,status,observed_at)
            )
            """,
            "params": [],
        },
        {
            "sql": "CREATE INDEX IF NOT EXISTS idx_product_availability_latest ON product_availability(household_id,sailing_id,booking_id,product_id,passenger_class,id DESC)",
            "params": [],
        },
    ]
    seen_products: set[str] = set()
    for row in rows:
        product_id = row["product_id"]
        spec = base.SPEC_BY_ID.get(product_id)
        if spec and product_id not in seen_products:
            category, unit = base.classify(spec.prefix)
            queries.append({
                "sql": "INSERT INTO products(id,product_code,prefix,name,price_unit,category,age_scope) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,price_unit=excluded.price_unit,category=excluded.category",
                "params": [spec.product_id, spec.product, spec.prefix, spec.name, unit, category, spec.age_field],
            })
            seen_products.add(product_id)
        queries.append({
            "sql": "INSERT OR IGNORE INTO product_availability(household_id,sailing_id,booking_id,product_id,passenger_class,status,observed_at,source,raw_status) VALUES(?,?,?,?,?,?,?,?,?)",
            "params": [
                profile.label,
                base.SAILING_ID,
                base.booking_id(profile),
                product_id,
                row["traveler_role"],
                row["status"],
                observed_at,
                row["source"],
                row.get("raw_status"),
            ],
        })
    base.d1_request(queries)
    return len(rows)


def persist_history(profile: base.Profile, snapshot: dict[str, Any]) -> int:
    count = _original_persist_history(profile, snapshot)
    try:
        availability_count = persist_availability(profile, snapshot)
        if availability_count:
            print(f"{profile.label} Royal availability history: PASS ({availability_count} traveler/product states observed)")
    except Exception as exc:
        print(f"::warning::Royal availability persistence failed for {profile.label}: {exc}")
    return count


def build_alerts(
    profile: base.Profile,
    snapshot: dict[str, Any],
    purchases: list[dict[str, Any]],
    state: adaptive.HistoryState,
) -> list[dict[str, Any]]:
    alerts = _original_build_alerts(profile, snapshot, purchases, state)
    if not getattr(state, "availability_history_available", False):
        return alerts

    previous = getattr(state, "availability_previous", {})
    products = {
        (row["product_id"], row["traveler_role"]): row
        for row in (snapshot.get("products") or [])
    }
    for row in snapshot.get("availability") or []:
        if row.get("status") != "available":
            continue
        key = (row["product_id"], row["traveler_role"])
        prior = previous.get(key)
        if not prior or prior.get("status") not in UNAVAILABLE_STATUSES:
            continue
        product = products.get(key)
        spec = base.SPEC_BY_ID.get(row["product_id"])
        if not product or not spec or product.get("current") is None:
            continue
        alerts.append({
            "kind": "availability_return",
            "product_id": spec.product_id,
            "spec": spec,
            "role": row["traveler_role"],
            "current": float(product["current"]),
            "savings": 0.0,
            "promotion": product.get("promotion"),
            "previous_status": prior.get("status"),
        })
    return alerts


def consolidate_alerts(profile: base.Profile, alerts: list[dict[str, Any]]) -> tuple[str, str]:
    availability = [item for item in alerts if item["kind"] == "availability_return"]
    standard = [item for item in alerts if item["kind"] != "availability_return"]

    if standard:
        title, body = _original_consolidate_alerts(profile, standard)
        lines = body.splitlines()
        if lines and lines[-1].startswith("No purchase"):
            lines.pop()
    else:
        title = f"Royal availability alert — {base.SHIP_NAME} {base.SAIL_DATE}"
        lines = [f"{base.SHIP_NAME} · {base.SAIL_DATE} · {base.NIGHTS} nights", f"Cabin {profile.cabin}", ""]

    grouped: dict[tuple[str, float, str | None, str], list[dict[str, Any]]] = {}
    for item in availability:
        key = (
            item["product_id"],
            float(item["current"]),
            item.get("promotion"),
            str(item.get("previous_status") or "unavailable"),
        )
        grouped.setdefault(key, []).append(item)

    for (product_id, current, promo, previous_status), rows in grouped.items():
        spec = rows[0]["spec"]
        roles = ", ".join(base.ROLE_LABELS.get(row["role"], row["role"]) for row in rows)
        prior_text = "Royal UI previously showed SOLD OUT" if previous_status == "sold_out" else "Royal previously returned unavailable/already booked"
        lines += [
            f"AVAILABLE AGAIN — {spec.name}: ${current:.2f} ({roles})",
            prior_text,
            base.royal_product_url(profile, spec),
        ]
        if promo:
            lines.append(f"Promo: {promo}")
        lines.append("")

    lines.append("No purchase, cancellation, or rebooking was performed automatically.")
    return title, "\n".join(lines)


adaptive.history_state = history_state
adaptive.choose_specs = choose_specs
adaptive.build_alerts = build_alerts
adaptive.consolidate_alerts = consolidate_alerts
base.parse_history = parse_history
base.persist_history = persist_history

if __name__ == "__main__":
    sys.exit(adaptive.main())

#!/usr/bin/env python3
"""Bounded retained-evidence replay for ALERT-REL-001.

Reconstruct at most the most recent qualifying missed transition per subject from a
small recent-row window. Replay is durability recovery only: it never authorizes a
send. Reconstructed intents still pass the delivery outbox's evidence-commit and
current-state eligibility gates before transport.
"""
from __future__ import annotations

from typing import Any

REPLAY_ROWS_PER_SUBJECT = 4
EPSILON = 0.009


def _float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _role(row: dict[str, Any]) -> str:
    return str(row.get("passenger_class") or row.get("passenger_scope") or "all")


def _rank(row: dict[str, Any]) -> int:
    try:
        return int(row.get("recency_rank") or 10**9)
    except (TypeError, ValueError):
        return 10**9


def _candidate(alert: dict[str, Any], observed_at: Any) -> dict[str, Any] | None:
    timestamp = str(observed_at or "").strip()
    if not timestamp:
        return None
    return {"observed_at": timestamp, "alert": alert}


def reconstruct_price_candidates(
    profile: Any,
    price_rows: list[dict[str, Any]],
    purchase_rows: list[dict[str, Any]],
    base: Any,
    adaptive: Any,
) -> list[dict[str, Any]]:
    """Rebuild the newest qualifying missed fare/watch/purchase event per subject."""
    purchases = {
        (str(row.get("product_id") or ""), _role(row)): row
        for row in purchase_rows
        if row.get("product_id")
    }
    ordered = sorted(
        price_rows,
        key=lambda row: (
            str(row.get("source") or ""),
            str(row.get("product_id") or ""),
            _role(row),
            _rank(row),
        ),
    )
    selected: set[tuple[str, str, str]] = set()
    candidates: list[dict[str, Any]] = []

    fare_product = f"cruise-fare:{getattr(profile, 'cabin_category', None) or 'unknown'}:all"
    paid_fare = _float(getattr(profile, "paid_price", None))

    for row in ordered:
        if _rank(row) > REPLAY_ROWS_PER_SUBJECT:
            continue
        source = str(row.get("source") or "")
        product_id = str(row.get("product_id") or "")
        role = _role(row)
        subject = (source, product_id, role)
        if subject in selected:
            continue
        current = _float(row.get("observed_price"))
        if current is None:
            continue
        prior_min = _float(row.get("prior_min"))
        observed_at = row.get("observed_at")

        alert: dict[str, Any] | None = None
        if source == "authenticated_royal" and product_id == fare_product:
            if paid_fare is None or paid_fare - current < float(base.FARE_ALERT_SAVINGS) - EPSILON:
                continue
            if prior_min is not None and current >= prior_min - EPSILON:
                continue
            alert = {
                "kind": "fare",
                "product_id": product_id,
                "name": f"Cabin {getattr(profile, 'cabin', '')} fare",
                "current": current,
                "savings": round(paid_fare - current, 2),
            }

        elif source == "authenticated_royal":
            spec = base.SPEC_BY_ID.get(product_id)
            if spec is None or role not in getattr(spec, "audience", ()):
                continue
            # First personalized observation establishes the baseline and was never an alert.
            if prior_min is None or current >= prior_min - EPSILON:
                continue
            meaningful, savings = adaptive.meaningful_drop(profile, prior_min, current, spec.prefix)
            if not meaningful:
                continue
            alert = {
                "kind": "watch_new_low",
                "product_id": product_id,
                "spec": spec,
                "role": role,
                "current": current,
                "previous_low": prior_min,
                "savings": savings,
                "promotion": row.get("promotion_label"),
            }

        elif source == "authenticated_royal_purchase":
            purchase = purchases.get((product_id, role))
            if purchase is None:
                continue
            paid = _float(purchase.get("paid_unit_price"))
            if paid is None or current >= paid - EPSILON:
                continue
            paid_total = _float(purchase.get("paid_total"))
            current_total = _float(row.get("normalized_total"))
            per_day = str(purchase.get("price_unit") or "") == "per_cruise_day"
            if paid_total is None:
                paid_total = round(paid * base.NIGHTS, 2) if per_day else round(paid, 2)
            if current_total is None:
                current_total = round(current * base.NIGHTS, 2) if per_day else round(current, 2)
            savings = round(paid_total - current_total, 2)
            try:
                threshold = float(profile.threshold)
            except (TypeError, ValueError):
                continue
            if savings + EPSILON < threshold:
                continue
            if prior_min is not None and current >= prior_min - EPSILON:
                continue
            alert = {
                "kind": "purchase_reprice",
                "product_id": product_id,
                "name": str(purchase.get("name") or "tracked Royal purchase"),
                "role": role,
                "paid": paid,
                "current": current,
                "savings": savings,
                "promotion": row.get("promotion_label"),
                "known_spec": product_id in base.SPEC_BY_ID,
                "per_day": per_day,
            }

        if alert is None:
            continue
        item = _candidate(alert, observed_at)
        if item is None:
            continue
        selected.add(subject)
        candidates.append(item)

    return candidates


def reconstruct_availability_candidates(
    availability_rows: list[dict[str, Any]],
    base: Any,
) -> list[dict[str, Any]]:
    """Rebuild the newest bounded unavailable->available transition per subject."""
    ordered = sorted(
        availability_rows,
        key=lambda row: (str(row.get("product_id") or ""), _role(row), _rank(row)),
    )
    selected: set[tuple[str, str]] = set()
    candidates: list[dict[str, Any]] = []
    unavailable = {"unavailable_or_already_booked", "sold_out"}

    for row in ordered:
        if _rank(row) > REPLAY_ROWS_PER_SUBJECT:
            continue
        product_id = str(row.get("product_id") or "")
        role = _role(row)
        subject = (product_id, role)
        if subject in selected:
            continue
        if row.get("status") != "available" or row.get("prior_status") not in unavailable:
            continue
        current = _float(row.get("current_price"))
        spec = base.SPEC_BY_ID.get(product_id)
        if current is None or spec is None or role not in getattr(spec, "audience", ()):
            continue
        item = _candidate(
            {
                "kind": "availability_return",
                "product_id": product_id,
                "spec": spec,
                "role": role,
                "current": current,
                "savings": 0.0,
                "promotion": row.get("promotion_label"),
                "previous_status": row.get("prior_status"),
            },
            row.get("observed_at"),
        )
        if item is None:
            continue
        selected.add(subject)
        candidates.append(item)
    return candidates


def load_bounded_replay_candidates(profile: Any, base: Any, adaptive: Any) -> list[dict[str, Any]]:
    """Query a bounded recent-row window and reconstruct missing transition candidates."""
    bid = base.booking_id(profile)
    common = [profile.label, base.SAILING_ID, bid]
    price_sql = f"""
    WITH sequenced AS (
      SELECT id,product_id,COALESCE(passenger_class,'all') AS passenger_class,source,
             observed_price,normalized_total,promotion_label,observed_at,
             MIN(observed_price) OVER (
               PARTITION BY product_id,COALESCE(passenger_class,'all'),source
               ORDER BY id
               ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
             ) AS prior_min,
             ROW_NUMBER() OVER (
               PARTITION BY product_id,COALESCE(passenger_class,'all'),source
               ORDER BY id DESC
             ) AS recency_rank
        FROM price_snapshots
       WHERE household_id=? AND sailing_id=? AND booking_id=?
         AND source IN ('authenticated_royal','authenticated_royal_purchase')
         AND observed_price IS NOT NULL
    )
    SELECT product_id,passenger_class,source,observed_price,normalized_total,
           promotion_label,observed_at,prior_min,recency_rank
      FROM sequenced
     WHERE recency_rank<={REPLAY_ROWS_PER_SUBJECT}
     ORDER BY source,product_id,passenger_class,recency_rank
    """
    purchase_sql = """
    SELECT p.product_id,p.passenger_scope,p.paid_unit_price,p.paid_total,p.currency,
           pr.name,pr.prefix,pr.price_unit
      FROM purchases p
      JOIN products pr ON pr.id=p.product_id
     WHERE p.household_id=? AND p.sailing_id=? AND p.booking_id=? AND p.active=1
    """
    results = base.d1_request([
        {"sql": price_sql, "params": common},
        {"sql": purchase_sql, "params": common},
    ])
    price_rows = (results[0].get("results") or []) if results else []
    purchase_rows = (results[1].get("results") or []) if len(results) > 1 else []
    candidates = reconstruct_price_candidates(profile, price_rows, purchase_rows, base, adaptive)

    availability_sql = f"""
    WITH sequenced AS (
      SELECT id,product_id,COALESCE(passenger_class,'all') AS passenger_class,
             status,observed_at,
             LAG(status) OVER (
               PARTITION BY product_id,COALESCE(passenger_class,'all') ORDER BY id
             ) AS prior_status,
             ROW_NUMBER() OVER (
               PARTITION BY product_id,COALESCE(passenger_class,'all') ORDER BY id DESC
             ) AS recency_rank
        FROM product_availability
       WHERE household_id=? AND sailing_id=? AND booking_id=?
    )
    SELECT s.product_id,s.passenger_class,s.status,s.prior_status,s.observed_at,s.recency_rank,
           (
             SELECT ps.observed_price
               FROM price_snapshots ps
              WHERE ps.household_id=? AND ps.sailing_id=? AND ps.booking_id=?
                AND ps.product_id=s.product_id
                AND COALESCE(ps.passenger_class,'all')=s.passenger_class
                AND ps.source='authenticated_royal'
                AND ps.observed_at=s.observed_at
                AND ps.observed_price IS NOT NULL
              ORDER BY ps.id DESC LIMIT 1
           ) AS current_price,
           (
             SELECT ps.promotion_label
               FROM price_snapshots ps
              WHERE ps.household_id=? AND ps.sailing_id=? AND ps.booking_id=?
                AND ps.product_id=s.product_id
                AND COALESCE(ps.passenger_class,'all')=s.passenger_class
                AND ps.source='authenticated_royal'
                AND ps.observed_at=s.observed_at
                AND ps.observed_price IS NOT NULL
              ORDER BY ps.id DESC LIMIT 1
           ) AS promotion_label
      FROM sequenced s
     WHERE s.recency_rank<={REPLAY_ROWS_PER_SUBJECT}
     ORDER BY s.product_id,s.passenger_class,s.recency_rank
    """
    try:
        availability_result = base.d1_request([{
            "sql": availability_sql,
            "params": [*common, *common, *common],
        }])
        availability_rows = (availability_result[0].get("results") or []) if availability_result else []
    except Exception:
        # A missing optional availability table means there is no retained restock
        # transition to replay. Other replay classes remain available.
        availability_rows = []

    candidates.extend(reconstruct_availability_candidates(availability_rows, base))
    return candidates

#!/usr/bin/env python3
"""Adaptive production entrypoint for the Royal cruise tracker.

This module deliberately reuses the tested upstream wrapper in run_tracker.py and adds:
- D1-backed adaptive watchlist selection;
- higher-frequency checks for high-value products while aging every product back into scope;
- automatic exclusion of recently observed purchased products from the unpurchased watchlist
  (upstream already checks purchased orders on every authenticated run);
- purchased-order price-history capture and consolidated reprice alerts;
- meaningful new-low alerts based on historical prices rather than arbitrary absolute targets.

Royal credentials, reservation identifiers, passenger names, and notification credentials remain
runner-only. D1 receives pseudonymous traveler roles and sanitized price/purchase observations.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import run_tracker as base

_ADAPTIVE = dict(base.RUNTIME_CONFIG.get("adaptive_policy", {}))
MAX_WATCH_SPECS = int(_ADAPTIVE.get("max_watch_specs", 6))
COLLECTOR_SENTINEL_PRICE = float(_ADAPTIVE.get("collector_sentinel_price", 9999.0))
RECENT_PURCHASE_HOURS = float(_ADAPTIVE.get("recent_purchase_hours", 20.0))
EPSILON = 0.009


@dataclass(frozen=True)
class PriorityPolicy:
    base_priority: float
    target_hours: float
    max_hours: float


POLICY: dict[str, PriorityPolicy] = {
    str(product_id): PriorityPolicy(
        float(values.get("base_priority", 40)),
        float(values.get("target_hours", 48)),
        float(values.get("max_hours", 72)),
    )
    for product_id, values in dict(_ADAPTIVE.get("priorities", {})).items()
}
AUTO_ANCHORS = tuple(str(value) for value in _ADAPTIVE.get("auto_anchors", []))
CHILD_TIER_PLAUSIBILITY_PRODUCTS = frozenset(
    str(value) for value in _ADAPTIVE.get("child_tier_plausibility_products", [])
)
CHILD_TIER_MAX_UPWARD_RATIO = float(_ADAPTIVE.get("child_tier_max_upward_ratio", 1.50))

PURCHASE_REBOOK = re.compile(
    r"^\s*(?P<passenger>[^:]+?):\s+Rebook!\s+(?P<name>.+?)\s+Price"
    r"(?P<per_day>\s+per night)?\s+is lower:\s+(?P<current>[0-9.]+)\s+"
    r"(?P<currency>[A-Z]{3})\s+than\s+(?P<paid>[0-9.]+)\s+[A-Z]{3}",
    re.I,
)
PURCHASE_BEST = re.compile(
    r"^\s*(?P<passenger>.+?)\s+\(Cabin\s+(?P<cabin>\d+)\)\s+has best price"
    r"(?P<per_day>\s+per night)?\s+for\s+(?P<name>.+?)\s+of:\s+"
    r"(?P<paid>[0-9.]+)\s+(?P<currency>[A-Z]{3})"
    r"(?:\s+\(now\s+(?P<current>[0-9.]+)\s+[A-Z]{3}\))?"
    r"(?P<no_sale>\s+\(No Longer for Sale\))?",
    re.I,
)


@dataclass
class HistoryState:
    available: bool
    previous: dict[tuple[str, str, str], dict[str, Any]]
    product_stats: dict[str, dict[str, Any]]
    recent_purchases: set[str]
    error: str | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def hours_since(value: str | None) -> float:
    parsed = parse_time(value)
    if not parsed:
        return 1e9
    return max(0.0, (utc_now() - parsed.astimezone(timezone.utc)).total_seconds() / 3600.0)


def history_state(profile: base.Profile) -> HistoryState:
    bid = base.booking_id(profile)
    latest_sql = """
    WITH ranked AS (
      SELECT product_id, COALESCE(passenger_class,'all') AS passenger_class, source,
             observed_price, promotion_label, observed_at,
             MIN(observed_price) OVER (
               PARTITION BY product_id, COALESCE(passenger_class,'all'), source
             ) AS historical_min,
             ROW_NUMBER() OVER (
               PARTITION BY product_id, COALESCE(passenger_class,'all'), source
               ORDER BY id DESC
             ) AS rn
        FROM price_snapshots
       WHERE household_id=? AND sailing_id=? AND booking_id=?
         AND source IN ('authenticated_royal','authenticated_royal_purchase')
    )
    SELECT product_id, passenger_class, source, observed_price, promotion_label,
           observed_at, historical_min
      FROM ranked WHERE rn=1
    """
    stats_sql = """
    WITH samples AS (
      SELECT product_id, observed_at, AVG(observed_price) AS price
        FROM price_snapshots
       WHERE household_id=? AND sailing_id=? AND booking_id=?
         AND source='authenticated_royal' AND observed_price IS NOT NULL
         AND product_id NOT LIKE 'cruise-fare:%'
       GROUP BY product_id, observed_at
    ), sequenced AS (
      SELECT product_id, observed_at, price,
             LAG(price) OVER (PARTITION BY product_id ORDER BY observed_at) AS previous_price
        FROM samples
    )
    SELECT product_id, MAX(observed_at) AS last_seen_at, COUNT(*) AS samples,
           SUM(CASE WHEN previous_price IS NOT NULL AND ABS(price-previous_price) > 0.009
                    THEN 1 ELSE 0 END) AS changes,
           MIN(price) AS min_price, MAX(price) AS max_price
      FROM sequenced
     GROUP BY product_id
    """
    purchase_sql = """
    SELECT product_id, MAX(observed_at) AS last_seen_at
      FROM price_snapshots
     WHERE household_id=? AND sailing_id=? AND booking_id=?
       AND source IN ('authenticated_royal_purchase','authenticated_royal_purchase_presence')
     GROUP BY product_id
    """
    params = [profile.label, base.SAILING_ID, bid]
    try:
        results = base.d1_request([
            {"sql": latest_sql, "params": params},
            {"sql": stats_sql, "params": params},
            {"sql": purchase_sql, "params": params},
        ])
        previous: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in (results[0].get("results") or []):
            previous[(row["source"], row["product_id"], row.get("passenger_class") or "all")] = row
        stats = {row["product_id"]: row for row in (results[1].get("results") or [])}
        recent = {
            row["product_id"] for row in (results[2].get("results") or [])
            if hours_since(row.get("last_seen_at")) <= RECENT_PURCHASE_HOURS
        }
        return HistoryState(True, previous, stats, recent)
    except Exception as exc:
        return HistoryState(False, {}, {}, set(), str(exc))


def reconcile_watch_snapshot(snapshot: dict[str, Any], state: HistoryState) -> tuple[dict[str, Any], int]:
    """Fail closed on demonstrated child-tier cross-product contamination."""
    if not state.available:
        return snapshot, 0

    quarantined: set[tuple[str, str]] = set()
    kept: list[dict[str, Any]] = []
    for row in snapshot.get("products") or []:
        product_id = str(row.get("product_id") or "")
        role = str(row.get("traveler_role") or "")
        current = row.get("current")
        if product_id not in CHILD_TIER_PLAUSIBILITY_PRODUCTS or not role.startswith("child_") or current is None:
            kept.append(row)
            continue
        baseline_row = state.previous.get(("authenticated_royal", product_id, "child"))
        baseline = float(baseline_row.get("observed_price") or 0.0) if baseline_row else 0.0
        if baseline <= 0 or float(current) <= baseline * CHILD_TIER_MAX_UPWARD_RATIO + EPSILON:
            kept.append(row)
            continue
        quarantined.add((product_id, role))
        print(f"::warning::Quarantined implausible Royal child-tier observation product={product_id} role={role}; generic child baseline retained")

    if not quarantined:
        return snapshot, 0
    snapshot["products"] = kept
    if "availability" in snapshot:
        snapshot["availability"] = [
            row for row in (snapshot.get("availability") or [])
            if (str(row.get("product_id") or ""), str(row.get("traveler_role") or "")) not in quarantined
        ]
    return snapshot, len(quarantined)


def policy_for(spec: base.WatchSpec) -> PriorityPolicy:
    return POLICY.get(spec.product_id, PriorityPolicy(40, 48, 72))


def scheduler_score(spec: base.WatchSpec, stat: dict[str, Any] | None) -> float:
    policy = policy_for(spec)
    if not stat:
        return 10000.0 + policy.base_priority

    age = hours_since(stat.get("last_seen_at"))
    samples = max(0, int(stat.get("samples") or 0))
    changes = max(0, int(stat.get("changes") or 0))
    min_price = float(stat.get("min_price") or 0.0)
    max_price = float(stat.get("max_price") or 0.0)

    due_pressure = min(5.0, age / max(1.0, policy.target_hours)) * 55.0
    change_rate = changes / max(1, samples - 1)
    volatility = change_rate * 65.0
    if min_price > 0 and max_price >= min_price:
        volatility += min(45.0, ((max_price - min_price) / min_price) * 120.0)

    # A higher whole-trip exposure means a small percentage change can matter more in dollars.
    exposure = 0.0
    if max_price > 0:
        exposure = base.normalized_total(spec.prefix, max_price) * max(1, len(spec.audience))
    value_bonus = min(35.0, exposure / 25.0)

    overdue_bonus = 1200.0 if age >= policy.max_hours else 0.0
    return policy.base_priority + due_pressure + volatility + value_bonus + overdue_bonus


def legacy_time_group() -> str:
    hour = datetime.now(ZoneInfo(base.LOCAL_TIMEZONE)).hour
    if hour < 8:
        return "A"
    if hour < 16:
        return "B"
    return "C"


def choose_specs(profile: base.Profile, state: HistoryState) -> tuple[list[base.WatchSpec], str]:
    requested = base.env("SCAN_GROUP", "auto").strip().upper()
    if requested in base.WATCH_GROUPS:
        return list(base.WATCH_GROUPS[requested]), f"manual group {requested}"
    if requested not in {"", "AUTO"}:
        raise SystemExit("SCAN_GROUP must be auto, A, B, or C")

    if not state.available:
        fallback = legacy_time_group()
        return list(base.WATCH_GROUPS[fallback]), f"fallback group {fallback} (D1 history unavailable)"

    purchased = state.recent_purchases
    # Purchased products are already checked by upstream get_orders(), so every recent purchased
    # product reduces the unpurchased watch budget by one, down to a safe floor of two.
    watch_budget = max(2, MAX_WATCH_SPECS - min(4, len(purchased)))
    eligible = [spec for spec in base.WATCH_SPECS if spec.product_id not in purchased]

    selected: list[base.WatchSpec] = []
    for product_id in AUTO_ANCHORS:
        spec = base.SPEC_BY_ID.get(product_id)
        if spec and spec in eligible and spec not in selected and len(selected) < watch_budget:
            selected.append(spec)

    ranked = sorted(
        (spec for spec in eligible if spec not in selected),
        key=lambda spec: (scheduler_score(spec, state.product_stats.get(spec.product_id)), spec.product_id),
        reverse=True,
    )
    selected.extend(ranked[: max(0, watch_budget - len(selected))])
    return selected, f"adaptive priority ({len(selected)} watch products; {len(purchased)} recent purchased products auto-monitored)"


def watch_item(spec: base.WatchSpec, reservation: str) -> dict[str, Any]:
    return {
        "name": f"{spec.name} [{spec.age_field}]",
        "prefix": spec.prefix,
        "product": spec.product,
        # Force the upstream lower-price path so promoDescription is included in its private output.
        # minimumSavingAlert remains impossibly high, so upstream itself still sends no alerts.
        "price": COLLECTOR_SENTINEL_PRICE,
        "enabled": True,
        "guestAgeString": spec.age_field,
        "reservations": [reservation],
    }


def build_config(profile: base.Profile, state: HistoryState) -> tuple[dict[str, Any], list[dict[str, Any]], str, list[base.WatchSpec]]:
    if profile.watchlist_json.strip() or not profile.use_default_watchlist:
        cfg, watchlist = base.build_config(profile)
        return cfg, watchlist, "repository-defined watchlist" if watchlist else "no watchlist", []

    if not profile.reservation_id:
        raise SystemExit(f"{profile.label} adaptive watchlist requires a reservation ID")
    specs, mode = choose_specs(profile, state)
    original = profile.watchlist_json
    profile.watchlist_json = json.dumps([watch_item(spec, "${RCCL_RESERVATION_ID}") for spec in specs])
    try:
        cfg, watchlist = base.build_config(profile)
    finally:
        profile.watchlist_json = original
    return cfg, watchlist, mode, specs


def synthetic_purchase_spec(name: str, role: str, per_day: bool) -> base.WatchSpec:
    digest = hashlib.sha256(name.lower().encode("utf-8")).hexdigest()[:12]
    prefix = "purchase-day" if per_day else "purchase"
    return base.WatchSpec(name.strip(), prefix, digest, "all", (role,), None)


def match_purchase_spec(name: str, role: str, per_day: bool) -> base.WatchSpec:
    candidates = [spec for spec in base.WATCH_SPECS if role in spec.audience]
    # Purchase/order evidence must use the same fail-closed identity matcher as
    # watch evidence and then meet a second lexical corroboration threshold.
    # This prevents a single identity word such as "key" from silently relabeling
    # a different Royal order as a known catalog product.
    matched = base.match_watch_spec(name, candidates)
    if matched is not None:
        actual_words = base.token_words(name)
        expected_words = base.token_words(matched.name)
        required = max(1, min(2, len(expected_words)))
        if len(actual_words & expected_words) >= required:
            return matched
    return synthetic_purchase_spec(name, role, per_day)


def parse_purchases(profile: base.Profile, lines: list[str], observed_at: str) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    seen: set[tuple[str, str]] = set()
    for line in lines:
        match = PURCHASE_REBOOK.search(line) or PURCHASE_BEST.search(line)
        if match:
            groups = match.groupdict()
            if groups.get("cabin") and profile.cabin and groups["cabin"] != profile.cabin:
                pending = None
                continue
            passenger = groups["passenger"].strip()
            role = base.traveler_role(profile, passenger)
            per_day = bool(groups.get("per_day"))
            spec = match_purchase_spec(groups["name"].strip(), role, per_day)
            key = (spec.product_id, role)
            if key in seen:
                pending = None
                continue
            seen.add(key)
            paid = float(groups["paid"])
            current_raw = groups.get("current")
            no_sale = bool(groups.get("no_sale"))
            current = float(current_raw) if current_raw else (None if no_sale else paid)
            row = {
                "product_id": spec.product_id,
                "prefix": spec.prefix,
                "product_code": spec.product,
                "age_field": spec.age_field,
                "name": spec.name,
                "traveler_role": role,
                "paid": paid,
                "current": current,
                "currency": groups.get("currency") or "USD",
                "promotion": None,
                "per_day": per_day or base.classify(spec.prefix)[1] == "per_cruise_day",
                "known_spec": spec.product_id in base.SPEC_BY_ID,
                "observed_at": observed_at,
                "no_longer_for_sale": no_sale,
            }
            observations.append(row)
            pending = row if PURCHASE_REBOOK.search(line) else None
            continue
        if pending and "Promotion:" in line:
            pending["promotion"] = line.split("Promotion:", 1)[1].strip()
            pending = None
    return observations


def purchase_total(row: dict[str, Any], price: float) -> float:
    return round(price * base.NIGHTS, 2) if row.get("per_day") else round(price, 2)


def persist_purchases(profile: base.Profile, purchases: list[dict[str, Any]]) -> int:
    if not purchases:
        return 0
    bid = base.booking_id(profile)
    queries: list[dict[str, Any]] = []
    for row in purchases:
        product_id = row["product_id"]
        category, price_unit = base.classify(row["prefix"])
        if row.get("per_day"):
            price_unit = "per_cruise_day"
        elif category in {"purchase-day", "purchase"}:
            price_unit = "unknown"
        queries.append({
            "sql": "INSERT INTO products(id,product_code,prefix,name,price_unit,category,age_scope) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,price_unit=excluded.price_unit,category=excluded.category",
            "params": [product_id, row["product_code"], row["prefix"], row["name"], price_unit, category, row["age_field"]],
        })
        paid_total = purchase_total(row, row["paid"])
        purchase_key = [profile.label, base.SAILING_ID, bid, product_id, row["traveler_role"]]
        queries.extend([
            {
                "sql": "UPDATE purchases SET paid_unit_price=?,paid_total=?,currency=?,active=1 WHERE household_id=? AND sailing_id=? AND booking_id=? AND product_id=? AND passenger_scope=? AND active=1",
                "params": [row["paid"], paid_total, row["currency"], *purchase_key],
            },
            {
                "sql": "INSERT INTO purchases(household_id,sailing_id,booking_id,product_id,passenger_scope,quantity,paid_unit_price,paid_total,currency,purchased_at,active,notes) SELECT ?,?,?,?,?,1,?,?,?,NULL,1,'Observed from authenticated Royal order history' WHERE NOT EXISTS (SELECT 1 FROM purchases WHERE household_id=? AND sailing_id=? AND booking_id=? AND product_id=? AND passenger_scope=? AND active=1)",
                "params": [*purchase_key, row["paid"], paid_total, row["currency"], *purchase_key],
            },
        ])
        source = "authenticated_royal_purchase" if row["current"] is not None else "authenticated_royal_purchase_presence"
        current_total = purchase_total(row, row["current"]) if row["current"] is not None else None
        queries.append({
            "sql": "INSERT OR IGNORE INTO price_snapshots(household_id,sailing_id,booking_id,product_id,source,passenger_class,observed_price,currency,normalized_total,promotion_label,promotion_end_at,observed_at,raw_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            "params": [profile.label, base.SAILING_ID, bid, product_id, source, row["traveler_role"], row["current"], row["currency"], current_total, row["promotion"], None, row["observed_at"], f"purchase_paid_unit={row['paid']:.2f};no_longer_for_sale={1 if row['no_longer_for_sale'] else 0}"],
        })
    base.d1_request(queries)
    return len(purchases)


def previous_row(state: HistoryState, source: str, product_id: str, role: str) -> dict[str, Any] | None:
    return state.previous.get((source, product_id, role))


def meaningful_drop(profile: base.Profile, old_price: float, new_price: float, prefix: str) -> tuple[bool, float]:
    try:
        threshold = float(profile.threshold)
    except ValueError as exc:
        raise SystemExit(f"{profile.label} minimum savings threshold must be numeric") from exc
    savings = round(base.normalized_total(prefix, old_price) - base.normalized_total(prefix, new_price), 2)
    return savings + EPSILON >= threshold, savings


def build_alerts(profile: base.Profile, snapshot: dict[str, Any], purchases: list[dict[str, Any]], state: HistoryState) -> list[dict[str, Any]]:
    if not state.available:
        return []
    alerts: list[dict[str, Any]] = []

    paid_fare = float(profile.paid_price) if profile.paid_price else None
    fare = snapshot.get("cabin_fare")
    fare_product = f"cruise-fare:{profile.cabin_category or 'unknown'}:all"
    if paid_fare is not None and fare is not None and paid_fare - fare >= base.FARE_ALERT_SAVINGS:
        old = previous_row(state, "authenticated_royal", fare_product, "all")
        historical_min = float(old.get("historical_min")) if old and old.get("historical_min") is not None else None
        if historical_min is None or fare < historical_min - EPSILON:
            alerts.append({"kind": "fare", "product_id": fare_product, "name": f"Cabin {profile.cabin} fare", "current": fare, "savings": round(paid_fare - fare, 2)})

    for row in snapshot["products"]:
        spec = base.SPEC_BY_ID.get(row["product_id"])
        if not spec or row["traveler_role"] not in spec.audience:
            continue
        old = previous_row(state, "authenticated_royal", row["product_id"], row["traveler_role"])
        if not old or old.get("historical_min") is None:
            continue  # first personalized observation establishes the baseline
        prior_low = float(old["historical_min"])
        if row["current"] >= prior_low - EPSILON:
            continue
        is_meaningful, savings = meaningful_drop(profile, prior_low, row["current"], spec.prefix)
        if not is_meaningful:
            continue
        alerts.append({
            "kind": "watch_new_low", "product_id": spec.product_id, "spec": spec,
            "role": row["traveler_role"], "current": row["current"], "previous_low": prior_low,
            "savings": savings, "promotion": row.get("promotion"),
        })

    for row in purchases:
        if row["current"] is None or row["current"] >= row["paid"] - EPSILON:
            continue
        savings = round(purchase_total(row, row["paid"]) - purchase_total(row, row["current"]), 2)
        try:
            threshold = float(profile.threshold)
        except ValueError as exc:
            raise SystemExit(f"{profile.label} minimum savings threshold must be numeric") from exc
        if savings + EPSILON < threshold:
            continue
        old = previous_row(state, "authenticated_royal_purchase", row["product_id"], row["traveler_role"])
        prior_market_low = float(old["historical_min"]) if old and old.get("historical_min") is not None else None
        if prior_market_low is not None and row["current"] >= prior_market_low - EPSILON:
            continue
        alerts.append({
            "kind": "purchase_reprice", "product_id": row["product_id"], "name": row["name"],
            "role": row["traveler_role"], "paid": row["paid"], "current": row["current"],
            "savings": savings, "promotion": row.get("promotion"), "known_spec": row["known_spec"],
            "per_day": row.get("per_day", False),
        })
    return alerts


def product_link(profile: base.Profile, product_id: str) -> str | None:
    spec = base.SPEC_BY_ID.get(product_id)
    return base.royal_product_url(profile, spec) if spec else None


def consolidate_alerts(profile: base.Profile, alerts: list[dict[str, Any]]) -> tuple[str, str]:
    title = f"Royal deal alert — {base.SHIP_NAME} {base.SAIL_DATE}"
    lines = [f"{base.SHIP_NAME} · {base.SAIL_DATE} · {base.NIGHTS} nights", f"Cabin {profile.cabin}", ""]

    fare = next((item for item in alerts if item["kind"] == "fare"), None)
    if fare:
        lines += [
            f"CABIN FARE NEW LOW: ${fare['current']:.2f}",
            f"Potential savings vs booked total: ${fare['savings']:.2f}",
            f"Verify/reprice manually while preserving cabin {profile.cabin}.", "",
        ]

    grouped: dict[tuple[str, str, float, str | None], list[dict[str, Any]]] = {}
    for item in alerts:
        if item["kind"] == "fare":
            continue
        key = (item["kind"], item["product_id"], item["current"], item.get("promotion"))
        grouped.setdefault(key, []).append(item)

    for (kind, product_id, current, promo), rows in grouped.items():
        roles = ", ".join(base.ROLE_LABELS.get(row["role"], row["role"]) for row in rows)
        total_savings = sum(float(row["savings"]) for row in rows)
        if kind == "purchase_reprice":
            name = rows[0]["name"]
            paid = rows[0]["paid"]
            traveler_count = len(rows)
            unit_delta = round(float(paid) - float(current), 2)
            per_day = all(bool(row.get("per_day")) for row in rows)
            unit_label = "per guest per day" if per_day else "per unit"
            if per_day:
                base_math = f"${unit_delta:.2f}/day × {base.NIGHTS} cruise days × {traveler_count} traveler{'s' if traveler_count != 1 else ''}"
            else:
                base_math = f"${unit_delta:.2f} × {traveler_count} traveler{'s' if traveler_count != 1 else ''}"
            lines += [
                f"REBOOK OPPORTUNITY — {name}",
                f"Current Royal base price: ${current:.2f} {unit_label} ({roles})",
                f"Recorded purchase base price: ${paid:.2f} {unit_label}",
                f"Whole-party base savings: ${total_savings:.2f} ({base_math}).",
            ]
            spec = base.SPEC_BY_ID.get(product_id)
            category = base.classify(spec.prefix)[0] if spec else ""
            if category in {"beverage", "dining"}:
                estimated_checkout_savings = round(total_savings * 1.18, 2)
                lines.append(
                    f"Estimated checkout savings with Royal's current 18% package gratuity: about ${estimated_checkout_savings:.2f}."
                )
            link = product_link(profile, product_id)
            if link:
                lines.append(link)
            else:
                lines.append(f"https://www.royalcaribbean.com/account/cruise-planner/order-history?bookingId={profile.reservation_id}&shipCode={base.SHIP_CODE}&sailDate={base.SAIL_DATE.replace("-", "")}")
        else:
            spec = rows[0]["spec"]
            prior_low = rows[0]["previous_low"]
            lines += [
                f"NEW LOW — {spec.name}: ${current:.2f} ({roles})",
                f"Previous best: ${prior_low:.2f}; affected-traveler base savings vs previous best: ${total_savings:.2f}",
                base.royal_product_url(profile, spec),
            ]
        if promo:
            lines.append(f"Promo: {promo}")
        lines.append("")

    lines.append("No purchase, cancellation, or rebooking was performed automatically.")
    return title, "\n".join(lines)


def persist_notification_events(profile: base.Profile, alerts: list[dict[str, Any]]) -> None:
    if not alerts:
        return
    queries: list[dict[str, Any]] = []
    sent_at = utc_now().isoformat()
    for item in alerts:
        event_type = item["kind"]
        summary = f"current={item['current']:.2f};savings={float(item.get('savings') or 0):.2f}"
        queries.append({
            "sql": "INSERT INTO notification_events(household_id,sailing_id,product_id,event_type,summary,sent_at) VALUES(?,?,?,?,?,?)",
            "params": [profile.label, base.SAILING_ID, item.get("product_id"), event_type, summary, sent_at],
        })
    base.d1_request(queries)


def append_summary(profile: base.Profile, mode: str, selected: list[base.WatchSpec], snapshot: dict[str, Any], watch_written: int | None, purchase_written: int | None, purchase_count: int, alerts: int, email_ok: bool | None, d1_warning: bool) -> None:
    path = base.env("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as out:
        out.write(f"\n### {profile.label} household\n")
        out.write(f"- Scan mode: {mode}\n")
        if selected:
            out.write(f"- Unpurchased watch products selected: {len(selected)}\n")
            out.write("- Selected: " + ", ".join(spec.name for spec in selected) + "\n")
        out.write(f"- Watchlist traveler/product observations parsed: {len(snapshot['products'])}\n")
        out.write(f"- Purchased-order observations parsed: {purchase_count}\n")
        if snapshot.get("cabin_fare") is not None:
            out.write(f"- Comparable cabin fare observed: ${snapshot['cabin_fare']:.2f}\n")
        if watch_written is not None:
            out.write(f"- D1 watch/fare history: PASS ({watch_written} observations written)\n")
        if purchase_written is not None:
            out.write(f"- D1 purchased-item history: PASS ({purchase_written} purchased items recorded)\n")
        if d1_warning:
            out.write("- D1 history/state: WARNING (Royal check still completed)\n")
        out.write(f"- Consolidated meaningful alerts: {alerts}\n")
        if email_ok is not None:
            out.write(f"- Consolidated email: {'PASS' if email_ok else 'WARNING'}\n")


def main() -> int:
    profiles = base.load_profiles()
    base.require("primary Royal username", profiles[0].username)
    base.require("primary Royal password", profiles[0].password)

    if base.truthy(base.env("NOTIFICATION_TEST")):
        ok = base.send_apprise(profiles[0], "Royal cruise tracker test", "Notification path is working. No Royal price check was run.")
        return 0 if ok else 1

    exit_code = 0
    for profile in profiles:
        state = history_state(profile)
        if not state.available:
            print(f"::warning::Could not load adaptive D1 state for {profile.label}; using bounded fallback: {state.error}")
        cfg, watchlist, mode, selected = build_config(profile, state)

        with tempfile.TemporaryDirectory(prefix=f"royal-{profile.label}-") as tmp:
            status, lines = base.run_upstream(profile, cfg, Path(tmp) / "config.json")
        if status != 0:
            exit_code = status
            if profile.apprise_url:
                base.send_apprise(profile, "Royal cruise tracker error", f"The {profile.label} Royal check failed. Review the protected workflow run.")
            continue

        snapshot = base.parse_history(profile, watchlist, lines)
        snapshot, quarantined_watch_rows = reconcile_watch_snapshot(snapshot, state)
        if quarantined_watch_rows:
            print(f"{profile.label} Royal child-tier plausibility guard: {quarantined_watch_rows} suspect observation(s) omitted")
        purchases = parse_purchases(profile, lines, snapshot["observed_at"])
        alerts = build_alerts(profile, snapshot, purchases, state)

        suppress = base.truthy(base.env("SUPPRESS_ALERTS"))
        email_ok: bool | None = None
        if alerts and not suppress:
            title, body = consolidate_alerts(profile, alerts)
            email_ok = base.send_apprise(profile, title, body)
            if email_ok:
                try:
                    persist_notification_events(profile, alerts)
                except Exception as exc:
                    print(f"::warning::Notification history write failed for {profile.label}: {exc}")
        elif alerts and suppress:
            print(f"{profile.label} proof mode suppressed {len(alerts)} otherwise-meaningful alert(s)")

        watch_written: int | None = None
        purchase_written: int | None = None
        d1_warning = not state.available
        try:
            watch_written = base.persist_history(profile, snapshot)
        except Exception as exc:
            d1_warning = True
            print(f"::warning::D1 watch/fare history write failed for {profile.label}: {exc}")
        try:
            purchase_written = persist_purchases(profile, purchases)
        except Exception as exc:
            d1_warning = True
            print(f"::warning::D1 purchased-item history write failed for {profile.label}: {exc}")

        append_summary(profile, mode, selected, snapshot, watch_written, purchase_written, len(purchases), len(alerts), email_ok, d1_warning)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())

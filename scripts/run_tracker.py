#!/usr/bin/env python3
"""Low-overhead Royal monitor with sanitized D1 history and deduplicated alerts.

Royal credentials, reservation identifiers, Apprise URLs, and passenger names remain ephemeral.
Cloudflare D1 stores pseudonymous traveler roles, sailing/cabin metadata, product IDs, prices,
promotions, and timestamps only. A D1 or notification failure never suppresses a successful
Royal check.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from runtime_profile import load_runtime_config

ANSI = re.compile(r"\x1b\[[0-9;]*m")
WATCH_HIGHER = re.compile(
    r"\[WATCH\]\s+(?P<passenger>.+?)\s+\(Cabin\s+(?P<cabin>\d+)\)\s+"
    r"(?P<name>.+?)\s+price is higher than watch price:\s+(?P<target>[0-9.]+)\s+USD"
    r"(?:.*?\(now\s+(?P<current>[0-9.]+)\s+USD\))?",
    re.I,
)
WATCH_BOOK = re.compile(
    r"\[WATCH\]\s+(?P<passenger>.+?)\s+\(Cabin\s+(?P<cabin>\d+)\):\s+Book!\s+"
    r"(?P<name>.+?)\s+Price is lower:\s+(?P<current>[0-9.]+)\s+USD\s+than\s+"
    r"(?P<target>[0-9.]+)\s+USD",
    re.I,
)
RUNTIME_CONFIG = load_runtime_config()
_SAILING = RUNTIME_CONFIG["sailing"]
SAILING_ID = str(_SAILING["id"])
SHIP_NAME = str(_SAILING["ship_name"])
SHIP_CODE = str(_SAILING["ship_code"])
SAIL_DATE = str(_SAILING["sail_date"])
NIGHTS = int(_SAILING.get("nights", 1))
ITINERARY = str(_SAILING.get("itinerary", ""))
COLLECTOR_PRICE = float(RUNTIME_CONFIG.get("collector_price", 0.01))
FARE_ALERT_SAVINGS = float(RUNTIME_CONFIG.get("fare_alert_savings", 25.0))
LOCAL_TIMEZONE = str(RUNTIME_CONFIG.get("local_timezone", "UTC"))
FAILURE_DIAGNOSTIC_LINES = 80
ROLE_LABELS = {
    str(key): str(value)
    for key, value in dict(RUNTIME_CONFIG.get("traveler_role_labels", {})).items()
}

try:
    _fare_date = datetime.fromisoformat(SAIL_DATE).strftime("%m/%d/%y")
except ValueError as exc:
    raise ValueError("sailing.sail_date must use YYYY-MM-DD") from exc

FARE_BEST = re.compile(
    rf"{re.escape(_fare_date)}\s+{re.escape(SHIP_NAME)}\s+BALCONY\s+(?P<category>\S+).*?"
    r"You have the best price of\s+(?P<price>[0-9.]+)\s+USD",
    re.I,
)

def truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def require(name: str, value: str) -> None:
    if not value:
        raise SystemExit(f"Required value {name} is not configured")


@dataclass(frozen=True)
class WatchSpec:
    name: str
    prefix: str
    product: str
    age_field: str
    audience: tuple[str, ...]
    alert_below: float | None = None

    @property
    def product_id(self) -> str:
        return f"{self.prefix}:{self.product}:{self.age_field}"


def _watch_spec_from_config(item: dict[str, Any]) -> WatchSpec:
    raw_alert = item.get("alert_below")
    alert = float(raw_alert) if raw_alert is not None else None
    return WatchSpec(
        name=str(item["name"]),
        prefix=str(item["prefix"]),
        product=str(item["product"]),
        age_field=str(item["age_field"]),
        audience=tuple(str(value) for value in item.get("audience", [])),
        alert_below=alert,
    )


WATCH_SPECS: tuple[WatchSpec, ...] = tuple(
    _watch_spec_from_config(item) for item in RUNTIME_CONFIG["watch_specs"]
)
SPEC_BY_ID = {spec.product_id: spec for spec in WATCH_SPECS}

_watch_groups = RUNTIME_CONFIG.get("watch_groups", {})
WATCH_GROUPS: dict[str, tuple[WatchSpec, ...]] = {}
for group_name, ids in dict(_watch_groups).items():
    selected = tuple(SPEC_BY_ID[item_id] for item_id in ids if item_id in SPEC_BY_ID)
    if selected:
        WATCH_GROUPS[str(group_name).upper()] = selected
if not WATCH_GROUPS:
    WATCH_GROUPS["A"] = WATCH_SPECS


@dataclass
class Profile:
    label: str
    display_name: str
    username: str
    password: str
    reservation_id: str
    paid_price: str
    apprise_url: str
    threshold: str
    watchlist_json: str
    eligible_military: bool
    booked_military: bool
    refundable: bool
    use_default_watchlist: bool
    cabin: str
    cabin_category: str
    traveler_roles: dict[str, str]


def _profile_env_name(spec: dict[str, Any], key: str) -> str:
    return str(spec.get(key, "") or "").strip()


def _profile_env_value(spec: dict[str, Any], key: str, default: str = "") -> str:
    name = _profile_env_name(spec, key)
    return env(name, default) if name else default


def load_profiles() -> list[Profile]:
    profiles: list[Profile] = []
    for spec in RUNTIME_CONFIG["profiles"]:
        enabled_env = _profile_env_name(spec, "enabled_env")
        enabled = truthy(env(enabled_env)) if enabled_env else bool(spec.get("enabled", True))
        if not enabled:
            continue

        profiles.append(Profile(
            label=str(spec["label"]),
            display_name=str(spec.get("display_name") or spec["label"]),
            username=_profile_env_value(spec, "username_env"),
            password=_profile_env_value(spec, "password_env"),
            reservation_id=_profile_env_value(spec, "reservation_id_env"),
            paid_price=_profile_env_value(spec, "paid_price_env"),
            apprise_url=_profile_env_value(spec, "apprise_url_env"),
            threshold=_profile_env_value(spec, "threshold_env", "10.00"),
            watchlist_json=_profile_env_value(spec, "watchlist_env"),
            eligible_military=bool(spec.get("eligible_military", False)),
            booked_military=bool(spec.get("booked_military", False)),
            refundable=bool(spec.get("refundable", False)),
            use_default_watchlist=bool(spec.get("use_default_watchlist", False)),
            cabin=str(spec.get("cabin") or ""),
            cabin_category=str(spec.get("cabin_category") or ""),
            traveler_roles={
                str(key): str(value)
                for key, value in dict(spec.get("traveler_roles", {})).items()
            },
        ))
    if not profiles:
        raise SystemExit("No enabled Royal profiles are configured")
    return profiles


def resolve_scan_group() -> str:
    requested = env("SCAN_GROUP", "auto").strip().upper()
    if requested in WATCH_GROUPS:
        return requested
    if requested not in {"", "AUTO"}:
        raise SystemExit("SCAN_GROUP must be auto, A, B, or C")
    hour = datetime.now(ZoneInfo(LOCAL_TIMEZONE)).hour
    if hour < 8:
        return "A"
    if hour < 16:
        return "B"
    return "C"


def default_watchlist(reservation: str) -> list[dict[str, Any]]:
    group = resolve_scan_group()
    specs = WATCH_GROUPS[group]
    print(f"Configured add-on scan group {group}: {len(specs)} products")
    return [{
        "name": f"{spec.name} [{spec.age_field}]", "prefix": spec.prefix,
        "product": spec.product, "price": COLLECTOR_PRICE, "enabled": True,
        "guestAgeString": spec.age_field, "reservations": [reservation],
    } for spec in specs]


def build_config(profile: Profile) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    require(f"{profile.label} Royal username", profile.username)
    require(f"{profile.label} Royal password", profile.password)
    if bool(profile.reservation_id) != bool(profile.paid_price):
        raise SystemExit(f"{profile.label} must provide both reservation ID and paid price, or neither")
    if profile.use_default_watchlist and not profile.reservation_id:
        raise SystemExit(f"{profile.label} default watchlist requires a reservation ID")

    cfg: dict[str, Any] = {
        "accountInfo": [{"username": "${RCCL_USERNAME}", "password": "${RCCL_PASSWORD}", "cruiseLine": "royal", "military": profile.eligible_military}],
        "displayCruisePrices": True,
        "minimumSavingAlert": 999999.00,
        "notifyOnError": False,
        "requestTimeout": 60,
        "showPromos": False,
    }
    if profile.reservation_id:
        override: dict[str, Any] = {
            "reservation": "${RCCL_RESERVATION_ID}", "paidPrice": "${RCCL_PAID_PRICE}",
            "gratuities": False, "tripInsurance": False, "refundable": profile.refundable,
        }
        if profile.booked_military:
            override["military"] = True
        cfg["reservationPricePaid"] = [override]

    watchlist: list[dict[str, Any]] = []
    if profile.watchlist_json.strip():
        try:
            loaded = json.loads(profile.watchlist_json)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{profile.label} watchlist repository variable is not valid JSON") from exc
        if not isinstance(loaded, list):
            raise SystemExit(f"{profile.label} watchlist repository variable must contain a JSON array")
        watchlist = loaded
    elif profile.use_default_watchlist:
        watchlist = default_watchlist("${RCCL_RESERVATION_ID}")
    if watchlist:
        cfg["watchList"] = watchlist
    return cfg, watchlist


def redact_failure_line(profile: Profile, line: str) -> str:
    value = ANSI.sub("", line.rstrip("\n"))
    if profile.reservation_id:
        value = value.replace(profile.reservation_id, "[reservation]")
    value = re.sub(r"Reservation\s+#\S+", "Reservation #[redacted]", value, flags=re.I)
    value = re.sub(r"\(In this cabin:.*?\)", "(travelers redacted)", value, flags=re.I)
    value = re.sub(r"(bookingId=)[^&\s]+", r"\1[redacted]", value, flags=re.I)
    value = re.sub(r"Using Royal Caribbean for user\s+\S+", "Using Royal Caribbean for user [redacted]", value, flags=re.I)
    value = re.sub(r"(\[WATCH\]\s+).+?(\s+\(Cabin\s+\d+\))", r"\1traveler\2", value)
    return value


def run_upstream(profile: Profile, cfg: dict[str, Any], config_path: Path) -> tuple[int, list[str]]:
    config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    image = env("UPSTREAM_IMAGE")
    require("UPSTREAM_IMAGE", image)
    child_env = os.environ.copy()
    child_env.update({
        "RCCL_USERNAME": profile.username, "RCCL_PASSWORD": profile.password,
        "RCCL_RESERVATION_ID": profile.reservation_id, "RCCL_PAID_PRICE": profile.paid_price,
        "TZ": "America/New_York",
    })
    cmd = [
        "docker", "run", "--rm", "-e", "RCCL_USERNAME", "-e", "RCCL_PASSWORD",
        "-e", "RCCL_RESERVATION_ID", "-e", "RCCL_PAID_PRICE", "-e", "TZ",
        "-v", f"{config_path}:/app/config.yaml:ro", image, "check",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=child_env)
    lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        lines.append(ANSI.sub("", line.rstrip("\n")))
    status = proc.wait()
    if status != 0:
        print(f"::error::{profile.label} Royal checker exited with status {status}")
        print("--- redacted Royal failure diagnostics ---")
        for line in lines[-FAILURE_DIAGNOSTIC_LINES:]:
            print(redact_failure_line(profile, line))
        print("--- end redacted diagnostics ---")
    else:
        print(f"{profile.label} Royal collector completed successfully ({len(lines)} output lines captured privately in runner memory)")
    return status, lines


def send_apprise(profile: Profile, title: str, body: str) -> bool:
    if not profile.apprise_url:
        return False
    image = env("UPSTREAM_IMAGE")
    child_env = os.environ.copy()
    child_env.update({"APPRISE_URL": profile.apprise_url, "APPRISE_TITLE": title, "APPRISE_BODY": body})
    code = (
        "import os,sys; from apprise import Apprise,NotifyFormat; "
        "a=Apprise(); a.add(os.environ['APPRISE_URL']); "
        "ok=a.notify(title=os.environ['APPRISE_TITLE'],body=os.environ['APPRISE_BODY'],body_format=NotifyFormat.TEXT); "
        "sys.exit(0 if ok else 1)"
    )
    try:
        result = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "python", "-e", "APPRISE_URL", "-e", "APPRISE_TITLE", "-e", "APPRISE_BODY", image, "-c", code],
            env=child_env, timeout=45, check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"::warning::{profile.label} consolidated email timed out")
        return False
    if result.returncode != 0:
        print(f"::warning::{profile.label} consolidated email failed")
        return False
    return True


def stable_traveler_alias(profile: Profile, passenger: str) -> str:
    digest = hashlib.sha256(f"{profile.label}\0{passenger}".encode("utf-8")).hexdigest()[:10]
    return f"traveler_{digest}"


def traveler_role(profile: Profile, passenger: str) -> str:
    if passenger in profile.traveler_roles:
        return profile.traveler_roles[passenger]
    return stable_traveler_alias(profile, passenger)


def token_words(value: str) -> set[str]:
    stop = {"package", "full", "cruise", "day", "pass", "adult", "child", "price", "field"}
    return {w for w in re.findall(r"[a-z0-9]+", value.lower()) if len(w) > 2 and w not in stop}


PRODUCT_IDENTITY_WORDS = frozenset({
    "deluxe", "beverage", "refreshment", "soda", "openbar", "nonalcoholic",
    "thrill", "waterpark", "voom", "dining", "key",
})


def product_identity_markers(value: str) -> set[str]:
    """Return identity-bearing Royal product markers, ignoring generic marketing words."""
    normalized = str(value or "").lower()
    normalized = re.sub(r"\bnon[\s-]+alcoholic\b", " nonalcoholic ", normalized)
    normalized = re.sub(r"\bopen[\s-]+bar\b", " openbar ", normalized)
    words = set(re.findall(r"[a-z0-9]+", normalized))
    markers = words & PRODUCT_IDENTITY_WORDS
    device = re.search(r"\b([1-4])\s+devices?\b", normalized)
    if device:
        markers.add(f"device{device.group(1)}")
    return markers


def match_watch_spec(actual_name: str, candidates: list[WatchSpec]) -> WatchSpec | None:
    """Return one unambiguous product match; ambiguous or contradictory names fail closed."""
    if not candidates:
        return None
    actual_markers = product_identity_markers(actual_name)
    actual_core = {m for m in actual_markers if not m.startswith("device")}
    actual_device = {m for m in actual_markers if m.startswith("device")}
    actual_words = token_words(actual_name)
    ranked: list[tuple[tuple[int, int, int], WatchSpec]] = []
    for spec in candidates:
        spec_markers = product_identity_markers(spec.name)
        spec_core = {m for m in spec_markers if not m.startswith("device")}
        spec_device = {m for m in spec_markers if m.startswith("device")}
        if actual_core or spec_core:
            if actual_core != spec_core:
                continue
            if actual_device and actual_device != spec_device:
                continue
        else:
            spec_words = token_words(spec.name)
            overlap = actual_words & spec_words
            required = max(1, min(2, len(spec_words)))
            if len(overlap) < required:
                continue
        spec_words = token_words(spec.name)
        score = (
            len(spec_core),
            1 if actual_device and actual_device == spec_device else 0,
            len(actual_words & spec_words),
        )
        ranked.append((score, spec))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    best = ranked[0][0]
    winners = [spec for score, spec in ranked if score == best]
    return winners[0] if len(winners) == 1 else None


def classify(prefix: str) -> tuple[str, str]:
    if prefix == "beverage": return "beverage", "per_cruise_day"
    if prefix == "packages": return "bundle", "per_cruise_day"
    if prefix in {"cococay", "royalbeachclub"}: return "destination", "per_admission"
    if prefix == "internet": return "internet", "per_cruise_day"
    if prefix == "dining": return "dining", "per_cruise_day"
    if prefix == "key": return "key", "per_cruise_day"
    return prefix or "other", "unknown"


def spec_from_watch_item(item: dict[str, Any]) -> WatchSpec:
    prefix = str(item.get("prefix") or "unknown")
    product = str(item.get("product") or "unknown")
    age = str(item.get("guestAgeString") or "adult")
    known = SPEC_BY_ID.get(f"{prefix}:{product}:{age}")
    if known: return known
    name = re.sub(r"\s*\[(?:adult|child)\]\s*", " ", str(item.get("name") or product)).strip()
    return WatchSpec(name, prefix, product, age, tuple(), None)


def parse_history(profile: Profile, watchlist: list[dict[str, Any]], lines: list[str]) -> dict[str, Any]:
    events_by_passenger: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pending: dict[str, Any] | None = None
    cabin_fare: float | None = None
    for line in lines:
        fare = FARE_BEST.search(line)
        if fare and (not profile.cabin_category or fare.group("category").upper() == profile.cabin_category.upper()):
            cabin_fare = float(fare.group("price"))
        match = WATCH_BOOK.search(line) or WATCH_HIGHER.search(line)
        if match and (not profile.cabin or match.group("cabin") == profile.cabin):
            event = {
                "passenger": match.group("passenger").strip(), "actual_name": match.group("name").strip(),
                "current": float(match.groupdict().get("current")) if match.groupdict().get("current") else None,
                "promotion": None,
            }
            events_by_passenger[event["passenger"]].append(event)
            pending = event if WATCH_BOOK.search(line) else None
            continue
        if pending and "Promotion:" in line:
            pending["promotion"] = line.split("Promotion:", 1)[1].strip()
            pending = None

    observations: list[dict[str, Any]] = []
    specs = [spec_from_watch_item(item) for item in watchlist]
    for passenger, events in events_by_passenger.items():
        role = traveler_role(profile, passenger)
        candidate_specs = [spec for spec in specs if not spec.audience or role in spec.audience]
        assignments: list[tuple[dict[str, Any], WatchSpec | None]] = []
        remaining = list(candidate_specs)
        for event in events:
            spec = match_watch_spec(event["actual_name"], remaining)
            assignments.append((event, spec))
            if spec is not None:
                remaining = [candidate for candidate in remaining if candidate != spec]
        for event, spec in assignments:
            if not spec or event["current"] is None: continue
            observations.append({
                "product_id": spec.product_id, "prefix": spec.prefix, "product_code": spec.product,
                "age_field": spec.age_field, "name": spec.name, "traveler_role": role,
                "current": event["current"], "promotion": event["promotion"],
            })
    return {"observed_at": datetime.now(timezone.utc).isoformat(), "cabin_fare": cabin_fare, "products": observations}


def d1_request(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    token, account, database = env("CLOUDFLARE_D1_TOKEN"), env("CLOUDFLARE_ACCOUNT_ID"), env("CLOUDFLARE_D1_DATABASE_ID")
    if not token or not account or not database: raise RuntimeError("D1 credential/account/database is not configured")
    endpoint = f"https://api.cloudflare.com/client/v4/accounts/{account}/d1/database/{database}/query"
    request = urllib.request.Request(endpoint, data=json.dumps({"batch": batch}).encode("utf-8"), headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response: result = json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Cloudflare D1 API HTTP {exc.code}: {body[:600]}") from exc
    if not result.get("success"): raise RuntimeError(f"Cloudflare D1 API failure: {result.get('errors')}")
    rows = result.get("result") or []
    if any(not row.get("success", False) for row in rows): raise RuntimeError("one or more D1 batch statements failed")
    return rows


def booking_id(profile: Profile) -> str:
    return f"{profile.label}:{SAILING_ID}:{profile.cabin or 'unknown'}"


def load_previous(profile: Profile) -> dict[tuple[str, str], dict[str, Any]]:
    sql = """
    SELECT ps.product_id, ps.passenger_class, ps.observed_price, ps.promotion_label
      FROM price_snapshots ps
      JOIN (SELECT product_id, passenger_class, MAX(id) AS max_id FROM price_snapshots
             WHERE household_id=? AND sailing_id=? AND booking_id=? AND source='authenticated_royal'
             GROUP BY product_id, passenger_class) latest ON latest.max_id = ps.id
    """
    rows = d1_request([{"sql": sql, "params": [profile.label, SAILING_ID, booking_id(profile)]}])
    previous: dict[tuple[str, str], dict[str, Any]] = {}
    for result in rows:
        for row in result.get("results") or []:
            previous[(row["product_id"], row.get("passenger_class") or "all")] = row
    return previous


def normalized_total(prefix: str, price: float) -> float:
    _category, unit = classify(prefix)
    return round(price * NIGHTS, 2) if unit == "per_cruise_day" else round(price, 2)


def persist_history(profile: Profile, snapshot: dict[str, Any]) -> int:
    bid = booking_id(profile)
    queries: list[dict[str, Any]] = [
        {"sql": "INSERT INTO households(id,display_name,active) VALUES(?,?,1) ON CONFLICT(id) DO UPDATE SET display_name=excluded.display_name,active=1", "params": [profile.label, profile.display_name]},
        {"sql": "INSERT INTO sailings(id,cruise_line,ship_code,ship_name,sail_date,nights,itinerary_label) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET ship_name=excluded.ship_name,sail_date=excluded.sail_date,nights=excluded.nights,itinerary_label=excluded.itinerary_label", "params": [SAILING_ID, "royal", SHIP_CODE, SHIP_NAME, SAIL_DATE, NIGHTS, ITINERARY]},
        {"sql": "INSERT INTO bookings(id,household_id,sailing_id,cabin_number,cabin_category,booked_total,booked_currency,refundable,active) VALUES(?,?,?,?,?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET cabin_number=excluded.cabin_number,cabin_category=excluded.cabin_category,booked_total=excluded.booked_total,refundable=excluded.refundable,active=1", "params": [bid, profile.label, SAILING_ID, profile.cabin or None, profile.cabin_category or None, float(profile.paid_price) if profile.paid_price else None, "USD", 1 if profile.refundable else 0]},
    ]
    observed_at, count, seen = snapshot["observed_at"], 0, set()
    for row in snapshot["products"]:
        key = (row["product_id"], row["traveler_role"])
        if key in seen: continue
        seen.add(key)
        category, price_unit = classify(row["prefix"])
        queries.append({"sql": "INSERT INTO products(id,product_code,prefix,name,price_unit,category,age_scope) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,price_unit=excluded.price_unit,category=excluded.category", "params": [row["product_id"], row["product_code"], row["prefix"], row["name"], price_unit, category, row["age_field"]]})
        queries.append({"sql": "INSERT OR IGNORE INTO price_snapshots(household_id,sailing_id,booking_id,product_id,source,passenger_class,observed_price,currency,normalized_total,promotion_label,promotion_end_at,observed_at,raw_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", "params": [profile.label, SAILING_ID, bid, row["product_id"], "authenticated_royal", row["traveler_role"], row["current"], "USD", normalized_total(row["prefix"], row["current"]), row["promotion"], None, observed_at, f"age_field={row['age_field']}"]})
        count += 1
    if snapshot.get("cabin_fare") is not None:
        fare_product = f"cruise-fare:{profile.cabin_category or 'unknown'}:all"
        queries.extend([
            {"sql": "INSERT INTO products(id,product_code,prefix,name,price_unit,category,age_scope) VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name", "params": [fare_product, profile.cabin_category or "unknown", "cruise-fare", f"Cabin fare — {profile.cabin_category or 'category'}", "per_booking", "cruise_fare", "all"]},
            {"sql": "INSERT OR IGNORE INTO price_snapshots(household_id,sailing_id,booking_id,product_id,source,passenger_class,observed_price,currency,normalized_total,promotion_label,promotion_end_at,observed_at,raw_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", "params": [profile.label, SAILING_ID, bid, fare_product, "authenticated_royal", "all", snapshot["cabin_fare"], "USD", snapshot["cabin_fare"], None, None, observed_at, "comparable_cabin_fare"]},
        ])
        count += 1
    d1_request(queries)
    return count


def royal_product_url(profile: Profile, spec: WatchSpec) -> str:
    return f"https://www.royalcaribbean.com/account/cruise-planner/category/{spec.prefix}/product/{spec.product}?bookingId={profile.reservation_id}&shipCode={SHIP_CODE}&sailDate={SAIL_DATE.replace("-", "")}"


def build_alerts(profile: Profile, snapshot: dict[str, Any], previous: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    paid, fare = (float(profile.paid_price) if profile.paid_price else None), snapshot.get("cabin_fare")
    fare_key = (f"cruise-fare:{profile.cabin_category or 'unknown'}:all", "all")
    if paid is not None and fare is not None and paid - fare >= FARE_ALERT_SAVINGS:
        old = previous.get(fare_key)
        if old is None or fare < float(old.get("observed_price") or 1e99) - 0.009:
            alerts.append({"kind": "fare", "name": f"Cabin {profile.cabin} fare", "current": fare, "savings": round(paid - fare, 2)})
    by_key = {(row["product_id"], row["traveler_role"]): row for row in snapshot["products"]}
    for (product_id, role), row in by_key.items():
        spec = SPEC_BY_ID.get(product_id)
        if not spec or spec.alert_below is None or role not in spec.audience or row["current"] > spec.alert_below + 0.009: continue
        old = previous.get((product_id, role))
        if old is None: continue
        old_price = float(old.get("observed_price") or 1e99)
        if row["current"] >= old_price - 0.009: continue
        alerts.append({"kind": "addon", "spec": spec, "role": role, "current": row["current"], "previous": old_price, "promotion": row["promotion"], "whole_trip": normalized_total(spec.prefix, row["current"])})
    return alerts


def consolidate_alerts(profile: Profile, alerts: list[dict[str, Any]]) -> tuple[str, str]:
    title = f"Royal deal alert — {SHIP_NAME} {SAIL_DATE}"
    lines = [f"{SHIP_NAME} · {SAIL_DATE} · {NIGHTS} nights", f"Cabin {profile.cabin}", ""]
    fares = [a for a in alerts if a["kind"] == "fare"]
    if fares:
        a = fares[0]
        lines += [f"CABIN FARE DROP: ${a['current']:.2f}", f"Potential savings vs booked total: ${a['savings']:.2f}", f"Verify/reprice manually while preserving cabin {profile.cabin}.", ""]
    grouped: dict[tuple[str, float, str | None], list[dict[str, Any]]] = defaultdict(list)
    for a in alerts:
        if a["kind"] == "addon": grouped[(a["spec"].product_id, a["current"], a.get("promotion"))].append(a)
    for (_pid, current, promo), rows in grouped.items():
        spec = rows[0]["spec"]
        roles = ", ".join(ROLE_LABELS.get(r["role"], r["role"]) for r in rows)
        lines += [f"{spec.name}: ${current:.2f} ({roles})", f"Whole-cruise/admission estimate per selected traveler: ${rows[0]['whole_trip']:.2f}"]
        if promo: lines.append(f"Promo: {promo}")
        lines += [royal_product_url(profile, spec), ""]
    lines.append("No purchase/cancel/rebook was performed automatically.")
    return title, "\n".join(lines)


def append_summary(profile: Profile, snapshot: dict[str, Any], persisted: int | None, alerts: int, email_ok: bool | None, d1_error: str | None) -> None:
    path = env("GITHUB_STEP_SUMMARY")
    if not path: return
    with open(path, "a", encoding="utf-8") as out:
        out.write(f"\n### {profile.label} household\n")
        if profile.use_default_watchlist and not profile.watchlist_json.strip():
            out.write(f"- Add-on scan group: {resolve_scan_group()} (6 products; every default add-on checked once daily)\n")
        out.write(f"- Traveler/product observations parsed: {len(snapshot['products'])}\n")
        if snapshot.get("cabin_fare") is not None: out.write(f"- Comparable cabin fare observed: ${snapshot['cabin_fare']:.2f}\n")
        if persisted is not None: out.write(f"- D1 history: PASS ({persisted} observations written)\n")
        elif d1_error: out.write("- D1 history: WARNING (Royal check still completed)\n")
        else: out.write("- D1 history: skipped\n")
        out.write(f"- Deduplicated meaningful alerts: {alerts}\n")
        if email_ok is not None: out.write(f"- Consolidated email: {'PASS' if email_ok else 'WARNING'}\n")


def main() -> int:
    profiles = load_profiles()
    require("primary Royal username", profiles[0].username)
    require("primary Royal password", profiles[0].password)
    if truthy(env("NOTIFICATION_TEST")):
        ok = send_apprise(profiles[0], "Royal cruise tracker test", "Notification path is working. No Royal price check was run.")
        return 0 if ok else 1

    exit_code = 0
    for profile in profiles:
        cfg, watchlist = build_config(profile)
        try:
            previous = load_previous(profile)
        except Exception as exc:
            previous = {}
            print(f"::warning::Could not load D1 prior history for {profile.label}: {exc}")
        with tempfile.TemporaryDirectory(prefix=f"royal-{profile.label}-") as tmp:
            status, lines = run_upstream(profile, cfg, Path(tmp) / "config.json")
        if status != 0:
            exit_code = status
            if profile.apprise_url: send_apprise(profile, "Royal cruise tracker error", f"The {profile.label} Royal check failed. Review the protected workflow run.")
            continue
        snapshot = parse_history(profile, watchlist, lines)
        alerts = build_alerts(profile, snapshot, previous)
        email_ok: bool | None = None
        if alerts:
            title, body = consolidate_alerts(profile, alerts)
            email_ok = send_apprise(profile, title, body)
        persisted: int | None = None
        d1_error: str | None = None
        try:
            persisted = persist_history(profile, snapshot)
        except Exception as exc:
            d1_error = str(exc)
            print(f"::warning::D1 history write failed for {profile.label}: {d1_error}")
        append_summary(profile, snapshot, persisted, len(alerts), email_ok, d1_error)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
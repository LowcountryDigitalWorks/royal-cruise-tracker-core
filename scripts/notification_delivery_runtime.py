#!/usr/bin/env python3
"""Durable notification-delivery reliability adapter for ALERT-REL-001.

This module is intentionally runtime-only. It preserves valid Royal observation history
independently of transport outcome while using a sanitized D1 outbox for notification
intent state. No booking/reservation identifiers, passenger names, notification
endpoints, credentials, or raw provider payloads are persisted in this outbox.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable

from notification_intent_bridge import (
    NotificationIntentBridgeError,
    safe_intent_payload,
)

EPSILON = 0.009
OPEN_DELIVERY_STATES = {"pending", "failed", "dispatching"}
RETRYABLE_DELIVERY_STATES = {"pending", "failed"}
PRICE_EVENT_TYPES = {"new_low", "meaningful_drop", "price_change"}

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS notification_delivery_intents (
  intent_id TEXT PRIMARY KEY,
  household_id TEXT NOT NULL,
  sailing_id TEXT NOT NULL,
  subject_key TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  observed_at TEXT,
  delivery_state TEXT NOT NULL CHECK (
    delivery_state IN ('pending','dispatching','delivered','failed','superseded')
  ),
  evidence_state TEXT NOT NULL CHECK (
    evidence_state IN ('unverified','committed')
  ),
  attempt_count INTEGER NOT NULL DEFAULT 0,
  attempt_id TEXT,
  last_attempt_at TEXT,
  delivered_at TEXT,
  superseded_at TEXT,
  superseded_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""

_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_notification_delivery_open
  ON notification_delivery_intents(household_id,sailing_id,delivery_state,evidence_state)
"""

_PROVIDER_RUN_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS notification_observation_runs_v1 (
  household_id TEXT NOT NULL,
  sailing_id TEXT NOT NULL,
  run_at TEXT NOT NULL,
  outcome TEXT NOT NULL CHECK (outcome IN ('started','success','provider_failed')),
  PRIMARY KEY(household_id,sailing_id,run_at)
)
"""

_PROVIDER_RUN_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_notification_observation_runs_v1
  ON notification_observation_runs_v1(household_id,sailing_id,run_at)
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _scope_for_alert(alert: dict[str, Any], base: Any) -> str:
    kind = str(alert.get("kind") or "")
    if kind == "fare":
        return "royal:fare:usd:per_booking"
    if kind == "purchase_reprice":
        return "royal:purchase:usd:per_cruise_day" if alert.get("per_day") else "royal:purchase:usd:per_unit"
    if kind == "availability_return":
        return "royal:availability"
    spec = alert.get("spec")
    prefix = getattr(spec, "prefix", "") if spec is not None else ""
    try:
        _category, unit = base.classify(prefix)
    except Exception:
        unit = "unknown"
    return f"royal:watch:usd:{unit}"


def _subject_for_alert(alert: dict[str, Any]) -> tuple[str, str, str, str]:
    kind = str(alert.get("kind") or "").strip()
    product_id = str(alert.get("product_id") or "").strip()
    if not product_id:
        raise ValueError("alert product_id is required for durable notification intent")
    role = str(alert.get("role") or "all").strip() or "all"
    source = {
        "fare": "fare",
        "watch_new_low": "watch",
        "watch_initial_good": "watch",
        "purchase_reprice": "purchase",
        "availability_return": "availability",
    }.get(kind)
    if source is None:
        raise ValueError(f"unsupported alert kind for durable notification intent: {kind}")
    return source, product_id, role, f"{source}|{product_id}|{role}"


def _semantic_generation(alert: dict[str, Any], observed_at: str) -> str:
    kind = str(alert.get("kind") or "")
    current = _safe_float(alert.get("current"))
    if kind == "availability_return":
        return f"availability:{observed_at}"
    if kind == "watch_initial_good":
        target = _safe_float(alert.get("target"))
        if target is None:
            raise ValueError("target alert requires target")
        return f"target:{target:.6f}"
    if current is None:
        raise ValueError("price alert requires current value")
    return f"price:{current:.6f}"


def intent_from_alert(alert: dict[str, Any], observed_at: str, base: Any) -> dict[str, Any]:
    """Map an existing protected-runtime alert into a public-safe deterministic intent."""
    _source, _product_id, _role, subject_key = _subject_for_alert(alert)
    kind = str(alert.get("kind") or "")
    event_type = {
        "fare": "new_low",
        "watch_new_low": "new_low",
        "watch_initial_good": "target_hit",
        "purchase_reprice": "meaningful_drop",
        "availability_return": "availability_restored",
    }[kind]

    policy_key = {
        "fare": "fare_new_low",
        "watch_new_low": "watch_new_low",
        "purchase_reprice": "purchase_reprice",
        "availability_return": "availability_restored",
    }.get(kind)
    if kind == "watch_initial_good":
        target = _safe_float(alert.get("target"))
        if target is None:
            raise ValueError("target alert requires target")
        policy_key = f"target:{target:.6f}"
    if policy_key is None:
        raise ValueError(f"unsupported alert kind for policy identity: {kind}")

    payload: dict[str, Any] = {
        "subject_key": subject_key,
        "group_key": f"royal:{base.SAILING_ID}",
        "comparison_scope_key": _scope_for_alert(alert, base),
        "event_type": event_type,
        "semantic_generation": _semantic_generation(alert, observed_at),
        "observed_at": observed_at,
        "freshness": "fresh",
        "priority_class": "high" if event_type != "target_hit" else "urgent",
        "current_value": _safe_float(alert.get("current")),
        "evidence_label": {
            "fare": "comparable cabin fare new low",
            "watch_new_low": "authenticated Royal watch new low",
            "watch_initial_good": "configured target currently reached",
            "purchase_reprice": "authenticated Royal purchase reprice opportunity",
            "availability_return": "authenticated Royal availability restored",
        }[kind],
        "actionability_state": "verify_before_send",
        "policy_state": "configured",
        "delivery_mode": "immediate",
        "route_class": (
            "verify_current_availability"
            if event_type == "availability_restored"
            else "verify_current_price"
        ),
        "policy_key": policy_key,
    }
    savings = _safe_float(alert.get("savings"))
    if savings is not None:
        payload["absolute_delta"] = savings
    if event_type == "target_hit":
        payload["target_state"] = "reached"
    if event_type == "availability_restored":
        payload["availability_state"] = "available"
        payload["current_value"] = None
    return safe_intent_payload(payload)


def alert_intents(alerts: list[dict[str, Any]], observed_at: str, base: Any) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for alert in alerts:
        intent = intent_from_alert(alert, observed_at, base)
        deduped[intent["intent_id"]] = intent
    return [deduped[key] for key in sorted(deduped)]


def _parse_subject(subject_key: str) -> tuple[str, str, str] | None:
    parts = str(subject_key or "").split("|", 2)
    if len(parts) != 3 or not all(parts):
        return None
    return parts[0], parts[1], parts[2]


def current_state_for_intent(
    intent: dict[str, Any],
    snapshot: dict[str, Any],
    purchases: list[dict[str, Any]],
    base: Any,
) -> dict[str, Any] | None:
    """Build current, in-memory evidence for strict pre-send reconciliation."""
    parsed = _parse_subject(str(intent.get("subject_key") or ""))
    if parsed is None:
        return None
    source, product_id, role = parsed
    observed_at = str(snapshot.get("observed_at") or "")
    scope = str(intent.get("comparison_scope_key") or "")

    state: dict[str, Any] = {
        "subject_key": intent["subject_key"],
        "comparison_scope_key": scope,
        "observed_at": observed_at,
        "freshness": "fresh",
    }

    if source == "fare":
        current = _safe_float(snapshot.get("cabin_fare"))
        if current is None:
            return None
        state.update({"comparable": True, "current_value": current})
        return state

    if source == "watch":
        row = next(
            (
                item for item in (snapshot.get("products") or [])
                if str(item.get("product_id")) == product_id
                and str(item.get("traveler_role") or "all") == role
            ),
            None,
        )
        if row is None:
            return None
        current = _safe_float(row.get("current"))
        if current is None:
            return {
                **state,
                "comparable": False,
                "current_value": None,
                "explicitly_unavailable": True,
            }
        state.update({"comparable": True, "current_value": current})
        if intent.get("event_type") == "target_hit":
            policy_key = str(intent.get("policy_key") or "")
            try:
                target = float(policy_key.split(":", 1)[1])
            except (IndexError, ValueError):
                return None
            state["target_state"] = "reached" if current <= target + EPSILON else "above"
        return state

    if source == "purchase":
        row = next(
            (
                item for item in purchases
                if str(item.get("product_id")) == product_id
                and str(item.get("traveler_role") or "all") == role
            ),
            None,
        )
        if row is None:
            return None
        current = _safe_float(row.get("current"))
        if current is None:
            return {
                **state,
                "comparable": False,
                "current_value": None,
                "explicitly_unavailable": bool(row.get("no_longer_for_sale")),
            }
        state.update({"comparable": True, "current_value": current})
        return state

    if source == "availability":
        row = next(
            (
                item for item in (snapshot.get("availability") or [])
                if str(item.get("product_id")) == product_id
                and str(item.get("traveler_role") or "all") == role
            ),
            None,
        )
        if row is None:
            return None
        state.update({
            "comparable": False,
            "availability_state": str(row.get("status") or "unknown"),
        })
        product = next(
            (
                item for item in (snapshot.get("products") or [])
                if str(item.get("product_id")) == product_id
                and str(item.get("traveler_role") or "all") == role
            ),
            None,
        )
        if product is not None:
            state["current_value"] = _safe_float(product.get("current"))
        return state

    return None


def evaluate_send_eligibility(
    record: dict[str, Any],
    current_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Strict sendability gate: missing/mismatched/unfresh evidence never sends."""
    if record.get("delivery_state") not in RETRYABLE_DELIVERY_STATES:
        return {"sendable": False, "supersede": False, "reason": "not_retryable_state"}
    if record.get("evidence_state") != "committed":
        return {"sendable": False, "supersede": False, "reason": "evidence_not_committed"}
    intent = record.get("intent")
    if not isinstance(intent, dict):
        return {"sendable": False, "supersede": False, "reason": "invalid_intent"}
    if not isinstance(current_state, dict):
        return {"sendable": False, "supersede": False, "reason": "missing_current_state"}
    if current_state.get("subject_key") != intent.get("subject_key"):
        return {"sendable": False, "supersede": False, "reason": "subject_mismatch"}
    if (
        intent.get("comparison_scope_key")
        and current_state.get("comparison_scope_key") != intent.get("comparison_scope_key")
    ):
        return {"sendable": False, "supersede": False, "reason": "scope_mismatch"}
    if current_state.get("freshness") != "fresh":
        return {"sendable": False, "supersede": False, "reason": "unfresh_current_state"}

    event_type = str(intent.get("event_type") or "")
    if event_type in PRICE_EVENT_TYPES:
        current = _safe_float(current_state.get("current_value"))
        original = _safe_float(intent.get("current_value"))
        if current is None or current_state.get("comparable") is not True:
            if current_state.get("explicitly_unavailable") is True:
                return {"sendable": False, "supersede": True, "reason": "no_current_comparable_price"}
            return {"sendable": False, "supersede": False, "reason": "current_price_unverifiable"}
        if original is None:
            return {"sendable": False, "supersede": False, "reason": "intent_price_missing"}
        if current > original + EPSILON:
            return {"sendable": False, "supersede": True, "reason": "price_increased"}
        return {"sendable": True, "supersede": False, "reason": "current"}

    if event_type == "target_hit":
        if current_state.get("target_state") == "above":
            return {"sendable": False, "supersede": True, "reason": "target_no_longer_reached"}
        if current_state.get("target_state") != "reached":
            return {"sendable": False, "supersede": False, "reason": "target_state_unverifiable"}
        return {"sendable": True, "supersede": False, "reason": "current"}

    if event_type == "availability_restored":
        availability = current_state.get("availability_state")
        if availability == "available":
            return {"sendable": True, "supersede": False, "reason": "current"}
        if availability in {"unavailable_or_already_booked", "sold_out", "unavailable"}:
            return {"sendable": False, "supersede": True, "reason": "no_longer_available"}
        return {"sendable": False, "supersede": False, "reason": "availability_unverifiable"}

    return {"sendable": False, "supersede": False, "reason": "unsupported_event_type"}


def deterministic_attempt_id(intent_id: str, attempt_number: int) -> str:
    digest = hashlib.sha256(f"{intent_id}:{int(attempt_number)}".encode("utf-8")).hexdigest()
    return f"nda1_{digest}"


def _changes(result_rows: list[dict[str, Any]]) -> int:
    if not result_rows:
        return 0
    try:
        return int((result_rows[0].get("meta") or {}).get("changes") or 0)
    except (TypeError, ValueError):
        return 0


class DeliveryStore:
    """Small D1 outbox adapter. All writes are idempotent or compare-and-set."""

    def __init__(self, base: Any, profile: Any):
        self.base = base
        self.profile = profile
        self.household_id = str(profile.label)
        self.sailing_id = str(base.SAILING_ID)

    def ensure_schema(self) -> None:
        self.base.d1_request([
            {"sql": _SCHEMA_SQL, "params": []},
            {"sql": _INDEX_SQL, "params": []},
            {"sql": _PROVIDER_RUN_SCHEMA_SQL, "params": []},
            {"sql": _PROVIDER_RUN_INDEX_SQL, "params": []},
        ])

    def record_provider_run(self, run_at: str, success: bool | None) -> None:
        """Persist sanitized provider-run continuity independently of product observations.

        ``success=None`` records a pre-acquisition ``started`` marker. The same marker is
        then compare-key updated to ``success`` or ``provider_failed``. If outcome update
        later fails, the durable ``started`` row still breaks restock continuity rather
        than silently making two availability observations look adjacent.
        """
        when = str(run_at or "").strip()
        if not when:
            raise ValueError("provider run timestamp is required")
        self.ensure_schema()
        outcome = "started" if success is None else ("success" if success else "provider_failed")
        self.base.d1_request([{
            "sql": """
            INSERT INTO notification_observation_runs_v1(
              household_id,sailing_id,run_at,outcome
            ) VALUES(?,?,?,?)
            ON CONFLICT(household_id,sailing_id,run_at)
            DO UPDATE SET outcome=excluded.outcome
            """,
            "params": [self.household_id, self.sailing_id, when, outcome],
        }])

    def availability_contiguous(self, product_id: str, role: str, current_observed_at: str) -> bool:
        """Prove the previous unavailable observation is from the immediately prior provider run."""
        bid = self.base.booking_id(self.profile)
        rows = self.base.d1_request([{
            "sql": """
            WITH previous AS (
              SELECT status,observed_at
                FROM product_availability
               WHERE household_id=? AND sailing_id=? AND booking_id=?
                 AND product_id=? AND COALESCE(passenger_class,'all')=?
                 AND julianday(observed_at)<julianday(?)
               ORDER BY id DESC LIMIT 1
            )
            SELECT p.status,p.observed_at AS prior_observed_at,
                   CASE
                     WHEN p.status IN ('unavailable_or_already_booked','sold_out')
                      AND EXISTS (
                        SELECT 1 FROM notification_observation_runs_v1 r
                         WHERE r.household_id=? AND r.sailing_id=?
                           AND r.run_at=p.observed_at AND r.outcome='success'
                      )
                      AND EXISTS (
                        SELECT 1 FROM notification_observation_runs_v1 r
                         WHERE r.household_id=? AND r.sailing_id=?
                           AND r.run_at=? AND r.outcome='success'
                      )
                      AND NOT EXISTS (
                        SELECT 1 FROM notification_observation_runs_v1 r
                         WHERE r.household_id=? AND r.sailing_id=?
                           AND julianday(r.run_at)>julianday(p.observed_at)
                           AND julianday(r.run_at)<julianday(?)
                      )
                     THEN 1 ELSE 0
                   END AS continuity_proven
              FROM previous p
            """,
            "params": [
                self.household_id, self.sailing_id, bid, product_id, role, current_observed_at,
                self.household_id, self.sailing_id,
                self.household_id, self.sailing_id, current_observed_at,
                self.household_id, self.sailing_id, current_observed_at,
            ],
        }])
        result = (rows[0].get("results") or []) if rows else []
        return bool(result and int(result[0].get("continuity_proven") or 0) == 1)

    def stage(self, intents: list[dict[str, Any]]) -> None:
        if not intents:
            return
        now = utc_now_iso()
        self.ensure_schema()
        queries = []
        for raw in intents:
            intent = safe_intent_payload(raw)
            queries.append({
                "sql": """
                INSERT OR IGNORE INTO notification_delivery_intents(
                  intent_id,household_id,sailing_id,subject_key,event_type,payload_json,
                  observed_at,delivery_state,evidence_state,attempt_count,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,'pending','unverified',0,?,?)
                """,
                "params": [
                    intent["intent_id"],
                    self.household_id,
                    self.sailing_id,
                    intent["subject_key"],
                    intent["event_type"],
                    json.dumps(intent, sort_keys=True, separators=(",", ":")),
                    intent.get("observed_at"),
                    now,
                    now,
                ],
            })
        self.base.d1_request(queries)

    def load_open(self) -> list[dict[str, Any]]:
        self.ensure_schema()
        rows = self.base.d1_request([{
            "sql": """
            SELECT intent_id,payload_json,delivery_state,evidence_state,attempt_count,
                   attempt_id,last_attempt_at,delivered_at,superseded_at,superseded_reason
              FROM notification_delivery_intents
             WHERE household_id=? AND sailing_id=?
               AND delivery_state IN ('pending','failed','dispatching')
             ORDER BY created_at,intent_id
            """,
            "params": [self.household_id, self.sailing_id],
        }])
        result = []
        for row in (rows[0].get("results") or []):
            try:
                payload = json.loads(row["payload_json"])
                intent = safe_intent_payload(payload)
            except Exception as exc:
                raise RuntimeError("stored notification intent failed safe validation") from exc
            result.append({
                "intent_id": row["intent_id"],
                "intent": intent,
                "delivery_state": row["delivery_state"],
                "evidence_state": row["evidence_state"],
                "attempt_count": int(row.get("attempt_count") or 0),
                "attempt_id": row.get("attempt_id"),
                "last_attempt_at": row.get("last_attempt_at"),
                "delivered_at": row.get("delivered_at"),
                "superseded_at": row.get("superseded_at"),
                "superseded_reason": row.get("superseded_reason"),
            })
        return result

    def _verify_evidence(self, intent: dict[str, Any]) -> bool:
        parsed = _parse_subject(str(intent.get("subject_key") or ""))
        if parsed is None:
            return False
        source, product_id, role = parsed
        observed_at = str(intent.get("observed_at") or "")
        if not observed_at:
            return False
        bid = self.base.booking_id(self.profile)

        if source in {"fare", "watch", "purchase"}:
            d1_source = "authenticated_royal_purchase" if source == "purchase" else "authenticated_royal"
            current = _safe_float(intent.get("current_value"))
            if current is None:
                return False
            rows = self.base.d1_request([{
                "sql": """
                SELECT 1 AS ok
                  FROM price_snapshots
                 WHERE household_id=? AND sailing_id=? AND booking_id=?
                   AND product_id=? AND COALESCE(passenger_class,'all')=?
                   AND source=? AND observed_at>=?
                   AND observed_price IS NOT NULL
                   AND ABS(observed_price-?)<=?
                 ORDER BY id DESC LIMIT 1
                """,
                "params": [
                    self.household_id,
                    self.sailing_id,
                    bid,
                    product_id,
                    role,
                    d1_source,
                    observed_at,
                    current,
                    EPSILON,
                ],
            }])
            return bool(rows and (rows[0].get("results") or []))

        if source == "availability":
            rows = self.base.d1_request([{
                "sql": """
                SELECT 1 AS ok
                  FROM product_availability
                 WHERE household_id=? AND sailing_id=? AND booking_id=?
                   AND product_id=? AND COALESCE(passenger_class,'all')=?
                   AND status='available' AND observed_at>=?
                 ORDER BY id DESC LIMIT 1
                """,
                "params": [
                    self.household_id,
                    self.sailing_id,
                    bid,
                    product_id,
                    role,
                    observed_at,
                ],
            }])
            return bool(rows and (rows[0].get("results") or []))
        return False

    def promote_verified_evidence(self) -> int:
        promoted = 0
        for record in self.load_open():
            if record["evidence_state"] == "committed" or record["delivery_state"] == "dispatching":
                continue
            if not self._verify_evidence(record["intent"]):
                continue
            now = utc_now_iso()
            rows = self.base.d1_request([{
                "sql": """
                UPDATE notification_delivery_intents
                   SET evidence_state='committed',updated_at=?
                 WHERE intent_id=? AND household_id=? AND sailing_id=?
                   AND evidence_state='unverified'
                   AND delivery_state IN ('pending','failed')
                """,
                "params": [
                    now,
                    record["intent_id"],
                    self.household_id,
                    self.sailing_id,
                ],
            }])
            promoted += _changes(rows)
        return promoted

    def supersede(self, record: dict[str, Any], reason: str) -> bool:
        now = utc_now_iso()
        rows = self.base.d1_request([{
            "sql": """
            UPDATE notification_delivery_intents
               SET delivery_state='superseded',superseded_at=?,
                   superseded_reason=?,attempt_id=NULL,updated_at=?
             WHERE intent_id=? AND household_id=? AND sailing_id=?
               AND delivery_state IN ('pending','failed')
            """,
            "params": [
                now,
                str(reason)[:120],
                now,
                record["intent_id"],
                self.household_id,
                self.sailing_id,
            ],
        }])
        return _changes(rows) == 1

    def claim(self, record: dict[str, Any]) -> dict[str, Any] | None:
        next_attempt = int(record.get("attempt_count") or 0) + 1
        attempt_id = deterministic_attempt_id(record["intent_id"], next_attempt)
        attempted_at = utc_now_iso()
        rows = self.base.d1_request([{
            "sql": """
            UPDATE notification_delivery_intents
               SET delivery_state='dispatching',attempt_count=attempt_count+1,
                   attempt_id=?,last_attempt_at=?,updated_at=?
             WHERE intent_id=? AND household_id=? AND sailing_id=?
               AND evidence_state='committed'
               AND delivery_state IN ('pending','failed')
               AND attempt_id IS NULL
            """,
            "params": [
                attempt_id,
                attempted_at,
                attempted_at,
                record["intent_id"],
                self.household_id,
                self.sailing_id,
            ],
        }])
        if _changes(rows) != 1:
            return None
        claimed = dict(record)
        claimed["delivery_state"] = "dispatching"
        claimed["attempt_count"] = next_attempt
        claimed["attempt_id"] = attempt_id
        claimed["last_attempt_at"] = attempted_at
        return claimed

    def release_claim(self, record: dict[str, Any]) -> bool:
        """Return a pre-transport claim to retryable state; guarded by exact attempt identity."""
        now = utc_now_iso()
        rows = self.base.d1_request([{
            "sql": """
            UPDATE notification_delivery_intents
               SET delivery_state='pending',attempt_id=NULL,updated_at=?
             WHERE intent_id=? AND household_id=? AND sailing_id=?
               AND delivery_state='dispatching' AND attempt_id=?
            """,
            "params": [
                now,
                record["intent_id"],
                self.household_id,
                self.sailing_id,
                record["attempt_id"],
            ],
        }])
        return _changes(rows) == 1

    def record_result(self, record: dict[str, Any], success: bool) -> bool:
        now = utc_now_iso()
        if success:
            sql = """
            UPDATE notification_delivery_intents
               SET delivery_state='delivered',delivered_at=?,updated_at=?
             WHERE intent_id=? AND household_id=? AND sailing_id=?
               AND delivery_state='dispatching' AND attempt_id=?
            """
            params = [
                now,
                now,
                record["intent_id"],
                self.household_id,
                self.sailing_id,
                record["attempt_id"],
            ]
        else:
            sql = """
            UPDATE notification_delivery_intents
               SET delivery_state='failed',attempt_id=NULL,updated_at=?
             WHERE intent_id=? AND household_id=? AND sailing_id=?
               AND delivery_state='dispatching' AND attempt_id=?
            """
            params = [
                now,
                record["intent_id"],
                self.household_id,
                self.sailing_id,
                record["attempt_id"],
            ]
        rows = self.base.d1_request([{"sql": sql, "params": params}])
        return _changes(rows) == 1

    def write_legacy_delivery_event(self, record: dict[str, Any]) -> None:
        intent = record["intent"]
        parsed = _parse_subject(intent["subject_key"])
        if parsed is None:
            return
        _source, product_id, _role = parsed
        sent_at = utc_now_iso()
        summary = f"intent={intent['intent_id'][:20]};state=delivered"
        self.base.d1_request([{
            "sql": """
            INSERT INTO notification_events(
              household_id,sailing_id,product_id,event_type,summary,sent_at
            ) VALUES(?,?,?,?,?,?)
            """,
            "params": [
                self.household_id,
                self.sailing_id,
                product_id,
                intent["event_type"],
                summary,
                sent_at,
            ],
        }])


def _current_lookup(
    record: dict[str, Any],
    snapshot: dict[str, Any],
    purchases: list[dict[str, Any]],
    base: Any,
) -> dict[str, Any] | None:
    try:
        return current_state_for_intent(record["intent"], snapshot, purchases, base)
    except Exception:
        return None


def _product_name(product_id: str, current: dict[str, Any] | None, base: Any) -> str:
    spec = base.SPEC_BY_ID.get(product_id)
    if spec is not None:
        return str(spec.name)
    if isinstance(current, dict) and current.get("name"):
        return str(current["name"])
    return "tracked Royal item"


def _current_row_for_subject(
    subject_key: str,
    snapshot: dict[str, Any],
    purchases: list[dict[str, Any]],
) -> dict[str, Any] | None:
    parsed = _parse_subject(subject_key)
    if parsed is None:
        return None
    source, product_id, role = parsed
    if source == "purchase":
        return next(
            (
                item for item in purchases
                if str(item.get("product_id")) == product_id
                and str(item.get("traveler_role") or "all") == role
            ),
            None,
        )
    return next(
        (
            item for item in (snapshot.get("products") or [])
            if str(item.get("product_id")) == product_id
            and str(item.get("traveler_role") or "all") == role
        ),
        None,
    )


def render_claimed_delivery(
    profile: Any,
    claimed: list[dict[str, Any]],
    snapshot: dict[str, Any],
    purchases: list[dict[str, Any]],
    base: Any,
) -> tuple[str, str]:
    title = f"Royal deal alert — {base.SHIP_NAME} {base.SAIL_DATE}"
    lines = [
        f"{base.SHIP_NAME} · {base.SAIL_DATE} · {base.NIGHTS} nights",
        f"Cabin {profile.cabin}",
        "",
    ]
    for record in claimed:
        intent = record["intent"]
        parsed = _parse_subject(intent["subject_key"])
        if parsed is None:
            continue
        source, product_id, role = parsed
        current_state = current_state_for_intent(intent, snapshot, purchases, base) or {}
        current = _safe_float(current_state.get("current_value"))
        row = _current_row_for_subject(intent["subject_key"], snapshot, purchases)
        role_label = base.ROLE_LABELS.get(role, role)
        name = _product_name(product_id, row, base)
        event_type = intent["event_type"]

        if source == "fare":
            lines += [
                f"CABIN FARE OPPORTUNITY: ${current:.2f}" if current is not None else "CABIN FARE OPPORTUNITY",
                f"Verify/reprice manually while preserving cabin {profile.cabin}.",
                "",
            ]
            continue

        if event_type == "availability_restored":
            lines += [
                f"AVAILABLE AGAIN — {name}" + (f": ${current:.2f}" if current is not None else "") + f" ({role_label})",
                "Current authenticated Royal evidence confirms availability.",
            ]
        elif event_type == "target_hit":
            lines += [
                f"GOOD CURRENT PRICE — {name}" + (f": ${current:.2f}" if current is not None else "") + f" ({role_label})",
                "The configured deal target remains reached in current evidence.",
            ]
        elif source == "purchase":
            lines += [
                f"REBOOK OPPORTUNITY — {name}" + (f": ${current:.2f}" if current is not None else "") + f" ({role_label})",
                "A previously detected purchase-price opportunity remains current.",
            ]
        else:
            lines += [
                f"NEW LOW — {name}" + (f": ${current:.2f}" if current is not None else "") + f" ({role_label})",
                "A previously detected actionable price improvement remains current.",
            ]

        spec = base.SPEC_BY_ID.get(product_id)
        if spec is not None:
            lines.append(base.royal_product_url(profile, spec))
        elif source == "purchase":
            lines.append(
                "https://www.royalcaribbean.com/account/cruise-planner/order-history"
                f"?bookingId={profile.reservation_id}&shipCode={base.SHIP_CODE}"
                f"&sailDate={base.SAIL_DATE.replace('-', '')}"
            )
        lines.append("")

    lines.append("No purchase, cancellation, or rebooking was performed automatically.")
    return title, "\n".join(lines)


def _stage_with_retry(
    store: DeliveryStore,
    intents: list[dict[str, Any]],
    *,
    after_history: bool = False,
) -> bool:
    if not intents:
        return True
    try:
        store.stage(intents)
        return True
    except Exception:
        phase = "after evidence persistence" if after_history else "before evidence persistence"
        print(f"::warning::Notification intent persistence failed {phase}; delivery is fail-closed")
        return False


def main(
    adaptive: Any,
    base: Any,
    *,
    store_factory: Callable[[Any, Any], DeliveryStore] = DeliveryStore,
) -> int:
    """Run the current adaptive tracker with durable, evidence-gated delivery state."""
    profiles = base.load_profiles()
    base.require("primary Royal username", profiles[0].username)
    base.require("primary Royal password", profiles[0].password)

    if base.truthy(base.env("NOTIFICATION_TEST")):
        ok = base.send_apprise(
            profiles[0],
            "Royal cruise tracker test",
            "Notification path is working. No Royal price check was run.",
        )
        return 0 if ok else 1

    exit_code = 0
    for profile in profiles:
        store = store_factory(base, profile)
        state = adaptive.history_state(profile)
        if not state.available:
            print(f"::warning::Could not load adaptive D1 state for {profile.label}; using bounded fallback")
        cfg, watchlist, mode, selected = adaptive.build_config(profile, state)

        # Establish a durable pre-acquisition marker before invoking Royal. This marker
        # is deliberately independent of product/history writes: a later crash, provider
        # exception, or outcome-write failure must break availability continuity rather
        # than disappear between two persisted availability observations.
        provider_run_at = utc_now_iso()
        continuity_started = False
        recorder = getattr(store, "record_provider_run", None)
        if recorder is not None:
            try:
                recorder(provider_run_at, None)
                continuity_started = True
            except Exception:
                print(f"::warning::Could not persist provider-start continuity marker for {profile.label}")
                exit_code = max(exit_code, 1)

        import tempfile
        from pathlib import Path
        try:
            with tempfile.TemporaryDirectory(prefix=f"royal-{profile.label}-") as tmp:
                status, lines = base.run_upstream(profile, cfg, Path(tmp) / "config.json")
        except Exception:
            # Preserve the existing exception/failure semantics, but first make the
            # attempted provider run durable whenever D1 is available.
            if recorder is not None:
                try:
                    recorder(provider_run_at, False)
                except Exception:
                    print(f"::warning::Could not persist provider-exception continuity marker for {profile.label}")
            raise
        if status != 0:
            exit_code = status
            if recorder is not None:
                try:
                    recorder(provider_run_at, False)
                except Exception:
                    print(f"::warning::Could not persist provider-failure continuity marker for {profile.label}")
            if profile.apprise_url:
                base.send_apprise(
                    profile,
                    "Royal cruise tracker error",
                    f"The {profile.label} Royal check failed. Review the protected workflow run.",
                )
            continue

        # Mark successful acquisition immediately, before parsing/derivation can raise.
        # The exact provider-run timestamp becomes the observation timestamp so endpoint
        # continuity markers and persisted availability evidence share one durable key.
        provider_continuity_recorded = continuity_started
        if recorder is not None:
            try:
                recorder(provider_run_at, True)
                provider_continuity_recorded = True
            except Exception:
                provider_continuity_recorded = False
                print(f"::warning::Could not persist provider-success continuity marker for {profile.label}")
                exit_code = max(exit_code, 1)

        snapshot = base.parse_history(profile, watchlist, lines)
        snapshot["observed_at"] = provider_run_at
        snapshot, quarantined_watch_rows = adaptive.reconcile_watch_snapshot(snapshot, state)
        if quarantined_watch_rows:
            print(
                f"{profile.label} Royal child-tier plausibility guard: "
                f"{quarantined_watch_rows} suspect observation(s) omitted"
            )
        purchases = adaptive.parse_purchases(profile, lines, snapshot["observed_at"])
        alerts = adaptive.build_alerts(profile, snapshot, purchases, state)

        current_observed_at = str(snapshot.get("observed_at") or provider_run_at)

        # v4 derives a current-run restock from the latest availability row. ALERT-REL-001
        # additionally requires provider-run adjacency: a failed or successful-but-unobserved
        # intervening run breaks the unavailable->available transition. Price/reprice alerts
        # remain independent of this availability-only continuity gate.
        continuity_checked_alerts: list[dict[str, Any]] = []
        for alert in alerts:
            if alert.get("kind") != "availability_return":
                continuity_checked_alerts.append(alert)
                continue
            contiguous = False
            try:
                checker = getattr(store, "availability_contiguous", None)
                if provider_continuity_recorded and checker is not None:
                    contiguous = bool(checker(
                        str(alert.get("product_id") or ""),
                        str(alert.get("role") or "all"),
                        current_observed_at,
                    ))
            except Exception:
                contiguous = False
            if contiguous:
                continuity_checked_alerts.append(alert)
            else:
                print("::warning::Availability-restored alert suppressed because provider-run continuity is unproven")
                exit_code = max(exit_code, 1)
        alerts = continuity_checked_alerts

        suppress = base.truthy(base.env("SUPPRESS_ALERTS"))
        intents: list[dict[str, Any]] = []
        staged = True
        if alerts and not suppress:
            try:
                intents = alert_intents(alerts, snapshot["observed_at"], base)
            except (NotificationIntentBridgeError, ValueError):
                print("::warning::Notification intent derivation failed closed")
                exit_code = max(exit_code, 1)
                intents = []
                staged = False
            if intents:
                staged = _stage_with_retry(store, intents)
        elif alerts and suppress:
            print(f"{profile.label} proof mode suppressed {len(alerts)} otherwise-meaningful alert(s)")

        watch_written: int | None = None
        purchase_written: int | None = None
        d1_warning = not state.available
        try:
            watch_written = base.persist_history(profile, snapshot)
        except Exception:
            d1_warning = True
            print(f"::warning::D1 watch/fare history write failed for {profile.label}")
        try:
            purchase_written = adaptive.persist_purchases(profile, purchases)
        except Exception:
            d1_warning = True
            print(f"::warning::D1 purchased-item history write failed for {profile.label}")

        # If intent persistence failed before observation persistence, retry the exact
        # deterministic IDs after history writes. This closes the common D1 REST
        # partial-failure ordering without suppressing valid observation history.
        if intents and not staged:
            staged = _stage_with_retry(store, intents, after_history=True)
            if not staged:
                exit_code = max(exit_code, 1)

        email_ok: bool | None = None
        if not suppress and staged:
            try:
                store.promote_verified_evidence()
                open_records = store.load_open()
            except Exception:
                print("::warning::Notification evidence reconciliation failed closed")
                open_records = []
                if intents:
                    exit_code = max(exit_code, 1)

            ambiguous = [r for r in open_records if r.get("delivery_state") == "dispatching"]
            if ambiguous:
                print(
                    f"::warning::{len(ambiguous)} notification delivery attempt(s) are "
                    "ambiguous; automatic resend is blocked"
                )
                exit_code = max(exit_code, 1)

            eligible: list[dict[str, Any]] = []
            for record in open_records:
                if record.get("delivery_state") == "dispatching":
                    continue
                current = _current_lookup(record, snapshot, purchases, base)
                decision = evaluate_send_eligibility(record, current)
                if decision["supersede"]:
                    try:
                        store.supersede(record, decision["reason"])
                    except Exception:
                        print("::warning::Notification supersede reconciliation failed closed")
                        exit_code = max(exit_code, 1)
                elif decision["sendable"]:
                    eligible.append(record)

            claimed: list[dict[str, Any]] = []
            claim_failed = False
            for record in eligible:
                try:
                    claim = store.claim(record)
                except Exception:
                    print("::warning::Notification delivery claim failed closed")
                    exit_code = max(exit_code, 1)
                    claim_failed = True
                    continue
                if claim is None:
                    claim_failed = True
                    exit_code = max(exit_code, 1)
                else:
                    claimed.append(claim)

            # Transport is all-or-none for the consolidated eligible set. A partial CAS
            # claim must never split one intended grouped notification across runs.
            if claim_failed or len(claimed) != len(eligible):
                if claimed:
                    print("::warning::Partial notification delivery claim; transport skipped and claims released")
                release_failed = False
                for record in claimed:
                    try:
                        releaser = getattr(store, "release_claim", None)
                        if releaser is None or not releaser(record):
                            release_failed = True
                    except Exception:
                        release_failed = True
                if release_failed:
                    # Any unreleased row remains dispatching/ambiguous and therefore blocks
                    # automatic resend until reconciled; still never send a subset.
                    print("::warning::Partial notification claim release is ambiguous; automatic send blocked")
                    exit_code = max(exit_code, 1)
                claimed = []

            if claimed and len(claimed) == len(eligible):
                title, body = render_claimed_delivery(profile, claimed, snapshot, purchases, base)
                email_ok = base.send_apprise(profile, title, body)
                result_write_failed = False
                delivered_records: list[dict[str, Any]] = []
                for record in claimed:
                    try:
                        if store.record_result(record, bool(email_ok)):
                            if email_ok:
                                delivered_records.append(record)
                        else:
                            result_write_failed = True
                    except Exception:
                        result_write_failed = True

                if result_write_failed:
                    # A transport-success acknowledgement write failure is intentionally
                    # left as dispatching/ambiguous. Never blind-resend it.
                    print("::warning::Notification delivery acknowledgement is ambiguous; automatic resend blocked")
                    exit_code = max(exit_code, 1)
                elif not email_ok:
                    # Known transport failure was durably returned to retryable state.
                    exit_code = max(exit_code, 1)

                if email_ok:
                    for record in delivered_records:
                        try:
                            store.write_legacy_delivery_event(record)
                        except Exception:
                            # The durable outbox is authoritative for dedupe. A legacy
                            # history failure must not cause a resend.
                            print("::warning::Legacy notification history write failed after durable delivery acknowledgement")

        adaptive.append_summary(
            profile,
            mode,
            selected,
            snapshot,
            watch_written,
            purchase_written,
            len(purchases),
            len(alerts),
            email_ok,
            d1_warning,
        )

    return exit_code

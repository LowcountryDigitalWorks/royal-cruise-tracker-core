#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import notification_delivery_runtime as delivery
import notification_replay as replay
import notification_replay_legacy as legacy
import notification_replay_store as replay_store


class Spec:
    product_id = "beverage:demo:adult"
    prefix = "beverage"
    name = "Synthetic Package"
    audience = ("adult",)


class Base:
    SAILING_ID = "synthetic-sailing"
    NIGHTS = 3
    FARE_ALERT_SAVINGS = 100.0
    SPEC_BY_ID = {Spec.product_id: Spec()}

    @staticmethod
    def classify(prefix):
        return ("beverage", "per_cruise_day") if prefix == "beverage" else ("other", "per_unit")

    @staticmethod
    def booking_id(_profile):
        return "synthetic-booking"


class Adaptive:
    @staticmethod
    def meaningful_drop(profile, old_price, new_price, _prefix):
        savings = round((old_price - new_price) * Base.NIGHTS, 2)
        return savings + 0.009 >= float(profile.threshold), savings


PROFILE = SimpleNamespace(
    label="primary", cabin="1234", cabin_category="4B", paid_price="1000", threshold="10"
)


def watch_row(price, prior, observed_at, rank=1):
    return {
        "source": "authenticated_royal",
        "product_id": Spec.product_id,
        "passenger_class": "adult",
        "observed_price": price,
        "prior_min": prior,
        "observed_at": observed_at,
        "recency_rank": rank,
    }


class ReplayTests(unittest.TestCase):
    def test_next_run_reconstructs_same_intent_after_history_advanced(self):
        rows = [
            watch_row(90.0, 90.0, "2030-01-02T13:00:00Z", 1),
            watch_row(90.0, 100.0, "2030-01-02T12:00:00Z", 2),
        ]
        candidate = replay.reconstruct_price_candidates(PROFILE, rows, [], Base, Adaptive)[0]
        replayed = delivery.intent_from_alert(candidate["alert"], candidate["observed_at"], Base)
        original = delivery.intent_from_alert(
            {
                "kind": "watch_new_low",
                "product_id": Spec.product_id,
                "spec": Base.SPEC_BY_ID[Spec.product_id],
                "role": "adult",
                "current": 90.0,
                "previous_low": 100.0,
                "savings": 30.0,
            },
            "2030-01-02T12:00:00Z",
            Base,
        )
        self.assertEqual(replayed["intent_id"], original["intent_id"])

    def test_replay_twice_yields_same_logical_intent(self):
        row = watch_row(90.0, 100.0, "2030-01-02T12:00:00Z")
        ids = []
        for _ in range(2):
            candidate = replay.reconstruct_price_candidates(PROFILE, [row], [], Base, Adaptive)[0]
            ids.append(delivery.intent_from_alert(candidate["alert"], candidate["observed_at"], Base)["intent_id"])
        self.assertEqual(ids[0], ids[1])

    def test_non_event_stale_and_out_of_window_rows_do_not_replay(self):
        rows = [
            watch_row(105.0, 100.0, "2030-01-02T12:00:00Z", 1),
            watch_row(None, 100.0, "2030-01-02T11:00:00Z", 2),
            watch_row(80.0, 100.0, "2030-01-01T12:00:00Z", replay.REPLAY_ROWS_PER_SUBJECT + 1),
        ]
        self.assertEqual(replay.reconstruct_price_candidates(PROFILE, rows, [], Base, Adaptive), [])

    def test_only_newest_qualifying_transition_per_subject_replays(self):
        rows = [
            watch_row(80.0, 90.0, "2030-01-03T12:00:00Z", 1),
            watch_row(90.0, 100.0, "2030-01-02T12:00:00Z", 2),
        ]
        candidates = replay.reconstruct_price_candidates(PROFILE, rows, [], Base, Adaptive)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["alert"]["current"], 80.0)

    def test_restock_reconstructs_original_transition_identity(self):
        rows = [
            {
                "product_id": Spec.product_id, "passenger_class": "adult", "status": "available",
                "prior_status": "available", "current_price": 75.0,
                "observed_at": "2030-01-03T12:00:00Z", "recency_rank": 1,
            },
            {
                "product_id": Spec.product_id, "passenger_class": "adult", "status": "available",
                "prior_status": "sold_out", "current_price": 75.0,
                "observed_at": "2030-01-02T12:00:00Z", "recency_rank": 2,
            },
        ]
        candidate = replay.reconstruct_availability_candidates(rows, Base)[0]
        replayed = delivery.intent_from_alert(candidate["alert"], candidate["observed_at"], Base)
        original = delivery.intent_from_alert(
            {
                "kind": "availability_return", "product_id": Spec.product_id,
                "spec": Base.SPEC_BY_ID[Spec.product_id], "role": "adult",
                "current": 75.0, "savings": 0.0, "previous_status": "sold_out",
            },
            "2030-01-02T12:00:00Z",
            Base,
        )
        self.assertEqual(replayed["intent_id"], original["intent_id"])

    def test_purchase_replay_preserves_original_threshold(self):
        rows = [{
            "source": "authenticated_royal_purchase", "product_id": "purchase:demo",
            "passenger_class": "adult", "observed_price": 80.0, "normalized_total": 240.0,
            "prior_min": 100.0, "observed_at": "2030-01-02T12:00:00Z", "recency_rank": 1,
        }]
        purchases = [{
            "product_id": "purchase:demo", "passenger_scope": "adult", "paid_unit_price": 100.0,
            "paid_total": 300.0, "price_unit": "per_cruise_day", "name": "Synthetic Purchase",
        }]
        candidate = replay.reconstruct_price_candidates(PROFILE, rows, purchases, Base, Adaptive)[0]
        self.assertEqual(candidate["alert"]["kind"], "purchase_reprice")
        self.assertEqual(candidate["alert"]["savings"], 60.0)

    def test_legacy_delivery_suppresses_rollout_duplicate(self):
        candidate = {
            "observed_at": "2030-01-02T12:00:00Z",
            "alert": {"kind": "watch_new_low", "product_id": Spec.product_id},
        }

        class LegacyBase(Base):
            @staticmethod
            def d1_request(_batch):
                return [{"results": [{
                    "product_id": Spec.product_id, "event_type": "watch_new_low",
                    "latest_sent_at": "2030-01-02T12:01:00Z",
                }]}]

        self.assertEqual(legacy.suppress_already_delivered_legacy([candidate], PROFILE, LegacyBase), [])

    def test_older_legacy_delivery_does_not_hide_new_missed_transition(self):
        candidate = {
            "observed_at": "2030-01-02T12:00:00Z",
            "alert": {"kind": "watch_new_low", "product_id": Spec.product_id},
        }

        class LegacyBase(Base):
            @staticmethod
            def d1_request(_batch):
                return [{"results": [{
                    "product_id": Spec.product_id, "event_type": "new_low",
                    "latest_sent_at": "2030-01-01T12:00:00Z",
                }]}]

        self.assertEqual(legacy.suppress_already_delivered_legacy([candidate], PROFILE, LegacyBase), [candidate])

    def test_replay_store_fails_health_closed_without_erasing_existing_outbox(self):
        class BaseStore:
            def __init__(self, base, profile):
                self.base, self.profile, self.staged = base, profile, []
            def stage(self, intents):
                self.staged.extend(intents)
            def promote_verified_evidence(self):
                return 7

        Store = replay_store.make_replay_store(BaseStore, Adaptive, delivery.intent_from_alert)
        store = Store(Base, PROFILE)
        with patch.object(replay_store, "load_bounded_replay_candidates", side_effect=RuntimeError("synthetic")):
            self.assertEqual(store.promote_verified_evidence(), 7)
        self.assertTrue(Store.replay_health_failed)
        self.assertEqual(store.staged, [])

    def test_outbox_stage_is_insert_or_ignore_for_replay_identity(self):
        calls = []

        class D1Base(Base):
            @staticmethod
            def d1_request(batch):
                calls.append(batch)
                return [{"success": True, "meta": {"changes": 0}} for _ in batch]

        store = delivery.DeliveryStore(D1Base, PROFILE)
        intent = delivery.intent_from_alert(
            {
                "kind": "watch_new_low", "product_id": Spec.product_id,
                "spec": Base.SPEC_BY_ID[Spec.product_id], "role": "adult",
                "current": 90.0, "previous_low": 100.0, "savings": 30.0,
            },
            "2030-01-02T12:00:00Z", Base,
        )
        store.stage([intent]); store.stage([intent])
        inserts = [q for batch in calls for q in batch if "INSERT OR IGNORE INTO notification_delivery_intents" in q["sql"]]
        self.assertEqual(len(inserts), 2)
        self.assertTrue(all(intent["intent_id"] in q["params"] for q in inserts))


if __name__ == "__main__":
    unittest.main()

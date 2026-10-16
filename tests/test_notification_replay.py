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


class FakeSpec:
    def __init__(self, product_id="beverage:demo:adult", prefix="beverage"):
        self.product_id = product_id
        self.prefix = prefix
        self.name = "Synthetic Package"
        self.audience = ("adult",)
        self.product = "synthetic"
        self.age_field = "all"


class FakeBase:
    SAILING_ID = "synthetic-sailing"
    NIGHTS = 3
    FARE_ALERT_SAVINGS = 100.0
    SPEC = FakeSpec()
    SPEC_BY_ID = {SPEC.product_id: SPEC}

    @staticmethod
    def classify(prefix):
        if prefix == "beverage":
            return ("beverage", "per_cruise_day")
        return ("other", "per_unit")

    @staticmethod
    def booking_id(_profile):
        return "synthetic-booking"


class FakeAdaptive:
    EPSILON = 0.009

    @staticmethod
    def meaningful_drop(profile, old_price, new_price, _prefix):
        savings = round((old_price - new_price) * FakeBase.NIGHTS, 2)
        return savings + 0.009 >= float(profile.threshold), savings


class ReplayReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.profile = SimpleNamespace(
            label="primary",
            cabin="1234",
            cabin_category="4B",
            paid_price="1000",
            threshold="10",
        )

    def test_next_run_reconstructs_same_watch_intent_after_history_advanced(self):
        rows = [
            {
                "source": "authenticated_royal",
                "product_id": FakeBase.SPEC.product_id,
                "passenger_class": "adult",
                "observed_price": 90.0,
                "prior_min": 90.0,
                "observed_at": "2030-01-02T13:00:00Z",
                "recency_rank": 1,
            },
            {
                "source": "authenticated_royal",
                "product_id": FakeBase.SPEC.product_id,
                "passenger_class": "adult",
                "observed_price": 90.0,
                "prior_min": 100.0,
                "observed_at": "2030-01-02T12:00:00Z",
                "promotion_label": "Synthetic promo",
                "recency_rank": 2,
            },
        ]
        candidates = replay.reconstruct_price_candidates(
            self.profile, rows, [], FakeBase, FakeAdaptive
        )
        self.assertEqual(len(candidates), 1)
        replayed = delivery.intent_from_alert(
            candidates[0]["alert"], candidates[0]["observed_at"], FakeBase
        )
        original_alert = {
            "kind": "watch_new_low",
            "product_id": FakeBase.SPEC.product_id,
            "spec": FakeBase.SPEC,
            "role": "adult",
            "current": 90.0,
            "previous_low": 100.0,
            "savings": 30.0,
            "promotion": "Synthetic promo",
        }
        original = delivery.intent_from_alert(
            original_alert, "2030-01-02T12:00:00Z", FakeBase
        )
        self.assertEqual(replayed["intent_id"], original["intent_id"])
        self.assertEqual(replayed["semantic_generation"], original["semantic_generation"])

    def test_replay_twice_remains_one_logical_intent(self):
        row = {
            "source": "authenticated_royal",
            "product_id": FakeBase.SPEC.product_id,
            "passenger_class": "adult",
            "observed_price": 90.0,
            "prior_min": 100.0,
            "observed_at": "2030-01-02T12:00:00Z",
            "recency_rank": 1,
        }
        first = replay.reconstruct_price_candidates(
            self.profile, [row], [], FakeBase, FakeAdaptive
        )
        second = replay.reconstruct_price_candidates(
            self.profile, [row], [], FakeBase, FakeAdaptive
        )
        first_id = delivery.intent_from_alert(first[0]["alert"], first[0]["observed_at"], FakeBase)["intent_id"]
        second_id = delivery.intent_from_alert(second[0]["alert"], second[0]["observed_at"], FakeBase)["intent_id"]
        self.assertEqual(first_id, second_id)

    def test_non_event_and_stale_price_rows_do_not_replay(self):
        rows = [
            {
                "source": "authenticated_royal",
                "product_id": FakeBase.SPEC.product_id,
                "passenger_class": "adult",
                "observed_price": 105.0,
                "prior_min": 100.0,
                "observed_at": "2030-01-02T12:00:00Z",
                "recency_rank": 1,
            },
            {
                "source": "authenticated_royal",
                "product_id": FakeBase.SPEC.product_id,
                "passenger_class": "adult",
                "observed_price": None,
                "prior_min": 100.0,
                "observed_at": "2030-01-02T11:00:00Z",
                "recency_rank": 2,
            },
        ]
        self.assertEqual(
            replay.reconstruct_price_candidates(self.profile, rows, [], FakeBase, FakeAdaptive),
            [],
        )

    def test_only_newest_qualifying_transition_per_subject_is_replayed(self):
        rows = [
            {
                "source": "authenticated_royal",
                "product_id": FakeBase.SPEC.product_id,
                "passenger_class": "adult",
                "observed_price": 80.0,
                "prior_min": 90.0,
                "observed_at": "2030-01-03T12:00:00Z",
                "recency_rank": 1,
            },
            {
                "source": "authenticated_royal",
                "product_id": FakeBase.SPEC.product_id,
                "passenger_class": "adult",
                "observed_price": 90.0,
                "prior_min": 100.0,
                "observed_at": "2030-01-02T12:00:00Z",
                "recency_rank": 2,
            },
        ]
        candidates = replay.reconstruct_price_candidates(
            self.profile, rows, [], FakeBase, FakeAdaptive
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["alert"]["current"], 80.0)

    def test_replay_window_is_bounded(self):
        row = {
            "source": "authenticated_royal",
            "product_id": FakeBase.SPEC.product_id,
            "passenger_class": "adult",
            "observed_price": 80.0,
            "prior_min": 100.0,
            "observed_at": "2030-01-01T12:00:00Z",
            "recency_rank": replay.REPLAY_ROWS_PER_SUBJECT + 1,
        }
        self.assertEqual(
            replay.reconstruct_price_candidates(self.profile, [row], [], FakeBase, FakeAdaptive),
            [],
        )

    def test_restock_transition_reconstructs_original_identity_after_later_available_row(self):
        rows = [
            {
                "product_id": FakeBase.SPEC.product_id,
                "passenger_class": "adult",
                "status": "available",
                "prior_status": "available",
                "current_price": 75.0,
                "observed_at": "2030-01-03T12:00:00Z",
                "recency_rank": 1,
            },
            {
                "product_id": FakeBase.SPEC.product_id,
                "passenger_class": "adult",
                "status": "available",
                "prior_status": "sold_out",
                "current_price": 75.0,
                "observed_at": "2030-01-02T12:00:00Z",
                "recency_rank": 2,
            },
        ]
        candidates = replay.reconstruct_availability_candidates(rows, FakeBase)
        self.assertEqual(len(candidates), 1)
        replayed = delivery.intent_from_alert(
            candidates[0]["alert"], candidates[0]["observed_at"], FakeBase
        )
        original = delivery.intent_from_alert(
            {
                "kind": "availability_return",
                "product_id": FakeBase.SPEC.product_id,
                "spec": FakeBase.SPEC,
                "role": "adult",
                "current": 75.0,
                "savings": 0.0,
                "promotion": None,
                "previous_status": "sold_out",
            },
            "2030-01-02T12:00:00Z",
            FakeBase,
        )
        self.assertEqual(replayed["intent_id"], original["intent_id"])

    def test_purchase_replay_requires_same_original_alert_thresholds(self):
        rows = [{
            "source": "authenticated_royal_purchase",
            "product_id": "purchase:demo",
            "passenger_class": "adult",
            "observed_price": 80.0,
            "normalized_total": 240.0,
            "prior_min": 100.0,
            "observed_at": "2030-01-02T12:00:00Z",
            "recency_rank": 1,
        }]
        purchases = [{
            "product_id": "purchase:demo",
            "passenger_scope": "adult",
            "paid_unit_price": 100.0,
            "paid_total": 300.0,
            "price_unit": "per_cruise_day",
            "name": "Synthetic Purchase",
        }]
        candidates = replay.reconstruct_price_candidates(
            self.profile, rows, purchases, FakeBase, FakeAdaptive
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["alert"]["kind"], "purchase_reprice")
        self.assertEqual(candidates[0]["alert"]["savings"], 60.0)


class LegacySuppressionTests(unittest.TestCase):
    def setUp(self):
        self.profile = SimpleNamespace(label="primary")
        self.candidate = {
            "observed_at": "2030-01-02T12:00:00Z",
            "alert": {
                "kind": "watch_new_low",
                "product_id": FakeBase.SPEC.product_id,
            },
        }

    def _base(self, sent_at, event_type="watch_new_low"):
        class Base(FakeBase):
            @staticmethod
            def d1_request(_batch):
                return [{"results": [{
                    "product_id": FakeBase.SPEC.product_id,
                    "event_type": event_type,
                    "latest_sent_at": sent_at,
                }]}]
        return Base

    def test_pre_outbox_legacy_delivery_suppresses_replay(self):
        kept = legacy.suppress_already_delivered_legacy(
            [self.candidate], self.profile, self._base("2030-01-02T12:01:00Z")
        )
        self.assertEqual(kept, [])

    def test_old_legacy_delivery_does_not_hide_newer_missed_transition(self):
        kept = legacy.suppress_already_delivered_legacy(
            [self.candidate], self.profile, self._base("2030-01-01T12:00:00Z")
        )
        self.assertEqual(kept, [self.candidate])

    def test_new_outbox_event_alias_also_suppresses_if_outbox_were_missing(self):
        kept = legacy.suppress_already_delivered_legacy(
            [self.candidate], self.profile,
            self._base("2030-01-02T12:01:00Z", event_type="new_low"),
        )
        self.assertEqual(kept, [])


class ReplayStoreTests(unittest.TestCase):
    def setUp(self):
        self.profile = SimpleNamespace(label="primary")
        self.intent = delivery.intent_from_alert(
            {
                "kind": "watch_new_low",
                "product_id": FakeBase.SPEC.product_id,
                "spec": FakeBase.SPEC,
                "role": "adult",
                "current": 90.0,
                "previous_low": 100.0,
                "savings": 30.0,
            },
            "2030-01-02T12:00:00Z",
            FakeBase,
        )

    def _base_store(self):
        class BaseStore:
            def __init__(inner, base, profile):
                inner.base = base
                inner.profile = profile
                inner.staged = []

            def stage(inner, intents):
                inner.staged.extend(intents)

            def promote_verified_evidence(inner):
                return 7
        return BaseStore

    def test_replay_store_stages_deterministic_candidate_before_promotion(self):
        candidate = {
            "observed_at": "2030-01-02T12:00:00Z",
            "alert": {
                "kind": "watch_new_low",
                "product_id": FakeBase.SPEC.product_id,
                "spec": FakeBase.SPEC,
                "role": "adult",
                "current": 90.0,
                "previous_low": 100.0,
                "savings": 30.0,
            },
        }
        Store = replay_store.make_replay_store(
            self._base_store(), FakeAdaptive, delivery.intent_from_alert
        )
        store = Store(FakeBase, self.profile)
        with patch.object(replay_store, "load_bounded_replay_candidates", return_value=[candidate]), \
             patch.object(replay_store, "suppress_already_delivered_legacy", side_effect=lambda c, _p, _b: c):
            result = store.promote_verified_evidence()
        self.assertEqual(result, 7)
        self.assertEqual([item["intent_id"] for item in store.staged], [self.intent["intent_id"]])
        self.assertFalse(Store.replay_health_failed)

    def test_replay_failure_sets_health_signal_but_does_not_erase_existing_outbox(self):
        Store = replay_store.make_replay_store(
            self._base_store(), FakeAdaptive, delivery.intent_from_alert
        )
        store = Store(FakeBase, self.profile)
        with patch.object(replay_store, "load_bounded_replay_candidates", side_effect=RuntimeError("synthetic")):
            result = store.promote_verified_evidence()
        self.assertEqual(result, 7)
        self.assertEqual(store.staged, [])
        self.assertTrue(Store.replay_health_failed)


class OutboxReplayIdempotencyTests(unittest.TestCase):
    def test_stage_uses_insert_or_ignore_for_replay_identity(self):
        calls = []

        class Base(FakeBase):
            @staticmethod
            def d1_request(batch):
                calls.append(batch)
                return [{"success": True, "meta": {"changes": 0}} for _ in batch]

        profile = SimpleNamespace(label="primary")
        store = delivery.DeliveryStore(Base, profile)
        intent = delivery.intent_from_alert(
            {
                "kind": "watch_new_low",
                "product_id": FakeBase.SPEC.product_id,
                "spec": FakeBase.SPEC,
                "role": "adult",
                "current": 90.0,
                "previous_low": 100.0,
                "savings": 30.0,
            },
            "2030-01-02T12:00:00Z",
            FakeBase,
        )
        store.stage([intent])
        store.stage([intent])
        insert_batches = [batch for batch in calls if any("INSERT OR IGNORE INTO notification_delivery_intents" in q["sql"] for q in batch)]
        self.assertEqual(len(insert_batches), 2)
        for batch in insert_batches:
            insert = next(q for q in batch if "INSERT OR IGNORE INTO notification_delivery_intents" in q["sql"])
            self.assertIn("INSERT OR IGNORE", insert["sql"])
            self.assertIn(intent["intent_id"], insert["params"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
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
                "prior_status": "available", "current_price": 75.0, "continuity_proven": 1,
                "observed_at": "2030-01-03T12:00:00Z", "recency_rank": 1,
            },
            {
                "product_id": Spec.product_id, "passenger_class": "adult", "status": "available",
                "prior_status": "sold_out", "current_price": 75.0, "continuity_proven": 1,
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

    def test_restock_replay_fails_closed_without_provider_continuity_proof(self):
        base_row = {
            "product_id": Spec.product_id, "passenger_class": "adult", "status": "available",
            "prior_status": "sold_out", "current_price": 75.0,
            "observed_at": "2030-01-02T12:00:00Z", "recency_rank": 1,
        }
        self.assertEqual(replay.reconstruct_availability_candidates([base_row], Base), [])
        failed_gap = {**base_row, "continuity_proven": 0}
        self.assertEqual(replay.reconstruct_availability_candidates([failed_gap], Base), [])
        adjacent = {**base_row, "continuity_proven": 1}
        self.assertEqual(len(replay.reconstruct_availability_candidates([adjacent], Base)), 1)

    def test_replay_floor_is_explicitly_time_bounded(self):
        from datetime import datetime, timezone
        floor = replay.replay_floor_iso(datetime(2030, 1, 3, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(floor, "2030-01-01T12:00:00+00:00")
        self.assertEqual(replay.REPLAY_MAX_AGE_HOURS, 48)

    def test_availability_replay_query_requires_adjacent_provider_run_markers(self):
        calls = []
        class ContinuityBase(Base):
            @staticmethod
            def d1_request(batch):
                calls.append(batch)
                if len(batch) == 2:
                    return [{"results": []}, {"results": []}]
                return [{"results": []}]
        self.assertEqual(replay.load_bounded_replay_candidates(PROFILE, ContinuityBase, Adaptive), [])
        price_sql = calls[0][0]["sql"]
        availability_sql = calls[1][0]["sql"]
        self.assertIn("julianday(observed_at)>=julianday(?)", price_sql)
        self.assertIn("julianday(s.observed_at)>=julianday(?)", availability_sql)
        self.assertIn("notification_observation_runs_v1", availability_sql)
        self.assertIn("notification_observation_run_confirmations_v1", availability_sql)
        self.assertIn("prior_observed_at", availability_sql)
        self.assertIn("NOT EXISTS", availability_sql)
        self.assertIn("continuity_proven", availability_sql)

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

    def test_replay_time_floor_excludes_old_price_transition(self):
        class SqliteBase(Base):
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            @classmethod
            def d1_request(cls, batch):
                result = []
                for query in batch:
                    cursor = cls.conn.execute(query["sql"], query.get("params") or [])
                    result.append({"success": True, "results": [dict(row) for row in cursor.fetchall()]})
                return result

        conn = SqliteBase.conn
        conn.executescript("""
        CREATE TABLE price_snapshots(
          id INTEGER PRIMARY KEY,household_id TEXT,sailing_id TEXT,booking_id TEXT,
          product_id TEXT,source TEXT,passenger_class TEXT,observed_price REAL,
          normalized_total REAL,promotion_label TEXT,observed_at TEXT
        );
        CREATE TABLE purchases(
          product_id TEXT,passenger_scope TEXT,paid_unit_price REAL,paid_total REAL,
          currency TEXT,household_id TEXT,sailing_id TEXT,booking_id TEXT,active INTEGER
        );
        CREATE TABLE products(id TEXT,name TEXT,prefix TEXT,price_unit TEXT);
        CREATE TABLE product_availability(
          id INTEGER PRIMARY KEY,household_id TEXT,sailing_id TEXT,booking_id TEXT,
          product_id TEXT,passenger_class TEXT,status TEXT,observed_at TEXT
        );
        CREATE TABLE notification_observation_runs_v1(
          household_id TEXT,sailing_id TEXT,run_at TEXT,outcome TEXT
        );
        CREATE TABLE notification_observation_run_confirmations_v1(
          household_id TEXT,sailing_id TEXT,run_at TEXT,confirmed_outcome TEXT
        );
        """)
        common = (PROFILE.label, Base.SAILING_ID, Base.booking_id(PROFILE), Spec.product_id, "authenticated_royal", "adult")
        conn.execute("INSERT INTO price_snapshots VALUES(1,?,?,?,?,?,?,?,?,?,?)", (*common, 100.0, 300.0, None, "2030-01-02T10:00:00+00:00"))
        conn.execute("INSERT INTO price_snapshots VALUES(2,?,?,?,?,?,?,?,?,?,?)", (*common, 90.0, 270.0, None, "2030-01-02T11:00:00+00:00"))
        conn.commit()

        with patch.object(replay, "replay_floor_iso", return_value="2030-01-02T11:30:00+00:00"):
            self.assertEqual(replay.load_bounded_replay_candidates(PROFILE, SqliteBase, Adaptive), [])
        with patch.object(replay, "replay_floor_iso", return_value="2030-01-02T10:30:00+00:00"):
            candidates = replay.load_bounded_replay_candidates(PROFILE, SqliteBase, Adaptive)
        self.assertEqual([item["alert"]["kind"] for item in candidates], ["watch_new_low"])
        conn.close()

    def test_restock_sql_continuity_breaks_on_any_intervening_provider_run(self):
        class SqliteBase(Base):
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            @classmethod
            def d1_request(cls, batch):
                result = []
                for query in batch:
                    cursor = cls.conn.execute(query["sql"], query.get("params") or [])
                    result.append({"success": True, "results": [dict(row) for row in cursor.fetchall()]})
                return result

        conn = SqliteBase.conn
        conn.executescript("""
        CREATE TABLE price_snapshots(
          id INTEGER PRIMARY KEY,household_id TEXT,sailing_id TEXT,booking_id TEXT,
          product_id TEXT,source TEXT,passenger_class TEXT,observed_price REAL,
          normalized_total REAL,promotion_label TEXT,observed_at TEXT
        );
        CREATE TABLE purchases(
          product_id TEXT,passenger_scope TEXT,paid_unit_price REAL,paid_total REAL,
          currency TEXT,household_id TEXT,sailing_id TEXT,booking_id TEXT,active INTEGER
        );
        CREATE TABLE products(id TEXT,name TEXT,prefix TEXT,price_unit TEXT);
        CREATE TABLE product_availability(
          id INTEGER PRIMARY KEY,household_id TEXT,sailing_id TEXT,booking_id TEXT,
          product_id TEXT,passenger_class TEXT,status TEXT,observed_at TEXT
        );
        CREATE TABLE notification_observation_runs_v1(
          household_id TEXT,sailing_id TEXT,run_at TEXT,outcome TEXT
        );
        CREATE TABLE notification_observation_run_confirmations_v1(
          household_id TEXT,sailing_id TEXT,run_at TEXT,confirmed_outcome TEXT
        );
        """)
        before = "2030-01-02T10:00:00+00:00"
        gap = "2030-01-02T11:00:00+00:00"
        current = "2030-01-02T12:00:00+00:00"
        conn.execute(
            "INSERT INTO product_availability VALUES(1,?,?,?,?,?,?,?)",
            (PROFILE.label, Base.SAILING_ID, Base.booking_id(PROFILE), Spec.product_id, "adult", "sold_out", before),
        )
        conn.execute(
            "INSERT INTO product_availability VALUES(2,?,?,?,?,?,?,?)",
            (PROFILE.label, Base.SAILING_ID, Base.booking_id(PROFILE), Spec.product_id, "adult", "available", current),
        )
        conn.execute(
            "INSERT INTO price_snapshots VALUES(1,?,?,?,?,?,?,?,?,?,?)",
            (PROFILE.label, Base.SAILING_ID, Base.booking_id(PROFILE), Spec.product_id,
             "authenticated_royal", "adult", 75.0, 225.0, None, current),
        )
        for run_at, outcome in [(before, "success"), (current, "success")]:
            conn.execute(
                "INSERT INTO notification_observation_runs_v1 VALUES(?,?,?,?)",
                (PROFILE.label, Base.SAILING_ID, run_at, outcome),
            )
            conn.execute(
                "INSERT INTO notification_observation_run_confirmations_v1 VALUES(?,?,?,?)",
                (PROFILE.label, Base.SAILING_ID, run_at, "success"),
            )
        conn.commit()

        adjacent = replay.load_bounded_replay_candidates(PROFILE, SqliteBase, Adaptive)
        self.assertEqual([item["alert"]["kind"] for item in adjacent], ["availability_return"])

        for outcome in ("provider_failed", "success"):
            conn.execute(
                "INSERT INTO notification_observation_runs_v1 VALUES(?,?,?,?)",
                (PROFILE.label, Base.SAILING_ID, gap, outcome),
            )
            conn.commit()
            broken = replay.load_bounded_replay_candidates(PROFILE, SqliteBase, Adaptive)
            self.assertFalse(any(item["alert"]["kind"] == "availability_return" for item in broken))
            conn.execute("DELETE FROM notification_observation_runs_v1 WHERE run_at=?", (gap,))
            conn.commit()

        conn.close()

    def test_success_row_without_post_readback_confirmation_cannot_replay_restock(self):
        class SqliteBase(Base):
            conn = sqlite3.connect(":memory:")
            conn.row_factory = sqlite3.Row
            @classmethod
            def d1_request(cls, batch):
                result = []
                for query in batch:
                    cursor = cls.conn.execute(query["sql"], query.get("params") or [])
                    cls.conn.commit()
                    result.append({"success": True, "results": [dict(row) for row in cursor.fetchall()]})
                return result

        conn = SqliteBase.conn
        conn.executescript("""
        CREATE TABLE price_snapshots(
          id INTEGER PRIMARY KEY,household_id TEXT,sailing_id TEXT,booking_id TEXT,
          product_id TEXT,source TEXT,passenger_class TEXT,observed_price REAL,
          normalized_total REAL,promotion_label TEXT,observed_at TEXT
        );
        CREATE TABLE purchases(
          product_id TEXT,passenger_scope TEXT,paid_unit_price REAL,paid_total REAL,
          currency TEXT,household_id TEXT,sailing_id TEXT,booking_id TEXT,active INTEGER
        );
        CREATE TABLE products(id TEXT,name TEXT,prefix TEXT,price_unit TEXT);
        CREATE TABLE product_availability(
          id INTEGER PRIMARY KEY,household_id TEXT,sailing_id TEXT,booking_id TEXT,
          product_id TEXT,passenger_class TEXT,status TEXT,observed_at TEXT
        );
        CREATE TABLE notification_observation_runs_v1(
          household_id TEXT,sailing_id TEXT,run_at TEXT,outcome TEXT
        );
        CREATE TABLE notification_observation_run_confirmations_v1(
          household_id TEXT,sailing_id TEXT,run_at TEXT,confirmed_outcome TEXT
        );
        """)
        before = "2030-01-02T10:00:00+00:00"
        current = "2030-01-02T12:00:00+00:00"
        common = (PROFILE.label, Base.SAILING_ID, Base.booking_id(PROFILE), Spec.product_id, "adult")
        conn.execute("INSERT INTO product_availability VALUES(1,?,?,?,?,?,?,?)", (*common, "sold_out", before))
        conn.execute("INSERT INTO product_availability VALUES(2,?,?,?,?,?,?,?)", (*common, "available", current))
        conn.execute("INSERT INTO price_snapshots VALUES(1,?,?,?,?,?,?,?,?,?,?)", (PROFILE.label, Base.SAILING_ID, Base.booking_id(PROFILE), Spec.product_id, "authenticated_royal", "adult", 75.0, 225.0, None, current))
        for run_at in (before, current):
            conn.execute("INSERT INTO notification_observation_runs_v1 VALUES(?,?,?,?)", (PROFILE.label, Base.SAILING_ID, run_at, "success"))
        # Prior endpoint was fully confirmed; current outcome exists but has no
        # confirmation artifact, modeling write-applied/readback-failed ambiguity.
        conn.execute("INSERT INTO notification_observation_run_confirmations_v1 VALUES(?,?,?,?)", (PROFILE.label, Base.SAILING_ID, before, "success"))
        conn.commit()
        with patch.object(replay, "replay_floor_iso", return_value="2030-01-01T00:00:00+00:00"):
            blocked = replay.load_bounded_replay_candidates(PROFILE, SqliteBase, Adaptive)
        self.assertFalse(any(item["alert"]["kind"] == "availability_return" for item in blocked))

        conn.execute("INSERT INTO notification_observation_run_confirmations_v1 VALUES(?,?,?,?)", (PROFILE.label, Base.SAILING_ID, current, "success"))
        conn.commit()
        with patch.object(replay, "replay_floor_iso", return_value="2030-01-01T00:00:00+00:00"):
            allowed = replay.load_bounded_replay_candidates(PROFILE, SqliteBase, Adaptive)
        self.assertTrue(any(item["alert"]["kind"] == "availability_return" for item in allowed))
        conn.close()

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

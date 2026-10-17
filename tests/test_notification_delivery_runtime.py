#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import notification_delivery_runtime as delivery


class FakeBaseForIntent:
    SAILING_ID = "synthetic-sailing"
    SHIP_NAME = "Synthetic Ship"
    SAIL_DATE = "2030-01-01"
    SHIP_CODE = "SS"
    NIGHTS = 3
    ROLE_LABELS = {"adult": "Adult"}
    SPEC_BY_ID = {}

    @staticmethod
    def classify(prefix):
        return ("demo", "per_cruise_day")

    @staticmethod
    def royal_product_url(profile, spec):
        return "https://example.invalid/product"


class NotificationDeliveryRuntimePureTests(unittest.TestCase):
    def setUp(self):
        self.base = FakeBaseForIntent()
        self.intent = {
            "intent_id": "ni1_demo",
            "subject_key": "watch|beverage:demo:adult|adult",
            "comparison_scope_key": "royal:watch:usd:per_cruise_day",
            "event_type": "new_low",
            "current_value": 90.0,
        }

    def record(self, **overrides):
        record = {
            "intent_id": "ni1_demo",
            "intent": dict(self.intent),
            "delivery_state": "pending",
            "evidence_state": "committed",
            "attempt_count": 0,
            "attempt_id": None,
        }
        record.update(overrides)
        return record

    def current(self, **overrides):
        state = {
            "subject_key": self.intent["subject_key"],
            "comparison_scope_key": self.intent["comparison_scope_key"],
            "freshness": "fresh",
            "comparable": True,
            "current_value": 90.0,
        }
        state.update(overrides)
        return state

    def test_price_intent_identity_is_stable_across_retry_observation_time(self):
        alert = {
            "kind": "watch_new_low",
            "product_id": "beverage:demo:adult",
            "role": "adult",
            "current": 90.0,
            "previous_low": 100.0,
            "savings": 30.0,
            "spec": SimpleNamespace(prefix="beverage"),
        }
        a = delivery.intent_from_alert(alert, "2030-01-01T00:00:00Z", self.base)
        b = delivery.intent_from_alert(alert, "2030-01-01T01:00:00Z", self.base)
        self.assertEqual(a["intent_id"], b["intent_id"])
        self.assertNotEqual(a["observed_at"], b["observed_at"])

    def test_missing_current_evidence_is_never_sendable(self):
        decision = delivery.evaluate_send_eligibility(self.record(), None)
        self.assertFalse(decision["sendable"])
        self.assertFalse(decision["supersede"])
        self.assertEqual(decision["reason"], "missing_current_state")

    def test_uncommitted_evidence_is_never_sendable(self):
        decision = delivery.evaluate_send_eligibility(
            self.record(evidence_state="unverified"),
            self.current(),
        )
        self.assertFalse(decision["sendable"])
        self.assertEqual(decision["reason"], "evidence_not_committed")

    def test_subject_and_scope_mismatch_fail_closed(self):
        subject = delivery.evaluate_send_eligibility(
            self.record(),
            self.current(subject_key="watch|other|adult"),
        )
        scope = delivery.evaluate_send_eligibility(
            self.record(),
            self.current(comparison_scope_key="royal:watch:usd:per_unit"),
        )
        self.assertEqual(subject["reason"], "subject_mismatch")
        self.assertEqual(scope["reason"], "scope_mismatch")
        self.assertFalse(subject["sendable"])
        self.assertFalse(scope["sendable"])

    def test_unfresh_evidence_is_never_sendable(self):
        decision = delivery.evaluate_send_eligibility(
            self.record(),
            self.current(freshness="failed"),
        )
        self.assertFalse(decision["sendable"])
        self.assertEqual(decision["reason"], "unfresh_current_state")

    def test_same_or_better_price_remains_eligible_but_price_increase_supersedes(self):
        same = delivery.evaluate_send_eligibility(self.record(), self.current(current_value=90.0))
        better = delivery.evaluate_send_eligibility(self.record(), self.current(current_value=80.0))
        higher = delivery.evaluate_send_eligibility(self.record(), self.current(current_value=110.0))
        self.assertTrue(same["sendable"])
        self.assertTrue(better["sendable"])
        self.assertFalse(higher["sendable"])
        self.assertTrue(higher["supersede"])
        self.assertEqual(higher["reason"], "price_increased")

    def test_dispatching_state_is_ambiguous_not_retryable(self):
        decision = delivery.evaluate_send_eligibility(
            self.record(delivery_state="dispatching", attempt_id="nda1_demo"),
            self.current(),
        )
        self.assertFalse(decision["sendable"])
        self.assertEqual(decision["reason"], "not_retryable_state")

    def test_attempt_id_is_deterministic_per_intent_and_attempt(self):
        first = delivery.deterministic_attempt_id("ni1_demo", 1)
        repeat = delivery.deterministic_attempt_id("ni1_demo", 1)
        second = delivery.deterministic_attempt_id("ni1_demo", 2)
        self.assertEqual(first, repeat)
        self.assertNotEqual(first, second)


class ScriptedD1Base:
    SAILING_ID = "synthetic-sailing"

    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = []

    def d1_request(self, batch):
        self.calls.append(batch)
        if not self.scripted:
            raise AssertionError("unexpected D1 call")
        outcome = self.scripted.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    @staticmethod
    def booking_id(profile):
        return "synthetic-booking"


class DeliveryStoreTests(unittest.TestCase):
    def test_claim_uses_compare_and_set_and_increments_attempt_only_after_change(self):
        base = ScriptedD1Base([[{"success": True, "meta": {"changes": 1}}]])
        profile = SimpleNamespace(label="primary")
        store = delivery.DeliveryStore(base, profile)
        record = {
            "intent_id": "ni1_demo",
            "intent": {},
            "delivery_state": "pending",
            "evidence_state": "committed",
            "attempt_count": 0,
            "attempt_id": None,
        }
        claimed = store.claim(record)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["delivery_state"], "dispatching")
        self.assertEqual(claimed["attempt_count"], 1)
        self.assertTrue(claimed["attempt_id"].startswith("nda1_"))
        sql = base.calls[0][0]["sql"]
        self.assertIn("delivery_state IN ('pending','failed')", sql)
        self.assertIn("evidence_state='committed'", sql)
        self.assertIn("attempt_id IS NULL", sql)

    def test_claim_loser_does_not_send(self):
        base = ScriptedD1Base([[{"success": True, "meta": {"changes": 0}}]])
        store = delivery.DeliveryStore(base, SimpleNamespace(label="primary"))
        record = {
            "intent_id": "ni1_demo",
            "intent": {},
            "delivery_state": "pending",
            "evidence_state": "committed",
            "attempt_count": 0,
            "attempt_id": None,
        }
        self.assertIsNone(store.claim(record))

    def test_availability_contiguity_requires_adjacent_successful_provider_runs(self):
        class ContinuityBase:
            SAILING_ID = "synthetic-sailing"
            @staticmethod
            def booking_id(_profile): return "synthetic-booking"
            def __init__(self, proven): self.proven, self.calls = proven, []
            def d1_request(self, batch):
                self.calls.append(batch)
                return [{"results": [{"status":"sold_out","prior_observed_at":"2030-01-02T11:00:00Z","continuity_proven":self.proven}]}]
        for proven, expected in [(1, True), (0, False)]:
            base = ContinuityBase(proven)
            store = delivery.DeliveryStore(base, SimpleNamespace(label="primary"))
            self.assertEqual(store.availability_contiguous("beverage:demo:adult", "adult", "2030-01-02T12:00:00Z"), expected)
            sql = base.calls[0][0]["sql"]
            self.assertIn("notification_observation_runs_v1", sql)
            self.assertIn("NOT EXISTS", sql)

    def test_release_claim_is_attempt_guarded_and_returns_pending(self):
        base = ScriptedD1Base([[{"success": True, "meta": {"changes": 1}}]])
        store = delivery.DeliveryStore(base, SimpleNamespace(label="primary"))
        record = {"intent_id": "ni1_demo", "attempt_id": "nda1_demo"}
        self.assertTrue(store.release_claim(record))
        sql = base.calls[0][0]["sql"]
        self.assertIn("delivery_state='pending'", sql)
        self.assertIn("delivery_state='dispatching' AND attempt_id=?", sql)
        self.assertIn("attempt_id=NULL", sql)

    def test_record_provider_run_uses_sanitized_continuity_table(self):
        base = ScriptedD1Base([
            [{"success": True, "meta": {"changes": 0}}] * 4,
            [{"success": True, "meta": {"changes": 1}}],
            [{"results": [{"outcome": "started"}]}],
            [{"success": True, "meta": {"changes": 0}}] * 4,
            [{"success": True, "meta": {"changes": 1}}],
            [{"results": [{"outcome": "provider_failed"}]}],
        ])
        store = delivery.DeliveryStore(base, SimpleNamespace(label="primary"))
        self.assertTrue(store.record_provider_run("2030-01-02T12:00:00Z", None))
        self.assertTrue(any("notification_observation_runs_v1" in q["sql"] for q in base.calls[0]))
        insert = base.calls[1][0]
        self.assertIn("ON CONFLICT(household_id,sailing_id,run_at)", insert["sql"])
        self.assertEqual(insert["params"], ["primary", "synthetic-sailing", "2030-01-02T12:00:00Z", "started"])
        self.assertIn("SELECT outcome", base.calls[2][0]["sql"])
        self.assertTrue(store.record_provider_run("2030-01-02T12:00:00Z", False))
        self.assertEqual(base.calls[4][0]["params"][-1], "provider_failed")

    def test_provider_run_readback_mismatch_fails_confirmation(self):
        base = ScriptedD1Base([
            [{"success": True, "meta": {"changes": 0}}] * 4,
            [{"success": True, "meta": {"changes": 1}}],
            [{"results": [{"outcome": "provider_failed"}]}],
        ])
        store = delivery.DeliveryStore(base, SimpleNamespace(label="primary"))
        self.assertFalse(store.record_provider_run("2030-01-02T12:00:00Z", None))

    def test_known_failure_returns_dispatching_attempt_to_retryable_failed(self):
        base = ScriptedD1Base([[{"success": True, "meta": {"changes": 1}}]])
        store = delivery.DeliveryStore(base, SimpleNamespace(label="primary"))
        record = {"intent_id": "ni1_demo", "attempt_id": "nda1_demo"}
        self.assertTrue(store.record_result(record, False))
        sql = base.calls[0][0]["sql"]
        self.assertIn("delivery_state='failed'", sql)
        self.assertIn("attempt_id=NULL", sql)
        self.assertIn("delivery_state='dispatching'", sql)

    def test_success_ack_is_guarded_by_attempt_id(self):
        base = ScriptedD1Base([[{"success": True, "meta": {"changes": 1}}]])
        store = delivery.DeliveryStore(base, SimpleNamespace(label="primary"))
        record = {"intent_id": "ni1_demo", "attempt_id": "nda1_demo"}
        self.assertTrue(store.record_result(record, True))
        sql = base.calls[0][0]["sql"]
        self.assertIn("delivery_state='delivered'", sql)
        self.assertIn("delivery_state='dispatching'", sql)
        self.assertIn("attempt_id=?", sql)


class MainOrderingTests(unittest.TestCase):
    def _modules(self, events, send_success=True):
        profile = SimpleNamespace(
            label="primary",
            username="u",
            password="p",
            apprise_url="configured",
            cabin="1234",
            reservation_id="synthetic-private-runtime-only",
        )
        spec = SimpleNamespace(
            name="Synthetic Package",
            prefix="beverage",
            product_id="beverage:demo:adult",
        )

        class Base:
            SAILING_ID = "synthetic-sailing"
            SHIP_NAME = "Synthetic Ship"
            SAIL_DATE = "2030-01-01"
            SHIP_CODE = "SS"
            NIGHTS = 3
            ROLE_LABELS = {"adult": "Adult"}
            SPEC_BY_ID = {"beverage:demo:adult": spec}

            @staticmethod
            def load_profiles():
                return [profile]

            @staticmethod
            def require(_name, _value):
                return None

            @staticmethod
            def truthy(value):
                return str(value or "").lower() in {"1", "true", "yes", "on"}

            @staticmethod
            def env(name, default=""):
                if name == "SUPPRESS_ALERTS":
                    return ""
                if name == "NOTIFICATION_TEST":
                    return ""
                return default

            @staticmethod
            def run_upstream(_profile, _cfg, _path):
                events.append("provider")
                return 0, []

            @staticmethod
            def parse_history(_profile, _watchlist, _lines):
                return {
                    "observed_at": "2030-01-02T12:00:00Z",
                    "cabin_fare": None,
                    "products": [{
                        "product_id": "beverage:demo:adult",
                        "traveler_role": "adult",
                        "current": 90.0,
                    }],
                    "availability": [],
                }

            @staticmethod
            def persist_history(_profile, _snapshot):
                events.append("history")
                return 1

            @staticmethod
            def send_apprise(_profile, _title, _body):
                events.append("send")
                return send_success

            @staticmethod
            def classify(_prefix):
                return ("beverage", "per_cruise_day")

            @staticmethod
            def royal_product_url(_profile, _spec):
                return "https://example.invalid/product"

        class State:
            available = True

        class Adaptive:
            MAX_WATCH_SPECS = 6

            @staticmethod
            def history_state(_profile):
                return State()

            @staticmethod
            def build_config(_profile, _state):
                return {}, [], "synthetic", []

            @staticmethod
            def reconcile_watch_snapshot(snapshot, _state):
                return snapshot, 0

            @staticmethod
            def parse_purchases(_profile, _lines, _observed_at):
                return []

            @staticmethod
            def build_alerts(_profile, _snapshot, _purchases, _state):
                return [{
                    "kind": "watch_new_low",
                    "product_id": "beverage:demo:adult",
                    "role": "adult",
                    "current": 90.0,
                    "previous_low": 100.0,
                    "savings": 30.0,
                    "spec": spec,
                }]

            @staticmethod
            def persist_purchases(_profile, _purchases):
                events.append("purchases")
                return 0

            @staticmethod
            def append_summary(*_args):
                events.append("summary")

        return Base, Adaptive

    def test_unconfirmed_start_marker_blocks_provider_acquisition(self):
        events = []
        Base, Adaptive = self._modules(events)
        class Store:
            def __init__(self, *_args): pass
            def record_provider_run(self, _run_at, _success):
                events.append("marker-attempt")
                return False
        code = delivery.main(Adaptive, Base, store_factory=Store)
        self.assertEqual(code, 1)
        self.assertNotIn("provider", events)
        self.assertEqual(events, ["marker-attempt"])

    def test_provider_exception_records_started_then_failed_before_reraising(self):
        events = []
        Base, Adaptive = self._modules(events)
        Base.run_upstream = staticmethod(lambda *_args: (_ for _ in ()).throw(RuntimeError("synthetic provider exception")))
        class Store:
            def __init__(self, *_args): pass
            def record_provider_run(self, run_at, success): events.append(("marker", run_at, success)); return True
        with self.assertRaisesRegex(RuntimeError, "synthetic provider exception"):
            delivery.main(Adaptive, Base, store_factory=Store)
        markers = [e for e in events if isinstance(e, tuple) and e[0] == "marker"]
        self.assertEqual([m[2] for m in markers], [None, False])
        self.assertEqual(markers[0][1], markers[1][1])

    def test_success_marker_precedes_parse_and_parse_exception_leaves_success_gap_marker(self):
        events = []
        Base, Adaptive = self._modules(events)
        Base.parse_history = staticmethod(lambda *_args: (_ for _ in ()).throw(RuntimeError("synthetic parse exception")))
        class Store:
            def __init__(self, *_args): pass
            def record_provider_run(self, run_at, success): events.append(("marker", run_at, success)); return True
        with self.assertRaisesRegex(RuntimeError, "synthetic parse exception"):
            delivery.main(Adaptive, Base, store_factory=Store)
        markers = [e for e in events if isinstance(e, tuple) and e[0] == "marker"]
        self.assertEqual([m[2] for m in markers], [None, True])
        self.assertEqual(markers[0][1], markers[1][1])
        self.assertLess(events.index(markers[1]), len(events))

    def test_initial_intent_write_failure_retries_same_id_after_history_persists(self):
        events = []
        Base, Adaptive = self._modules(events)

        class Store:
            def __init__(self, _base, _profile):
                self.stage_calls = 0
                self.intent = None

            def record_provider_run(self, _run_at, _success):
                events.append("provider-marker")
                return True

            def stage(self, intents):
                self.stage_calls += 1
                self.intent = intents[0]
                events.append(f"stage{self.stage_calls}:{self.intent['intent_id']}")
                if self.stage_calls == 1:
                    raise RuntimeError("synthetic first stage failure")

            def promote_verified_evidence(self):
                events.append("promote")
                return 1

            def load_open(self):
                events.append("load")
                return [{
                    "intent_id": self.intent["intent_id"],
                    "intent": self.intent,
                    "delivery_state": "pending",
                    "evidence_state": "committed",
                    "attempt_count": 0,
                    "attempt_id": None,
                }]

            def supersede(self, _record, _reason):
                raise AssertionError("should not supersede")

            def claim(self, record):
                events.append("claim")
                return {
                    **record,
                    "delivery_state": "dispatching",
                    "attempt_count": 1,
                    "attempt_id": "nda1_demo",
                }

            def record_result(self, _record, _success):
                events.append("result")
                return True

            def write_legacy_delivery_event(self, _record):
                events.append("legacy")

        code = delivery.main(Adaptive, Base, store_factory=Store)
        self.assertEqual(code, 0)
        stage_events = [event for event in events if event.startswith("stage")]
        self.assertEqual(len(stage_events), 2)
        self.assertEqual(stage_events[0].split(":", 1)[1], stage_events[1].split(":", 1)[1])
        self.assertLess(events.index("history"), events.index(stage_events[1]))
        self.assertLess(events.index("promote"), events.index("send"))

    def test_partial_group_claim_releases_claimed_rows_and_never_sends_subset(self):
        events = []
        Base, Adaptive = self._modules(events, send_success=True)
        second_spec = SimpleNamespace(name="Synthetic Package 2", prefix="beverage", product_id="beverage:demo2:adult")
        Base.SPEC_BY_ID[second_spec.product_id] = second_spec
        original_parse = Base.parse_history
        def parse_history(profile, watchlist, lines):
            snapshot = original_parse(profile, watchlist, lines)
            snapshot["products"].append({"product_id": second_spec.product_id, "traveler_role": "adult", "current": 80.0})
            return snapshot
        Base.parse_history = staticmethod(parse_history)
        Adaptive.build_alerts = staticmethod(lambda *_args: [
            {"kind":"watch_new_low","product_id":"beverage:demo:adult","role":"adult","current":90.0,"previous_low":100.0,"savings":30.0,"spec":Base.SPEC_BY_ID["beverage:demo:adult"]},
            {"kind":"watch_new_low","product_id":second_spec.product_id,"role":"adult","current":80.0,"previous_low":100.0,"savings":60.0,"spec":second_spec},
        ])

        class Store:
            def __init__(self, _base, _profile):
                self.intents = []
                self.claim_count = 0
            def record_provider_run(self, _run_at, _success): events.append("provider-marker"); return True
            def stage(self, intents): self.intents = list(intents); events.append("stage")
            def promote_verified_evidence(self): return len(self.intents)
            def load_open(self):
                return [{"intent_id":i["intent_id"],"intent":i,"delivery_state":"pending","evidence_state":"committed","attempt_count":0,"attempt_id":None} for i in self.intents]
            def supersede(self, *_args): raise AssertionError
            def claim(self, record):
                self.claim_count += 1
                events.append(f"claim{self.claim_count}")
                if self.claim_count == 2: return None
                return {**record,"delivery_state":"dispatching","attempt_count":1,"attempt_id":"nda1_first"}
            def release_claim(self, record):
                events.append("release:"+record["intent_id"])
                return True
            def record_result(self, *_args): raise AssertionError("transport must not run")
            def write_legacy_delivery_event(self, *_args): raise AssertionError

        code = delivery.main(Adaptive, Base, store_factory=Store)
        self.assertEqual(code, 1)
        self.assertEqual(events.count("send"), 0)
        self.assertEqual(len([e for e in events if e.startswith("release:")]), 1)

    def test_transport_success_with_ack_write_failure_returns_nonzero_and_does_not_blind_resend(self):
        events = []
        Base, Adaptive = self._modules(events, send_success=True)

        class Store:
            def __init__(self, _base, _profile):
                self.intent = None

            def record_provider_run(self, _run_at, _success):
                events.append("provider-marker")
                return True

            def stage(self, intents):
                self.intent = intents[0]
                events.append("stage")

            def promote_verified_evidence(self):
                events.append("promote")
                return 1

            def load_open(self):
                return [{
                    "intent_id": self.intent["intent_id"],
                    "intent": self.intent,
                    "delivery_state": "pending",
                    "evidence_state": "committed",
                    "attempt_count": 0,
                    "attempt_id": None,
                }]

            def supersede(self, _record, _reason):
                raise AssertionError

            def claim(self, record):
                events.append("claim")
                return {**record, "delivery_state": "dispatching", "attempt_id": "nda1_demo"}

            def record_result(self, _record, _success):
                events.append("ack-failed")
                raise RuntimeError("synthetic acknowledgement write failure")

            def write_legacy_delivery_event(self, _record):
                raise AssertionError("legacy write must not precede durable acknowledgement")

        code = delivery.main(Adaptive, Base, store_factory=Store)
        self.assertEqual(code, 1)
        self.assertEqual(events.count("send"), 1)
        self.assertIn("ack-failed", events)
        self.assertNotIn("legacy", events)


if __name__ == "__main__":
    unittest.main()

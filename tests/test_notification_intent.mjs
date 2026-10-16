import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  DELIVERY_STATES,
  INTENT_CONTRACT_VERSION,
  deriveIntents,
  groupIntents,
  intentFingerprint,
  recordDeliveryAttempt,
  reconcileNotificationIntents,
  safeIntentLifecycleRecord,
  safeIntentPayload,
} from "../src/notification-intent.mjs";

function state(overrides = {}) {
  return {
    subject_key: "subject:synthetic-demo",
    group_key: "group:synthetic-sailing",
    comparison_scope_key: "scope:usd-total-demo",
    observed_at: "2030-01-02T12:00:00Z",
    freshness: "fresh",
    comparable: true,
    current_value: 100,
    historical_low: 95,
    target_state: "above",
    availability_state: "available",
    availability_continuity: true,
    evidence_label: "synthetic evidence",
    actionability_state: "verify",
    policy_state: "configured",
    ...overrides,
  };
}

const DROP_ANY = Object.freeze({
  meaningful_drop: {
    enabled: true,
    absolute_threshold: 5,
    percent_threshold: 5,
    combine: "ANY",
  },
});

function only(intents, eventType) {
  return intents.filter((intent) => intent.event_type === eventType);
}

test("contract version is explicit and stable", () => {
  assert.equal(INTENT_CONTRACT_VERSION, "alert-intent/v1");
});

test("first observation establishes baseline and emits no generic price change", () => {
  const intents = deriveIntents(null, state({ current_value: 80 }), DROP_ANY);
  assert.deepEqual(intents, []);
});

test("first reached target is silent when bootstrap policy is disabled", () => {
  const intents = deriveIntents(null, state({ target_state: "reached", target_generation: "g1" }), {
    target: { enabled: true, bootstrap: false },
  });
  assert.deepEqual(intents, []);
});

test("configured bootstrap target hit emits one explicit target intent", () => {
  const intents = deriveIntents(null, state({ target_state: "reached", target_generation: "g1" }), {
    target: { enabled: true, bootstrap: true },
  });
  assert.equal(intents.length, 1);
  assert.equal(intents[0].event_type, "target_hit");
  assert.equal(intents[0].transition, "baseline->reached");
});

test("steady reached target does not duplicate", () => {
  const previous = state({ target_state: "reached", target_generation: "g1" });
  const current = state({ target_state: "reached", target_generation: "g1", current_value: 99 });
  assert.deepEqual(deriveIntents(previous, current, { target: { enabled: true } }), []);
});

test("above to reached emits target hit", () => {
  const intents = deriveIntents(
    state({ target_state: "above", current_value: 110 }),
    state({ target_state: "reached", target_generation: "g2", current_value: 95 }),
    { target: { enabled: true } },
  );
  assert.equal(only(intents, "target_hit").length, 1);
});

test("reached to above is a silent re-arm transition", () => {
  const intents = deriveIntents(
    state({ target_state: "reached", current_value: 95 }),
    state({ target_state: "above", current_value: 110 }),
    { target: { enabled: true } },
  );
  assert.deepEqual(intents, []);
});

test("a later re-armed target generation receives a distinct semantic id", () => {
  const first = deriveIntents(
    state({ target_state: "above", current_value: 110 }),
    state({ target_state: "reached", target_generation: "generation-1", current_value: 95 }),
    { target: { enabled: true } },
  )[0];
  const later = deriveIntents(
    state({ target_state: "above", current_value: 108 }),
    state({ target_state: "reached", target_generation: "generation-2", current_value: 95 }),
    { target: { enabled: true } },
  )[0];
  assert.notEqual(first.intent_id, later.intent_id);
});

test("target transitions fail closed without explicit generation identity", () => {
  assert.throws(() => deriveIntents(
    state({ target_state: "above" }),
    state({ target_state: "reached" }),
    { target: { enabled: true } },
  ), /target_generation is required/);
});

test("equal comparable values emit no change intent", () => {
  assert.deepEqual(deriveIntents(state({ current_value: 100 }), state({ current_value: 100 }), DROP_ANY), []);
});

test("epsilon-sized noise does not emit a change intent", () => {
  const policy = { epsilon: 0.01, meaningful_drop: { enabled: true, absolute_threshold: 0, combine: "ANY" } };
  assert.deepEqual(deriveIntents(state({ current_value: 100 }), state({ current_value: 99.995 }), policy), []);
});

test("absolute meaningful-drop threshold emits a comparable drop", () => {
  const intents = deriveIntents(
    state({ current_value: 100 }),
    state({ current_value: 94, price_generation: "94" }),
    { meaningful_drop: { enabled: true, absolute_threshold: 5, combine: "ANY" } },
  );
  assert.equal(only(intents, "meaningful_drop").length, 1);
  assert.equal(only(intents, "meaningful_drop")[0].absolute_delta, 6);
});

test("percentage meaningful-drop threshold emits a comparable drop", () => {
  const intents = deriveIntents(
    state({ current_value: 200 }),
    state({ current_value: 180, price_generation: "180" }),
    { meaningful_drop: { enabled: true, percent_threshold: 9, combine: "ANY" } },
  );
  assert.equal(only(intents, "meaningful_drop").length, 1);
  assert.equal(only(intents, "meaningful_drop")[0].percent_delta, 10);
});

test("ANY threshold policy passes when one configured threshold passes", () => {
  const intents = deriveIntents(
    state({ current_value: 100 }),
    state({ current_value: 94, price_generation: "94" }),
    { meaningful_drop: { enabled: true, absolute_threshold: 5, percent_threshold: 20, combine: "ANY" } },
  );
  assert.equal(only(intents, "meaningful_drop").length, 1);
});

test("ALL threshold policy requires every configured threshold", () => {
  const fail = deriveIntents(
    state({ current_value: 100 }),
    state({ current_value: 94, price_generation: "94" }),
    { meaningful_drop: { enabled: true, absolute_threshold: 5, percent_threshold: 20, combine: "ALL" } },
  );
  assert.equal(only(fail, "meaningful_drop").length, 0);
  const pass = deriveIntents(
    state({ current_value: 100 }),
    state({ current_value: 75, price_generation: "75" }),
    { meaningful_drop: { enabled: true, absolute_threshold: 5, percent_threshold: 20, combine: "ALL" } },
  );
  assert.equal(only(pass, "meaningful_drop").length, 1);
});

test("non-comparable evidence cannot emit misleading price intent", () => {
  assert.deepEqual(deriveIntents(
    state({ current_value: 100 }),
    state({ current_value: 80, comparable: false }),
    DROP_ANY,
  ), []);
});

test("comparison-scope mismatch cannot emit price intent", () => {
  assert.deepEqual(deriveIntents(
    state({ current_value: 100, comparison_scope_key: "scope:a" }),
    state({ current_value: 80, comparison_scope_key: "scope:b" }),
    DROP_ANY,
  ), []);
});

for (const freshness of ["stale", "incomplete", "failed"]) {
  test(`${freshness} latest price evidence cannot emit fresh price or target intent`, () => {
    const intents = deriveIntents(
      state({ current_value: 100, target_state: "above" }),
      state({ current_value: 80, freshness, target_state: "reached", target_generation: "g2" }),
      { ...DROP_ANY, target: { enabled: true } },
    );
    assert.equal(only(intents, "meaningful_drop").length, 0);
    assert.equal(only(intents, "target_hit").length, 0);
  });
}

test("down-only any-change mode ignores an upward move", () => {
  const intents = deriveIntents(
    state({ current_value: 100 }),
    state({ current_value: 110, price_generation: "110" }),
    { any_change: { enabled: true, direction: "DOWN", absolute_threshold: 5, combine: "ANY" } },
  );
  assert.equal(only(intents, "price_change").length, 0);
});

test("bidirectional any-change mode can report an upward move", () => {
  const intents = deriveIntents(
    state({ current_value: 100 }),
    state({ current_value: 110, price_generation: "110" }),
    { any_change: { enabled: true, direction: "BOTH", absolute_threshold: 5, combine: "ANY" } },
  );
  assert.equal(only(intents, "price_change").length, 1);
  assert.equal(only(intents, "price_change")[0].transition, "lower->higher");
});

test("new-low mode emits only when the current value is materially below prior historical low", () => {
  const intents = deriveIntents(
    state({ current_value: 100, historical_low: 90 }),
    state({ current_value: 85, price_generation: "85" }),
    { new_low: { enabled: true } },
  );
  assert.equal(only(intents, "new_low").length, 1);
  const equal = deriveIntents(
    state({ current_value: 100, historical_low: 90 }),
    state({ current_value: 90, price_generation: "90" }),
    { new_low: { enabled: true } },
  );
  assert.equal(only(equal, "new_low").length, 0);
});

test("contiguous unavailable to available evidence emits one restock intent", () => {
  const intents = deriveIntents(
    state({ availability_state: "unavailable", freshness: "fresh" }),
    state({ availability_state: "available", availability_generation: "r1", freshness: "fresh" }),
    { availability: { enabled: true } },
  );
  assert.equal(only(intents, "availability_restored").length, 1);
});

test("failed current evidence cannot become restock", () => {
  const intents = deriveIntents(
    state({ availability_state: "unavailable", freshness: "fresh" }),
    state({ availability_state: "available", availability_generation: "r1", freshness: "failed" }),
    { availability: { enabled: true } },
  );
  assert.equal(only(intents, "availability_restored").length, 0);
});

test("failed previous evidence cannot be treated as a contiguous unavailable fact", () => {
  const intents = deriveIntents(
    state({ availability_state: "unavailable", freshness: "failed" }),
    state({ availability_state: "available", availability_generation: "r1", freshness: "fresh" }),
    { availability: { enabled: true } },
  );
  assert.equal(only(intents, "availability_restored").length, 0);
});

test("explicit availability continuity gap blocks fabricated restock", () => {
  const intents = deriveIntents(
    state({ availability_state: "unavailable", freshness: "fresh" }),
    state({ availability_state: "available", availability_generation: "r1", freshness: "fresh", availability_continuity: false }),
    { availability: { enabled: true } },
  );
  assert.equal(only(intents, "availability_restored").length, 0);
});

test("repeated identical restock generation has the same deterministic id", () => {
  const previous = state({ availability_state: "unavailable" });
  const current = state({ availability_state: "available", availability_generation: "r1" });
  const one = deriveIntents(previous, current, { availability: { enabled: true } })[0];
  const two = deriveIntents(previous, { ...current, observed_at: "2030-01-02T13:00:00Z" }, { availability: { enabled: true } })[0];
  assert.equal(one.intent_id, two.intent_id);
});

test("a later availability generation can produce a distinct legitimate restock id", () => {
  const previous = state({ availability_state: "unavailable" });
  const one = deriveIntents(previous, state({ availability_state: "available", availability_generation: "r1" }), { availability: { enabled: true } })[0];
  const two = deriveIntents(previous, state({ availability_state: "available", availability_generation: "r2" }), { availability: { enabled: true } })[0];
  assert.notEqual(one.intent_id, two.intent_id);
});

test("health intent starts only when repeated failure threshold is crossed", () => {
  const intents = deriveIntents(
    state({ freshness: "failed", comparable: false, current_value: null, consecutive_failures: 2 }),
    state({ freshness: "failed", comparable: false, current_value: null, consecutive_failures: 3, health_generation: "h1" }),
    { health: { enabled: true, failure_threshold: 3 } },
  );
  assert.equal(only(intents, "observation_failed").length, 1);
  const steady = deriveIntents(
    state({ freshness: "failed", comparable: false, current_value: null, consecutive_failures: 3 }),
    state({ freshness: "failed", comparable: false, current_value: null, consecutive_failures: 4, health_generation: "h1" }),
    { health: { enabled: true, failure_threshold: 3 } },
  );
  assert.equal(only(steady, "observation_failed").length, 0);
});

test("safe serialization rejects forbidden private fields", () => {
  assert.throws(() => safeIntentPayload({
    subject_key: "subject:x",
    event_type: "target_hit",
    semantic_generation: "target:g1",
    reservation_id: "synthetic-forbidden",
  }), /Forbidden private field/);
});

test("safe serialization is allowlist-based and omits benign unknown fields", () => {
  const payload = safeIntentPayload({
    subject_key: "subject:x",
    event_type: "target_hit",
    semantic_generation: "target:g1",
    evidence_summary: "synthetic",
    harmless_internal_note: "must not serialize",
  });
  assert.equal(payload.harmless_internal_note, undefined);
  assert.equal(payload.contract_version, INTENT_CONTRACT_VERSION);
});

test("fingerprint is stable across object key insertion order", () => {
  const a = { subject_key: "subject:x", event_type: "target_hit", semantic_generation: "target:g1", transition: "above->reached" };
  const b = { transition: "above->reached", semantic_generation: "target:g1", event_type: "target_hit", subject_key: "subject:x" };
  assert.equal(intentFingerprint(a), intentFingerprint(b));
});

test("formatting and evidence-summary wording do not change semantic fingerprint", () => {
  const base = { subject_key: "subject:x", event_type: "target_hit", semantic_generation: "target:g1", transition: "above->reached" };
  assert.equal(
    intentFingerprint({ ...base, evidence_summary: "wording one" }),
    intentFingerprint({ ...base, evidence_summary: "wording two" }),
  );
});

test("routing and delivery preference do not change semantic fingerprint", () => {
  const base = { subject_key: "subject:x", event_type: "target_hit", semantic_generation: "target:g1", transition: "above->reached" };
  assert.equal(
    intentFingerprint({ ...base, route_class: "route:a", delivery_mode: "immediate" }),
    intentFingerprint({ ...base, route_class: "route:b", delivery_mode: "digest_eligible" }),
  );
});

test("retry or observation timestamp alone does not change semantic fingerprint", () => {
  const base = { subject_key: "subject:x", event_type: "target_hit", semantic_generation: "target:g1", transition: "above->reached" };
  assert.equal(
    intentFingerprint({ ...base, observed_at: "2030-01-01T00:00:00Z" }),
    intentFingerprint({ ...base, observed_at: "2030-01-02T00:00:00Z" }),
  );
});

test("semantic generation is material to fingerprint", () => {
  const base = { subject_key: "subject:x", event_type: "target_hit", transition: "above->reached" };
  assert.notEqual(
    intentFingerprint({ ...base, semantic_generation: "target:g1" }),
    intentFingerprint({ ...base, semantic_generation: "target:g2" }),
  );
});

test("grouping is deterministic regardless of input order and dedupes identical ids", () => {
  const target = deriveIntents(
    state({ target_state: "above", current_value: 110 }),
    state({ target_state: "reached", target_generation: "g1", current_value: 95 }),
    { target: { enabled: true } },
  )[0];
  const drop = deriveIntents(
    state({ current_value: 100 }),
    state({ current_value: 90, price_generation: "90" }),
    DROP_ANY,
  )[0];
  const first = groupIntents([drop, target, target]);
  const second = groupIntents([target, drop]);
  assert.deepEqual(first, second);
  assert.equal(first.flatMap((group) => group.intent_ids).length, 2);
});

test("grouping preserves deterministic priority within a related group", () => {
  const target = deriveIntents(
    state({ target_state: "above", current_value: 110 }),
    state({ target_state: "reached", target_generation: "g1", current_value: 95 }),
    { target: { enabled: true } },
  )[0];
  const drop = deriveIntents(
    state({ current_value: 100 }),
    state({ current_value: 90, price_generation: "90" }),
    DROP_ANY,
  )[0];
  const groups = groupIntents([drop, target]);
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].intents.map((intent) => intent.event_type), ["target_hit", "meaningful_drop"]);
});

test("grouping keeps delivery modes and safe group keys distinct", () => {
  const immediate = safeIntentPayload({
    subject_key: "subject:a",
    group_key: "group:a",
    event_type: "target_hit",
    semantic_generation: "target:g1",
    delivery_mode: "immediate",
  });
  const digest = safeIntentPayload({
    subject_key: "subject:b",
    group_key: "group:a",
    event_type: "price_change",
    semantic_generation: "price:90",
    delivery_mode: "digest_eligible",
  });
  assert.equal(groupIntents([immediate, digest]).length, 2);
});

test("returned intents and groups are immutable", () => {
  const intent = deriveIntents(
    state({ target_state: "above" }),
    state({ target_state: "reached", target_generation: "g1" }),
    { target: { enabled: true } },
  )[0];
  assert.equal(Object.isFrozen(intent), true);
  const groups = groupIntents([intent]);
  assert.equal(Object.isFrozen(groups), true);
  assert.equal(Object.isFrozen(groups[0]), true);
});

test("delivery suppression policy is not modeled as a delivery failure event", () => {
  const intents = deriveIntents(
    state({ current_value: 100 }),
    state({ current_value: 90, price_generation: "90" }),
    { ...DROP_ANY, suppress_delivery: true },
  );
  assert.equal(intents.some((intent) => /delivery.*fail/i.test(intent.event_type)), false);
});

test("safe payload contains no persisted delivery acknowledgement or retry state", () => {
  const payload = safeIntentPayload({
    subject_key: "subject:x",
    event_type: "target_hit",
    semantic_generation: "target:g1",
  });
  for (const field of ["pending", "delivered", "retry_count", "last_attempt_at", "delivered_at", "transport_state"]) {
    assert.equal(Object.hasOwn(payload, field), false);
  }
});

test("semantic module contains no environment, network, filesystem, clock, or randomness access", () => {
  const source = readFileSync(new URL("../src/notification-intent.mjs", import.meta.url), "utf8");
  for (const forbidden of ["process.env", "fetch(", "WebSocket", "node:fs", "Date.now", "new Date(", "Math.random"]){
    assert.equal(source.includes(forbidden), false, `semantic module unexpectedly contains ${forbidden}`);
  }
});

test("safeIntentLifecycleRecord validates lifecycle fields and rejects forbidden private keys", () => {
  const valid = safeIntentLifecycleRecord({
    subject_key: "subject:x",
    event_type: "target_hit",
    semantic_generation: "g1",
  });
  assert.equal(valid.delivery_state, "pending");
  assert.equal(valid.attempt_count, 0);
  assert.equal(valid.last_attempt_at, null);
  assert.equal(valid.delivered_at, null);
  assert.equal(valid.superseded_at, null);

  assert.throws(() => safeIntentLifecycleRecord({
    subject_key: "subject:x",
    event_type: "target_hit",
    semantic_generation: "g1",
    delivery_state: "invalid_state",
  }), /invalid delivery_state/);

  assert.throws(() => safeIntentLifecycleRecord({
    subject_key: "subject:x",
    event_type: "target_hit",
    semantic_generation: "g1",
    apprise_url: "https://synthetic.forbidden",
  }), /Forbidden private field/);
});

test("ALERT-REL-001 matrix 1 & 2: new-low intent delivery success vs failure", () => {
  const previous = state({ current_value: 100, historical_low: 100 });
  const current = state({ current_value: 90, historical_low: 90, price_generation: "90" });
  const policy = { new_low: { enabled: true } };

  const reconciled = reconcileNotificationIntents({ existingRecords: [], currentState: current, previousState: previous, policy });
  assert.equal(reconciled.length, 1);
  assert.equal(reconciled[0].delivery_state, "pending");
  assert.equal(reconciled[0].event_type, "new_low");

  const success = recordDeliveryAttempt(reconciled, { status: "delivered", timestamp: "2030-01-02T12:05:00Z" });
  assert.equal(success[0].delivery_state, "delivered");
  assert.equal(success[0].delivered_at, "2030-01-02T12:05:00Z");

  const failure = recordDeliveryAttempt(reconciled, { status: "failed", timestamp: "2030-01-02T12:05:00Z" });
  assert.equal(failure[0].delivery_state, "failed");
  assert.equal(failure[0].attempt_count, 1);
});

test("ALERT-REL-001 matrix 3, 4, 5: same price next run keeps pending intent retryable without duplicates", () => {
  const p1 = state({ current_value: 100, historical_low: 100 });
  const c1 = state({ current_value: 90, historical_low: 90, price_generation: "90", observed_at: "2030-01-02T12:00:00Z" });
  const policy = { new_low: { enabled: true } };

  const initial = reconcileNotificationIntents({ existingRecords: [], currentState: c1, previousState: p1, policy });
  const failedAttempt = recordDeliveryAttempt(initial, { status: "failed", timestamp: "2030-01-02T12:01:00Z" });

  const c2 = state({ current_value: 90, historical_low: 90, price_generation: "90", observed_at: "2030-01-02T13:00:00Z" });
  const rawDerivedNextRun = deriveIntents(c1, c2, policy);
  assert.equal(rawDerivedNextRun.length, 0);

  const retryReconciled = reconcileNotificationIntents({ existingRecords: failedAttempt, currentState: c2, previousState: c1, policy });
  assert.equal(retryReconciled.length, 1);
  assert.equal(retryReconciled[0].delivery_state, "failed");
  assert.equal(retryReconciled[0].intent_id, failedAttempt[0].intent_id);

  const retrySuccess = recordDeliveryAttempt(retryReconciled, { status: "delivered", timestamp: "2030-01-02T13:02:00Z" });
  assert.equal(retrySuccess[0].delivery_state, "delivered");

  const c3 = state({ current_value: 90, historical_low: 90, price_generation: "90", observed_at: "2030-01-02T14:00:00Z" });
  const run3Reconciled = reconcileNotificationIntents({ existingRecords: retrySuccess, currentState: c3, previousState: c2, policy });
  assert.equal(run3Reconciled.length, 1);
  assert.equal(run3Reconciled[0].delivery_state, "delivered");
});

test("ALERT-REL-001 matrix 6: price rises before retry supersedes pending drop intent", () => {
  const p1 = state({ current_value: 100 });
  const c1 = state({ current_value: 90, price_generation: "90" });
  const policy = DROP_ANY;

  const initial = reconcileNotificationIntents({ existingRecords: [], currentState: c1, previousState: p1, policy });
  const failedAttempt = recordDeliveryAttempt(initial, { status: "failed", timestamp: "2030-01-02T12:01:00Z" });

  const c2 = state({ current_value: 110, price_generation: "110", observed_at: "2030-01-02T13:00:00Z" });
  const reconciled = reconcileNotificationIntents({ existingRecords: failedAttempt, currentState: c2, previousState: c1, policy, timestamp: "2030-01-02T13:00:00Z" });

  assert.equal(reconciled.length, 1);
  assert.equal(reconciled[0].delivery_state, "superseded");
  assert.equal(reconciled[0].superseded_reason, "price_increased");
  assert.equal(reconciled[0].superseded_at, "2030-01-02T13:00:00Z");
});

test("ALERT-REL-001 matrix 7 & 8: restock intent retry and superseding", () => {
  const p1 = state({ availability_state: "unavailable", freshness: "fresh" });
  const c1 = state({ availability_state: "available", freshness: "fresh", availability_generation: "r1" });
  const policy = { availability: { enabled: true } };

  const initial = reconcileNotificationIntents({ existingRecords: [], currentState: c1, previousState: p1, policy });
  const failedAttempt = recordDeliveryAttempt(initial, { status: "failed", timestamp: "2030-01-02T12:01:00Z" });

  const c2 = state({ availability_state: "available", freshness: "fresh", availability_generation: "r1" });
  assert.equal(deriveIntents(c1, c2, policy).length, 0);

  const retryReconciled = reconcileNotificationIntents({ existingRecords: failedAttempt, currentState: c2, previousState: c1, policy });
  assert.equal(retryReconciled.length, 1);
  assert.equal(retryReconciled[0].delivery_state, "failed");

  const c3 = state({ availability_state: "unavailable", freshness: "fresh" });
  const supersededReconciled = reconcileNotificationIntents({ existingRecords: failedAttempt, currentState: c3, previousState: c2, policy, timestamp: "2030-01-02T14:00:00Z" });
  assert.equal(supersededReconciled.length, 1);
  assert.equal(supersededReconciled[0].delivery_state, "superseded");
  assert.equal(supersededReconciled[0].superseded_reason, "became_unavailable");
});

test("ALERT-REL-001 matrix 9: provider failure is distinct from notification delivery failure", () => {
  const p1 = state({ current_value: 100 });
  const c1 = state({ current_value: 90, price_generation: "90" });
  const policy = DROP_ANY;

  const initial = reconcileNotificationIntents({ existingRecords: [], currentState: c1, previousState: p1, policy });
  const failedAttempt = recordDeliveryAttempt(initial, { status: "failed", timestamp: "2030-01-02T12:01:00Z" });

  const c2 = state({ freshness: "failed", comparable: false, current_value: null });
  const reconciled = reconcileNotificationIntents({ existingRecords: failedAttempt, currentState: c2, previousState: c1, policy });

  assert.equal(reconciled.length, 1);
  assert.equal(reconciled[0].delivery_state, "failed");
});

test("ALERT-REL-001 matrix 11: suppressed/proof-mode alerts are treated as policy decisions not transport failures", () => {
  const p1 = state({ current_value: 100 });
  const c1 = state({ current_value: 90, price_generation: "90" });
  const policy = DROP_ANY;

  const initial = reconcileNotificationIntents({ existingRecords: [], currentState: c1, previousState: p1, policy });
  const result = recordDeliveryAttempt(initial, { status: "suppressed", timestamp: "2030-01-02T12:01:00Z", reason: "proof_mode" });

  assert.equal(result[0].delivery_state, "delivered");
  assert.equal(result[0].superseded_reason, "proof_mode");
});

test("ALERT-REL-001 matrix 12: lifecycle records strictly reject forbidden sensitive fields", () => {
  for (const forbidden of ["reservation_id", "booking_id", "apprise_url", "passenger_name", "payment_card"]) {
    assert.throws(() => safeIntentLifecycleRecord({
      subject_key: "subject:x",
      event_type: "target_hit",
      semantic_generation: "g1",
      delivery_state: "pending",
      [forbidden]: "sensitive_data",
    }), /Forbidden private field/);
  }
});

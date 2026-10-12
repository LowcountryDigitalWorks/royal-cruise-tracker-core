import assert from "node:assert/strict";
import test from "node:test";
import {
  groupIntents,
  intentFingerprint,
  safeIntentPayload,
} from "../src/notification-intent.mjs";

test("safeIntentPayload rejects a caller-supplied intent id that does not match semantic identity", () => {
  assert.throws(() => safeIntentPayload({
    intent_id: "ni1_not-the-semantic-id",
    subject_key: "subject:synthetic",
    event_type: "target_hit",
    semantic_generation: "target:g1",
    transition: "above->reached",
  }), /intent_id does not match/);
});

test("safeIntentPayload accepts the exact deterministic semantic id", () => {
  const candidate = {
    subject_key: "subject:synthetic",
    event_type: "target_hit",
    semantic_generation: "target:g1",
    transition: "above->reached",
  };
  const expected = intentFingerprint(candidate);
  const payload = safeIntentPayload({ ...candidate, intent_id: expected });
  assert.equal(payload.intent_id, expected);
});

test("groupIntents fails closed rather than grouping a forged semantic id", () => {
  assert.throws(() => groupIntents([{
    intent_id: "ni1_forged",
    subject_key: "subject:synthetic",
    event_type: "meaningful_drop",
    semantic_generation: "price:90",
    current_value: 90,
  }]), /intent_id does not match/);
});

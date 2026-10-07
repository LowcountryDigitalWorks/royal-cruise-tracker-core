import test from "node:test";
import assert from "node:assert/strict";
import { analyzeHistory } from "../src/decision-history.mjs";

const scope = Object.freeze({
  itemId: "DEMO-ITEM",
  sailingId: "DEMO-SAILING",
  category: "DEMO-CATEGORY",
  variant: "DEMO-VARIANT",
  occupancy: "DEMO-OCCUPANCY",
  promotionScope: "DEMO-PUBLIC",
});

function observation(id, minute, value, overrides = {}) {
  return {
    id,
    observedAt: `2030-01-01T00:${String(minute).padStart(2, "0")}:00Z`,
    status: "valid",
    stale: false,
    value,
    currency: "USD",
    priceBasis: "total",
    taxesFees: "included",
    sourceClass: "synthetic",
    availability: "available",
    scope: { ...scope },
    ...overrides,
    scope: { ...scope, ...(overrides.scope ?? {}) },
  };
}

function failed(id, minute) {
  return observation(id, minute, null, { status: "failed", availability: "unknown" });
}

const target = Object.freeze({
  value: 90,
  currency: "USD",
  priceBasis: "total",
  taxesFees: "included",
  scope: { ...scope },
});

test("vector 0: no observations remains unknown without invented evidence", () => {
  const result = analyzeHistory([]);
  assert.equal(result.latestState, "unknown");
  assert.equal(result.observationCount, 0);
  assert.equal(result.delta, null);
  assert.deepEqual(result.labels, []);
});

test("vector 1: one valid observation establishes context without a drop claim", () => {
  const result = analyzeHistory([observation("a", 1, 100)]);
  assert.equal(result.observationCount, 1);
  assert.equal(result.previous, null);
  assert.equal(result.delta, null);
  assert.deepEqual(result.labels, []);
});

test("vector 2: two comparable observations use the first as previous", () => {
  const result = analyzeHistory([observation("a", 1, 100), observation("b", 2, 95)]);
  assert.equal(result.previous.id, "a");
  assert.deepEqual(result.delta, { comparable: true, reasons: [], value: -5, percent: -5 });
});

test("vector 3: three observations use the immediately previous valid comparable observation", () => {
  const result = analyzeHistory([observation("a", 1, 120), observation("b", 2, 110), observation("c", 3, 105)]);
  assert.equal(result.previous.id, "b");
  assert.equal(result.delta.value, -5);
});

test("vector 4: repeated equal values report no meaningful change", () => {
  const result = analyzeHistory([observation("a", 1, 100), observation("b", 2, 100)]);
  assert.equal(result.delta.value, 0);
  assert.deepEqual(result.labels, ["no meaningful change"]);
});

test("vector 5: drop then rise uses the immediately previous point", () => {
  const result = analyzeHistory([observation("a", 1, 100), observation("b", 2, 80), observation("c", 3, 90)]);
  assert.equal(result.previous.id, "b");
  assert.equal(result.delta.value, 10);
  assert.equal(result.recordLow, 80);
  assert.deepEqual(result.labels, []);
});

test("vector 6: rise then drop uses the immediately previous point", () => {
  const result = analyzeHistory([observation("a", 1, 80), observation("b", 2, 100), observation("c", 3, 90)]);
  assert.equal(result.previous.id, "b");
  assert.equal(result.delta.value, -10);
  assert.equal(result.recordLow, 80);
  assert.deepEqual(result.labels, ["meaningful drop"]);
});

test("vector 7: a failed observation is retained as raw evidence but skipped for price comparison", () => {
  const result = analyzeHistory([observation("a", 1, 100), failed("failure", 2), observation("b", 3, 90)]);
  assert.equal(result.rawObservationCount, 3);
  assert.equal(result.observationCount, 2);
  assert.equal(result.previous.id, "a");
  assert.equal(result.delta.value, -10);
});

test("vector 8: available to unavailable to available produces a single restock transition", () => {
  const result = analyzeHistory([
    observation("a", 1, 100, { availability: "available" }),
    observation("b", 2, 100, { availability: "unavailable" }),
    observation("c", 3, 100, { availability: "available" }),
  ]);
  assert.deepEqual(result.availability, { current: "available", transition: "available-again", restock: true });
  assert.ok(result.labels.includes("available again"));
});

test("vector 9: provider failure breaks availability continuity and cannot imply restock", () => {
  const result = analyzeHistory([
    observation("a", 1, 100, { availability: "unavailable" }),
    failed("failure", 2),
    observation("b", 3, 95, { availability: "available" }),
  ]);
  assert.deepEqual(result.availability, { current: "available", transition: null, restock: false });
  assert.ok(!result.labels.includes("available again"));
});

test("vector 10: exact duplicate observation ids are idempotently de-duplicated", () => {
  const a = observation("a", 1, 100);
  const b = observation("b", 2, 90);
  const result = analyzeHistory([a, b, { ...b, scope: { ...b.scope } }]);
  assert.equal(result.rawObservationCount, 2);
  assert.equal(result.duplicateCount, 1);
  assert.equal(result.observationCount, 2);
});

test("vector 11: out-of-order timestamps fail closed", () => {
  assert.throws(
    () => analyzeHistory([observation("later", 2, 100), observation("earlier", 1, 90)]),
    /strictly chronological/,
  );
});

test("vector 12: record low is computed across valid comparable history", () => {
  const result = analyzeHistory([observation("a", 1, 110), observation("b", 2, 85), observation("c", 3, 95)]);
  assert.equal(result.recordLow, 85);
});

test("vector 13: observation count and recent range are explicit", () => {
  const result = analyzeHistory(
    [observation("a", 1, 110), observation("b", 2, 100), observation("c", 3, 90), observation("d", 4, 95)],
    { recentWindow: 3 },
  );
  assert.equal(result.observationCount, 4);
  assert.deepEqual(result.recentRange, { min: 90, max: 100, count: 3 });
  assert.deepEqual(result.priorRecentRange, { min: 90, max: 110, count: 3 });
});

test("vector 14: currency and scope mismatch are explicitly non-comparable", () => {
  const currencyMismatch = analyzeHistory([
    observation("a", 1, 100),
    observation("b", 2, 95, { currency: "CAD" }),
  ]);
  assert.equal(currencyMismatch.comparisonToLatestPriorPrice.comparable, false);
  assert.ok(currencyMismatch.comparisonToLatestPriorPrice.reasons.includes("currency-mismatch"));
  assert.equal(currencyMismatch.delta, null);

  for (const [key, expectedReason] of [["category", "scope-category-mismatch"], ["variant", "scope-variant-mismatch"], ["occupancy", "scope-occupancy-mismatch"]]) {
    const scopeMismatch = analyzeHistory([
      observation("a", 1, 100),
      observation("b", 2, 95, { scope: { [key]: "DEMO-OTHER" } }),
    ]);
    assert.equal(scopeMismatch.comparisonToLatestPriorPrice.comparable, false);
    assert.ok(scopeMismatch.comparisonToLatestPriorPrice.reasons.includes(expectedReason));
    assert.equal(scopeMismatch.delta, null);
  }
});

test("vector 15: stale latest observation stays stale and suppresses decision labels and alerts", () => {
  const result = analyzeHistory([observation("a", 1, 100), observation("b", 2, 80, { stale: true })], { target });
  assert.equal(result.latestState, "stale");
  assert.equal(result.freshPriceEvidence, false);
  assert.deepEqual(result.labels, ["stale observation"]);
  assert.equal(result.target.reached, true);
  assert.equal(result.target.alert, false);
  assert.equal(result.target.event, null);
});

test("vector 16: first target threshold hit alerts once", () => {
  const result = analyzeHistory([observation("a", 1, 100), observation("b", 2, 89)], { target });
  assert.deepEqual(result.target, { status: "comparable", reached: true, event: "hit", alert: true, reasons: [] });
  assert.ok(result.labels.includes("target reached"));
});

test("vector 17: unchanged threshold condition does not repeatedly alert without re-arm", () => {
  const steady = analyzeHistory([observation("a", 1, 100), observation("b", 2, 89), observation("c", 3, 88)], { target });
  assert.deepEqual(steady.target, { status: "comparable", reached: true, event: "steady-reached", alert: false, reasons: [] });

  const rearmed = analyzeHistory([observation("a", 1, 100), observation("b", 2, 89), observation("c", 3, 95)], { target });
  assert.deepEqual(rearmed.target, { status: "comparable", reached: false, event: "rearmed", alert: false, reasons: [] });

  const hitAgain = analyzeHistory([
    observation("a", 1, 100),
    observation("b", 2, 89),
    observation("c", 3, 95),
    observation("d", 4, 87),
  ], { target });
  assert.deepEqual(hitAgain.target, { status: "comparable", reached: true, event: "hit", alert: true, reasons: [] });
});

test("unknown policy remains unknown; configured policy is descriptive only", () => {
  assert.deepEqual(analyzeHistory([observation("a", 1, 100)]).policy, { status: "unknown" });
  const result = analyzeHistory([observation("a", 1, 100)], {
    policy: {
      status: "configured",
      sourceClass: "synthetic-config",
      market: "DEMO-MARKET",
      effectiveDate: "2030-01-01",
      bookingCreatedDate: "2030-01-02",
      deadline: "2030-02-01T12:00:00Z",
    },
  });
  assert.deepEqual(result.policy, {
    status: "configured",
    sourceClass: "synthetic-config",
    market: "DEMO-MARKET",
    effectiveDate: "2030-01-01",
    bookingCreatedDate: "2030-01-02",
    deadline: "2030-02-01T12:00:00Z",
  });
  assert.ok(!JSON.stringify(result).includes("reprice eligible"));
});

test("duplicate equality is order-independent for semantically identical scope", () => {
  const a = observation("a", 1, 100);
  const b = observation("b", 2, 90);
  const reorderedScope = Object.fromEntries(Object.entries(b.scope).reverse());
  const result = analyzeHistory([a, b, { ...b, scope: reorderedScope }]);
  assert.equal(result.duplicateCount, 1);
  assert.equal(result.rawObservationCount, 2);
});

test("duplicate equality fails closed when a material scope field changes", () => {
  const b = observation("b", 2, 90);
  assert.throws(
    () => analyzeHistory([b, { ...b, scope: { ...b.scope, variant: "DEMO-OTHER" } }]),
    /conflicting duplicate observation id/,
  );
});

test("latest valid observation without a value keeps older price history but emits no fresh price labels", () => {
  const result = analyzeHistory([
    observation("a", 1, 100),
    observation("b", 2, 80),
    observation("c", 3, null),
  ], { target });
  assert.equal(result.latest.id, "c");
  assert.equal(result.current.id, "b");
  assert.equal(result.previous.id, "a");
  assert.equal(result.freshPriceEvidence, false);
  assert.equal(result.target.reached, true);
  assert.equal(result.target.event, null);
  assert.equal(result.target.alert, false);
  assert.deepEqual(result.labels, []);
});

test("latest valid observation with unknown price basis emits no fresh price labels", () => {
  const result = analyzeHistory([
    observation("a", 1, 100),
    observation("b", 2, 80),
    observation("c", 3, 70, { priceBasis: null }),
  ], { target });
  assert.equal(result.latest.id, "c");
  assert.equal(result.current.id, "b");
  assert.equal(result.freshPriceEvidence, false);
  assert.equal(result.target.event, null);
  assert.equal(result.target.alert, false);
  assert.deepEqual(result.labels, []);
});

test("latest valid observation with incomplete comparable scope emits no fresh price labels", () => {
  const result = analyzeHistory([
    observation("a", 1, 100),
    observation("b", 2, 80),
    observation("c", 3, 70, { scope: { variant: null } }),
  ], { target });
  assert.equal(result.latest.id, "c");
  assert.equal(result.current.id, "b");
  assert.equal(result.freshPriceEvidence, false);
  assert.equal(result.target.event, null);
  assert.equal(result.target.alert, false);
  assert.deepEqual(result.labels, []);
});

test("availability remains independently current when latest price evidence is incomplete", () => {
  const result = analyzeHistory([
    observation("a", 1, 100, { availability: "unavailable" }),
    observation("b", 2, null, { availability: "available" }),
  ]);
  assert.equal(result.current.id, "a");
  assert.equal(result.freshPriceEvidence, false);
  assert.deepEqual(result.availability, { current: "available", transition: "available-again", restock: true });
  assert.deepEqual(result.labels, ["available again"]);
});

test("below-prior-range label is tied to an explicit prior-only window", () => {
  const result = analyzeHistory([
    observation("a", 1, 100),
    observation("b", 2, 105),
    observation("c", 3, 90),
  ]);
  assert.deepEqual(result.priorRecentRange, { min: 100, max: 105, count: 2 });
  assert.deepEqual(result.recentRange, { min: 90, max: 105, count: 3 });
  assert.ok(result.labels.includes("below prior recent observed range"));
});

test("current equal to prior minimum is not below the prior recent range", () => {
  const result = analyzeHistory([
    observation("a", 1, 100),
    observation("b", 2, 105),
    observation("c", 3, 100),
  ]);
  assert.equal(result.priorRecentRange.min, 100);
  assert.ok(!result.labels.includes("below prior recent observed range"));
});

test("current above prior minimum is not below the prior recent range", () => {
  const result = analyzeHistory([
    observation("a", 1, 100),
    observation("b", 2, 105),
    observation("c", 3, 102),
  ]);
  assert.equal(result.priorRecentRange.min, 100);
  assert.ok(!result.labels.includes("below prior recent observed range"));
});

test("one prior observation is insufficient for a below-prior-range claim", () => {
  const result = analyzeHistory([observation("a", 1, 100), observation("b", 2, 90)]);
  assert.deepEqual(result.priorRecentRange, { min: 100, max: 100, count: 1 });
  assert.ok(!result.labels.includes("below prior recent observed range"));
});

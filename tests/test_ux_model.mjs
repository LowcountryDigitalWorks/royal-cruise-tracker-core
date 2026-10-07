import test from "node:test";
import assert from "node:assert/strict";
import { MATRIX_CASES, UX_FIXTURES, getFixture } from "../src/ux-fixtures.mjs";
import { buildPrototypeView } from "../src/ux-model.mjs";

function view(id) {
  return buildPrototypeView(getFixture(id));
}

function fixture(id) {
  return getFixture(id);
}

test("UX-001 state matrix covers every required case 1 through 20", () => {
  assert.deepEqual([...MATRIX_CASES.keys()].sort((a, b) => a - b), Array.from({ length: 20 }, (_, index) => index + 1));
  assert.equal(MATRIX_CASES.get(1).length, 2, "case 1 must explicitly show both zero and first-observation states");
  assert.equal(UX_FIXTURES.length, 21);
});

test("case 1: zero observations never fabricates a price, direction, or change", () => {
  const result = view("case-01-zero");
  assert.equal(result.health.text, "No observations yet");
  assert.equal(result.value.value, "—");
  assert.equal(result.analysis.delta, null);
  assert.equal(result.historyRows.length, 0);
  assert.equal(result.changes.entries.length, 0);
});

test("case 1: first observation establishes context without a historical drop claim", () => {
  const result = view("case-01-first");
  assert.equal(result.analysis.observationCount, 1);
  assert.equal(result.analysis.previous, null);
  assert.equal(result.delta, "Not available");
  assert.ok(!result.evidenceLabels.includes("Meaningful drop"));
});

test("case 2: two comparable observations use the first as previous", () => {
  const result = view("case-02-two");
  assert.equal(result.analysis.previous.id, "two-a");
  assert.equal(result.analysis.current.id, "two-b");
  assert.equal(result.comparison.status, "comparable");
});

test("case 3: 3+ history keeps immediately previous comparable evidence", () => {
  const result = view("case-03-multi");
  assert.equal(result.analysis.previous.id, "multi-c");
  assert.equal(result.analysis.current.id, "multi-d");
  assert.equal(result.analysis.observationCount, 4);
});

test("case 4: equal values surface no meaningful change", () => {
  const result = view("case-04-equal");
  assert.ok(result.evidenceLabels.includes("No meaningful change"));
  assert.equal(result.analysis.delta.value, 0);
  assert.ok(result.changes.entries.some((entry) => entry.title === "No meaningful change"));
});

test("case 5: meaningful drop is visible without predictive language", () => {
  const result = view("case-05-drop");
  assert.deepEqual(result.evidenceLabels, ["Meaningful drop"]);
  assert.ok(result.changes.entries.some((entry) => entry.title === "Meaningful drop"));
});

test("case 6: rise after drop compares against the immediate previous point", () => {
  const result = view("case-06-rise");
  assert.equal(result.analysis.previous.id, "rise-b");
  assert.ok(result.analysis.delta.value > 0);
  assert.ok(result.changes.entries.some((entry) => entry.title === "Observed value increased"));
});

test("case 7: record low is explicit and not color-only", () => {
  const result = view("case-07-low");
  assert.equal(result.analysis.recordLow, 86);
  assert.ok(result.evidenceLabels.includes("New low observed"));
  assert.ok(result.historyRows.some((row) => row.markers.includes("Record low")));
});

test("case 8: target first hit alerts once", () => {
  const result = view("case-08-target-hit");
  assert.equal(result.analysis.target.event, "hit");
  assert.equal(result.analysis.target.alert, true);
  assert.ok(result.changes.entries.some((entry) => entry.title === "Target reached"));
});

test("case 9: steady reached target does not create a repeated latest alert", () => {
  const result = view("case-09-target-steady");
  assert.equal(result.analysis.target.event, "steady-reached");
  assert.equal(result.analysis.target.alert, false);
  assert.match(result.changes.note, /No new target alert/);
  assert.equal(result.changes.entries.filter((entry) => entry.title === "Target reached").length, 1, "the threshold crossing appears once and is not repeated by steady state");
});

test("case 10: threshold re-arms and can later hit again", () => {
  const result = view("case-10-target-rearm");
  const titles = result.changes.entries.map((entry) => entry.title);
  assert.equal(result.analysis.target.event, "hit");
  assert.equal(result.analysis.target.alert, true);
  assert.ok(titles.includes("Target condition re-armed"));
  assert.equal(titles.filter((title) => title === "Target reached").length, 2);
  const latestTargetHit = result.changes.entries.find((entry) => entry.title === "Target reached");
  assert.match(latestTargetHit.detail, /again after a re-arm/);
});

test("case 11: contiguous unavailable to available evidence yields restock", () => {
  const result = view("case-11-restock");
  assert.equal(result.analysis.availability.restock, true);
  assert.ok(result.evidenceLabels.includes("Available again"));
  assert.ok(result.changes.entries.some((entry) => entry.title === "Available again"));
});

test("case 12: provider failure breaks restock continuity", () => {
  const result = view("case-12-failure-gap");
  assert.equal(result.analysis.availability.restock, false);
  assert.ok(!result.evidenceLabels.includes("Available again"));
  assert.match(result.changes.note, /provider failure broke continuity/);
  assert.ok(result.changes.entries.some((entry) => entry.title === "Observation failed"));
});

test("case 13: stale latest evidence is labeled stale and cannot emit a fresh event", () => {
  const result = view("case-13-stale");
  assert.equal(result.analysis.latestState, "stale");
  assert.equal(result.analysis.freshPriceEvidence, false);
  assert.equal(result.health.text, "Observation stale");
  assert.ok(result.evidenceLabels.includes("Observation stale"));
  assert.equal(result.analysis.target.alert, false);
});

test("case 14: failed latest evidence is not converted to sold out or a current price", () => {
  const result = view("case-14-failed");
  assert.equal(result.analysis.latestState, "failed");
  assert.equal(result.value.value, "Unavailable");
  assert.equal(result.availability.text, "Observation unknown");
  assert.ok(result.evidenceLabels.includes("Observation failed"));
  assert.ok(!JSON.stringify(result).toLowerCase().includes("sold out"));
});

test("case 15: incomplete latest price keeps old price historical only while availability remains independent", () => {
  const result = view("case-15-incomplete");
  assert.equal(result.analysis.freshPriceEvidence, false);
  assert.equal(result.value.value, "Incomplete");
  assert.match(result.value.qualifier, /Last complete price/);
  assert.equal(result.delta, "Not available");
  assert.equal(result.analysis.target.alert, false);
  assert.equal(result.availability.text, "Available");
  assert.ok(result.changes.entries.some((entry) => entry.title === "Latest price incomplete"));
});

test("case 16: currency, price basis, source, and scope mismatch render comparison unavailable", () => {
  const result = view("case-16-noncomparable");
  const reasons = result.analysis.comparisonToLatestPriorPrice.reasons;
  assert.equal(result.comparison.status, "non-comparable");
  assert.ok(result.evidenceLabels.includes("Comparison unavailable"));
  assert.ok(reasons.includes("currency-mismatch"));
  assert.ok(reasons.includes("priceBasis-mismatch"));
  assert.ok(reasons.includes("taxesFees-mismatch"));
  assert.ok(reasons.includes("sourceClass-mismatch"));
  assert.ok(reasons.includes("scope-category-mismatch"));
  assert.ok(reasons.includes("scope-variant-mismatch"));
  assert.ok(reasons.includes("scope-occupancy-mismatch"));
  assert.ok(reasons.includes("scope-promotionScope-mismatch"));
});

test("case 17: missing policy stays explicitly unknown", () => {
  const result = view("case-17-policy-unknown");
  assert.equal(result.analysis.policy.status, "unknown");
  assert.equal(result.policy.title, "Policy / deadline unknown");
});

test("case 18: configured policy remains descriptive rather than entitlement language", () => {
  const result = view("case-18-policy-configured");
  assert.equal(result.analysis.policy.status, "configured");
  assert.equal(result.policy.title, "Policy configured descriptively");
  assert.match(result.policy.detail, /DEMO-MARKET/);
  assert.match(result.policy.detail, /Verify eligibility/);
});

test("case 19: one prior point is not treated as a prior-range claim", () => {
  const result = view("case-19-range-insufficient");
  assert.equal(result.analysis.priorRecentRange.count, 1);
  assert.ok(!result.evidenceLabels.includes("Below prior recent observed range"));
});

test("case 20: prior-only and current-inclusive ranges remain distinct", () => {
  const result = view("case-20-below-range");
  assert.deepEqual(result.analysis.priorRecentRange, { min: 100, max: 105, count: 2 });
  assert.deepEqual(result.analysis.recentRange, { min: 90, max: 105, count: 3 });
  assert.ok(result.evidenceLabels.includes("Below prior recent observed range"));
});

test("every fixture is visibly synthetic and produces a safe next-step string", () => {
  for (const entry of UX_FIXTURES) {
    const result = buildPrototypeView(entry);
    const serialized = JSON.stringify(entry);
    assert.match(serialized, /DEMO|Demo|synthetic/i, entry.id);
    assert.equal(typeof result.nextStep, "string");
    assert.ok(result.nextStep.length > 0);
  }
});

test("missing baseline and target remain explicit rather than fabricated defaults", () => {
  const result = view("case-01-first");
  assert.equal(result.baseline, null);
  assert.equal(result.target, null);
});

test("long synthetic item names remain data rather than being truncated in the view model", () => {
  const result = view("case-15-incomplete");
  assert.match(result.item.name, /Extra-Long Synthetic/);
  assert.ok(result.item.name.length > 70);
});

test("prohibited certainty claims never appear in fixture or rendered view text", () => {
  const banned = ["buy now", "wait", "best possible price", "guaranteed savings", "rebook now", "you can reprice", "predicted next drop"];
  for (const entry of UX_FIXTURES) {
    const haystack = `${JSON.stringify(entry)} ${JSON.stringify(buildPrototypeView(entry))}`.toLowerCase();
    for (const phrase of banned) {
      assert.equal(haystack.includes(phrase), false, `${entry.id} contains prohibited phrase: ${phrase}`);
    }
  }
});

test("lower fresh observed price versus comparable baseline uses verify-eligibility language", () => {
  const result = view("case-05-drop");
  assert.equal(result.nextStep, "Lower observed price — verify eligibility");
});

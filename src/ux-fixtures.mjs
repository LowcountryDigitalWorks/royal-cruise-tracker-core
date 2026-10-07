const baseScope = Object.freeze({
  itemId: "DEMO-ITEM-ALPHA",
  sailingId: "DEMO-SAILING-ALPHA",
  category: "DEMO-CATEGORY",
  variant: "DEMO-VARIANT",
  occupancy: "DEMO-OCCUPANCY",
  promotionScope: "DEMO-PUBLIC",
});

const item = Object.freeze({
  name: "Demo Ocean View Beverage Package",
  category: "Demo package",
  variant: "Demo all-inclusive variant",
});

function at(minute) {
  return `2030-05-01T12:${String(minute).padStart(2, "0")}:00Z`;
}

function observation(id, minute, value, overrides = {}) {
  return {
    id,
    observedAt: at(minute),
    status: "valid",
    stale: false,
    value,
    currency: "USD",
    priceBasis: "total",
    taxesFees: "included",
    sourceClass: "synthetic-public",
    availability: "available",
    scope: { ...baseScope },
    ...overrides,
    scope: { ...baseScope, ...(overrides.scope ?? {}) },
  };
}

function failed(id, minute) {
  return observation(id, minute, null, {
    status: "failed",
    availability: "unknown",
  });
}

function target(value = 90) {
  return {
    value,
    currency: "USD",
    priceBasis: "total",
    taxesFees: "included",
    scope: { ...baseScope },
  };
}

function baseline(value = 112) {
  return {
    value,
    currency: "USD",
    priceBasis: "total",
    taxesFees: "included",
    sourceClass: "synthetic-public",
    scope: { ...baseScope },
  };
}

const configuredPolicy = Object.freeze({
  status: "configured",
  sourceClass: "synthetic-policy",
  market: "DEMO-MARKET",
  effectiveDate: "2030-01-01",
  bookingCreatedDate: "2030-02-01",
  deadline: "2030-06-15T17:00:00Z",
});

function fixture(id, matrixCase, name, observations, options = {}, overrides = {}) {
  return {
    id,
    matrixCase,
    name,
    description: overrides.description ?? name,
    item: overrides.item ?? item,
    observations,
    options: {
      recentWindow: 5,
      meaningfulChange: 2,
      ...options,
    },
  };
}

export const UX_FIXTURES = Object.freeze([
  fixture("case-01-zero", 1, "No observations yet", [], {}, {
    description: "Empty state with no invented price, direction, or alert.",
  }),
  fixture("case-01-first", 1, "First observation only", [observation("first-a", 1, 104)], {}, {
    description: "A first observation establishes context without a historical drop claim.",
  }),
  fixture("case-02-two", 2, "Two comparable observations", [
    observation("two-a", 1, 108),
    observation("two-b", 2, 101),
  ], { baseline: baseline(), target: target(96) }),
  fixture("case-03-multi", 3, "Three-plus observation history", [
    observation("multi-a", 1, 118),
    observation("multi-b", 2, 109),
    observation("multi-c", 3, 103),
    observation("multi-d", 4, 99),
  ], { baseline: baseline(120), target: target(95) }),
  fixture("case-04-equal", 4, "No meaningful change", [
    observation("equal-a", 1, 100),
    observation("equal-b", 2, 100),
  ], { target: target(94) }),
  fixture("case-05-drop", 5, "Meaningful drop", [
    observation("drop-a", 1, 90),
    observation("drop-b", 2, 112),
    observation("drop-c", 3, 100),
  ], { baseline: baseline(115) }),
  fixture("case-06-rise", 6, "Rise after an earlier drop", [
    observation("rise-a", 1, 112),
    observation("rise-b", 2, 90),
    observation("rise-c", 3, 97),
  ], { baseline: baseline(115) }),
  fixture("case-07-low", 7, "New record low", [
    observation("low-a", 1, 108),
    observation("low-b", 2, 101),
    observation("low-c", 3, 86),
  ], { baseline: baseline(112), meaningfulChange: 30 }),
  fixture("case-08-target-hit", 8, "Target first hit", [
    observation("target-a", 1, 101),
    observation("target-b", 2, 89),
  ], { baseline: baseline(110), target: target(90) }),
  fixture("case-09-target-steady", 9, "Target remains reached without repeat alert", [
    observation("steady-a", 1, 101),
    observation("steady-b", 2, 89),
    observation("steady-c", 3, 88),
  ], { baseline: baseline(110), target: target(90) }),
  fixture("case-10-target-rearm", 10, "Target re-arm and later hit", [
    observation("rearm-a", 1, 101),
    observation("rearm-b", 2, 89),
    observation("rearm-c", 3, 96),
    observation("rearm-d", 4, 87),
  ], { baseline: baseline(110), target: target(90) }),
  fixture("case-11-restock", 11, "Availability restored", [
    observation("restock-a", 1, 101, { availability: "available" }),
    observation("restock-b", 2, 101, { availability: "unavailable" }),
    observation("restock-c", 3, 99, { availability: "available" }),
  ], { target: target(94) }),
  fixture("case-11-unavailable", 11, "Current availability is unavailable", [
    observation("unavailable-a", 1, 103, { availability: "available" }),
    observation("unavailable-b", 2, 101, { availability: "unavailable" }),
  ], { target: target(94) }, {
    description: "Explicit final unavailable state with valid synthetic price evidence.",
  }),
  fixture("case-11-not-open", 11, "Current availability is not open", [
    observation("not-open-a", 1, 103, { availability: "available" }),
    observation("not-open-b", 2, null, { availability: "not-open" }),
  ], { target: target(94) }, {
    description: "Explicit final not-open state with incomplete latest price evidence.",
  }),
  fixture("case-12-failure-gap", 12, "Provider failure breaks restock continuity", [
    observation("gap-a", 1, 101, { availability: "unavailable" }),
    failed("gap-failure", 2),
    observation("gap-b", 3, 98, { availability: "available" }),
  ], { target: target(94) }),
  fixture("case-13-stale", 13, "Stale latest observation", [
    observation("stale-a", 1, 101),
    observation("stale-b", 2, 88, { stale: true }),
  ], { baseline: baseline(110), target: target(90) }),
  fixture("case-14-failed", 14, "Failed latest observation", [
    observation("failed-a", 1, 101),
    failed("failed-b", 2),
  ], { baseline: baseline(110), target: target(90) }),
  fixture("case-15-incomplete", 15, "Latest price evidence is incomplete", [
    observation("incomplete-a", 1, 104, { availability: "unavailable" }),
    observation("incomplete-b", 2, null, { availability: "available" }),
  ], { baseline: baseline(110), target: target(90) }, {
    item: {
      name: "Demo Extra-Long Synthetic Multi-Device Connectivity Package With Descriptive Variant Name",
      category: "Demo connectivity package",
      variant: "Demo multi-device extended-duration variant",
    },
  }),
  fixture("case-16-noncomparable", 16, "Comparison unavailable because scope changed", [
    observation("compare-a", 1, 104, {
      currency: "USD",
      priceBasis: "total",
      taxesFees: "included",
      sourceClass: "synthetic-public",
    }),
    observation("compare-b", 2, 92, {
      currency: "CAD",
      priceBasis: "per-person",
      taxesFees: "unknown",
      sourceClass: "synthetic-personalized-demo",
      scope: {
        category: "DEMO-OTHER-CATEGORY",
        variant: "DEMO-OTHER-VARIANT",
        occupancy: "DEMO-OTHER-OCCUPANCY",
        promotionScope: "DEMO-PERSONALIZED",
      },
    }),
  ], { target: target(90) }),
  fixture("case-17-policy-unknown", 17, "Policy and deadline unknown", [
    observation("policy-unknown-a", 1, 104),
    observation("policy-unknown-b", 2, 95),
  ], { baseline: baseline(110), target: target(94) }),
  fixture("case-18-policy-configured", 18, "Policy configured descriptively", [
    observation("policy-a", 1, 104),
    observation("policy-b", 2, 92),
  ], { baseline: baseline(110), target: target(94), policy: configuredPolicy }),
  fixture("case-19-range-insufficient", 19, "Too little prior history for a range claim", [
    observation("range-short-a", 1, 100),
    observation("range-short-b", 2, 90),
  ], { baseline: baseline(110), target: target(88) }),
  fixture("case-20-below-range", 20, "Below prior recent observed range", [
    observation("range-a", 1, 100),
    observation("range-b", 2, 105),
    observation("range-c", 3, 90),
  ], { baseline: baseline(110), target: target(88) }),
]);

export const MATRIX_CASES = Object.freeze(
  UX_FIXTURES.reduce((map, entry) => {
    const existing = map.get(entry.matrixCase) ?? [];
    existing.push(entry.id);
    map.set(entry.matrixCase, existing);
    return map;
  }, new Map()),
);

export function getFixture(id) {
  return UX_FIXTURES.find((entry) => entry.id === id) ?? UX_FIXTURES[0];
}

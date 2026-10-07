const AVAILABILITY = new Set(["available", "unavailable", "not-open", "unknown"]);
const STATUS = new Set(["valid", "failed"]);
const REQUIRED_SCOPE_KEYS = ["itemId", "sailingId", "category", "variant", "occupancy", "promotionScope"];

function invariant(condition, message) {
  if (!condition) throw new TypeError(message);
}

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function cloneScope(scope) {
  invariant(scope && typeof scope === "object" && !Array.isArray(scope), "scope must be an object");
  return Object.fromEntries(Object.entries(scope));
}

function normalizeObservation(raw) {
  invariant(raw && typeof raw === "object" && !Array.isArray(raw), "observation must be an object");
  invariant(typeof raw.id === "string" && raw.id.length > 0, "observation.id is required");
  invariant(typeof raw.observedAt === "string" && raw.observedAt.length > 0, "observation.observedAt is required");
  const timestampMs = Date.parse(raw.observedAt);
  invariant(Number.isFinite(timestampMs), `invalid observedAt for ${raw.id}`);
  invariant(STATUS.has(raw.status), `invalid status for ${raw.id}`);
  invariant(AVAILABILITY.has(raw.availability), `invalid availability for ${raw.id}`);
  invariant(typeof raw.sourceClass === "string" && raw.sourceClass.length > 0, `sourceClass is required for ${raw.id}`);
  invariant(typeof raw.currency === "string" || raw.currency === null, `currency must be string or null for ${raw.id}`);
  invariant(typeof raw.priceBasis === "string" || raw.priceBasis === null, `priceBasis must be string or null for ${raw.id}`);
  invariant(typeof raw.taxesFees === "string" || raw.taxesFees === null, `taxesFees must be string or null for ${raw.id}`);
  invariant(raw.value === null || finiteNumber(raw.value), `value must be finite or null for ${raw.id}`);
  invariant(typeof raw.stale === "boolean", `stale must be boolean for ${raw.id}`);
  if (raw.status === "failed") {
    invariant(raw.availability === "unknown", `failed observation ${raw.id} must use unknown availability`);
  }
  return {
    id: raw.id,
    observedAt: raw.observedAt,
    timestampMs,
    status: raw.status,
    stale: raw.stale,
    value: raw.value,
    currency: raw.currency,
    priceBasis: raw.priceBasis,
    taxesFees: raw.taxesFees,
    sourceClass: raw.sourceClass,
    availability: raw.availability,
    scope: cloneScope(raw.scope),
  };
}

function comparableReasons(a, b, { compareSourceClass = true } = {}) {
  const reasons = [];
  if (a.status !== "valid" || b.status !== "valid") reasons.push("observation-status");
  if (!finiteNumber(a.value) || !finiteNumber(b.value)) reasons.push("missing-value");
  const topLevelKeys = compareSourceClass ? ["currency", "priceBasis", "taxesFees", "sourceClass"] : ["currency", "priceBasis", "taxesFees"];
  for (const key of topLevelKeys) {
    if (!a[key] || !b[key]) reasons.push(`unknown-${key}`);
    else if (a[key] !== b[key]) reasons.push(`${key}-mismatch`);
  }
  for (const key of REQUIRED_SCOPE_KEYS) {
    const left = a.scope?.[key];
    const right = b.scope?.[key];
    if (left === undefined || left === null || left === "" || right === undefined || right === null || right === "") {
      reasons.push(`unknown-scope-${key}`);
    } else if (left !== right) {
      reasons.push(`scope-${key}-mismatch`);
    }
  }
  return [...new Set(reasons)];
}

export function compareObservations(a, b) {
  const left = a.timestampMs === undefined ? normalizeObservation(a) : a;
  const right = b.timestampMs === undefined ? normalizeObservation(b) : b;
  const reasons = comparableReasons(left, right);
  return { comparable: reasons.length === 0, reasons };
}

function sameScope(a, b) {
  const leftKeys = Object.keys(a).sort();
  const rightKeys = Object.keys(b).sort();
  if (leftKeys.length !== rightKeys.length) return false;
  return leftKeys.every((key, index) => key === rightKeys[index] && Object.is(a[key], b[key]));
}

function sameNormalizedObservation(a, b) {
  const keys = ["id", "observedAt", "status", "stale", "value", "currency", "priceBasis", "taxesFees", "sourceClass", "availability"];
  return keys.every((key) => Object.is(a[key], b[key])) && sameScope(a.scope, b.scope);
}

function normalizeHistory(rawObservations) {
  invariant(Array.isArray(rawObservations), "observations must be an array");
  const byId = new Map();
  const observations = [];
  let duplicateCount = 0;
  for (const raw of rawObservations) {
    const observation = normalizeObservation(raw);
    const existing = byId.get(observation.id);
    if (existing) {
      invariant(sameNormalizedObservation(existing, observation), `conflicting duplicate observation id: ${observation.id}`);
      duplicateCount += 1;
      continue;
    }
    byId.set(observation.id, observation);
    observations.push(observation);
  }
  for (let index = 1; index < observations.length; index += 1) {
    invariant(
      observations[index].timestampMs > observations[index - 1].timestampMs,
      `observations must be strictly chronological after duplicate removal: ${observations[index].id}`,
    );
  }
  return { observations, duplicateCount };
}

function hasCompletePriceBasis(observation) {
  if (observation.status !== "valid" || !finiteNumber(observation.value)) return false;
  if (!observation.currency || !observation.priceBasis || !observation.taxesFees || !observation.sourceClass) return false;
  return REQUIRED_SCOPE_KEYS.every((key) => {
    const value = observation.scope?.[key];
    return value !== undefined && value !== null && value !== "";
  });
}

function stripInternal(observation) {
  if (!observation) return null;
  const { timestampMs, ...publicObservation } = observation;
  return publicObservation;
}

function normalizeReference(raw, kind, { requireSourceClass = true } = {}) {
  if (!raw) return null;
  invariant(raw && typeof raw === "object" && !Array.isArray(raw), `${kind} must be an object`);
  invariant(finiteNumber(raw.value), `${kind}.value must be finite`);
  return {
    id: `${kind}-reference`,
    observedAt: "reference",
    timestampMs: Number.NEGATIVE_INFINITY,
    status: "valid",
    stale: false,
    value: raw.value,
    currency: raw.currency ?? null,
    priceBasis: raw.priceBasis ?? null,
    taxesFees: raw.taxesFees ?? null,
    sourceClass: raw.sourceClass ?? (requireSourceClass ? "configured" : null),
    availability: "unknown",
    scope: cloneScope(raw.scope ?? {}),
  };
}

function deltaResult(current, previous) {
  if (!current || !previous) return null;
  const comparison = compareObservations(current, previous);
  if (!comparison.comparable) return { comparable: false, reasons: comparison.reasons, value: null, percent: null };
  const value = current.value - previous.value;
  const percent = previous.value === 0 ? null : (value / previous.value) * 100;
  return { comparable: true, reasons: [], value, percent };
}

function targetComparable(observation, target) {
  const reasons = comparableReasons(observation, target, { compareSourceClass: false });
  return { comparable: reasons.length === 0, reasons };
}

function targetResult(comparableSeries, target) {
  if (!target) return { status: "unknown", reached: null, event: null, alert: false, reasons: [] };
  const targetCompatible = comparableSeries.filter((observation) => targetComparable(observation, target).comparable);
  if (targetCompatible.length === 0) {
    const latest = comparableSeries.at(-1);
    const reasons = latest ? targetComparable(latest, target).reasons : ["no-current-observation"];
    return { status: "non-comparable", reached: null, event: null, alert: false, reasons };
  }
  const states = targetCompatible.map((observation) => observation.value <= target.value);
  const reached = states.at(-1);
  const previousReached = states.length > 1 ? states.at(-2) : false;
  let event = null;
  let alert = false;
  if (reached && !previousReached) {
    event = "hit";
    alert = true;
  } else if (reached && previousReached) {
    event = "steady-reached";
  } else if (!reached && previousReached) {
    event = "rearmed";
  } else {
    event = "not-reached";
  }
  return { status: "comparable", reached, event, alert, reasons: [] };
}

function availabilityResult(observations) {
  const latest = observations.at(-1);
  if (!latest || latest.status !== "valid" || latest.stale || !["available", "unavailable", "not-open"].includes(latest.availability)) {
    return { current: latest?.availability ?? "unknown", transition: null, restock: false };
  }
  const previous = observations.length > 1 ? observations.at(-2) : null;
  if (!previous || previous.status !== "valid" || previous.stale || !["available", "unavailable", "not-open"].includes(previous.availability)) {
    return { current: latest.availability, transition: null, restock: false };
  }
  if (previous.availability === "unavailable" && latest.availability === "available") {
    return { current: latest.availability, transition: "available-again", restock: true };
  }
  if (previous.availability === "available" && latest.availability === "unavailable") {
    return { current: latest.availability, transition: "became-unavailable", restock: false };
  }
  if (previous.availability === "not-open" && latest.availability === "available") {
    return { current: latest.availability, transition: "became-available", restock: false };
  }
  return { current: latest.availability, transition: null, restock: false };
}

function policyResult(policy) {
  if (!policy) return { status: "unknown" };
  invariant(policy.status === "configured", "policy.status must be configured when policy is supplied");
  invariant(typeof policy.sourceClass === "string" && policy.sourceClass.length > 0, "policy.sourceClass is required");
  return {
    status: "configured",
    sourceClass: policy.sourceClass,
    market: policy.market ?? null,
    effectiveDate: policy.effectiveDate ?? null,
    bookingCreatedDate: policy.bookingCreatedDate ?? null,
    deadline: policy.deadline ?? null,
  };
}

function rangeResult(values) {
  return values.length ? { min: Math.min(...values), max: Math.max(...values), count: values.length } : null;
}

export function analyzeHistory(rawObservations, options = {}) {
  const { observations, duplicateCount } = normalizeHistory(rawObservations);
  const latest = observations.at(-1) ?? null;
  const priceObservations = observations.filter(hasCompletePriceBasis);
  const current = priceObservations.at(-1) ?? null;
  const comparableSeries = current
    ? priceObservations.filter((observation) => compareObservations(observation, current).comparable)
    : [];
  const previous = comparableSeries.length > 1 ? comparableSeries.at(-2) : null;
  const latestPriorPrice = priceObservations.length > 1 ? priceObservations.at(-2) : null;
  const latestPriorComparison = current && latestPriorPrice
    ? compareObservations(current, latestPriorPrice)
    : { comparable: false, reasons: ["no-prior-price-observation"] };
  const recentWindow = Number.isInteger(options.recentWindow) && options.recentWindow > 0 ? options.recentWindow : 5;
  const recent = comparableSeries.slice(-recentWindow);
  const recentValues = recent.map((observation) => observation.value);
  const priorRecent = comparableSeries.slice(0, -1).slice(-recentWindow);
  const priorRecentValues = priorRecent.map((observation) => observation.value);
  const recordLow = comparableSeries.length ? Math.min(...comparableSeries.map((observation) => observation.value)) : null;
  const previousRecordLow = comparableSeries.length > 1 ? Math.min(...comparableSeries.slice(0, -1).map((observation) => observation.value)) : null;
  const delta = deltaResult(current, previous);
  const target = normalizeReference(options.target ?? null, "target", { requireSourceClass: false });
  const baseline = normalizeReference(options.baseline ?? null, "baseline");
  let targetState = targetResult(comparableSeries, target);
  const baselineDelta = current && baseline ? deltaResult(current, baseline) : null;
  const availability = availabilityResult(observations);
  const meaningfulChange = finiteNumber(options.meaningfulChange) && options.meaningfulChange >= 0 ? options.meaningfulChange : 0.01;
  const labels = [];
  const latestState = !latest ? "unknown" : latest.status === "failed" ? "failed" : latest.stale ? "stale" : "valid";
  const freshPriceEvidence = Boolean(latestState === "valid" && current && latest && current.id === latest.id);
  if (!freshPriceEvidence) {
    targetState = { ...targetState, event: null, alert: false };
  }

  if (latestState === "failed") labels.push("observation failed");
  if (latestState === "stale") labels.push("stale observation");
  if (latestState === "valid") {
    if (availability.restock) labels.push("available again");
    if (freshPriceEvidence) {
      if (targetState.reached === true) labels.push("target reached");
      if (previousRecordLow !== null && current.value < previousRecordLow) labels.push("new low observed");
      if (priorRecentValues.length >= 2 && current.value < Math.min(...priorRecentValues)) labels.push("below prior recent observed range");
      if (delta?.comparable && delta.value < 0 && Math.abs(delta.value) >= meaningfulChange) labels.push("meaningful drop");
      if (delta?.comparable && Math.abs(delta.value) < meaningfulChange) labels.push("no meaningful change");
    }
  }

  return {
    latestState,
    latest: stripInternal(latest),
    current: stripInternal(current),
    freshPriceEvidence,
    previous: stripInternal(previous),
    rawObservationCount: observations.length,
    duplicateCount,
    observationCount: comparableSeries.length,
    recordLow,
    recentRange: rangeResult(recentValues),
    priorRecentRange: rangeResult(priorRecentValues),
    comparisonToLatestPriorPrice: latestPriorComparison,
    delta,
    target: targetState,
    baseline: baselineDelta,
    availability,
    policy: policyResult(options.policy ?? null),
    labels,
  };
}

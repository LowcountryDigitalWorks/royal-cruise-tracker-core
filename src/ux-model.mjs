import { analyzeHistory, compareObservations } from "./decision-history.mjs";

const REQUIRED_SCOPE_KEYS = ["itemId", "sailingId", "category", "variant", "occupancy", "promotionScope"];

const LABEL_TEXT = Object.freeze({
  "new low observed": "New low observed",
  "below prior recent observed range": "Below prior recent observed range",
  "target reached": "Target reached",
  "meaningful drop": "Meaningful drop",
  "available again": "Available again",
  "no meaningful change": "No meaningful change",
  "stale observation": "Observation stale",
  "observation failed": "Observation failed",
});

const AVAILABILITY_TEXT = Object.freeze({
  available: "Available",
  unavailable: "Unavailable",
  "not-open": "Not open",
  unknown: "Observation unknown",
});

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function formatMoney(value, currency = "USD") {
  if (!finiteNumber(value) || !currency) return "Unavailable";
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      maximumFractionDigits: Number.isInteger(value) ? 0 : 2,
    }).format(value);
  } catch {
    return `${currency} ${value.toFixed(2)}`;
  }
}

function formatPercent(value) {
  if (!finiteNumber(value)) return "Unavailable";
  const rounded = Math.round(value * 10) / 10;
  return `${rounded > 0 ? "+" : ""}${rounded}%`;
}

function formatDelta(value, currency) {
  if (!finiteNumber(value)) return "Unavailable";
  const amount = formatMoney(Math.abs(value), currency);
  if (value < 0) return `−${amount}`;
  if (value > 0) return `+${amount}`;
  return formatMoney(0, currency);
}

function formatObservedAt(value) {
  if (!value) return "Not observed";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  }).format(date) + " UTC";
}

function humanizeReason(reason) {
  const replacements = {
    "observation-status": "observation status differs or failed",
    "missing-value": "price value is missing",
    "currency-mismatch": "currency differs",
    "priceBasis-mismatch": "price basis differs",
    "taxesFees-mismatch": "taxes and fees basis differs",
    "sourceClass-mismatch": "source class differs",
    "no-prior-price-observation": "no prior complete price observation",
  };
  if (replacements[reason]) return replacements[reason];
  if (reason.startsWith("unknown-")) return `${reason.slice(8).replaceAll("-", " ")} is unknown`;
  if (reason.startsWith("scope-") && reason.endsWith("-mismatch")) {
    return `${reason.slice(6, -9).replaceAll("-", " ")} scope differs`;
  }
  if (reason.startsWith("unknown-scope-")) return `${reason.slice(14).replaceAll("-", " ")} scope is unknown`;
  return reason.replaceAll("-", " ");
}

function isCompleteRawPrice(observation) {
  if (!observation || observation.status !== "valid" || !finiteNumber(observation.value)) return false;
  if (!observation.currency || !observation.priceBasis || !observation.taxesFees || !observation.sourceClass) return false;
  return REQUIRED_SCOPE_KEYS.every((key) => {
    const value = observation.scope?.[key];
    return value !== undefined && value !== null && value !== "";
  });
}

function stateTone(state) {
  if (["failed", "unavailable"].includes(state)) return "danger";
  if (["stale", "incomplete", "not-open", "unknown", "non-comparable"].includes(state)) return "warning";
  if (["available", "fresh", "positive"].includes(state)) return "positive";
  return "neutral";
}

function latestHealth(analysis) {
  if (!analysis.latest) return { key: "unknown", text: "No observations yet", tone: "neutral" };
  if (analysis.latestState === "failed") return { key: "failed", text: "Observation failed", tone: "danger" };
  if (analysis.latestState === "stale") return { key: "stale", text: "Observation stale", tone: "warning" };
  if (!analysis.freshPriceEvidence) return { key: "incomplete", text: "Latest price incomplete", tone: "warning" };
  return { key: "fresh", text: "Fresh synthetic observation", tone: "positive" };
}

function currentValueView(analysis) {
  if (!analysis.latest) {
    return { value: "—", qualifier: "No observations yet", historical: false };
  }
  if (analysis.latestState === "failed") {
    return {
      value: "Unavailable",
      qualifier: analysis.current ? `Last complete price: ${formatMoney(analysis.current.value, analysis.current.currency)}` : "No complete price history",
      historical: true,
    };
  }
  if (analysis.latestState === "stale") {
    return {
      value: analysis.current ? formatMoney(analysis.current.value, analysis.current.currency) : "Unavailable",
      qualifier: "Stale observation — not a fresh price fact",
      historical: true,
    };
  }
  if (!analysis.freshPriceEvidence) {
    return {
      value: "Incomplete",
      qualifier: analysis.current ? `Last complete price: ${formatMoney(analysis.current.value, analysis.current.currency)}` : "No complete price history",
      historical: true,
    };
  }
  return {
    value: formatMoney(analysis.current.value, analysis.current.currency),
    qualifier: "Latest complete synthetic price evidence",
    historical: false,
  };
}

function comparisonView(analysis) {
  if (!analysis.latest) {
    return { status: "unavailable", text: "Comparison unavailable", reasons: ["No observations yet"] };
  }
  if (!analysis.freshPriceEvidence) {
    return {
      status: "unavailable",
      text: "Comparison unavailable",
      reasons: ["Latest observation does not contain fresh complete price evidence"],
    };
  }
  if (analysis.delta?.comparable) {
    return {
      status: "comparable",
      text: "Like-for-like comparison",
      reasons: [],
    };
  }
  const reasons = analysis.comparisonToLatestPriorPrice?.reasons ?? [];
  if (reasons.length && !reasons.includes("no-prior-price-observation")) {
    return {
      status: "non-comparable",
      text: "Comparison unavailable",
      reasons: reasons.map(humanizeReason),
    };
  }
  return {
    status: "unavailable",
    text: "No previous comparable observation yet",
    reasons: [],
  };
}

function policyView(analysis) {
  if (analysis.policy.status !== "configured") {
    return {
      status: "unknown",
      title: "Policy / deadline unknown",
      detail: "Eligibility is not established from price evidence alone.",
    };
  }
  const bits = [analysis.policy.market, analysis.policy.deadline ? `deadline ${formatObservedAt(analysis.policy.deadline)}` : null]
    .filter(Boolean);
  return {
    status: "configured",
    title: "Policy configured descriptively",
    detail: `${bits.join(" · ") || "Configured synthetic policy"}. Verify eligibility before acting.`,
  };
}

function nextStepView(analysis) {
  if (!analysis.latest) return "A synthetic observation is required before comparing evidence.";
  if (analysis.latestState === "failed") return "Observation failed — retry only through the authorized monitoring path.";
  if (analysis.latestState === "stale") return "Observation stale — verify with fresh evidence before acting.";
  if (!analysis.freshPriceEvidence) return "Latest price evidence is incomplete — do not treat older history as a new price.";
  if (analysis.baseline?.comparable && analysis.baseline.value < 0) return "Lower observed price — verify eligibility";
  if (analysis.comparisonToLatestPriorPrice?.comparable === false && analysis.rawObservationCount > 1) return "Comparison unavailable — verify that scope and price basis match.";
  return "Review the evidence and policy state before taking any manual action.";
}

function historyRows(fixture, analysis) {
  const currentId = analysis.current?.id ?? null;
  const previousId = analysis.previous?.id ?? null;
  const recordLow = analysis.recordLow;
  return fixture.observations.map((observation) => {
    const complete = isCompleteRawPrice(observation);
    const rawValue = finiteNumber(observation.value) ? formatMoney(observation.value, observation.currency) : "Not observed";
    const markers = [];
    if (observation.id === currentId) markers.push(analysis.freshPriceEvidence ? "Current" : "Last complete price");
    if (observation.id === previousId) markers.push("Previous comparable");
    if (complete && recordLow !== null && observation.value === recordLow) markers.push("Record low");
    if (observation.status === "failed") markers.push("Failed observation");
    else if (observation.stale) markers.push("Stale observation");
    else if (!complete) markers.push("Incomplete price evidence");
    return {
      id: observation.id,
      observedAt: formatObservedAt(observation.observedAt),
      rawValue,
      numericValue: finiteNumber(observation.value) ? observation.value : null,
      status: observation.status,
      stale: observation.stale,
      complete,
      availability: AVAILABILITY_TEXT[observation.availability] ?? "Observation unknown",
      sourceClass: observation.sourceClass,
      priceBasis: [observation.currency ?? "unknown currency", observation.priceBasis ?? "unknown basis", observation.taxesFees ?? "unknown taxes/fees"].join(" · "),
      markers,
      tone: observation.status === "failed" ? "danger" : observation.stale || !complete ? "warning" : "neutral",
    };
  });
}

function prefixAnalyses(fixture) {
  return fixture.observations.map((_, index) => analyzeHistory(fixture.observations.slice(0, index + 1), fixture.options));
}

function eventForPrefix(analysis, previousAnalysis) {
  const latest = analysis.latest;
  if (!latest) return null;
  const at = formatObservedAt(latest.observedAt);

  if (analysis.latestState === "failed") {
    return { kind: "health", title: "Observation failed", detail: "Provider health evidence is unknown; this is not a sold-out signal.", at, tone: "danger" };
  }
  if (analysis.latestState === "stale") {
    return { kind: "health", title: "Observation stale", detail: "Last known evidence is retained but is not treated as fresh.", at, tone: "warning" };
  }
  if (!analysis.freshPriceEvidence) {
    return { kind: "health", title: "Latest price incomplete", detail: "Older complete price history remains historical only.", at, tone: "warning" };
  }
  if (analysis.availability.restock) {
    return { kind: "availability", title: "Available again", detail: "Availability was restored from contiguous valid evidence.", at, tone: "positive" };
  }
  if (analysis.availability.transition === "became-unavailable") {
    return { kind: "availability", title: "Observed unavailable", detail: "The latest valid observation is unavailable; provider failure is handled separately.", at, tone: "warning" };
  }
  if (analysis.target.event === "hit" && analysis.target.alert) {
    const wasPreviouslyReached = previousAnalysis?.target?.reached === true;
    return {
      kind: "target",
      title: "Target reached",
      detail: wasPreviouslyReached ? "Target was reached again after a re-arm." : "Target threshold crossed for the first time in this sequence.",
      at,
      tone: "positive",
    };
  }
  if (analysis.target.event === "rearmed") {
    return { kind: "target", title: "Target condition re-armed", detail: "The observed value moved back outside the configured target threshold.", at, tone: "neutral" };
  }
  if (analysis.labels.includes("new low observed")) {
    return { kind: "price", title: "New low observed", detail: "This is the lowest valid comparable observation in the retained synthetic history.", at, tone: "positive" };
  }
  if (analysis.labels.includes("below prior recent observed range")) {
    return { kind: "price", title: "Below prior recent observed range", detail: "The current value is below the explicit prior-only comparison window.", at, tone: "positive" };
  }
  if (analysis.labels.includes("meaningful drop")) {
    return { kind: "price", title: "Meaningful drop", detail: "The decrease exceeds the configured synthetic meaningful-change threshold.", at, tone: "positive" };
  }
  if (analysis.labels.includes("no meaningful change")) {
    return { kind: "price", title: "No meaningful change", detail: "The observed value did not move enough to create a new alert-worthy price change.", at, tone: "neutral" };
  }
  if (analysis.delta?.comparable && analysis.delta.value > 0) {
    return { kind: "price", title: "Observed value increased", detail: "The latest comparable value is higher than the immediately previous comparable observation.", at, tone: "neutral" };
  }
  if (analysis.comparisonToLatestPriorPrice?.comparable === false && previousAnalysis?.current) {
    return { kind: "comparison", title: "Comparison unavailable", detail: "Price basis or scope changed, so no savings claim is shown.", at, tone: "warning" };
  }
  return null;
}

function changeFeed(fixture, analysis) {
  if (!fixture.observations.length) {
    return {
      note: "No changes yet. The prototype will not fabricate activity before an observation exists.",
      entries: [],
    };
  }
  const prefixes = prefixAnalyses(fixture);
  const entries = [];
  prefixes.forEach((prefix, index) => {
    const event = eventForPrefix(prefix, prefixes[index - 1] ?? null);
    if (event) entries.push({ ...event, key: `${fixture.id}-${index}` });
  });

  let note = "Newest meaningful evidence first. Unchanged conditions are suppressed.";
  if (analysis.target.event === "steady-reached" && analysis.target.alert === false) {
    note = "No new target alert — the target remains reached and unchanged conditions are not repeated.";
  } else if (analysis.availability.current === "available" && analysis.availability.restock === false && analysis.latestState === "valid" && fixture.observations.some((item) => item.status === "failed")) {
    note = "Availability is currently observed as available, but a provider failure broke continuity; no restock was inferred.";
  }

  return { note, entries: entries.reverse() };
}

function rangeText(range, currency) {
  if (!range) return "Not established";
  return `${formatMoney(range.min, currency)} – ${formatMoney(range.max, currency)} (${range.count} observations)`;
}

export function buildPrototypeView(fixture) {
  const analysis = analyzeHistory(fixture.observations, fixture.options);
  const health = latestHealth(analysis);
  const value = currentValueView(analysis);
  const comparison = comparisonView(analysis);
  const policy = policyView(analysis);
  const latest = analysis.latest;
  const currency = analysis.current?.currency ?? latest?.currency ?? "USD";
  const baselineValue = fixture.options.baseline?.value;
  const targetValue = fixture.options.target?.value;
  const baselineComparable = analysis.freshPriceEvidence && analysis.baseline?.comparable;
  const targetStatus = analysis.target.status === "unknown"
    ? "Not configured"
    : analysis.target.status === "non-comparable"
      ? "Comparison unavailable"
      : analysis.target.reached
        ? analysis.freshPriceEvidence ? "Reached" : "Reached in prior complete history"
        : "Not reached";
  const evidenceLabels = analysis.labels.map((label) => LABEL_TEXT[label] ?? label);
  if (comparison.status === "non-comparable" && !evidenceLabels.includes("Comparison unavailable")) evidenceLabels.push("Comparison unavailable");

  return {
    id: fixture.id,
    matrixCase: fixture.matrixCase,
    scenarioName: fixture.name,
    scenarioDescription: fixture.description,
    item: fixture.item,
    analysis,
    health,
    healthTone: stateTone(health.key),
    value,
    observedAt: formatObservedAt(latest?.observedAt),
    provenance: latest?.sourceClass ?? "No observation source",
    availability: {
      key: analysis.availability.current,
      text: AVAILABILITY_TEXT[analysis.availability.current] ?? "Observation unknown",
      tone: stateTone(analysis.availability.current),
    },
    basis: latest
      ? `${latest.currency ?? "Unknown currency"} · ${latest.priceBasis ?? "Unknown price basis"} · ${latest.taxesFees ?? "Unknown taxes/fees basis"}`
      : "Not established",
    comparison,
    previous: analysis.previous
      ? formatMoney(analysis.previous.value, analysis.previous.currency)
      : "Not available",
    delta: analysis.freshPriceEvidence && analysis.delta?.comparable
      ? formatDelta(analysis.delta.value, currency)
      : "Not available",
    percentDelta: analysis.freshPriceEvidence && analysis.delta?.comparable
      ? formatPercent(analysis.delta.percent)
      : "Not available",
    baseline: finiteNumber(baselineValue)
      ? {
          value: formatMoney(baselineValue, fixture.options.baseline.currency),
          difference: baselineComparable ? formatDelta(analysis.baseline.value, currency) : "Not evaluated from fresh comparable evidence",
        }
      : null,
    target: finiteNumber(targetValue)
      ? { value: formatMoney(targetValue, fixture.options.target.currency), status: targetStatus }
      : null,
    recordLow: analysis.recordLow === null ? "Not established" : formatMoney(analysis.recordLow, currency),
    recentRange: rangeText(analysis.recentRange, currency),
    priorRecentRange: rangeText(analysis.priorRecentRange, currency),
    evidenceLabels,
    policy,
    nextStep: nextStepView(analysis),
    historyRows: historyRows(fixture, analysis),
    changes: changeFeed(fixture, analysis),
  };
}

export function compareFixtureLatestToPrior(fixture) {
  if (fixture.observations.length < 2) return { comparable: false, reasons: ["insufficient-history"] };
  return compareObservations(fixture.observations.at(-1), fixture.observations.at(-2));
}

import { createHash } from "node:crypto";

export const INTENT_CONTRACT_VERSION = "alert-intent/v1";

const SAFE_FIELDS = Object.freeze([
  "intent_id",
  "contract_version",
  "subject_key",
  "group_key",
  "comparison_scope_key",
  "event_type",
  "semantic_generation",
  "transition",
  "observed_at",
  "freshness",
  "priority_class",
  "absolute_delta",
  "percent_delta",
  "current_value",
  "target_state",
  "availability_state",
  "evidence_label",
  "actionability_state",
  "policy_state",
  "delivery_mode",
  "route_class",
  "evidence_summary",
  "policy_key",
]);

const FORBIDDEN_KEY = /(?:password|secret|token|credential|cookie|session|reservation(?:_?id)?|booking(?:_?id)?|passenger(?:_?name)?|payment|apprise(?:_?url)?|destination(?:_?url)?|raw(?:_?provider)?(?:_?payload)?|protected(?:_?url)?)/i;
const PRIORITY_RANK = Object.freeze({ urgent: 0, high: 1, normal: 2, low: 3 });
const EVENT_RANK = Object.freeze({
  target_hit: 0,
  meaningful_drop: 1,
  new_low: 2,
  availability_restored: 3,
  price_change: 4,
  observation_failed: 5,
});
const DELIVERY_RANK = Object.freeze({ immediate: 0, digest_eligible: 1 });

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function finiteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function canonicalNumber(value) {
  if (!finiteNumber(value)) return null;
  return Object.is(value, -0) ? 0 : value;
}

function deepFreeze(value) {
  if (!value || typeof value !== "object" || Object.isFrozen(value)) return value;
  for (const child of Object.values(value)) deepFreeze(child);
  return Object.freeze(value);
}

function stableNormalize(value) {
  if (Array.isArray(value)) return value.map(stableNormalize);
  if (isRecord(value)) {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, stableNormalize(value[key])]),
    );
  }
  if (typeof value === "number") return canonicalNumber(value);
  return value;
}

function canonicalJson(value) {
  return JSON.stringify(stableNormalize(value));
}

function assertNoForbiddenKeys(value, path = "value") {
  if (Array.isArray(value)) {
    value.forEach((item, index) => assertNoForbiddenKeys(item, `${path}[${index}]`));
    return;
  }
  if (!isRecord(value)) return;
  for (const [key, child] of Object.entries(value)) {
    if (FORBIDDEN_KEY.test(key)) {
      throw new TypeError(`Forbidden private field '${path}.${key}' is not allowed in notification-intent semantics`);
    }
    assertNoForbiddenKeys(child, `${path}.${key}`);
  }
}

function requiredString(value, label) {
  const text = String(value ?? "").trim();
  if (!text) throw new TypeError(`${label} is required`);
  return text;
}

function optionalString(value, fallback = null) {
  if (value === undefined || value === null) return fallback;
  const text = String(value).trim();
  return text || fallback;
}

function positiveThreshold(value, label) {
  if (value === undefined || value === null) return null;
  if (!finiteNumber(value) || value < 0) throw new TypeError(`${label} must be a non-negative finite number`);
  return value;
}

function freshPriceState(state) {
  return Boolean(
    isRecord(state)
      && state.freshness === "fresh"
      && state.comparable === true
      && finiteNumber(state.current_value),
  );
}

function trustworthyAvailabilityState(state) {
  return Boolean(isRecord(state) && state.freshness === "fresh");
}

function sameComparisonScope(previousState, currentState) {
  const previous = optionalString(previousState?.comparison_scope_key);
  const current = optionalString(currentState?.comparison_scope_key);
  return previous === current;
}

function thresholdDecision(absoluteDelta, percentDelta, rule, epsilon) {
  if (!isRecord(rule) || rule.enabled !== true) return false;
  const absoluteThreshold = positiveThreshold(rule.absolute_threshold, "absolute_threshold");
  const percentThreshold = positiveThreshold(rule.percent_threshold, "percent_threshold");
  const checks = [];
  if (absoluteThreshold !== null) checks.push(absoluteDelta + epsilon >= absoluteThreshold);
  if (percentThreshold !== null) checks.push(percentDelta + epsilon >= percentThreshold);
  if (!checks.length) return false;
  const combine = String(rule.combine ?? "ANY").toUpperCase();
  if (!["ANY", "ALL"].includes(combine)) throw new TypeError("threshold combine must be ANY or ALL");
  return combine === "ALL" ? checks.every(Boolean) : checks.some(Boolean);
}

function eventGeneration(prefix, explicit, fallback = null) {
  const value = optionalString(explicit, fallback === null ? null : String(fallback));
  if (value === null) throw new TypeError(`${prefix}_generation is required for repeatable transition identity`);
  return `${prefix}:${value}`;
}

function evidenceSummary(eventType, currentState, details = {}) {
  if (details.summary) return String(details.summary);
  const subject = requiredString(currentState.subject_key, "subject_key");
  switch (eventType) {
    case "target_hit": return `${subject}: configured target reached in current evidence`;
    case "meaningful_drop": return `${subject}: meaningful comparable decrease observed`;
    case "new_low": return `${subject}: new comparable historical low observed`;
    case "availability_restored": return `${subject}: availability restored in contiguous evidence`;
    case "price_change": return `${subject}: comparable price change exceeded configured threshold`;
    case "observation_failed": return `${subject}: repeated observation failure threshold reached`;
    default: return `${subject}: notification-worthy semantic event observed`;
  }
}

function materialIdentity(intent) {
  const eventType = requiredString(intent.event_type, "intent.event_type");
  const identity = {
    contract_version: intent.contract_version ?? INTENT_CONTRACT_VERSION,
    subject_key: requiredString(intent.subject_key, "intent.subject_key"),
    group_key: optionalString(intent.group_key, "default"),
    comparison_scope_key: optionalString(intent.comparison_scope_key),
    event_type: eventType,
    semantic_generation: requiredString(intent.semantic_generation, "intent.semantic_generation"),
    transition: optionalString(intent.transition),
    target_state: optionalString(intent.target_state),
    availability_state: optionalString(intent.availability_state),
    policy_key: optionalString(intent.policy_key),
  };
  if (["meaningful_drop", "new_low", "price_change"].includes(eventType)) {
    identity.current_value = canonicalNumber(intent.current_value);
  }
  return identity;
}

export function intentFingerprint(intent) {
  if (!isRecord(intent)) throw new TypeError("intent must be an object");
  assertNoForbiddenKeys(intent, "intent");
  const digest = createHash("sha256").update(canonicalJson(materialIdentity(intent))).digest("hex");
  return `ni1_${digest}`;
}

export function safeIntentPayload(intent) {
  if (!isRecord(intent)) throw new TypeError("intent must be an object");
  assertNoForbiddenKeys(intent, "intent");
  const payload = {};
  for (const field of SAFE_FIELDS) {
    if (field === "intent_id") continue;
    if (intent[field] !== undefined) payload[field] = stableNormalize(intent[field]);
  }
  payload.contract_version = optionalString(payload.contract_version, INTENT_CONTRACT_VERSION);
  const expectedId = intentFingerprint(payload);
  const suppliedId = optionalString(intent.intent_id);
  if (suppliedId !== null && suppliedId !== expectedId) {
    throw new TypeError("intent_id does not match deterministic notification-intent fingerprint");
  }
  payload.intent_id = expectedId;
  return deepFreeze(stableNormalize(payload));
}

function createIntent(currentState, eventType, details = {}) {
  return safeIntentPayload({
    contract_version: INTENT_CONTRACT_VERSION,
    subject_key: requiredString(currentState.subject_key, "subject_key"),
    group_key: optionalString(currentState.group_key, "default"),
    comparison_scope_key: optionalString(currentState.comparison_scope_key),
    event_type: eventType,
    semantic_generation: requiredString(details.semantic_generation, "semantic_generation"),
    transition: optionalString(details.transition),
    observed_at: optionalString(currentState.observed_at),
    freshness: optionalString(currentState.freshness, "unknown"),
    priority_class: optionalString(details.priority_class, "normal"),
    absolute_delta: canonicalNumber(details.absolute_delta),
    percent_delta: canonicalNumber(details.percent_delta),
    current_value: canonicalNumber(currentState.current_value),
    target_state: optionalString(currentState.target_state),
    availability_state: optionalString(currentState.availability_state),
    evidence_label: optionalString(details.evidence_label, optionalString(currentState.evidence_label, "")),
    actionability_state: optionalString(currentState.actionability_state, "unknown"),
    policy_state: optionalString(currentState.policy_state, "unknown"),
    delivery_mode: optionalString(details.delivery_mode, "immediate"),
    route_class: optionalString(details.route_class, "open_tracker"),
    evidence_summary: evidenceSummary(eventType, currentState, details),
    policy_key: optionalString(details.policy_key),
  });
}

function priceTransition(previousState, currentState, policy, intents) {
  if (!freshPriceState(previousState) || !freshPriceState(currentState)) return;
  if (!sameComparisonScope(previousState, currentState)) return;

  const epsilon = positiveThreshold(policy.epsilon ?? 0.009, "epsilon");
  const previousValue = previousState.current_value;
  const currentValue = currentState.current_value;
  const signedDrop = previousValue - currentValue;
  const absoluteChange = Math.abs(signedDrop);
  if (absoluteChange <= epsilon) return;
  const percentChange = previousValue === 0 ? 0 : (absoluteChange / Math.abs(previousValue)) * 100;
  const percentDrop = previousValue === 0 ? 0 : (signedDrop / Math.abs(previousValue)) * 100;

  const meaningfulRule = policy.meaningful_drop;
  if (signedDrop > epsilon && thresholdDecision(signedDrop, percentDrop, meaningfulRule, epsilon)) {
    intents.push(createIntent(currentState, "meaningful_drop", {
      semantic_generation: eventGeneration("price", currentState.price_generation, currentValue),
      transition: "higher->lower",
      absolute_delta: signedDrop,
      percent_delta: percentDrop,
      priority_class: optionalString(meaningfulRule.priority_class, "high"),
      delivery_mode: optionalString(meaningfulRule.delivery_mode, "immediate"),
      route_class: optionalString(meaningfulRule.route_class, "verify_current_price"),
      policy_key: optionalString(meaningfulRule.policy_key, "meaningful_drop"),
      evidence_label: "meaningful drop",
    }));
  }

  const changeRule = policy.any_change;
  if (isRecord(changeRule) && changeRule.enabled === true) {
    const direction = String(changeRule.direction ?? "BOTH").toUpperCase();
    if (!["DOWN", "BOTH"].includes(direction)) throw new TypeError("any_change direction must be DOWN or BOTH");
    const directionAllowed = direction === "BOTH" || signedDrop > epsilon;
    if (directionAllowed && thresholdDecision(absoluteChange, percentChange, changeRule, epsilon)) {
      intents.push(createIntent(currentState, "price_change", {
        semantic_generation: eventGeneration("price", currentState.price_generation, currentValue),
        transition: signedDrop > 0 ? "higher->lower" : "lower->higher",
        absolute_delta: signedDrop,
        percent_delta: previousValue === 0 ? 0 : (signedDrop / Math.abs(previousValue)) * 100,
        priority_class: optionalString(changeRule.priority_class, "normal"),
        delivery_mode: optionalString(changeRule.delivery_mode, "digest_eligible"),
        route_class: optionalString(changeRule.route_class, "view_history"),
        policy_key: optionalString(changeRule.policy_key, `any_change:${direction}`),
        evidence_label: "comparable change",
      }));
    }
  }

  const newLowRule = policy.new_low;
  const priorLow = canonicalNumber(previousState.historical_low);
  if (isRecord(newLowRule) && newLowRule.enabled === true && priorLow !== null && currentValue < priorLow - epsilon) {
    const improvement = priorLow - currentValue;
    const improvementPercent = priorLow === 0 ? 0 : (improvement / Math.abs(priorLow)) * 100;
    const noThreshold = newLowRule.absolute_threshold === undefined && newLowRule.percent_threshold === undefined;
    if (noThreshold || thresholdDecision(improvement, improvementPercent, { ...newLowRule, enabled: true }, epsilon)) {
      intents.push(createIntent(currentState, "new_low", {
        semantic_generation: eventGeneration("low", currentState.price_generation, currentValue),
        transition: "prior_low->new_low",
        absolute_delta: improvement,
        percent_delta: improvementPercent,
        priority_class: optionalString(newLowRule.priority_class, "high"),
        delivery_mode: optionalString(newLowRule.delivery_mode, "immediate"),
        route_class: optionalString(newLowRule.route_class, "verify_current_price"),
        policy_key: optionalString(newLowRule.policy_key, "new_low"),
        evidence_label: "new low observed",
      }));
    }
  }
}

function targetTransition(previousState, currentState, policy, intents) {
  const rule = policy.target;
  if (!isRecord(rule) || rule.enabled !== true) return;
  if (!freshPriceState(currentState) || currentState.target_state !== "reached") return;
  const previousTarget = previousState?.target_state ?? null;
  const bootstrap = previousState === null && rule.bootstrap === true;
  const crossing = previousTarget === "above";
  if (!bootstrap && !crossing) return;
  intents.push(createIntent(currentState, "target_hit", {
    semantic_generation: eventGeneration("target", currentState.target_generation),
    transition: `${previousTarget ?? "baseline"}->reached`,
    priority_class: optionalString(rule.priority_class, "urgent"),
    delivery_mode: optionalString(rule.delivery_mode, "immediate"),
    route_class: optionalString(rule.route_class, "verify_current_price"),
    policy_key: optionalString(rule.policy_key, "target"),
    evidence_label: bootstrap ? "initial target reached" : "target reached",
  }));
}

function availabilityTransition(previousState, currentState, policy, intents) {
  const rule = policy.availability;
  if (!isRecord(rule) || rule.enabled !== true || !isRecord(previousState)) return;
  if (!trustworthyAvailabilityState(previousState) || !trustworthyAvailabilityState(currentState)) return;
  if (previousState.availability_state !== "unavailable" || currentState.availability_state !== "available") return;
  if (previousState.availability_continuity === false || currentState.availability_continuity === false) return;
  intents.push(createIntent(currentState, "availability_restored", {
    semantic_generation: eventGeneration("availability", currentState.availability_generation),
    transition: "unavailable->available",
    priority_class: optionalString(rule.priority_class, "high"),
    delivery_mode: optionalString(rule.delivery_mode, "immediate"),
    route_class: optionalString(rule.route_class, "verify_current_availability"),
    policy_key: optionalString(rule.policy_key, "availability"),
    evidence_label: "available again",
  }));
}

function healthTransition(previousState, currentState, policy, intents) {
  const rule = policy.health;
  if (!isRecord(rule) || rule.enabled !== true || currentState.freshness !== "failed") return;
  const threshold = Math.max(1, Math.trunc(positiveThreshold(rule.failure_threshold ?? 3, "failure_threshold")));
  const currentFailures = Math.max(0, Math.trunc(Number(currentState.consecutive_failures ?? 0)));
  const previousFailures = Math.max(0, Math.trunc(Number(previousState?.consecutive_failures ?? 0)));
  if (!Number.isFinite(currentFailures) || !Number.isFinite(previousFailures)) throw new TypeError("consecutive_failures must be finite");
  if (currentFailures < threshold || previousFailures >= threshold) return;
  intents.push(createIntent(currentState, "observation_failed", {
    semantic_generation: eventGeneration("health", currentState.health_generation),
    transition: `${previousFailures}->${currentFailures}_failures`,
    priority_class: optionalString(rule.priority_class, "low"),
    delivery_mode: optionalString(rule.delivery_mode, "digest_eligible"),
    route_class: optionalString(rule.route_class, "view_tracker_health"),
    policy_key: optionalString(rule.policy_key, `health:${threshold}`),
    evidence_label: "repeated observation failure",
  }));
}

export function deriveIntents(previousState, currentState, policy = {}) {
  if (previousState !== null && previousState !== undefined && !isRecord(previousState)) {
    throw new TypeError("previousState must be an object or null");
  }
  if (!isRecord(currentState)) throw new TypeError("currentState must be an object");
  if (!isRecord(policy)) throw new TypeError("policy must be an object");
  assertNoForbiddenKeys(previousState, "previousState");
  assertNoForbiddenKeys(currentState, "currentState");
  assertNoForbiddenKeys(policy, "policy");
  requiredString(currentState.subject_key, "subject_key");

  const intents = [];
  targetTransition(previousState ?? null, currentState, policy, intents);
  priceTransition(previousState ?? null, currentState, policy, intents);
  availabilityTransition(previousState ?? null, currentState, policy, intents);
  healthTransition(previousState ?? null, currentState, policy, intents);

  const seen = new Map();
  for (const intent of intents) seen.set(intent.intent_id, intent);
  return deepFreeze([...seen.values()].sort(compareIntents));
}

function compareIntents(left, right) {
  const priority = (PRIORITY_RANK[left.priority_class] ?? 99) - (PRIORITY_RANK[right.priority_class] ?? 99);
  if (priority) return priority;
  const event = (EVENT_RANK[left.event_type] ?? 99) - (EVENT_RANK[right.event_type] ?? 99);
  if (event) return event;
  const value = Math.abs(Number(right.absolute_delta ?? 0)) - Math.abs(Number(left.absolute_delta ?? 0));
  if (value) return value;
  const observed = String(right.observed_at ?? "").localeCompare(String(left.observed_at ?? ""));
  if (observed) return observed;
  const subject = String(left.subject_key ?? "").localeCompare(String(right.subject_key ?? ""));
  if (subject) return subject;
  return String(left.intent_id).localeCompare(String(right.intent_id));
}

const VALID_DELIVERY_STATES = Object.freeze(["pending", "delivered", "failed", "superseded"]);

export function evaluateIntentActionability(intent, currentState) {
  if (!isRecord(intent)) throw new TypeError("intent must be an object");
  assertNoForbiddenKeys(intent, "intent");
  const safeIntent = safeIntentPayload(intent);

  if (!isRecord(currentState)) {
    return deepFreeze({ actionable: true, reason: "no_current_state" });
  }
  assertNoForbiddenKeys(currentState, "currentState");

  const intentSubject = safeIntent.subject_key;
  const currentSubject = optionalString(currentState.subject_key);
  if (currentSubject && intentSubject !== currentSubject) {
    return deepFreeze({ actionable: true, reason: "subject_mismatch" });
  }

  const intentScope = safeIntent.comparison_scope_key;
  const currentScope = optionalString(currentState.comparison_scope_key);
  if (intentScope && currentScope && intentScope !== currentScope) {
    return deepFreeze({ actionable: true, reason: "scope_mismatch" });
  }

  const eventType = safeIntent.event_type;
  const epsilon = 0.009;

  switch (eventType) {
    case "meaningful_drop":
    case "new_low":
    case "price_change": {
      if (!freshPriceState(currentState)) {
        return deepFreeze({ actionable: true, reason: "unverifiable_observation_pending" });
      }
      const intentPrice = canonicalNumber(safeIntent.current_value);
      const currentPrice = canonicalNumber(currentState.current_value);
      if (intentPrice !== null && currentPrice !== null) {
        if (currentPrice > intentPrice + epsilon) {
          return deepFreeze({ actionable: false, reason: "price_increased" });
        }
      }
      return deepFreeze({ actionable: true, reason: "current" });
    }
    case "target_hit": {
      if (!freshPriceState(currentState)) {
        return deepFreeze({ actionable: true, reason: "unverifiable_observation_pending" });
      }
      if (currentState.target_state === "above") {
        return deepFreeze({ actionable: false, reason: "target_no_longer_reached" });
      }
      return deepFreeze({ actionable: true, reason: "current" });
    }
    case "availability_restored": {
      if (!trustworthyAvailabilityState(currentState)) {
        return deepFreeze({ actionable: true, reason: "unverifiable_observation_pending" });
      }
      if (currentState.availability_state === "unavailable") {
        return deepFreeze({ actionable: false, reason: "no_longer_available" });
      }
      return deepFreeze({ actionable: true, reason: "current" });
    }
    case "observation_failed": {
      if (currentState.freshness === "fresh") {
        return deepFreeze({ actionable: false, reason: "observation_recovered" });
      }
      return deepFreeze({ actionable: true, reason: "current" });
    }
    default: {
      return deepFreeze({ actionable: true, reason: "current" });
    }
  }
}

export function safeDeliveryRecord(record) {
  if (!isRecord(record)) throw new TypeError("delivery record must be an object");
  assertNoForbiddenKeys(record, "record");

  const rawIntent = record.intent ?? (record.event_type ? record : null);
  if (!rawIntent) throw new TypeError("delivery record missing required intent");
  const safeIntent = safeIntentPayload(rawIntent);

  const deliveryState = requiredString(record.delivery_state ?? "pending", "delivery_state").toLowerCase();
  if (!VALID_DELIVERY_STATES.includes(deliveryState)) {
    throw new TypeError(`invalid delivery_state '${deliveryState}'`);
  }

  const attemptCount = Math.max(0, Math.trunc(Number(record.attempt_count ?? 0)));
  if (!Number.isFinite(attemptCount)) throw new TypeError("attempt_count must be a non-negative finite number");

  const deliveryRecord = {
    intent_id: safeIntent.intent_id,
    intent: safeIntent,
    delivery_state: deliveryState,
    attempt_count: attemptCount,
    last_attempt_at: optionalString(record.last_attempt_at),
    delivered_at: optionalString(record.delivered_at),
    superseded_at: optionalString(record.superseded_at),
    superseded_reason: optionalString(record.superseded_reason),
  };

  return deepFreeze(stableNormalize(deliveryRecord));
}

export function reconcileDeliveryState(existingRecords = [], derivedIntents = [], currentState = null, options = {}) {
  if (!Array.isArray(existingRecords)) throw new TypeError("existingRecords must be an array");
  if (!Array.isArray(derivedIntents)) throw new TypeError("derivedIntents must be an array");
  if (currentState !== null && !isRecord(currentState)) throw new TypeError("currentState must be an object or null");
  if (!isRecord(options)) throw new TypeError("options must be an object");

  assertNoForbiddenKeys(existingRecords, "existingRecords");
  assertNoForbiddenKeys(derivedIntents, "derivedIntents");
  if (currentState) assertNoForbiddenKeys(currentState, "currentState");
  assertNoForbiddenKeys(options, "options");

  const recordMap = new Map();

  for (const item of existingRecords) {
    const safeRec = safeDeliveryRecord(item);
    recordMap.set(safeRec.intent_id, safeRec);
  }

  for (const rawIntent of derivedIntents) {
    const safeInt = safeIntentPayload(rawIntent);
    if (!recordMap.has(safeInt.intent_id)) {
      const isSuppressed = options.suppress_delivery === true || options.proof_mode === true;
      const initialRecord = safeDeliveryRecord({
        intent_id: safeInt.intent_id,
        intent: safeInt,
        delivery_state: isSuppressed ? "delivered" : "pending",
        attempt_count: isSuppressed ? 1 : 0,
        delivered_at: isSuppressed ? (safeInt.observed_at ?? null) : null,
        last_attempt_at: isSuppressed ? (safeInt.observed_at ?? null) : null,
      });
      recordMap.set(safeInt.intent_id, initialRecord);
    }
  }

  const updatedRecords = [];
  const actionableIntents = [];

  for (const record of recordMap.values()) {
    let currentRecord = record;

    if (currentRecord.delivery_state === "pending" || currentRecord.delivery_state === "failed") {
      const evalResult = evaluateIntentActionability(currentRecord.intent, currentState);
      if (!evalResult.actionable) {
        currentRecord = safeDeliveryRecord({
          ...currentRecord,
          delivery_state: "superseded",
          superseded_at: currentState?.observed_at ?? null,
          superseded_reason: evalResult.reason,
        });
      } else {
        actionableIntents.push(currentRecord.intent);
      }
    }

    updatedRecords.push(currentRecord);
  }

  const sortedRecords = deepFreeze([...updatedRecords].sort((a, b) => a.intent_id.localeCompare(b.intent_id)));
  const sortedActionableIntents = deepFreeze([...actionableIntents].sort(compareIntents));

  return deepFreeze({
    records: sortedRecords,
    actionableIntents: sortedActionableIntents,
  });
}

export function recordDeliveryResult(existingRecords = [], targetIntentIds = [], deliveryResult = {}, options = {}) {
  if (!Array.isArray(existingRecords)) throw new TypeError("existingRecords must be an array");
  if (!Array.isArray(targetIntentIds)) throw new TypeError("targetIntentIds must be an array");
  if (!isRecord(deliveryResult)) throw new TypeError("deliveryResult must be an object");
  if (!isRecord(options)) throw new TypeError("options must be an object");

  assertNoForbiddenKeys(existingRecords, "existingRecords");
  assertNoForbiddenKeys(deliveryResult, "deliveryResult");
  assertNoForbiddenKeys(options, "options");

  const targetSet = new Set(targetIntentIds.map((id) => String(id).trim()));
  const attemptedAt = optionalString(deliveryResult.attempted_at, null);
  const isSuccess = deliveryResult.success === true || deliveryResult.proof_mode === true || options.proof_mode === true;

  const updated = existingRecords.map((item) => {
    const rec = safeDeliveryRecord(item);
    if (!targetSet.has(rec.intent_id)) return rec;

    if (isSuccess) {
      return safeDeliveryRecord({
        ...rec,
        delivery_state: "delivered",
        attempt_count: rec.attempt_count + 1,
        last_attempt_at: attemptedAt ?? rec.intent.observed_at,
        delivered_at: rec.delivered_at ?? attemptedAt ?? rec.intent.observed_at,
      });
    } else {
      return safeDeliveryRecord({
        ...rec,
        delivery_state: "failed",
        attempt_count: rec.attempt_count + 1,
        last_attempt_at: attemptedAt ?? rec.intent.observed_at,
      });
    }
  });

  return deepFreeze([...updated].sort((a, b) => a.intent_id.localeCompare(b.intent_id)));
}

export function groupIntents(intents) {
  if (!Array.isArray(intents)) throw new TypeError("intents must be an array");
  const deduped = new Map();
  for (const candidate of intents) {
    const safe = safeIntentPayload(candidate);
    const current = deduped.get(safe.intent_id);
    if (!current || canonicalJson(safe) < canonicalJson(current)) deduped.set(safe.intent_id, safe);
  }
  const ordered = [...deduped.values()].sort(compareIntents);
  const groups = new Map();
  for (const intent of ordered) {
    const key = `${intent.delivery_mode ?? "immediate"}\u0000${intent.group_key ?? "default"}`;
    if (!groups.has(key)) {
      groups.set(key, {
        delivery_mode: intent.delivery_mode ?? "immediate",
        group_key: intent.group_key ?? "default",
        intents: [],
      });
    }
    groups.get(key).intents.push(intent);
  }
  const result = [...groups.values()]
    .sort((a, b) => (
      (DELIVERY_RANK[a.delivery_mode] ?? 99) - (DELIVERY_RANK[b.delivery_mode] ?? 99)
      || String(a.group_key).localeCompare(String(b.group_key))
    ))
    .map((group) => deepFreeze({
      delivery_mode: group.delivery_mode,
      group_key: group.group_key,
      intent_ids: group.intents.map((intent) => intent.intent_id),
      intents: group.intents,
    }));
  return deepFreeze(result);
}

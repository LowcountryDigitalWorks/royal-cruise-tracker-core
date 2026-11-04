import { compareObservations } from './decision-history.mjs';

const REQUIRED = ['currentAdjustments', 'baselineAdjustments', 'unknownValueComponents', 'materialTermDifferences'];
const own = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
const object = (value, label) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError(`${label} must be an object`);
  return value;
};
const list = (value, label) => {
  if (!Array.isArray(value)) throw new TypeError(`${label} must be an array`);
  return value;
};
function minor(value, label, nonNegative = false) {
  if (typeof value !== 'number' || !Number.isFinite(value) || (nonNegative && value < 0)) {
    throw new TypeError(`${label} must be a finite ${nonNegative ? 'nonnegative ' : ''}amount`);
  }
  const scaled = value * 100;
  const rounded = Math.round(scaled);
  if (!Number.isSafeInteger(rounded) || Math.abs(scaled - rounded) > 1e-8) {
    throw new TypeError(`${label} must be representable in whole cents`);
  }
  return rounded;
}
function safeAdd(a, b, label) {
  const n = a + b;
  if (!Number.isSafeInteger(n)) throw new TypeError(`${label} exceeds safe monetary range`);
  return n;
}
function reasons(values, label) {
  const found = new Set();
  for (const value of list(values, label)) {
    if (typeof value !== 'string' || !value.trim()) throw new TypeError(`${label} entries must be nonempty strings`);
    found.add(value.trim());
  }
  return [...found].sort();
}
function sum(adjustments, label, currency) {
  const seen = new Set();
  let total = 0;
  for (const item of list(adjustments, label)) {
    object(item, `${label} entry`);
    if (typeof item.id !== 'string' || !item.id.trim()) throw new TypeError(`${label}.id is required`);
    if (seen.has(item.id)) throw new TypeError(`${label} has duplicate adjustment id: ${item.id}`);
    seen.add(item.id);
    if (!['credit', 'inclusion', 'adjustment'].includes(item.kind)) throw new TypeError(`${label}.${item.id}.kind is not recognized`);
    if (typeof item.sourceClass !== 'string' || !item.sourceClass.trim()) throw new TypeError(`${label}.${item.id}.sourceClass is required`);
    if (item.currency !== currency) throw new TypeError(`${label}.${item.id}.currency must match the comparable price currency`);
    // Signed value: positive credits/inclusions reduce effective cost; negative penalties increase cost.
    total = safeAdd(total, minor(item.amount, `${label}.${item.id}.amount`), label);
  }
  return total;
}
function output(status, {
  headlineDelta = null, knownValueDelta = null, knownNetDelta = null,
  direction = 'unknown', unknownComponents = [], comparabilityReasons = [], manualNextStep,
}) {
  return Object.freeze({
    status, headlineDelta, knownValueDelta, knownNetDelta, direction,
    unknownComponents: Object.freeze([...unknownComponents]),
    comparabilityReasons: Object.freeze([...comparabilityReasons]),
    manualNextStep,
  });
}

// Explicit evidence only; positive differences favor current. No provider/network/clock/storage/eligibility logic.
// The caller must explicitly supply both adjustment lists, value unknowns, term differences, and termsCompared.
// This API does not promise the caller has found every material component.
export function analyzeNetValue(input) {
  object(input, 'input');
  const { current, baseline, currentAdjustments, baselineAdjustments, termsCompared,
    unknownValueComponents, materialTermDifferences } = input;
  object(current, 'current');
  object(baseline, 'baseline');
  for (const key of REQUIRED) if (!own(input, key)) throw new TypeError(`${key} must be supplied explicitly`);
  if (typeof termsCompared !== 'boolean') throw new TypeError('termsCompared must be an explicit boolean');
  const unknown = reasons(unknownValueComponents, 'unknownValueComponents');
  const terms = reasons(materialTermDifferences, 'materialTermDifferences');
  if (current.status !== 'valid' || baseline.status !== 'valid' || current.stale === true || baseline.stale === true
      || current.value == null || baseline.value == null) {
    return output('insufficient-evidence', {
      unknownComponents: unknown, comparabilityReasons: [baseline.stale === true ? 'baseline-evidence-stale' : 'latest-evidence-unavailable'],
      manualNextStep: 'Verify fresh complete price evidence before comparing known value.',
    });
  }
  const c = minor(current.value, 'current.value', true);
  const b = minor(baseline.value, 'baseline.value', true);
  const comparable = compareObservations(current, baseline);
  if (!comparable.comparable) {
    return output('non-comparable', {
      unknownComponents: unknown, comparabilityReasons: comparable.reasons,
      manualNextStep: 'Confirm matching currency, taxes, price basis, source and product scope before comparing.',
    });
  }
  const cv = sum(currentAdjustments, 'currentAdjustments', current.currency);
  const bv = sum(baselineAdjustments, 'baselineAdjustments', current.currency);
  const headline = safeAdd(b, -c, 'headlineDelta');
  const value = safeAdd(cv, -bv, 'knownValueDelta');
  const net = safeAdd(headline, value, 'knownNetDelta');
  const blockers = [...new Set([...unknown, ...terms.map(t => `term:${t}`), ...(!termsCompared ? ['terms-not-verified'] : [])])].sort();
  const partial = blockers.length > 0;
  return output(partial ? 'partial' : 'complete', {
    headlineDelta: headline / 100, knownValueDelta: value / 100, knownNetDelta: net / 100,
    direction: partial ? 'unknown' : net > 0 ? 'better' : net < 0 ? 'worse' : 'same',
    unknownComponents: blockers, comparabilityReasons: [],
    manualNextStep: partial
      ? 'Known net difference is partial; verify unquantified value and booking terms before acting.'
      : 'Known net value can be compared; verify personalized eligibility before any manual action.',
  });
}

const TYPES = ['booking-specific', 'market', 'generic'];
const ISO = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,3})?(Z|[+-]\d{2}:\d{2})$/;
function object(v) { if (!v || typeof v !== 'object' || Array.isArray(v)) throw new TypeError('Expected object'); return v; }
function instant(v) {
  if (typeof v !== 'string') throw new TypeError('Expected timezone-aware timestamp');
  const m = ISO.exec(v);
  if (!m) throw new TypeError('Expected timezone-aware timestamp');
  const [y, mo, d, h, mm, s] = m.slice(1, 7).map(Number);
  if (y < 100 || mo < 1 || mo > 12 || d < 1 || d > new Date(Date.UTC(y, mo, 0)).getUTCDate()
    || h > 23 || mm > 59 || s > 59) throw new TypeError('Invalid calendar date/time');
  const zone = m[7];
  if (zone !== 'Z') {
    const hours = Number(zone.slice(1, 3)), minutes = Number(zone.slice(4));
    if (hours > 14 || minutes > 59 || (hours === 14 && minutes !== 0)) throw new TypeError('Invalid zone');
  }
  const ms = Date.parse(v);
  if (!Number.isFinite(ms)) throw new TypeError('Invalid instant');
  return ms;
}
function names(v) {
  if (!Array.isArray(v)) throw new TypeError('Expected explicit blocker list');
  for (const x of v) if (typeof x !== 'string' || !x.trim()) throw new TypeError('Invalid blocker');
  return [...new Set(v.map(x => x.trim()))].sort();
}
function result(state, evidenceClass, deadlineState, reasons, manualNextStep) {
  return Object.freeze({ state, evidenceClass, deadlineState,
    reasons: Object.freeze([...new Set(reasons)].sort()), manualNextStep, canClaimActionable: false });
}
// Consumes trusted decision-history analysis; cannot grant booking eligibility.
export function analyzeActionability({ asOf, analysis, policies, knownBlockers, unknownBlockers } = {}) {
  const now = instant(asOf);
  object(analysis);
  if (!Array.isArray(policies)) throw new TypeError('Expected explicit policies');
  const known = names(knownBlockers), unknown = names(unknownBlockers);
  const candidates = policies.map(p => {
    object(p);
    if (!TYPES.includes(p.sourceClass) || typeof p.sourceRef !== 'string' || !p.sourceRef.trim())
      throw new TypeError('Invalid policy provenance');
    return { sourceClass: p.sourceClass, deadline: p.deadline == null ? null : instant(p.deadline) };
  });
  const latest = analysis.latest, current = analysis.current, base = analysis.baseline;
  const fresh = analysis.latestState === 'valid' && analysis.freshPriceEvidence === true
    && latest && current && latest.id === current.id && latest.status === 'valid' && !latest.stale
    && current.status === 'valid' && !current.stale && Number.isFinite(latest.value)
    && latest.value === current.value && current.value >= 0;
  if (!fresh || base?.comparable !== true || !Number.isFinite(base.value)) {
    const why = [!fresh ? 'latest-price-not-fresh-complete' : 'baseline-not-comparable'];
    if (analysis.latestState === 'stale') why.push('latest-observation-stale');
    if (analysis.latestState === 'failed') why.push('latest-observation-failed');
    return result('insufficient-evidence', 'none', 'unknown', why,
      'Verify a fresh, complete, like-for-like price observation before considering action.');
  }
  if (base.value >= 0) return result('no-price-opportunity', 'none', 'unknown',
    ['no-lower-comparable-price'], 'No lower comparable price was observed; no price-based action is indicated.');
  if (!candidates.length) return result('policy-unknown', 'none', 'unknown', ['no-policy-evidence'],
    'Verify your booking-specific policy and deadline before taking any action.');
  const highest = candidates.find(p => p.sourceClass === 'booking-specific')?.sourceClass
    || candidates.find(p => p.sourceClass === 'market')?.sourceClass || 'generic';
  const dates = [...new Set(candidates.filter(p => p.sourceClass === highest).map(p => p.deadline))];
  if (dates.length !== 1) return result('verification-required', highest, 'conflicting',
    ['conflicting-policy-deadlines'], 'Conflicting policy/deadline evidence requires manual verification before acting.');
  if (highest !== 'booking-specific') return result('verification-required', highest, 'context-only',
    ['policy-not-booking-specific'], 'Generic or market policy is context only; verify actual booking terms.');
  if (dates[0] === null) return result('deadline-unknown', highest, 'unknown', ['booking-deadline-missing'],
    'Booking-specific policy exists, but its deadline is unknown; verify before acting.');
  if (dates[0] <= now) return result('deadline-passed', highest, 'passed',
    ['booking-deadline-reached-or-passed'], 'Supplied booking-specific deadline has passed; verify options manually.');
  if (known.length || unknown.length) return result('verification-required', highest, 'future',
    [...known.map(x => 'blocker:' + x), ...unknown.map(x => 'unknown:' + x)],
    'Review known or unresolved booking restrictions before considering any manual change.');
  return result('review-candidate', highest, 'future',
    ['fresh-lower-comparable-price', 'booking-deadline-future'],
    'Review candidate — verify personalized booking eligibility, credits, fees and penalties before acting.');
}

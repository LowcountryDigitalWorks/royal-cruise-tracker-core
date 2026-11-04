import test from 'node:test';
import assert from 'node:assert/strict';
import { analyzeActionability } from '../src/actionability.mjs';

const now = '2030-01-10T00:00:00Z';
const later = '2030-01-11T00:00:00Z';
const earlier = '2030-01-09T23:59:59Z';
const observation = (id, patch = {}) => ({ id, status: 'valid', stale: false, value: 400, ...patch });
const analysis = (patch = {}) => ({
  latestState: 'valid', freshPriceEvidence: true,
  latest: observation('current'), current: observation('current'),
  baseline: { comparable: true, value: -100 }, ...patch,
});
const policy = (sourceClass = 'booking-specific', deadline = later, sourceRef = 'synthetic-evidence') =>
  ({ sourceClass, deadline, sourceRef });
const evaluate = (patch = {}) => analyzeActionability({
  asOf: now, analysis: analysis(), policies: [policy()], knownBlockers: [], unknownBlockers: [], ...patch,
});

test('booking-specific future deadline and fresh lower price yield review candidate, not eligibility', () => {
  const result = evaluate();
  assert.deepEqual([result.state, result.evidenceClass, result.deadlineState, result.canClaimActionable],
    ['review-candidate', 'booking-specific', 'future', false]);
  assert.match(result.manualNextStep, /verify personalized booking eligibility/i);
});
test('missing policy does not invent final-payment rules', () => {
  assert.equal(evaluate({ policies: [] }).state, 'policy-unknown');
});
test('generic and market policies remain contextual even with deadline', () => {
  for (const source of ['generic', 'market']) {
    const result = evaluate({ policies: [policy(source)] });
    assert.deepEqual([result.state, result.deadlineState], ['verification-required', 'context-only']);
  }
});
test('booking-specific evidence outranks conflicting weaker generic deadline', () => {
  const result = evaluate({ policies: [policy('generic', earlier), policy('booking-specific', later)] });
  assert.equal(result.state, 'review-candidate');
});
test('conflicting booking-specific deadlines fail closed', () => {
  const result = evaluate({ policies: [policy('booking-specific', earlier), policy('booking-specific', later)] });
  assert.deepEqual([result.state, result.deadlineState], ['verification-required', 'conflicting']);
});
test('identical booking-specific deadlines are deterministic', () => {
  assert.equal(evaluate({ policies: [policy(), policy()] }).state, 'review-candidate');
});
test('unknown booking-specific deadline is never inferred from sailing', () => {
  assert.equal(evaluate({ policies: [policy('booking-specific', null)] }).state, 'deadline-unknown');
});
test('deadline exact boundary and already passed both fail closed', () => {
  for (const deadline of [now, earlier]) assert.equal(evaluate({ policies: [policy('booking-specific', deadline)] }).state, 'deadline-passed');
});
test('stale latest cannot be review candidate even with future booking deadline', () => {
  const result = evaluate({ analysis: analysis({ latestState: 'stale', latest: observation('current', { stale: true }) }) });
  assert.equal(result.state, 'insufficient-evidence');
});
test('failed latest cannot make older lower price actionable', () => {
  const result = evaluate({ analysis: analysis({ latestState: 'failed', freshPriceEvidence: false,
    latest: observation('failure', { status: 'failed', value: null }) }) });
  assert.equal(result.state, 'insufficient-evidence');
});
test('incomplete latest and invalid comparable baseline fail closed', () => {
  assert.equal(evaluate({ analysis: analysis({ current: observation('old') }) }).state, 'insufficient-evidence');
  assert.equal(evaluate({ analysis: analysis({ baseline: { comparable: false, value: -100 } }) }).state, 'insufficient-evidence');
  assert.equal(evaluate({ analysis: analysis({ baseline: null }) }).state, 'insufficient-evidence');
});
test('no lower comparable price produces no price opportunity', () => {
  for (const value of [0, 12]) assert.equal(evaluate({ analysis: analysis({ baseline: { comparable: true, value } }) }).state, 'no-price-opportunity');
});
test('known and unknown blockers suppress review candidate', () => {
  for (const key of ['knownBlockers', 'unknownBlockers']) {
    const result = evaluate({ [key]: ['deposit-restriction'] });
    assert.equal(result.state, 'verification-required');
    assert.equal(result.canClaimActionable, false);
  }
});
test('unknown blockers are sorted and deduplicated without exposing personal data', () => {
  const result = evaluate({ unknownBlockers: ['x', 'y', 'x'] });
  assert.deepEqual(result.reasons, ['unknown:x', 'unknown:y']);
});
test('offset timestamps compare as instants without using system clock', () => {
  const result = evaluate({ asOf: '2030-01-10T18:59:59-05:00' });
  assert.equal(result.state, 'review-candidate');
});
test('invalid asOf timestamps and calendar dates reject deterministically', () => {
  for (const value of ['2030-01-10', '2030-02-30T00:00:00Z', '2030-01-10T25:00:00Z', '2030-01-10T00:00:00', '2030-01-10T00:00:00+15:00']) {
    assert.throws(() => evaluate({ asOf: value }), TypeError);
  }
});
test('invalid deadline rejects deterministically', () => {
  assert.throws(() => evaluate({ policies: [policy('booking-specific', '2030-02-30T00:00:00Z')] }), TypeError);
});
test('unknown policy type and missing provenance reject', () => {
  assert.throws(() => evaluate({ policies: [policy('invented')] }), TypeError);
  assert.throws(() => evaluate({ policies: [policy('market', later, '')] }), TypeError);
});
test('missing explicit blocker lists do not assume no restrictions', () => {
  assert.throws(() => evaluate({ knownBlockers: undefined }), TypeError);
  assert.throws(() => evaluate({ unknownBlockers: undefined }), TypeError);
});
test('no hidden network, transaction or eligibility result contract', () => {
  const result = evaluate();
  assert.ok(Object.isFrozen(result) && Object.isFrozen(result.reasons));
  assert.equal(result.canClaimActionable, false);
  assert.equal('eligible' in result, false);
});

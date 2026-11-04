import test from 'node:test';
import assert from 'node:assert/strict';
import { transitionItemIntent } from '../src/item-intent.mjs';
const empty = { pinned: false, watched: false, preferences: null };
const pinned = { pinned: true, watched: false, preferences: null };
const watching = { pinned: false, watched: true, preferences: null };
const both = { pinned: true, watched: true, preferences: null };
const patch = { deliveryMode: 'digest', quietHoursEnabled: true };
const apply = (current, action, preferencesPatch) => transitionItemIntent({ current, action, preferencesPatch });
test('pin is passive: never creates Watch or side effects', () => {
  const r = apply(empty, 'pin');
  assert.deepEqual(r.state, pinned);
  assert.deepEqual(r.events, ['pinned']);
  assert.equal(r.notificationDispatchRequested, false);
  assert.equal(r.acquisitionChangeRequested, false);
});
test('watch is explicit and never auto-pins', () => {
  const r = apply(empty, 'watch');
  assert.deepEqual(r.state, watching);
  assert.deepEqual(r.events, ['watch-enabled']);
});
test('watch while pinned preserves pin', () => assert.deepEqual(apply(pinned, 'watch').state, both));
test('unwatch while pinned preserves pin and stops active preferences', () => {
  const before = { ...both, preferences: patch };
  const r = apply(before, 'unwatch');
  assert.deepEqual(r.state, pinned);
  assert.deepEqual(r.events, ['watch-disabled']);
  assert.deepEqual(r.changedFields, ['watched', 'preferences']);
});
test('unpin while watching keeps Watch unchanged', () => assert.deepEqual(apply(both, 'unpin').state, watching));
test('all four intent actions are idempotent', () => {
  for (const [state, action] of [[pinned, 'pin'], [empty, 'unpin'], [watching, 'watch'], [empty, 'unwatch']]) {
    const r = apply(state, action);
    assert.deepEqual(r.changedFields, []);
    assert.deepEqual(r.events, []);
  }
});
test('explicit watch can include subordinate preferences', () => {
  const r = apply(empty, 'watch', patch);
  assert.deepEqual(r.state, { pinned: false, watched: true, preferences: patch });
});
test('preference patch does not create Watch independently', () => {
  assert.throws(() => apply(empty, 'set-preferences', patch), TypeError);
  assert.throws(() => apply(pinned, 'set-preferences', patch), TypeError);
});
test('watching item may update preferences without changing intent', () => {
  const r = apply(watching, 'set-preferences', patch);
  assert.deepEqual(r.changedFields, ['preferences']);
  assert.deepEqual(r.events, []);
});
test('repeated preferences are idempotent', () => {
  assert.deepEqual(apply({ ...watching, preferences: patch }, 'set-preferences', patch).changedFields, []);
});
test('target configuration and identity fields cannot silently grant Watch', () => {
  assert.throws(() => apply({ ...empty, targetPrice: 100 }, 'pin'), TypeError);
  assert.throws(() => apply({ ...empty, householdId: 'fake' }, 'pin'), TypeError);
  assert.deepEqual(apply(empty, 'pin').state, pinned);
});
test('invalid action, malformed state and preference payload fail closed', () => {
  assert.throws(() => apply(empty, 'notify'), TypeError);
  assert.throws(() => apply({ ...empty, pinned: 1 }, 'watch'), TypeError);
  assert.throws(() => apply({ ...empty, preferences: patch }, 'pin'), TypeError);
  assert.throws(() => apply(watching, 'set-preferences'), TypeError);
  assert.throws(() => apply(empty, 'pin', patch), TypeError);
  assert.throws(() => apply(watching, 'set-preferences', { deliveryMode: 'every-second', quietHoursEnabled: false }), TypeError);
  assert.throws(() => apply(watching, 'set-preferences', { ...patch, webhook: 'anything' }), TypeError);
});
test('explicit unwatch never deletes historical evidence or causes transport', () => {
  const r = apply(watching, 'unwatch');
  assert.deepEqual(r.events, ['watch-disabled']);
  assert.equal('deleteHistory' in r, false);
  assert.equal(r.notificationDispatchRequested, false);
  assert.equal(r.acquisitionChangeRequested, false);
});
test('result state, change list and events are frozen', () => {
  const r = apply(empty, 'pin');
  assert.ok(Object.isFrozen(r) && Object.isFrozen(r.state)
    && Object.isFrozen(r.changedFields) && Object.isFrozen(r.events));
});

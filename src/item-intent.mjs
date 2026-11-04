const ACTIONS = new Set(['pin', 'unpin', 'watch', 'unwatch', 'set-preferences']);
const MODES = new Set(['instant', 'digest', 'off']);
function expectObject(v, label) {
  if (!v || typeof v !== 'object' || Array.isArray(v)) throw new TypeError(label + ' must be an object');
  return v;
}
function expectKeys(v, allowed, label) {
  for (const key of Object.keys(v)) if (!allowed.includes(key)) throw new TypeError(label + ' has unexpected field');
}
function prefs(value) {
  if (value === null) return null;
  const p = expectObject(value, 'preferences');
  expectKeys(p, ['deliveryMode', 'quietHoursEnabled'], 'preferences');
  if (!MODES.has(p.deliveryMode) || typeof p.quietHoursEnabled !== 'boolean') {
    throw new TypeError('Invalid notification preferences');
  }
  return Object.freeze({ deliveryMode: p.deliveryMode, quietHoursEnabled: p.quietHoursEnabled });
}
function initial(current) {
  const s = expectObject(current, 'current');
  expectKeys(s, ['pinned', 'watched', 'preferences'], 'current');
  if (typeof s.pinned !== 'boolean' || typeof s.watched !== 'boolean' || !Object.hasOwn(s, 'preferences')) {
    throw new TypeError('Explicit pin/watch booleans and preferences are required');
  }
  const p = prefs(s.preferences);
  if (!s.watched && p !== null) throw new TypeError('Unwatched items cannot have active notification preferences');
  return { pinned: s.pinned, watched: s.watched, preferences: p };
}
// Pure intent semantics. A Watch event is NOT a provider authorization, a
// scheduler dispatch, or a notification send. Those remain separately gated.
export function transitionItemIntent({ current, action, preferencesPatch } = {}) {
  const before = initial(current);
  if (!ACTIONS.has(action)) throw new TypeError('Unknown intent action');
  const provided = preferencesPatch !== undefined;
  if (provided && !['watch', 'set-preferences'].includes(action)) {
    throw new TypeError('Preferences can only accompany an explicit watch or preference action');
  }
  if (action === 'set-preferences' && !provided) throw new TypeError('Preferences patch is required');
  if (action === 'set-preferences' && !before.watched) throw new TypeError('Cannot set preferences without Watch');
  const after = { ...before };
  if (action === 'pin') after.pinned = true;
  if (action === 'unpin') after.pinned = false;
  if (action === 'watch') after.watched = true;
  if (action === 'unwatch') { after.watched = false; after.preferences = null; }
  if (provided) after.preferences = prefs(preferencesPatch);
  const changedFields = [];
  const events = [];
  if (before.pinned !== after.pinned) {
    changedFields.push('pinned');
    events.push(after.pinned ? 'pinned' : 'unpinned');
  }
  if (before.watched !== after.watched) {
    changedFields.push('watched');
    events.push(after.watched ? 'watch-enabled' : 'watch-disabled');
  }
  const samePrefs = (before.preferences === null && after.preferences === null)
    || (before.preferences !== null && after.preferences !== null
      && before.preferences.deliveryMode === after.preferences.deliveryMode
      && before.preferences.quietHoursEnabled === after.preferences.quietHoursEnabled);
  if (!samePrefs) changedFields.push('preferences');
  return Object.freeze({
    state: Object.freeze(after),
    changedFields: Object.freeze(changedFields),
    events: Object.freeze(events),
    acquisitionChangeRequested: false,
    notificationDispatchRequested: false,
  });
}

# ALERT-001A — provider-neutral notification intent semantics

ALERT-001A establishes a pure, dependency-free notification-intent contract before transport.

It is intentionally separate from Royal/provider acquisition, D1 persistence, Apprise delivery, retry state, scheduler behavior, and production activation.

## Boundary

`src/notification-intent.mjs` accepts already-sanitized semantic state plus explicit policy and deterministically returns provider-neutral notification intents.

The module may use deterministic local computation only. It must not:

- read environment variables;
- access the network, filesystem, database, provider, or notification transport;
- read the system clock or randomness;
- construct protected Royal/booking URLs;
- persist delivery state;
- imply a purchase, cancellation, rebooking, refund, payment, or check-in action.

All time/evidence/generation values are explicit inputs.

Incremental cash target: `$0`.

## Public-safe intent contract

The contract version is `alert-intent/v1`.

A normalized intent contains only allowlisted product-level semantics such as:

- deterministic `intent_id`;
- `subject_key` and optional comparable/group keys;
- `event_type`;
- explicit semantic generation used for legitimate re-arm/re-hit identity;
- semantic transition/reason;
- evidence observation time and freshness;
- product priority class;
- comparable current/delta values only when valid;
- target/availability/evidence/actionability/policy state;
- `delivery_mode` such as `immediate` or `digest_eligible`;
- safe route/action class such as `verify_current_price`;
- safe human evidence summary;
- non-secret policy key.

The public-safe contract rejects private-field names including credentials/tokens/cookies/sessions, booking/reservation identifiers, passenger names, payment/card data, notification destination URLs, raw provider payloads, and protected URLs.

Unknown benign fields are not serialized because `safeIntentPayload()` is allowlist-based.

## Pure functions

### `deriveIntents(previousState, currentState, policy)`

Derives only evidence-supported events. The current release supports bounded semantics for:

- `target_hit`;
- `meaningful_drop`;
- `price_change` for explicit any-change policy;
- `new_low`;
- `availability_restored`;
- `observation_failed` after an explicit repeated-failure threshold.

The first observation establishes a baseline and does not imply a generic change. A bootstrap target hit is allowed only when explicitly enabled.

Fresh comparable price evidence is required for price/target events. Non-comparable, stale, incomplete, or failed latest evidence cannot create fresh price advice.

A restock requires contiguous trustworthy unavailable -> available evidence. Provider/observation failure is not sold-out/restock evidence.

### `intentFingerprint(intent)`

Creates a deterministic SHA-256 identity over canonical material semantic identity.

Fingerprint identity includes the subject/scope, event type, semantic generation, transition, material semantic value/state, and policy identity needed to distinguish legitimate generations.

Fingerprint identity deliberately excludes:

- rendered wording/formatting;
- delivery provider or route class;
- delivery mode;
- observation/retry timestamp alone;
- object insertion order.

Equivalent semantic events therefore produce the same ID. A legitimate target/restock generation after re-arm produces a distinct ID without using randomness or retry time as identity.

### `groupIntents(intents)`

Deduplicates by semantic intent ID, applies deterministic priority/event/value/evidence ordering, and groups related intents by safe group key and delivery mode while retaining every constituent intent ID.

This is grouping semantics only; it is not a persisted digest queue or schedule.

### `safeIntentPayload(intent)`

Produces the immutable public-safe allowlisted representation and validates deterministic identity.

## Threshold policy

Meaningful-drop and any-change modes may specify:

- non-negative absolute threshold;
- non-negative percentage threshold;
- explicit `ANY` or `ALL` combination mode.

An epsilon suppresses meaningless numeric wobble. Any-change direction is explicitly `DOWN` or `BOTH` in this release.

## Delivery-state lifecycle and retry semantics (ALERT-REL-001)

ALERT-REL-001 introduces durable notification intent lifecycle and delivery state management decoupleable from raw observation history.

### Conceptual delivery states

- `pending`: derived semantic intent awaiting successful transport acknowledgment.
- `delivered`: successfully delivered and acknowledged transport state.
- `failed`: delivery attempt failed; intent remains retryable while actionable.
- `superseded`: event is no longer actionable under current observation evidence before retry (e.g. price increased, item became unavailable, target exceeded, observation recovered, or scope changed).

### Pure lifecycle functions

#### `safeIntentLifecycleRecord(record)`

Sanitizes and returns an allowlisted intent lifecycle record with explicit tracking fields (`delivery_state`, `attempt_count`, `last_attempt_at`, `delivered_at`, `superseded_at`, `superseded_reason`).

#### `reconcileNotificationIntents({ existingRecords, currentState, previousState, policy, timestamp })`

Evaluates existing pending/failed intent records against current observation evidence:

- **Price drops / new lows**: remain `pending` when current observation is $ \le $ original drop price. If price increases above original drop price, intent transitions to `superseded` (`price_increased`).
- **Restocks**: remain `pending` while item stays `available`. If item becomes `unavailable`, intent transitions to `superseded` (`became_unavailable`).
- **Target hits**: remain `pending` while target is `reached`. If state becomes `above`, transitions to `superseded` (`target_exceeded`).
- **Observation failures**: remain `pending` while freshness is `failed`. On fresh observation, transitions to `superseded` (`observation_recovered`).
- **Scope mismatch**: transitions to `superseded` (`scope_changed`).
- **Provider / observation failures**: do not supersede price or availability intents.

Newly derived intents are merged and deduplicated by deterministic fingerprint (`intent_id`). Delivered, pending, or superseded records are preserved.

#### `recordDeliveryAttempt(records, attemptResult)`

Updates records following transport attempt (`status`: `delivered`, `failed`, or `suppressed`). `suppressed` / proof mode alerts are treated as acknowledged policy decisions rather than transport failures.

## Compatibility

This release does not replace or alter the current runtime tracker alert pipeline:

- `build_alerts()` remains unchanged;
- `consolidate_alerts()` remains unchanged;
- `send_apprise()` remains unchanged;
- `persist_notification_events()` remains unchanged;
- no D1 schema or production workflow changes are made.

A later separately authorized integration may adapt legacy alert dictionaries to the accepted pure intent boundary with parity tests.

## Test contract

`tests/test_notification_intent.mjs` and focused integrity tests cover:

- baseline/no-change behavior;
- bootstrap target policy;
- target steady/re-arm/re-hit generation;
- epsilon/noise suppression;
- absolute and percentage thresholds;
- `ANY` / `ALL` threshold combination;
- one-way and bidirectional any-change policy;
- meaningful drop / new low;
- stale/incomplete/failed/non-comparable suppression;
- comparison-scope mismatch;
- contiguous restock and failure-gap suppression;
- repeated-failure health threshold;
- deterministic fingerprint invariants;
- allowlist/private-field behavior;
- deterministic grouping and constituent ID preservation;
- output immutability;
- no persisted delivery-state surface;
- no environment/network/filesystem/clock/randomness access.

Public Core CI must run these tests in addition to all existing repository gates before the release is review-ready.

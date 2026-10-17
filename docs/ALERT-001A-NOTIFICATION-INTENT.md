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

## Delivery state lifecycle and reliability (ALERT-REL-001)

ALERT-REL-001 decouples observation history advancement from notification transport delivery acknowledgment.

An observation can advance price/watch history independently of notification transport. Actionable alert intents remain pending and retriable across subsequent runs until delivery is acknowledged or the underlying event is superseded.

### Conceptual delivery states

- `pending`: Intent created, delivery pending or not yet attempted;
- `failed`: Notification transport call failed on attempt, retryable if actionable;
- `delivered`: Notification transport acknowledged successfully;
- `superseded`: Underlying event is no longer actionable/current before retry (e.g. price increased or item became unavailable).

### Pure delivery functions

#### `evaluateIntentActionability(intent, currentState)`
Evaluates whether a previously generated pending/failed intent remains actionable relative to latest observation state:
- Price drop / new low / change: actionable if current price is still <= original intent price; superseded with `price_increased` if current price increased;
- Target hit: actionable if target state remains `reached`; superseded with `target_no_longer_reached` if target state is `above`;
- Availability restored: actionable if availability state remains `available`; superseded with `no_longer_available` if state becomes `unavailable`;
- Observation failed: actionable if freshness remains `failed`; superseded with `observation_recovered` when observation becomes `fresh`.

Provider or observation failure during a subsequent run keeps pending intents unverifiable and pending rather than incorrectly superseding them.

#### `safeDeliveryRecord(record)`
Produces a frozen, allowlisted delivery record object containing:
- `intent_id` and safe `intent` payload;
- `delivery_state` (`pending` | `delivered` | `failed` | `superseded`);
- `attempt_count` (non-negative integer);
- optional `last_attempt_at`, `delivered_at`, `superseded_at`, `superseded_reason`.

Enforces privacy checks (`assertNoForbiddenKeys`) to ensure no PII or transport secrets exist in stored delivery state.

#### `reconcileDeliveryState(existingRecords, derivedIntents, currentState, options)`
Reconciles existing delivery records and newly derived intents against current observation state:
- Adds new derived intents as `pending` records;
- Evaluates pending/failed records against `currentState` using `evaluateIntentActionability()`;
- Marks non-actionable records as `superseded`;
- Preserves original intent context (including original `observed_at`) for retryable intents;
- Returns `{ records, actionableIntents }`.

#### `recordDeliveryResult(existingRecords, targetIntentIds, deliveryResult, options)`
Updates delivery records following a transport attempt:
- `success: true` or `proof_mode: true` transitions matching records to `delivered` state with `delivered_at` timestamp;
- `success: false` transitions matching records to `failed` state and increments `attempt_count`.

`SUPPRESS_ALERTS`/proof mode is a delivery policy and is recorded as `delivered` rather than a production delivery failure.

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

### Final reliability gates

Retained availability/restock replay is fail-closed unless sanitized provider-run continuity proves the unavailable and available observations came from adjacent successful provider runs; failed or intervening unobserved runs break continuity. Consolidated transport is also all-or-none: every eligible constituent intent must win its guarded pre-transport claim or no subset is sent; already-claimed rows are returned to retryable state with exact attempt-ID guards.

Provider-run continuity uses a durable pre-acquisition `started` marker updated to `success` or `provider_failed` on the same run key. Successful acquisition is marked before parsing/derivation; a raised provider call or raised parser therefore cannot disappear between availability observations, and an outcome-write failure leaves the `started` marker as a fail-closed continuity break. The `started` write is read back before Royal acquisition; if it cannot be confirmed durable, that provider attempt is not started. A provider `success` endpoint is eligible for restock continuity only when a separate success-confirmation artifact exists; that artifact is written only after the runtime successfully reads back the matching `success` outcome. This prevents an outcome write whose readback was ambiguous from later re-entering replay as trusted continuity. Retained transition replay is a crash-recovery mechanism only and is limited to the newest four rows per subject whose transition timestamp is no older than 48 hours; older or malformed timestamps fail closed.

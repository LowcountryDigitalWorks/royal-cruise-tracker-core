# Architecture

## Purpose

Royal Cruise Tracker Core separates reusable monitoring software from deployment-specific household and infrastructure data.

The design objective is:

`public generic code -> protected runtime profile/secrets -> bounded provider calls -> sanitized history -> owner-facing alert`

## Components

### Runtime profile

`scripts/runtime_profile.py` requires an explicit runtime mode. Production mode requires protected runtime configuration and cannot fall back to the synthetic demo fixture; demo mode is reserved for CI/tests/examples.

It supplies:

- sailing identity and display metadata;
- target-window schedule;
- generic profile slots;
- traveler-to-pseudonymous-role mapping;
- watch specifications and grouping;
- non-secret behavioral policy.

A real deployment must supply private values at runtime instead of modifying committed source. FOUNDATION-001 permits exactly one enabled production profile; multi-profile production is deferred until due evidence is profile-scoped.

For that single-profile production contract, the provider-capable process receives only the active primary profile's credential, reservation, notification, threshold, and watchlist environment family. A disabled profile slot may remain described in protected runtime-profile structure, but its secret values are intentionally absent from the provider process. `load_profiles()` evaluates a profile's enable control before reading that profile's secret-backed environment names, so a missing/false disabled-profile control safely prevents dormant secret access.

### Tracker

`scripts/run_tracker.py` provides the core wrapper:

- builds ephemeral upstream configuration;
- invokes the pinned upstream checker;
- parses bounded watch/fare output;
- pseudonymizes traveler observations;
- writes sanitized D1 history;
- produces deduplicated alerts.

Adaptive v1-v4 extend this behavior with scheduling, history, purchased-order state, promotion context, and availability/restock intelligence.

### Decision/history semantic core

`src/decision-history.mjs` is a pure, dependency-free semantic layer for a later synthetic-only decision/history UX.

It has no network, storage, provider, or rendering behavior. It accepts synthetic/provider-neutral observation records and deterministically derives only evidence-supported state such as:

- current and immediately previous valid comparable observations;
- comparable observation count, record low, and recent observed range;
- comparable delta/percentage;
- target threshold state and re-arm semantics;
- contiguous availability transitions/restock;
- stale/failed/unknown state;
- evidence labels such as `new low observed`, `target reached`, `meaningful drop`, `available again`, or `no meaningful change`.

Unknown comparison dimensions do not silently equal one another. Failed observations remain health/history evidence but do not become price or availability facts. Stale/failed latest evidence cannot generate a fresh decision alert.

Configured policy/deadline metadata may be carried descriptively, but one universal Royal final-payment/repricing/cancellation rule is not encoded. Lower observed price alone does not imply eligibility.

The exact contract and first-UI asset policy are documented in `docs/FOUNDATION-002-SEMANTICS.md`.

### Due gate

`scripts/royal_check_due.py` loads target windows from the runtime profile.

For automatic/event wakes it:

1. identifies the most recent configured target;
2. reads latest persisted authenticated evidence;
3. proceeds only when that target remains unsatisfied;
4. fails closed on an authoritative-state read failure.

Manual owner dispatch remains distinct.

### Scheduler bridge

`cloudflare/scheduler-wake.js` contains the generic dispatch helper.

Both the expected cron and GitHub workflow-dispatch URL are runtime values. The source does not name a production repository or hard-code a production cron.

Scheduler dispatch uses two separate secret purposes:

- `GITHUB_ACTIONS_DISPATCH_TOKEN` is the GitHub bearer credential and is used only for the workflow-dispatch authorization header;
- `SCHEDULER_TARGET_PROOF_KEY` is the HMAC key for `scheduler_target_at` and is mapped by GitHub Actions only to runtime `ROYAL_TARGET_PROOF_KEY` for verification.

There is no production-code fallback from the proof key to the bearer token. If the proof key is unavailable or a supplied proof is invalid, the automatic wake may still proceed, but the exact scheduler timestamp is not trusted; sanitized due-gate fallback telemetry is used instead. Telemetry authentication failure therefore cannot silently authorize false exact timing and does not become a Royal/provider acquisition failure.

## Public workflow trust boundary

Public pull requests are untrusted input.

Therefore CI:

- uses no Royal/Cloudflare credentials;
- makes no authenticated provider calls;
- does not deploy;
- does not use `pull_request_target`.

The price-check workflow is not triggered by pull requests. It remains manual/event-dispatch only.

Because this repository is public, workflow stdout/stderr and step summaries are treated as a public interface. Provider-capable execution runs behind `scripts/public_runner.py`, which keeps child logs and child summaries ephemeral and emits only generic success/failure classification. The due gate similarly suppresses exact target/evidence timestamps in public summaries.

The production workflow validates the protected profile before provider traffic, then injects only the currently authorized active profile secret family into the provider-capable tracker process. Dormant profile secrets are not exposed merely because the protected profile schema still describes a disabled slot.

## Public source and asset boundary

Tracked relative paths/filenames are public data and are scanned against the accepted private-literal classes. Tracked UTF-8/text-like content is scanned without relying on a UI-extension allowlist, and the same path/content protections apply across every commit reachable from HEAD.

These validators are safety guardrails, not exhaustive PII detection. Synthetic-only source/fixtures, secret-history scanning, and exact-artifact human review remain required layers.

For the first UX prototype, use synthetic text/source assets by default. Screenshots, exported customer/provider artifacts, opaque downloaded images/fonts/archives, and similar binary assets are not permitted merely for visual polish. Any later binary asset requires separate provenance, privacy, and metadata review.

## Private deployment boundary

A production deployment may provide:

- `ROYAL_PROFILE_JSON`;
- the active profile credential/notification/watch values permitted by the single-profile contract;
- D1 token/account/database values;
- GitHub workflow-dispatch bearer credential;
- dedicated scheduler target-proof key;
- scheduler target configuration;
- optional non-secret notification/watch policy settings.

Those values are deployment configuration, not public source. Source acceptance does not provision, rotate, or activate any secret.

## Data handling

D1 may store sanitized operational history needed for price intelligence. Credentials, raw booking documents, reservation IDs, raw passenger names, payment-card data, and private notification endpoints do not belong in the public repository.

Public-repository sanitization is distinct from private deployment observation state. Private runtime observations may remain in protected deployment state, while only synthetic/public-safe source, history, logs, and fixtures belong here.

## Cutover model

Public-core acceptance and production activation are separate phases.

A deployment should prove deterministic parity first. Only then may the owning workstream propose provisioning the matching deployment secrets, changing a scheduler or production workflow target, or otherwise activating the accepted source, with explicit current/proposed/rollback evidence. Merging code alone does not create the dedicated proof key, update Cloudflare/GitHub secret state, or authorize production cutover.

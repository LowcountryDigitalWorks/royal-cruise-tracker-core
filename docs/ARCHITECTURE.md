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

### Tracker

`scripts/run_tracker.py` provides the core wrapper:

- builds ephemeral upstream configuration;
- invokes the pinned upstream checker;
- parses bounded watch/fare output;
- pseudonymizes traveler observations;
- writes sanitized D1 history;
- produces deduplicated alerts.

Adaptive v1-v4 extend this behavior with scheduling, history, purchased-order state, promotion context, and availability/restock intelligence.

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

The dispatch target is signed with the existing HMAC proof contract so GitHub can distinguish a scheduler-originated target from a human/manual dispatch.

## Public workflow trust boundary

Public pull requests are untrusted input.

Therefore CI:

- uses no Royal/Cloudflare credentials;
- makes no authenticated provider calls;
- does not deploy;
- does not use `pull_request_target`.

The price-check workflow is not triggered by pull requests. It remains manual/event-dispatch only.

Because this repository is public, workflow stdout/stderr and step summaries are treated as a public interface. Provider-capable execution runs behind `scripts/public_runner.py`, which keeps child logs and child summaries ephemeral and emits only generic success/failure classification. The due gate similarly suppresses exact target/evidence timestamps in public summaries.

## Private deployment boundary

A production deployment may provide:

- `ROYAL_PROFILE_JSON`;
- generic profile credential secrets;
- D1 token/account/database values;
- scheduler dispatch credential and target;
- optional notification/watch settings.

Those values are deployment configuration, not public source.

## Data handling

D1 may store sanitized operational history needed for price intelligence. Credentials, raw booking documents, reservation IDs, raw passenger names, payment-card data, and private notification endpoints do not belong in the public repository.

## Cutover model

Public-core acceptance and production activation are separate phases.

A deployment should prove deterministic parity first. Only then may the owning workstream propose changing a scheduler or production workflow target, with explicit current/proposed/rollback evidence.

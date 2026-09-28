# Royal Cruise Tracker Core — Agent Rules

This repository is the public reusable-code authority for Royal Cruise Tracker Core.

## Live-state-first

Before changing code:

1. read current `main`;
2. read open/recent pull requests and issues;
3. read this file and `docs/ARCHITECTURE.md`;
4. inspect relevant workflows/tests;
5. verify the exact current branch/base/head before review or merge.

Live repository state overrides chat history and old handoffs.

## Public-data boundary

Never commit real deployment data.

Do not add:

- traveler or household identity;
- reservation/booking IDs;
- real cabin or household booking evidence;
- payment-card or payment-account data;
- real notification destinations;
- credentials, tokens, cookies, or sessions;
- Cloudflare account/database IDs;
- production scheduler URLs/cron values;
- raw provider responses containing personal data.

Use synthetic/demo fixtures only.

## Production boundary

Public-core development does not authorize production changes.

Do not:

- redirect a live scheduler;
- add/rotate credentials;
- deploy to a production Worker;
- mutate production D1;
- increase provider request volume;
- perform booking/payment/check-in actions.

Those require the owning production workstream and applicable owner approval.

## Workflow rules

- treat all public Actions stdout/stderr and step summaries as public data;
- provider-capable execution must use explicit `ROYAL_RUNTIME_MODE=production` and a protected real profile;
- FOUNDATION-001 production supports exactly one enabled profile until profile-scoped due evidence is designed;
- no `pull_request_target`;
- PR CI must receive no production secrets;
- keep `GITHUB_TOKEN` least privilege;
- due-gate automatic wakes before provider traffic;
- pin third-party runtime/action dependencies where practical;
- preserve no-auto-buy/cancel/refund/rebook/payment/check-in behavior.

## Change discipline

For meaningful work:

1. branch from live `main`;
2. keep scope bounded;
3. run Python compile/tests plus current-tree and reachable-history public-boundary validation;
4. run pinned actionlint and secret-history scanning when source/config/workflow surfaces change materially;
5. open a PR;
6. independently review the exact head;
7. merge only when required checks are clean;
8. verify `main` afterward.

Do not copy private-repository history into this repository.

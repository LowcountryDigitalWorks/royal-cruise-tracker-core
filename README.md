# Royal Cruise Tracker Core

Reusable, privacy-separated Royal Caribbean monitoring core maintained by Lowcountry Digital Works.

This repository is intentionally public and contains **generic software and synthetic examples only**. It is a fresh-history extraction; it does not contain the private operational history or household data of any deployment.

## Current state

**Foundation / parity candidate — not yet a production dispatch target.**

The current public foundation includes:

- runtime-configured sailing and household profiles;
- a synthetic committed demo profile;
- bounded Royal price/watch parsing;
- adaptive watch selection and historical comparison;
- Cloudflare D1 persistence helpers;
- product availability/restock handling;
- promotion-catalog handling;
- target-window due gating before Royal traffic;
- a runtime-configured Cloudflare-to-GitHub dispatch helper;
- a manual/event-dispatch price-check workflow;
- deterministic public CI.

No workflow in this repository automatically buys, cancels, refunds, rebooks, pays for, or checks in a reservation.

## Public/private boundary

Public source owns reusable algorithms, schemas/contracts, synthetic fixtures, and validation.

Real deployment data belongs in protected runtime configuration and must never be committed here, including:

- traveler names or household identity;
- reservation or booking identifiers;
- cabin details tied to a real household;
- booking or purchase financial evidence;
- notification destinations;
- credentials, tokens, cookies, or sessions;
- Cloudflare account/database identifiers;
- production scheduler targets.

Production configuration is expected through protected GitHub/Cloudflare runtime values. The committed `config/demo-profile.json` exists only for tests and examples.

## Runtime profile

Every executable use must set `ROYAL_RUNTIME_MODE` explicitly:

- `production` — requires `ROYAL_PROFILE_JSON` or an explicit non-demo `ROYAL_PROFILE_PATH`; it never falls back to committed demo data.
- `demo` — may use `config/demo-profile.json` for public CI/tests/examples.

FOUNDATION-001 intentionally supports **exactly one enabled production profile** because target-window completion evidence is global. Multi-profile production use requires a later profile-scoped due-gate design.

The profile defines the sailing, watch policy, traveler-to-pseudonymous-role mapping, schedule, and the names of environment variables that hold protected credentials.

## Workflows

### Public CI

`.github/workflows/ci.yml` runs only deterministic local checks. It receives no Royal or Cloudflare secrets and performs no network calls to those providers.

### Price check

`.github/workflows/price-check.yml` is `workflow_dispatch` only. It has no GitHub-native schedule. A future production scheduler may dispatch it only after a separately accepted cutover.

The workflow preserves a due gate before Docker or Royal traffic. Automatic/event wakes that do not represent an unsatisfied configured target window stop before Royal requests.

## Security model

- PR validation uses no production secrets.
- No `pull_request_target` workflow is used.
- Repository permission in the price-check workflow is read-only.
- Runtime credentials remain ephemeral.
- Provider-capable public runs suppress child stdout/stderr and child step summaries; only allowlisted generic status/failure classes may reach public Actions output.
- D1 stores sanitized/pseudonymous operational history, not Royal credentials.
- Production execution fails closed before D1/Docker/Royal when a protected real profile is absent.
- Public source must pass current-tree and reachable-history boundary validation, pinned actionlint, and a pinned Betterleaks history scan before acceptance.

## Upstream

The monitoring wrapper uses `jdeath/CheckRoyalCaribbeanPrice`, pinned by immutable container digest in the workflow.

The upstream MIT notice is preserved at `LICENSES/upstream-MIT.txt`.

## License

Repository visibility does not itself grant a project-wide reuse license. No LDW project-wide license has been granted yet. Third-party components remain governed by their own included notices.

## Development

Run the deterministic checks:

```bash
ROYAL_RUNTIME_MODE=demo python3 -m py_compile scripts/*.py
ROYAL_RUNTIME_MODE=demo python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/validate_public_boundary.py
python3 scripts/validate_public_history.py
git diff --check
```

Meaningful changes should use branches and pull requests. Production cutover, scheduler-target changes, credential changes, and real-data migration remain separately gated.

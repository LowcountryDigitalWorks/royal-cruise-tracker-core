# FOUNDATION-001 Independent QA Correction

Status: **CORRECTED CANDIDATE — INDEPENDENT EXACT-HEAD RE-REVIEW REQUIRED**

Date: 2026-09-28

This candidate supersedes public-core PR #2. It is reconstructed from public `main` rather than from the prior PR branch, so the prior internal device marker is not an ancestor of this candidate.

## Accepted correction scope

1. Public Actions output is treated as public data.
   - Provider child stdout/stderr stays in an ephemeral runner-only file.
   - Child `GITHUB_STEP_SUMMARY` is redirected to an ephemeral file.
   - Public logs expose only generic success or failure class.
   - Due-gate public summaries omit exact target/evidence timestamps.
2. Production runtime configuration fails closed.
   - `ROYAL_RUNTIME_MODE` is mandatory.
   - Production mode requires an explicit protected profile.
   - Production mode cannot fall back to `config/demo-profile.json`.
3. FOUNDATION-001 is explicitly single-enabled-profile in production.
   - Multi-profile production use is deferred until completion evidence is profile-scoped.
4. Public boundary enforcement is deterministic.
   - current-tree validation;
   - reachable-HEAD history validation;
   - pinned actionlint v1.7.12 with verified SHA-256;
   - pinned Betterleaks v1.8.1 reachable-HEAD history scan with verified SHA-256;
   - Betterleaks live validation remains disabled.
5. Documentation and contract tests are synchronized.

## Explicit non-scope

This correction does not authorize production scheduler migration, repository/environment secret creation, Cloudflare or D1 mutation, Royal provider traffic, booking/payment/check-in changes, production deployment, or merge without independent exact-head review.

The private production repository remains the production/rollback authority.

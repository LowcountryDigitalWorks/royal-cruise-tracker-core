# FOUNDATION-002 public boundary and decision/history semantics

FOUNDATION-002 establishes safety and correctness primitives for a later synthetic-only Decision Card / History / Changes Feed. It does not implement UI, provider access, production cutover, or transaction behavior.

## Layered public-data safety

The public-boundary validator is a guardrail, not exhaustive PII detection. A green result does not prove arbitrary content is safe to publish.

Public acceptance therefore layers:

- synthetic-only committed examples and fixtures;
- scanning of every tracked relative path/filename against the accepted private-literal classes;
- content scanning of tracked UTF-8/text-like files without relying on a UI-extension allowlist;
- deterministic sentinels for HTML, CSS, SVG, JavaScript/module source, and paths;
- reachable-history path and text scanning;
- pinned Betterleaks reachable-history scanning;
- exact-artifact human review before merge.

Validator implementation files remain content-self-exempt because they necessarily contain the detection expressions they enforce; their repository paths are still scanned.

## First-UI asset policy

The first public UX prototype must default to synthetic text/source assets:

- semantic HTML, CSS, JavaScript/ES modules, and TypeScript/JSX only if later justified;
- SVG only as synthetic, text-scanned source;
- no screenshots or exported customer/provider artifacts;
- no opaque downloaded images, font binaries, archives, or similar assets merely for visual polish.

A binary asset is not safe merely because the text validator skips it. Any later binary asset requires separate provenance, privacy, and metadata review before it may enter the public repository.

## Provider-neutral observation contract

`src/decision-history.mjs` is pure and dependency-free. It has no network, storage, provider, or UI behavior.

Each observation carries:

- stable synthetic/event identity;
- observation timestamp;
- valid or failed state;
- stale state;
- observed numeric value where available;
- currency;
- price basis;
- taxes/fees basis;
- provenance/source class;
- availability state;
- comparable scope identity: item, sailing, category, variant, occupancy, and promotion scope.

Unknown comparison dimensions do not silently equal one another.

## Deterministic history rules

- Input is required to be strictly chronological after exact duplicate-ID removal. Out-of-order or same-time ambiguous distinct observations fail closed.
- Exact duplicate IDs with identical evidence are idempotently removed. Conflicting duplicate IDs fail closed.
- `previous` means the immediately previous valid comparable observation, never the first/oldest point by accident.
- Failed observations remain raw history evidence but are not price points.
- Current comparison scope determines observation count, record low, and recent range.
- Delta and percentage are emitted only when a comparable prior observation exists.
- Currency, price basis, taxes/fees basis, provenance, or scope mismatch is explicitly non-comparable.
- Stale/failed latest evidence cannot generate a fresh decision alert.

## Availability rules

Availability is distinct from provider health:

- available;
- unavailable;
- not open;
- unknown/provider failure.

Restock is emitted only for contiguous valid `unavailable -> available` evidence. A provider failure breaks continuity, so the system does not infer sold-out or restock across that gap.

## Targets, labels, and re-arm

A configured target may produce descriptive state such as `target reached`. A target-hit alert occurs only on a false-to-true threshold transition; an unchanged satisfied condition does not repeatedly alert. Leaving the threshold re-arms it for a later hit.

Evidence-backed labels may include:

- `new low observed`;
- `below recent observed range`;
- `target reached`;
- `meaningful drop`;
- `available again`;
- `no meaningful change`.

These labels are descriptive evidence summaries, not predictions or recommendation-accuracy claims.

## Policy/deadline boundary

The module may carry configured policy/deadline metadata, including source class, market, effective date, booking-created date, and deadline. Missing policy remains `unknown`.

FOUNDATION-002 does not encode one universal Royal final-payment, repricing, or cancellation rule and does not infer eligibility from a lower observed price or days-to-sailing. Later UX should use evidence-first language such as `Lower observed price — verify eligibility` when policy support is incomplete.

## Explicit non-scope

FOUNDATION-002 does not add:

- customer-facing UI;
- Royal/provider calls;
- production or private D1 access;
- Cloudflare changes;
- credentials/secrets;
- buy/wait prediction;
- future-price prediction;
- reprice/cancel/refund/payment/check-in actions;
- new package-manager dependencies or paid infrastructure.

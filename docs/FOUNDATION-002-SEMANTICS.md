# FOUNDATION-002 public boundary and decision/history semantics

FOUNDATION-002 establishes safety and correctness primitives for a later synthetic-only Decision Card / History / Changes Feed. It does not implement UI, provider access, production cutover, or transaction behavior.

## Layered public-data safety

The public-boundary validator is a guardrail, not exhaustive PII detection. A green result does not prove arbitrary content is safe to publish.

Public acceptance therefore layers:

- synthetic-only committed examples and fixtures;
- scanning of every tracked relative path/filename against the accepted private-literal classes;
- content scanning of tracked UTF-8/text-like files without relying on a UI-extension allowlist;
- deterministic current-tree traversal sentinels for HTML, CSS, SVG, JavaScript/module source, and paths;
- disposable-git reachable-history tests proving deleted/renamed historical artifacts remain detectable with their originating commit;
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
- Exact duplicate IDs with identical semantic evidence are idempotently removed. Scope equality is structural and order-independent; a genuinely changed represented scope field is conflicting evidence and fails closed.
- `latest` is the actual latest normalized observation, regardless of whether it has complete price evidence.
- `current` is the latest complete price observation available for historical comparison. It may therefore be older than `latest`.
- `freshPriceEvidence` is true only when the latest state is valid and `current` is the actual latest observation.
- Fresh price-derived labels and target transition events/alerts require `freshPriceEvidence`. Older complete price history may remain available descriptively, but it cannot masquerade as a new/latest price event.
- `previous` means the immediately previous valid comparable observation relative to `current`, never the first/oldest point by accident.
- Failed observations remain raw history evidence but are not price points.
- Currency, price basis, taxes/fees basis, provenance, or scope mismatch is explicitly non-comparable.
- Stale/failed latest evidence cannot generate a fresh decision alert.

## Recent-range contract

Two windows are returned deliberately:

- `recentRange` is current-inclusive. It uses up to `recentWindow` valid observations comparable with `current`, including `current` itself.
- `priorRecentRange` excludes `current`. It uses up to `recentWindow` immediately prior valid observations comparable with `current`.

The label `below prior recent observed range` is derived only from `priorRecentRange`, never from the current-inclusive range. The label requires at least two prior comparable observations; one prior point is not treated as a meaningful recent range.

This keeps returned evidence and label semantics consistent. For example, prior observations of 100 and 105 followed by current 90 produce `priorRecentRange = 100..105`, current-inclusive `recentRange = 90..105`, and the label `below prior recent observed range`.

## Availability rules

Availability is distinct from provider health and price completeness:

- available;
- unavailable;
- not open;
- unknown/provider failure.

Restock is emitted only for contiguous valid `unavailable -> available` evidence. A provider failure breaks continuity, so the system does not infer sold-out or restock across that gap. Availability may still be current even when the latest observation does not contain complete price evidence.

## Targets, labels, and re-arm

A configured target may preserve descriptive reached/not-reached state from complete comparable price history. A fresh target event/alert occurs only when the actual latest observation carries complete fresh price evidence and produces the corresponding threshold transition. An unchanged satisfied condition does not repeatedly alert. Leaving the threshold re-arms it for a later fresh hit.

Evidence-backed labels may include:

- `new low observed`;
- `below prior recent observed range`;
- `target reached`;
- `meaningful drop`;
- `available again`;
- `no meaningful change`.

Price-derived labels require fresh price evidence. `available again` follows independent availability evidence. These labels are descriptive evidence summaries, not predictions or recommendation-accuracy claims.

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

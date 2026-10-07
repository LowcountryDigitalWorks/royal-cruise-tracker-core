# UX-001 synthetic Decision / History prototype

UX-001 is a public-safe prototype over the accepted FOUNDATION-002 semantic core. It demonstrates the first Decision Card, History, and Changes Since Last Visit surfaces without adding provider traffic, production integration, private data, prediction, transaction behavior, persistence, or paid infrastructure.

## Architecture

The prototype intentionally uses only repository-native source:

- `prototype/index.html` — semantic landmarks and static surface structure;
- `prototype/styles.css` — mobile-first responsive presentation using system fonts only;
- `prototype/app.mjs` — small DOM renderer using native controls and `textContent`;
- `src/ux-fixtures.mjs` — deterministic synthetic fixtures for the accepted state matrix;
- `src/ux-model.mjs` — pure presentation/view-model transformation over `src/decision-history.mjs`;
- `tests/test_ux_model.mjs` — semantic fixture/view-model tests;
- `tests/test_ux_static.mjs` — static accessibility, privacy, dependency, and responsive-contract checks.

There is no framework, package manager, chart library, external font, analytics service, tracker, database, or hosted dependency.

## Decision Card contract

The Decision Card renders only evidence supported by the selected fixture and FOUNDATION-002 outputs:

- synthetic item/category/variant identity;
- actual latest observation health and timestamp;
- source class and price basis;
- current complete price evidence when fresh;
- explicitly historical last-complete price when latest evidence is failed/stale/incomplete;
- previous valid comparable observation;
- absolute and percentage delta only from fresh comparable evidence;
- optional synthetic baseline and target;
- record low;
- current-inclusive recent range and prior-only recent range;
- availability independent from price completeness;
- configured vs unknown policy/deadline state;
- evidence-first next-step wording.

When latest price evidence is incomplete, stale, or failed, older complete price evidence may remain visible as historical context but is not rendered as a new/current price event.

## History contract

The visual history strip is descriptive and marked `aria-hidden="true"`. The adjacent semantic table is the non-visual equivalent and carries:

- observation time;
- raw observed value or `Not observed`;
- current/previous/record-low/health markers;
- availability;
- source class and price basis.

Failed observations are not price observations. Stale and incomplete observations remain visibly labeled. Record-low meaning is expressed in text and not by color alone.

## Changes Since Last Visit contract

The feed is derived from repeated prefix analysis of the same synthetic observation sequence. It prefers semantic events rather than raw log rows, including:

- meaningful drop;
- no meaningful change;
- target first hit;
- target re-arm and later hit;
- available again;
- observation failed;
- observation stale;
- latest price incomplete;
- comparison unavailable.

A steady target condition is not repeated as a new target alert. Provider failure breaks availability continuity and does not imply restock or sold-out state.

## Synthetic state matrix

Matrix case 1 deliberately has two fixtures so both empty and first-observation behavior are directly inspectable. Cases 2 through 20 have one fixture each.

1. `case-01-zero`, `case-01-first` — no observations / first observation
2. `case-02-two` — two comparable observations
3. `case-03-multi` — 3+ observation history
4. `case-04-equal` — equal/no-change values
5. `case-05-drop` — meaningful drop
6. `case-06-rise` — rise after drop
7. `case-07-low` — record low
8. `case-08-target-hit` — target first hit
9. `case-09-target-steady` — target steady reached without repeat alert
10. `case-10-target-rearm` — target re-arm and later hit
11. `case-11-restock` — available -> unavailable -> available
12. `case-12-failure-gap` — provider failure breaks availability continuity
13. `case-13-stale` — stale latest
14. `case-14-failed` — failed latest
15. `case-15-incomplete` — latest valid but incomplete price evidence
16. `case-16-noncomparable` — currency/basis/source/scope mismatch
17. `case-17-policy-unknown` — policy/deadline unknown
18. `case-18-policy-configured` — policy/deadline configured descriptively
19. `case-19-range-insufficient` — insufficient prior history for range claim
20. `case-20-below-range` — below prior recent observed range

Every fixture uses obvious `DEMO` / `synthetic` identifiers and values.

## Accessibility and responsive design

The static structure provides:

- skip navigation;
- semantic header/main/footer/section/article/aside hierarchy;
- native scenario `<select>`;
- native `<details>/<summary>` for comparison evidence;
- visible `:focus-visible` treatment;
- touch-sized select and summary controls;
- state text in addition to color;
- an accessible history table equivalent to the visual strip;
- no live region, avoiding noisy announcements when the QA scenario changes;
- no animation or motion dependency;
- mobile-first one-column layout;
- intermediate layout at 560/760px;
- two-column desktop layout at 1050px;
- local horizontal scrolling only for the detailed history table, with the scroll region labeled and keyboard focusable;
- unrestricted text wrapping for long synthetic names and labels.

The primary Decision Card itself does not require horizontal scrolling.

## Local deterministic validation

From repository root:

```bash
node --check src/ux-fixtures.mjs
node --check src/ux-model.mjs
node --check prototype/app.mjs
node --test tests/test_ux_model.mjs tests/test_ux_static.mjs
```

The existing Public Core CI additionally runs the accepted Python suite, decision/history semantic tests, public-boundary scans, Cloudflare JavaScript syntax, pinned actionlint, pinned Betterleaks, and whitespace checks.

## Browser review path

No browser package is required. Where an execution environment already provides a browser, serve the repository locally and inspect representative URLs such as:

```text
/prototype/?scenario=case-05-drop
/prototype/?scenario=case-13-stale
/prototype/?scenario=case-15-incomplete
/prototype/?scenario=case-16-noncomparable
/prototype/?scenario=case-20-below-range
```

Review at approximately 320–375px, 768px, and desktop widths. Browser/rendering evidence is an execution-time QA result and should not be inferred merely from static tests.

## Evidence language

The prototype uses descriptive wording such as:

- `New low observed`;
- `Below prior recent observed range`;
- `Target reached`;
- `Meaningful drop`;
- `Available again`;
- `No meaningful change`;
- `Observation stale`;
- `Observation failed`;
- `Lower observed price — verify eligibility`;
- `Comparison unavailable`.

Historical lows/ranges remain evidence, not forecasts. The prototype does not establish booking eligibility or automate any transaction.

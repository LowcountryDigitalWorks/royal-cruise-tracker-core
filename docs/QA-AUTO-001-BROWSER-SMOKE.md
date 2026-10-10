# QA-AUTO-001 — dependency-free browser smoke harness

QA-AUTO-001 adds repeatable rendered-browser smoke evidence for the public synthetic Royal Cruise Tracker UX without adding an npm/browser-testing dependency or a paid browser service.

## Boundary

The harness is a QA guardrail, not a WCAG conformance claim and not a replacement for independent human usability/accessibility review.

It uses only public synthetic repository content and must not:

- call Royal or another provider;
- read production credentials or private D1 data;
- mutate Cloudflare, scheduler, repository settings, or production state;
- retain screenshots or browser artifacts by default;
- use a user's normal Chrome profile;
- silently skip when Chrome or CDP is unavailable.

Incremental cash target is `$0`.

## Runtime design

`scripts/browser-smoke.mjs` is a single-process Node harness using only built-in modules plus Node's global `fetch` and `WebSocket`.

It:

1. binds a small HTTP server to `127.0.0.1` on an ephemeral port;
2. serves only `/prototype/*` and `/src/*` from the exact checkout;
3. creates a temporary Chrome user-data directory;
4. launches the runner's preinstalled Chrome in modern headless mode with an ephemeral localhost DevTools port;
5. discovers the browser endpoint from Chrome's `DevToolsActivePort` file;
6. drives Chrome directly through CDP;
7. performs rendered assertions;
8. closes the page/browser/server and removes temporary profile state in cleanup.

The harness intentionally does not pass `--no-sandbox`. If a future runner image proves that flag necessary, that should be an explicit reviewed change rather than a default weakening.

## Rendered evidence

The smoke matrix covers 12 representative synthetic states at four widths: 320, 375, 768, and 1440 CSS pixels.

The states include:

- no observations;
- multi-observation history;
- meaningful drop;
- target re-arm/re-hit;
- unavailable;
- not open;
- provider failure gap;
- stale latest evidence;
- failed latest evidence;
- incomplete latest price;
- non-comparable evidence;
- below-prior-range evidence.

For each rendered case the harness requires:

- no root/page horizontal overflow;
- Decision Card containment;
- item-name containment;
- required state/evidence text;
- a readable non-fragmented availability badge;
- local history-table overflow at narrow widths rather than page overflow;
- no uncaught runtime JavaScript error.

A separate synthetic long-content stress mutates only the already-loaded local DOM and verifies long item/evidence wording remains contained without making the page wider.

## Keyboard and accessibility smoke

Chrome `Input.dispatchKeyEvent` is used for the native keyboard path. The harness verifies:

- first Tab reaches the skip link;
- next Tab reaches the scenario select;
- ArrowDown changes the native select value and therefore the rendered scenario;
- next Tab reaches the comparison `summary`;
- Space opens its native `details` element;
- focus can move onward without a trap;
- keyboard-focused controls expose a visible outline under the accepted CSS contract.

The CDP Accessibility domain is used only for bounded structural checks: main landmark, named scenario combobox, semantic history table, and named comparison disclosure control.

This does not emulate a screen reader.

## Scale smoke

`Emulation.setPageScaleFactor` runs a bounded approximately-200% browser-scale smoke. It checks that required evidence remains rendered and root overflow is not introduced.

This is deliberately documented as an automation approximation. Actual interactive Chrome browser zoom/text scaling may behave differently and remains a higher-level human review step when a release requires it.

## Network/privacy guard

The page target's CDP Network events are recorded. HTTP(S) requests outside the harness's ephemeral localhost origin are a hard failure.

The workflow uses `contents: read`, persists no checkout credentials, receives no production secrets, and never uses `pull_request_target`.

## CI integration

`.github/workflows/browser-smoke.yml` is a dedicated browser-smoke workflow for relevant pull-request paths and manual dispatch. It uses the existing `ubuntu-24.04` runner and preinstalled Google Chrome. It installs no npm/browser package and uploads no artifacts.

The workflow is intentionally not made a required branch/ruleset check by repository mutation in QA-AUTO-001. Product Orchestrator may consider that only after the harness demonstrates stable, low-noise behavior.

## Failure contract

The smoke run fails when:

- Chrome is unavailable or exits before DevTools is ready;
- the localhost page does not render deterministically;
- a CDP command/assertion cannot be executed;
- runtime JavaScript throws;
- required evidence text is absent;
- root/card/long-content containment fails;
- keyboard focus/select/details behavior fails;
- required accessibility-tree structure is absent;
- the page makes an external request.

A browser launch or runtime failure is never converted to PASS or SKIPPED.

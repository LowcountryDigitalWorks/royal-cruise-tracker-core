import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const html = await readFile(new URL("../prototype/index.html", import.meta.url), "utf8");
const css = await readFile(new URL("../prototype/styles.css", import.meta.url), "utf8");
const app = await readFile(new URL("../prototype/app.mjs", import.meta.url), "utf8");

const all = `${html}\n${css}\n${app}`;

test("prototype uses semantic landmarks, headings, native scenario control, and skip navigation", () => {
  for (const token of ["<header", "<main", "<footer", "<section", "<article", "<aside", "<h1", "<h2", "<select", "Skip to decision evidence"]) {
    assert.ok(html.includes(token), token);
  }
});

test("history has both a visual surface and accessible tabular equivalent", () => {
  assert.match(html, /id="history-visual"[^>]*aria-hidden="true"/);
  assert.ok(html.includes("<table>"));
  assert.ok(html.includes("<caption>"));
  assert.ok(html.includes("Synthetic observation history"));
});

test("dynamic updates avoid noisy live regions", () => {
  assert.equal(/aria-live\s*=/.test(html), false);
  assert.equal(/role\s*=\s*["']alert["']/.test(html), false);
});

test("prototype has visible focus styling and touch-sized native controls", () => {
  assert.ok(css.includes(":focus-visible"));
  assert.match(css, /select\s*\{[\s\S]*?min-height:\s*48px/);
  assert.match(css, /comparison-details summary[\s\S]*?min-height:\s*44px/);
});

test("prototype is mobile-first and has intermediate and desktop breakpoints", () => {
  assert.ok(css.includes("@media (min-width: 560px)"));
  assert.ok(css.includes("@media (min-width: 760px)"));
  assert.ok(css.includes("@media (min-width: 1050px)"));
  assert.ok(css.includes("overflow-wrap: anywhere"));
  assert.ok(css.includes("minmax(0, 1fr)"));
});

test("primary page does not force horizontal scrolling while table overflow is local and labeled", () => {
  assert.match(css, /\.table-wrap\s*\{[\s\S]*?overflow-x:\s*auto/);
  assert.match(html, /class="table-wrap" tabindex="0" aria-label="Scrollable synthetic history table region"/);
  const bodyBlock = css.match(/body\s*\{([\s\S]*?)\}/)?.[1] ?? "";
  assert.equal(/overflow-x:\s*auto/.test(bodyBlock), false);
});

test("prototype loads no remote scripts, styles, fonts, analytics, or trackers", () => {
  assert.equal(/https?:\/\//i.test(all), false);
  assert.equal(/google-analytics|gtag|segment|mixpanel|plausible|hotjar/i.test(all), false);
  assert.equal(/@font-face/i.test(css), false);
});

test("rendering uses textContent rather than synthetic fixture HTML injection", () => {
  assert.ok(app.includes("textContent"));
  assert.equal(app.includes("innerHTML"), false);
});

test("prototype page contains explicit non-prediction and non-transaction trust wording", () => {
  assert.match(html, /Historical lows and ranges are evidence, not forecasts/);
  assert.match(html, /performs no purchase, rebooking, cancellation, refund, payment, check-in, notification, or provider action/);
});

test("prohibited decision claims are absent from static prototype source", () => {
  const banned = ["buy now", "best possible price", "guaranteed savings", "rebook now", "you can reprice", "predicted next drop"];
  const lower = all.toLowerCase();
  for (const phrase of banned) assert.equal(lower.includes(phrase), false, phrase);
});

function relativeLuminance(hex) {
  const rgb = hex.match(/[0-9a-f]{2}/gi).map((part) => parseInt(part, 16) / 255);
  const linear = rgb.map((channel) => channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4);
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(foreground, background) {
  const a = relativeLuminance(foreground);
  const b = relativeLuminance(background);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

test("normal text palette pairs meet 4.5:1 and focus indicator meets 3:1 contrast", () => {
  const textPairs = [
    ["102a3a", "ffffff"],
    ["536672", "ffffff"],
    ["225c57", "ffffff"],
    ["1e5a48", "e7f4ef"],
    ["6b4a00", "fff3d7"],
    ["792929", "fbe8e8"],
    ["344650", "edf1f3"],
    ["ffffff", "102a3a"],
  ];
  for (const [foreground, background] of textPairs) {
    assert.ok(contrastRatio(foreground, background) >= 4.5, `${foreground} on ${background}`);
  }
  assert.ok(contrastRatio("8b5cf6", "ffffff") >= 3, "focus indicator on white");
});

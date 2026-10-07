import { UX_FIXTURES, getFixture } from "../src/ux-fixtures.mjs";
import { buildPrototypeView } from "../src/ux-model.mjs";

const select = document.querySelector("#scenario-select");

function setText(selector, value) {
  const node = document.querySelector(selector);
  if (node) node.textContent = value ?? "";
}

function setBadge(selector, text, tone = "neutral") {
  const node = document.querySelector(selector);
  if (!node) return;
  node.textContent = text;
  node.dataset.tone = tone;
}

function createChip(text, tone = "neutral") {
  const span = document.createElement("span");
  span.className = "chip";
  span.dataset.tone = tone;
  span.textContent = text;
  return span;
}

function renderReference(containerSelector, reference, emptyText) {
  const container = document.querySelector(containerSelector);
  container.replaceChildren();
  const value = document.createElement("p");
  value.className = "reference-value";
  const detail = document.createElement("p");
  detail.className = "reference-detail";
  if (!reference) {
    value.textContent = "Not configured";
    detail.textContent = emptyText;
  } else {
    value.textContent = reference.value;
    detail.textContent = reference.difference ?? reference.status;
  }
  container.append(value, detail);
}

function renderEvidence(view) {
  const container = document.querySelector("#evidence-labels");
  container.replaceChildren();
  if (!view.evidenceLabels.length) {
    container.append(createChip("No fresh evidence label", "neutral"));
    return;
  }
  for (const label of view.evidenceLabels) {
    let tone = "neutral";
    if (["Observation failed"].includes(label)) tone = "danger";
    else if (["Observation stale", "Comparison unavailable"].includes(label)) tone = "warning";
    else if (["New low observed", "Below prior recent observed range", "Target reached", "Meaningful drop", "Available again"].includes(label)) tone = "positive";
    container.append(createChip(label, tone));
  }
}

function renderComparison(view) {
  setText("#comparison-status", view.comparison.text);
  const list = document.querySelector("#comparison-reasons");
  list.replaceChildren();
  for (const reason of view.comparison.reasons) {
    const item = document.createElement("li");
    item.textContent = reason;
    list.append(item);
  }
}

function renderHistory(view) {
  setText("#history-count", `${view.historyRows.length} raw observation${view.historyRows.length === 1 ? "" : "s"}`);
  const visual = document.querySelector("#history-visual");
  const tableBody = document.querySelector("#history-table-body");
  visual.replaceChildren();
  tableBody.replaceChildren();

  if (!view.historyRows.length) {
    const empty = document.createElement("p");
    empty.className = "empty-change";
    empty.textContent = "No observations yet — no chart or price direction is fabricated.";
    visual.append(empty);

    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.textContent = "No synthetic observations yet.";
    row.append(cell);
    tableBody.append(row);
    return;
  }

  const values = view.historyRows.map((row) => row.numericValue).filter((value) => typeof value === "number");
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 0;
  const span = Math.max(max - min, 1);

  for (const row of view.historyRows) {
    const item = document.createElement("div");
    item.className = "history-bar-item";
    const track = document.createElement("div");
    track.className = "history-bar-track";
    const bar = document.createElement("div");
    bar.className = "history-bar";
    bar.dataset.tone = row.tone;
    const height = typeof row.numericValue === "number" ? 24 + ((row.numericValue - min) / span) * 72 : 2;
    bar.style.height = `${height}%`;
    track.append(bar);
    const valueLabel = document.createElement("div");
    valueLabel.className = "history-bar-label";
    valueLabel.textContent = row.rawValue;
    const stateLabel = document.createElement("div");
    stateLabel.className = "history-bar-state";
    stateLabel.textContent = row.markers[0] ?? row.availability;
    item.append(track, valueLabel, stateLabel);
    visual.append(item);

    const tr = document.createElement("tr");
    const cells = [
      row.observedAt,
      row.rawValue,
      row.markers.length ? row.markers.join(" · ") : row.complete ? "Valid price observation" : "Incomplete price evidence",
      row.availability,
      `${row.sourceClass} · ${row.priceBasis}`,
    ];
    cells.forEach((text, index) => {
      const td = document.createElement("td");
      td.textContent = text;
      if (index === 2) {
        td.className = "table-state";
        td.dataset.tone = row.tone;
      }
      tr.append(td);
    });
    tableBody.append(tr);
  }
}

function renderChanges(view) {
  setText("#changes-note", view.changes.note);
  const list = document.querySelector("#changes-list");
  list.replaceChildren();
  if (!view.changes.entries.length) {
    const li = document.createElement("li");
    li.className = "empty-change";
    li.textContent = "No meaningful change entry for this scenario.";
    list.append(li);
    return;
  }
  for (const entry of view.changes.entries) {
    const li = document.createElement("li");
    li.className = "change-entry";
    li.dataset.tone = entry.tone;
    const marker = document.createElement("span");
    marker.className = "change-marker";
    marker.setAttribute("aria-hidden", "true");
    const body = document.createElement("div");
    const title = document.createElement("p");
    title.className = "change-title";
    title.textContent = entry.title;
    const detail = document.createElement("p");
    detail.className = "change-detail";
    detail.textContent = entry.detail;
    const time = document.createElement("p");
    time.className = "change-time";
    time.textContent = entry.at;
    body.append(title, detail, time);
    li.append(marker, body);
    list.append(li);
  }
}

function renderScenario(fixture) {
  const view = buildPrototypeView(fixture);
  setText("#matrix-case", `Matrix case ${view.matrixCase}`);
  setText("#scenario-description", view.scenarioDescription);
  setText("#item-category", view.item.category);
  setText("#item-name", view.item.name);
  setText("#item-variant", view.item.variant);
  setBadge("#health-badge", view.health.text, view.health.tone);
  setBadge("#availability-badge", view.availability.text, view.availability.tone);
  setText("#current-value", view.value.value);
  setText("#current-qualifier", view.value.qualifier);
  setText("#observed-at", view.observedAt);
  setText("#provenance", view.provenance);
  setText("#basis", view.basis);
  setText("#previous-value", view.previous);
  setText("#delta-value", view.delta);
  setText("#percent-delta", view.percentDelta);
  setText("#record-low", view.recordLow);
  setText("#recent-range", view.recentRange);
  setText("#prior-recent-range", view.priorRecentRange);
  renderReference("#baseline-content", view.baseline, "No synthetic purchased/baseline reference is configured for this case.");
  renderReference("#target-content", view.target, "No synthetic target is configured for this case.");
  renderEvidence(view);
  renderComparison(view);
  setText("#policy-heading", view.policy.title);
  setText("#policy-detail", view.policy.detail);
  setText("#next-step-text", view.nextStep);
  renderHistory(view);
  renderChanges(view);
}

for (const fixture of UX_FIXTURES) {
  const option = document.createElement("option");
  option.value = fixture.id;
  option.textContent = `Case ${fixture.matrixCase}: ${fixture.name}`;
  select.append(option);
}

const requestedScenario = new URLSearchParams(window.location.search).get("scenario");
const initialScenario = UX_FIXTURES.some((entry) => entry.id === requestedScenario) ? requestedScenario : "case-05-drop";
select.value = initialScenario;
select.addEventListener("change", () => renderScenario(getFixture(select.value)));
renderScenario(getFixture(select.value));

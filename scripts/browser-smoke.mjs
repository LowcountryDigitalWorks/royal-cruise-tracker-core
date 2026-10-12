import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT_DIR = path.resolve(__dirname, "..");

const REQUIRED_STATES = [
  { id: "case-01-zero", name: "empty", expectedText: "No observations yet" },
  { id: "case-03-multi", name: "3+ history", expectedText: "Observed evidence over time" },
  { id: "case-05-drop", name: "meaningful drop", expectedText: "Meaningful drop" },
  { id: "case-10-target-rearm", name: "target re-arm", expectedText: "Target re-arm and later hit" },
  { id: "case-11-unavailable", name: "unavailable", expectedText: "Current availability is unavailable" },
  { id: "case-11-not-open", name: "not-open", expectedText: "Current availability is not open" },
  { id: "case-12-failure-gap", name: "provider failure", expectedText: "Provider failure breaks restock continuity" },
  { id: "case-13-stale", name: "stale", expectedText: "Observation stale" },
  { id: "case-14-failed", name: "failed", expectedText: "Observation failed" },
  { id: "case-15-incomplete", name: "incomplete latest price", expectedText: "Incomplete price evidence" },
  { id: "case-16-noncomparable", name: "non-comparable", expectedText: "Comparison unavailable" },
  { id: "case-20-below-range", name: "below-prior-range", expectedText: "Below prior recent observed range" },
];

const REQUIRED_VIEWPORTS = [
  { name: "320px", width: 320, height: 640 },
  { name: "375px", width: 375, height: 812 },
  { name: "768px", width: 768, height: 1024 },
  { name: "1280px (desktop)", width: 1280, height: 800 },
];

function mimeType(filepath) {
  if (filepath.endsWith(".html")) return "text/html; charset=utf-8";
  if (filepath.endsWith(".mjs") || filepath.endsWith(".js")) return "application/javascript; charset=utf-8";
  if (filepath.endsWith(".css")) return "text/css; charset=utf-8";
  if (filepath.endsWith(".json")) return "application/json; charset=utf-8";
  return "application/octet-stream";
}

function startLocalServer() {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => {
      const urlPath = new URL(req.url, "http://127.0.0.1").pathname;
      let relativePath = urlPath;
      if (relativePath === "/") relativePath = "/prototype/index.html";

      const safePath = path.normalize(relativePath).replace(/^(\.\.[\/\\])+/, "");
      const fullPath = path.join(ROOT_DIR, safePath);

      if (!fullPath.startsWith(ROOT_DIR)) {
        res.writeHead(403);
        res.end("Forbidden");
        return;
      }

      fs.readFile(fullPath, (err, data) => {
        if (err) {
          res.writeHead(404);
          res.end("Not Found");
          return;
        }
        res.writeHead(200, { "Content-Type": mimeType(fullPath) });
        res.end(data);
      });
    });

    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      resolve({ server, port, baseUrl: `http://127.0.0.1:${port}/prototype/index.html` });
    });
    server.on("error", reject);
  });
}

function findChromeBinary() {
  if (process.env.CHROME_BIN && fs.existsSync(process.env.CHROME_BIN)) {
    return process.env.CHROME_BIN;
  }
  const candidates = [
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
  ];
  for (const candidate of candidates) {
    try {
      const res = spawnSync(candidate, ["--version"]);
      if (res.status === 0) return candidate;
    } catch {
      // continue
    }
  }
  throw new Error("No Google Chrome or Chromium executable found in system PATH.");
}

async function launchChrome(cdpPort) {
  const chromeBin = findChromeBinary();
  const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "chrome-smoke-"));

  const chromeProc = spawn(chromeBin, [
    "--headless=new",
    `--remote-debugging-port=${cdpPort}`,
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    `--user-data-dir=${tmpDir}`,
  ], { stdio: ["ignore", "ignore", "pipe"] });

  let errLogs = "";
  chromeProc.stderr.on("data", (chunk) => { errLogs += chunk.toString(); });

  let connected = false;
  for (let i = 0; i < 30; i++) {
    await new Promise((r) => setTimeout(r, 200));
    try {
      const res = await fetch(`http://127.0.0.1:${cdpPort}/json/version`);
      if (res.ok) {
        connected = true;
        break;
      }
    } catch {
      // retry
    }
  }

  if (!connected) {
    chromeProc.kill("SIGKILL");
    fs.rmSync(tmpDir, { recursive: true, force: true });
    throw new Error(`Failed to launch Chrome on port ${cdpPort}.\nLogs: ${errLogs}`);
  }

  return { chromeProc, tmpDir };
}

class CDPClient {
  constructor(wsUrl) {
    this.ws = new WebSocket(wsUrl);
    this.msgId = 1;
    this.callbacks = new Map();
    this.events = new Map();
    this.uncaughtErrors = [];

    this.ws.onmessage = (evt) => {
      const msg = JSON.parse(evt.data);
      if (msg.id && this.callbacks.has(msg.id)) {
        const { resolve, reject } = this.callbacks.get(msg.id);
        this.callbacks.delete(msg.id);
        if (msg.error) reject(msg.error);
        else resolve(msg.result);
      } else if (msg.method) {
        if (msg.method === "Runtime.exceptionThrown") {
          const desc = msg.params.exceptionDetails?.exception?.description || msg.params.exceptionDetails?.text;
          this.uncaughtErrors.push(desc);
        }
        const listeners = this.events.get(msg.method) || [];
        for (const cb of listeners) cb(msg.params);
      }
    };
  }

  async connect() {
    await new Promise((resolve, reject) => {
      this.ws.onopen = resolve;
      this.ws.onerror = reject;
    });
  }

  send(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = this.msgId++;
      this.callbacks.set(id, { resolve, reject });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  on(method, cb) {
    if (!this.events.has(method)) this.events.set(method, []);
    this.events.get(method).push(cb);
  }

  async eval(expression) {
    const res = await this.send("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (res.exceptionDetails) {
      throw new Error(`Eval exception: ${JSON.stringify(res.exceptionDetails)}`);
    }
    return res.result.value;
  }

  close() {
    try { this.ws.close(); } catch {}
  }
}

async function runSmokeHarness() {
  console.log("Starting QA-AUTO-001 dependency-free CDP browser smoke harness...");

  const { server, port, baseUrl } = await startLocalServer();
  console.log(`Local server listening at ${baseUrl}`);

  const cdpPort = 9222 + Math.floor(Math.random() * 500);
  let chromeInstance = null;

  try {
    chromeInstance = await launchChrome(cdpPort);
    console.log(`Chrome launched with CDP remote debugging port ${cdpPort}`);

    const targetRes = await fetch(`http://127.0.0.1:${cdpPort}/json/new?about:blank`, { method: "PUT" });
    const targetData = await targetRes.json();
    const cdp = new CDPClient(targetData.webSocketDebuggerUrl);
    await cdp.connect();

    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("DOM.enable");
    await cdp.send("Accessibility.enable");

    // 1. Responsive & State Matrix Tests
    console.log("\n--- Executing Responsive & State Matrix Tests ---");
    for (const state of REQUIRED_STATES) {
      const scenarioUrl = `${baseUrl}?scenario=${state.id}`;
      for (const vp of REQUIRED_VIEWPORTS) {
        await cdp.send("Emulation.setDeviceMetricsOverride", {
          width: vp.width,
          height: vp.height,
          deviceScaleFactor: 1,
          mobile: vp.width < 768,
        });

        await cdp.send("Page.navigate", { url: scenarioUrl });
        await new Promise((r) => setTimeout(r, 250));

        if (cdp.uncaughtErrors.length > 0) {
          throw new Error(`Uncaught JS exception on ${scenarioUrl}: ${cdp.uncaughtErrors.join("\n")}`);
        }

        const layoutCheck = await cdp.eval(`(() => {
          const docEl = document.documentElement;
          const body = document.body;
          const decisionCard = document.querySelector('.decision-card');
          const decisionSection = document.querySelector('.decision-section');
          const tableWrap = document.querySelector('.table-wrap');
          const pageText = document.body.innerText;

          return {
            pageScrollWidth: docEl.scrollWidth,
            windowInnerWidth: window.innerWidth,
            cardWidth: decisionCard ? decisionCard.getBoundingClientRect().width : 0,
            sectionWidth: decisionSection ? decisionSection.getBoundingClientRect().width : 0,
            tableWrapScrollable: tableWrap ? tableWrap.scrollWidth > tableWrap.clientWidth : false,
            hasExpectedText: pageText.includes(${JSON.stringify(state.expectedText)}),
            itemName: document.querySelector('#item-name')?.textContent || '',
          };
        })()`);

        if (!layoutCheck.hasExpectedText) {
          throw new Error(`State [${state.id}] (${state.name}) missing required text "${state.expectedText}" at viewport ${vp.name}`);
        }

        if (layoutCheck.pageScrollWidth > layoutCheck.windowInnerWidth) {
          throw new Error(`State [${state.id}] created page horizontal overflow at viewport ${vp.name}: scrollWidth (${layoutCheck.pageScrollWidth}) > innerWidth (${layoutCheck.windowInnerWidth})`);
        }

        if (layoutCheck.cardWidth > layoutCheck.sectionWidth + 1) {
          throw new Error(`Decision Card overflowed its section at viewport ${vp.name} in state [${state.id}]`);
        }
      }
      console.log(`  ✓ State [${state.id}] (${state.name}) passed layout & content assertions across all viewports`);
    }

    // 2. Keyboard / Focus Navigation Tests
    console.log("\n--- Executing Keyboard / Focus Navigation Tests ---");
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
    await cdp.send("Page.navigate", { url: `${baseUrl}?scenario=case-05-drop` });
    await new Promise((r) => setTimeout(r, 250));

    // Tab 1: Skip link
    await cdp.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
    await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });

    const skipFocused = await cdp.eval(`(() => {
      const active = document.activeElement;
      if (!active || !active.classList.contains('skip-link')) return { ok: false, reason: 'Active element is not skip link' };
      const style = getComputedStyle(active);
      const visible = style.outlineStyle !== 'none' || style.boxShadow !== 'none';
      return { ok: visible, activeTag: active.tagName, activeClass: active.className };
    })()`);

    if (!skipFocused.ok) {
      throw new Error(`Skip link keyboard focus check failed: ${JSON.stringify(skipFocused)}`);
    }
    console.log("  ✓ Skip link receives keyboard focus and displays focus indicator");

    // Tab 2: Scenario select
    await cdp.send("Input.dispatchKeyEvent", { type: "keyDown", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });
    await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: "Tab", code: "Tab", windowsVirtualKeyCode: 9 });

    const selectFocused = await cdp.eval(`(() => {
      const active = document.activeElement;
      if (!active || active.id !== 'scenario-select') return { ok: false, activeId: active?.id };
      const style = getComputedStyle(active);
      const visible = style.outlineStyle !== 'none' || style.boxShadow !== 'none';
      return { ok: visible, value: active.value };
    })()`);

    if (!selectFocused.ok) {
      throw new Error(`Scenario select focus check failed: ${JSON.stringify(selectFocused)}`);
    }
    console.log("  ✓ Scenario <select> receives focus and displays focus indicator");

    // Change scenario via CDP value set & dispatch event
    await cdp.eval(`(() => {
      const sel = document.querySelector('#scenario-select');
      sel.value = 'case-07-low';
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    })()`);
    await new Promise((r) => setTimeout(r, 100));
    const scenarioChangedText = await cdp.eval(`document.body.innerText`);
    if (!scenarioChangedText.includes("New record low")) {
      throw new Error("Scenario <select> change failed to render new record low state");
    }
    console.log("  ✓ Scenario <select> can change scenario and re-render evidence");

    // Tab into details summary & toggle
    await cdp.eval(`document.querySelector('.comparison-details summary').focus()`);
    const summaryFocused = await cdp.eval(`(() => {
      const active = document.activeElement;
      if (!active || active.tagName !== 'SUMMARY') return false;
      const details = active.closest('details');
      return { focused: true, openBefore: details.open };
    })()`);

    if (!summaryFocused.focused) {
      throw new Error("<summary> element failed to receive focus");
    }

    // Toggle via Space key (Standard browser keyboard interaction for <summary>)
    await cdp.send("Input.dispatchKeyEvent", { type: "keyDown", key: " ", code: "Space", windowsVirtualKeyCode: 32, text: " " });
    await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", key: " ", code: "Space", windowsVirtualKeyCode: 32 });
    await new Promise((r) => setTimeout(r, 100));

    const detailsToggled = await cdp.eval(`document.querySelector('.comparison-details').open`);
    if (detailsToggled === summaryFocused.openBefore) {
      throw new Error("<details>/<summary> failed to toggle open state on keyboard Space");
    }
    console.log("  ✓ <details>/<summary> receives focus and toggles open state on keypress");

    // 3. Zoom / Text Scale Tests
    console.log("\n--- Executing Zoom / Text Scale (~200%) Tests ---");
    await cdp.send("Page.navigate", { url: `${baseUrl}?scenario=case-05-drop` });
    await new Promise((r) => setTimeout(r, 250));

    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width: 1280,
      height: 800,
      deviceScaleFactor: 2,
      mobile: false,
    });
    await cdp.eval(`document.body.style.zoom = "200%"`);
    await new Promise((r) => setTimeout(r, 200));

    const zoomCheck = await cdp.eval(`(() => {
      const docEl = document.documentElement;
      const card = document.querySelector('.decision-card');
      const text = card ? card.innerText : '';
      return {
        hasPrimaryEvidence: text.includes('CURRENT EVIDENCE') && text.includes('Meaningful drop'),
        pageScrollWidth: docEl.scrollWidth,
        windowInnerWidth: window.innerWidth,
      };
    })()`);

    if (!zoomCheck.hasPrimaryEvidence) {
      throw new Error("Primary evidence text disappeared under 200% zoom scaling");
    }
    if (zoomCheck.pageScrollWidth > zoomCheck.windowInnerWidth) {
      throw new Error(`Horizontal page overflow introduced under 200% zoom: ${zoomCheck.pageScrollWidth} > ${zoomCheck.windowInnerWidth}`);
    }
    console.log("  ✓ Primary evidence remains readable under ~200% zoom");

    // Reset zoom and scale
    await cdp.eval(`document.body.style.zoom = "100%"`);
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });

    // 4. Accessibility-Tree Sanity Checks
    console.log("\n--- Executing Accessibility-Tree Sanity Checks ---");
    const axTree = await cdp.send("Accessibility.getFullAXTree");
    const nodes = axTree.nodes;

    const hasMainLandmark = nodes.some((n) => n.role?.value === "main" || n.role?.value === "Main");
    const hasCombobox = nodes.some((n) => n.role?.value === "combobox" || n.role?.value === "select" || n.role?.value === "PopUpButton");
    const hasTable = nodes.some((n) => n.role?.value === "table" || n.role?.value === "Table");

    if (!hasMainLandmark) throw new Error("AX Tree sanity check failed: <main> landmark missing");
    if (!hasCombobox) throw new Error("AX Tree sanity check failed: native scenario select combobox missing");
    if (!hasTable) throw new Error("AX Tree sanity check failed: history table missing");

    const visualHistoryHidden = await cdp.eval(`(() => {
      const visual = document.querySelector('#history-visual');
      return visual && visual.getAttribute('aria-hidden') === 'true';
    })()`);

    if (!visualHistoryHidden) {
      throw new Error("AX Tree sanity check failed: #history-visual is not aria-hidden='true'");
    }

    console.log("  ✓ <main> landmark exists in accessible tree");
    console.log("  ✓ Native scenario <select> exists as combobox in accessible tree");
    console.log("  ✓ History <table> exposes table semantics in accessible tree");
    console.log("  ✓ Decorative visual history strip is aria-hidden='true'");

    console.log("\n✅ All CDP browser smoke harness assertions PASSED successfully.");
  } finally {
    if (chromeInstance) {
      try {
        chromeInstance.chromeProc.kill("SIGKILL");
        setTimeout(() => {
          fs.rmSync(chromeInstance.tmpDir, { recursive: true, force: true });
        }, 300);
      } catch {}
    }
    server.close();
  }
}

runSmokeHarness().catch((err) => {
  console.error("\n❌ CDP Browser Smoke Harness FAILED:");
  console.error(err);
  process.exit(1);
});

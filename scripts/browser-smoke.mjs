#!/usr/bin/env node
import { spawn, spawnSync } from "node:child_process";
import { createServer } from "node:http";
import { access, mkdtemp, readFile, rm, stat } from "node:fs/promises";
import { constants as fsConstants } from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const RUNNER_TEMP = process.env.RUNNER_TEMP || os.tmpdir();
const REQUEST_TIMEOUT_MS = 12_000;
const START_TIMEOUT_MS = 12_000;
const PAGE_TIMEOUT_MS = 8_000;
const VIEWPORT_HEIGHT = 900;
const VIEWPORTS = [320, 375, 768, 1440];
const SCENARIOS = [
  ["case-01-zero", "No observations yet"],
  ["case-03-multi", "raw observations"],
  ["case-05-drop", "Meaningful drop"],
  ["case-10-target-rearm", "Target reached"],
  ["case-11-unavailable", "Unavailable"],
  ["case-11-not-open", "Not open"],
  ["case-12-failure-gap", "Observation failed"],
  ["case-13-stale", "Observation stale"],
  ["case-14-failed", "Observation failed"],
  ["case-15-incomplete", "Incomplete"],
  ["case-16-noncomparable", "Comparison unavailable"],
  ["case-20-below-range", "Below prior recent observed range"],
];

const CONTENT_TYPES = new Map([
  [".html", "text/html; charset=utf-8"],
  [".css", "text/css; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".mjs", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
]);

function fail(message, detail = undefined) {
  const error = new Error(message);
  if (detail !== undefined) error.detail = detail;
  throw error;
}

function assert(condition, message, detail = undefined) {
  if (!condition) fail(message, detail);
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitForProcessExit(processHandle, timeoutMs) {
  if (!processHandle || processHandle.exitCode !== null) return true;
  await Promise.race([
    new Promise((resolve) => processHandle.once("exit", resolve)),
    sleep(timeoutMs),
  ]);
  return processHandle.exitCode !== null;
}

async function withTimeout(promise, timeoutMs, label) {
  let timer;
  try {
    return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(`${label} timed out after ${timeoutMs}ms`)), timeoutMs);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

async function executable(pathname) {
  try {
    await access(pathname, fsConstants.X_OK);
    return true;
  } catch {
    return false;
  }
}

async function findChrome() {
  const explicit = String(process.env.CHROME_BIN || "").trim();
  const candidates = [explicit, "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"].filter(Boolean);
  for (const candidate of candidates) {
    if (candidate.includes(path.sep)) {
      if (await executable(candidate)) return candidate;
      continue;
    }
    const result = spawnSync("which", [candidate], { encoding: "utf8" });
    if (result.status === 0) {
      const resolved = result.stdout.trim();
      if (resolved && await executable(resolved)) return resolved;
    }
  }
  fail("Chrome executable not found; browser smoke cannot be downgraded to PASS");
}

function safeRepositoryPath(requestUrl) {
  const parsed = new URL(requestUrl, "http://127.0.0.1");
  let pathname;
  try {
    pathname = decodeURIComponent(parsed.pathname);
  } catch {
    return null;
  }
  if (pathname === "/prototype" || pathname === "/prototype/") pathname = "/prototype/index.html";
  if (!(pathname.startsWith("/prototype/") || pathname.startsWith("/src/"))) return null;
  const resolved = path.resolve(ROOT, `.${pathname}`);
  if (!(resolved === ROOT || resolved.startsWith(`${ROOT}${path.sep}`))) return null;
  return resolved;
}

async function startStaticServer() {
  const server = createServer(async (request, response) => {
    if (!request.url || !["GET", "HEAD"].includes(request.method || "")) {
      response.writeHead(405, { "Content-Type": "text/plain; charset=utf-8" });
      response.end("Method not allowed");
      return;
    }

    const filePath = safeRepositoryPath(request.url);
    if (!filePath) {
      response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      response.end("Not found");
      return;
    }

    try {
      const metadata = await stat(filePath);
      if (!metadata.isFile()) throw new Error("not a file");
      const data = await readFile(filePath);
      const contentType = CONTENT_TYPES.get(path.extname(filePath).toLowerCase()) || "application/octet-stream";
      response.writeHead(200, {
        "Content-Type": contentType,
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'",
      });
      if (request.method === "HEAD") response.end();
      else response.end(data);
    } catch {
      response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      response.end("Not found");
    }
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  assert(address && typeof address === "object", "localhost server did not expose an address");
  return {
    server,
    origin: `http://127.0.0.1:${address.port}`,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

async function waitForDevTools(profileDir, chromeProcess, stderr) {
  const activePort = path.join(profileDir, "DevToolsActivePort");
  const deadline = Date.now() + START_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (chromeProcess.exitCode !== null) {
      fail(`Chrome exited before DevTools became available (code ${chromeProcess.exitCode})`, stderr.value);
    }
    try {
      const contents = await readFile(activePort, "utf8");
      const [portLine, browserPath] = contents.trim().split(/\r?\n/);
      const port = Number(portLine);
      if (Number.isInteger(port) && port > 0 && browserPath?.startsWith("/devtools/browser/")) {
        return { port, websocketUrl: `ws://127.0.0.1:${port}${browserPath}` };
      }
    } catch {
      // Chrome creates DevToolsActivePort asynchronously.
    }
    await sleep(50);
  }
  fail("Chrome DevToolsActivePort was not created before timeout", stderr.value);
}

class CdpConnection {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 1;
    this.pending = new Map();
    this.handlers = new Set();

    socket.addEventListener("message", (event) => {
      let message;
      try {
        message = JSON.parse(String(event.data));
      } catch {
        return;
      }
      if (message.id) {
        const pending = this.pending.get(message.id);
        if (!pending) return;
        this.pending.delete(message.id);
        clearTimeout(pending.timer);
        if (message.error) pending.reject(new Error(`${pending.method}: ${message.error.message}`));
        else pending.resolve(message.result || {});
        return;
      }
      for (const handler of this.handlers) handler(message);
    });

    socket.addEventListener("close", () => {
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timer);
        pending.reject(new Error("CDP WebSocket closed while command was pending"));
      }
      this.pending.clear();
    });
  }

  static async connect(url) {
    assert(typeof WebSocket === "function", "Node built-in WebSocket is unavailable; QA-AUTO-001 requires the accepted dependency-free runtime");
    const socket = new WebSocket(url);
    await withTimeout(new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve, { once: true });
      socket.addEventListener("error", () => reject(new Error("CDP WebSocket connection failed")), { once: true });
    }), REQUEST_TIMEOUT_MS, "CDP WebSocket connection");
    return new CdpConnection(socket);
  }

  on(handler) {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  send(method, params = {}, sessionId = undefined) {
    const id = this.nextId++;
    const message = { id, method, params };
    if (sessionId) message.sessionId = sessionId;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method} timed out`));
      }, REQUEST_TIMEOUT_MS);
      this.pending.set(id, { resolve, reject, timer, method });
      this.socket.send(JSON.stringify(message));
    });
  }

  async close() {
    if (this.socket.readyState === WebSocket.OPEN) this.socket.close();
    await sleep(20);
  }
}

async function evaluate(cdp, sessionId, expression) {
  const response = await cdp.send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
    userGesture: true,
  }, sessionId);
  if (response.exceptionDetails) {
    fail("Runtime.evaluate raised an exception", response.exceptionDetails.text || response.exceptionDetails);
  }
  return response.result?.value;
}

async function waitForExpression(cdp, sessionId, expression, label, timeoutMs = PAGE_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  let lastError;
  while (Date.now() < deadline) {
    try {
      if (await evaluate(cdp, sessionId, expression)) return;
    } catch (error) {
      lastError = error;
    }
    await sleep(50);
  }
  fail(`${label} did not become true before timeout`, lastError?.message);
}

async function setViewport(cdp, sessionId, width, pageScaleFactor = 1) {
  await cdp.send("Emulation.setDeviceMetricsOverride", {
    width,
    height: VIEWPORT_HEIGHT,
    deviceScaleFactor: 1,
    mobile: false,
    screenWidth: width,
    screenHeight: VIEWPORT_HEIGHT,
  }, sessionId);
  await cdp.send("Emulation.setPageScaleFactor", { pageScaleFactor }, sessionId);
}

async function navigateScenario(cdp, sessionId, origin, scenario) {
  const url = `${origin}/prototype/?scenario=${encodeURIComponent(scenario)}`;
  await cdp.send("Page.navigate", { url }, sessionId);
  await waitForExpression(
    cdp,
    sessionId,
    `document.readyState === "complete" && document.querySelector("#scenario-select")?.value === ${JSON.stringify(scenario)} && document.querySelector("#item-name")?.textContent.trim().length > 0`,
    `scenario ${scenario} render`,
  );
  return url;
}

async function layoutSnapshot(cdp, sessionId) {
  return evaluate(cdp, sessionId, `(() => {
    const root = document.documentElement;
    const card = document.querySelector('.decision-card');
    const cardRect = card.getBoundingClientRect();
    const tableWrap = document.querySelector('.table-wrap');
    const item = document.querySelector('#item-name');
    const badge = document.querySelector('#availability-badge');
    const badgeStyle = getComputedStyle(badge);
    return {
      innerWidth: window.innerWidth,
      clientWidth: root.clientWidth,
      scrollWidth: root.scrollWidth,
      bodyText: document.body.innerText,
      cardLeft: cardRect.left,
      cardRight: cardRect.right,
      cardClientWidth: card.clientWidth,
      cardScrollWidth: card.scrollWidth,
      tableClientWidth: tableWrap.clientWidth,
      tableScrollWidth: tableWrap.scrollWidth,
      itemClientWidth: item.clientWidth,
      itemScrollWidth: item.scrollWidth,
      badgeWidth: badge.getBoundingClientRect().width,
      badgeHeight: badge.getBoundingClientRect().height,
      badgeLineHeight: parseFloat(badgeStyle.lineHeight) || 0,
      badgeWhiteSpace: badgeStyle.whiteSpace,
    };
  })()`);
}

function assertLayout(snapshot, width, scenario, expectedText) {
  assert(snapshot.scrollWidth <= snapshot.clientWidth, `${scenario} @ ${width}px has root horizontal overflow`, snapshot);
  assert(snapshot.clientWidth <= snapshot.innerWidth, `${scenario} @ ${width}px has invalid viewport geometry`, snapshot);
  assert(snapshot.cardLeft >= -0.5 && snapshot.cardRight <= snapshot.clientWidth + 0.5, `${scenario} @ ${width}px Decision Card escapes viewport`, snapshot);
  assert(snapshot.cardScrollWidth <= snapshot.cardClientWidth + 1, `${scenario} @ ${width}px Decision Card content overflows card`, snapshot);
  assert(snapshot.itemScrollWidth <= snapshot.itemClientWidth + 1, `${scenario} @ ${width}px item name clips horizontally`, snapshot);
  assert(snapshot.bodyText.includes(expectedText), `${scenario} @ ${width}px missing required rendered text: ${expectedText}`);
  assert(snapshot.badgeWhiteSpace === "nowrap", `${scenario} @ ${width}px status badge allows mid-word wrapping`, snapshot);
  const maxBadgeHeight = Math.max(snapshot.badgeLineHeight * 2.2, 48);
  assert(snapshot.badgeHeight <= maxBadgeHeight, `${scenario} @ ${width}px status badge is vertically fragmented`, snapshot);
  if (width <= 375) {
    assert(snapshot.tableScrollWidth > snapshot.tableClientWidth, `${scenario} @ ${width}px should keep history table overflow local`, snapshot);
  }
}

async function runLongContentStress(cdp, sessionId) {
  const result = await evaluate(cdp, sessionId, `(() => {
    const item = document.querySelector('#item-name');
    const chips = document.querySelector('#evidence-labels');
    item.textContent = 'Synthetic demonstration item with a deliberately long name that must wrap inside the Decision Card without clipping or pushing the page wider';
    const chip = document.createElement('span');
    chip.className = 'chip';
    chip.textContent = 'Synthetic evidence label with deliberately long browser smoke wording that must wrap safely';
    chips.append(chip);
    const root = document.documentElement;
    const card = document.querySelector('.decision-card');
    return {
      rootFits: root.scrollWidth <= root.clientWidth,
      cardFits: card.scrollWidth <= card.clientWidth + 1,
      itemFits: item.scrollWidth <= item.clientWidth + 1,
      chipFits: chip.getBoundingClientRect().right <= chips.getBoundingClientRect().right + 1,
      itemHeight: item.getBoundingClientRect().height,
      chipHeight: chip.getBoundingClientRect().height,
    };
  })()`);
  assert(result.rootFits && result.cardFits && result.itemFits && result.chipFits, "long synthetic content escapes its responsive container", result);
  assert(result.itemHeight > 20 && result.chipHeight > 20, "long synthetic content did not remain visibly rendered", result);
}

async function activeElementSnapshot(cdp, sessionId) {
  return evaluate(cdp, sessionId, `(() => {
    const element = document.activeElement;
    const style = element ? getComputedStyle(element) : null;
    return {
      tag: element?.tagName || '',
      id: element?.id || '',
      className: typeof element?.className === 'string' ? element.className : '',
      ariaLabel: element?.getAttribute?.('aria-label') || '',
      outlineStyle: style?.outlineStyle || '',
      outlineWidth: style?.outlineWidth || '',
      outlineColor: style?.outlineColor || '',
    };
  })()`);
}

async function dispatchKey(cdp, sessionId, key, code, keyCode, text = undefined) {
  const base = {
    key,
    code,
    windowsVirtualKeyCode: keyCode,
    nativeVirtualKeyCode: keyCode,
  };
  await cdp.send("Input.dispatchKeyEvent", { type: "keyDown", ...base, ...(text ? { text } : {}) }, sessionId);
  await cdp.send("Input.dispatchKeyEvent", { type: "keyUp", ...base }, sessionId);
}

function focusVisible(snapshot) {
  return snapshot.outlineStyle !== "none"
    && snapshot.outlineWidth !== "0px"
    && snapshot.outlineColor !== "rgba(0, 0, 0, 0)"
    && snapshot.outlineColor !== "transparent";
}

async function runKeyboardSmoke(cdp, sessionId, origin) {
  await setViewport(cdp, sessionId, 375, 1);
  await navigateScenario(cdp, sessionId, origin, "case-05-drop");
  await cdp.send("Page.bringToFront", {}, sessionId);
  await evaluate(cdp, sessionId, `document.activeElement?.blur(); window.scrollTo(0, 0); true`);

  await dispatchKey(cdp, sessionId, "Tab", "Tab", 9);
  let active = await activeElementSnapshot(cdp, sessionId);
  assert(active.tag === "A" && active.className.includes("skip-link"), "first keyboard Tab did not reach skip link", active);
  assert(focusVisible(active), "skip-link keyboard focus indicator is not visibly styled", active);

  await dispatchKey(cdp, sessionId, "Tab", "Tab", 9);
  active = await activeElementSnapshot(cdp, sessionId);
  assert(active.id === "scenario-select", "second keyboard Tab did not reach scenario select", active);
  assert(focusVisible(active), "scenario select keyboard focus indicator is not visibly styled", active);

  const before = await evaluate(cdp, sessionId, `document.querySelector('#scenario-select').value`);
  await dispatchKey(cdp, sessionId, "ArrowDown", "ArrowDown", 40);
  await waitForExpression(cdp, sessionId, `document.querySelector('#scenario-select').value !== ${JSON.stringify(before)}`, "keyboard scenario selection change");
  const after = await evaluate(cdp, sessionId, `document.querySelector('#scenario-select').value`);
  assert(after !== before, "ArrowDown did not change native scenario select value", { before, after });

  await dispatchKey(cdp, sessionId, "Tab", "Tab", 9);
  active = await activeElementSnapshot(cdp, sessionId);
  assert(active.tag === "SUMMARY", "keyboard Tab did not reach comparison summary", active);
  assert(focusVisible(active), "comparison summary keyboard focus indicator is not visibly styled", active);

  await dispatchKey(cdp, sessionId, " ", "Space", 32, " ");
  await waitForExpression(cdp, sessionId, `document.querySelector('.comparison-details').open === true`, "keyboard details toggle");

  await dispatchKey(cdp, sessionId, "Tab", "Tab", 9);
  const escaped = await activeElementSnapshot(cdp, sessionId);
  assert(!(escaped.tag === "SUMMARY"), "keyboard focus is trapped on comparison summary", escaped);
  assert(focusVisible(escaped), "post-summary keyboard focus indicator is not visibly styled", escaped);
}

async function runAccessibilitySmoke(cdp, sessionId) {
  const response = await cdp.send("Accessibility.getFullAXTree", {}, sessionId);
  const nodes = response.nodes || [];
  const role = (node) => node.role?.value || "";
  const name = (node) => node.name?.value || "";
  assert(nodes.some((node) => role(node) === "main"), "accessibility tree is missing main landmark");
  const combobox = nodes.find((node) => role(node) === "combobox");
  assert(Boolean(combobox), "accessibility tree is missing native scenario combobox");
  assert(name(combobox).trim().length > 0, "accessibility scenario combobox has no accessible name", { role: role(combobox), name: name(combobox) });
  assert(nodes.some((node) => role(node) === "table"), "accessibility tree is missing semantic history table");
  assert(nodes.some((node) => name(node).includes("How trustworthy is this comparison?")), "accessibility tree is missing named comparison disclosure control");
}

async function runScaleSmoke(cdp, sessionId, origin) {
  await setViewport(cdp, sessionId, 375, 1);
  await navigateScenario(cdp, sessionId, origin, "case-15-incomplete");
  await cdp.send("Emulation.setPageScaleFactor", { pageScaleFactor: 2 }, sessionId);
  await sleep(50);
  const snapshot = await layoutSnapshot(cdp, sessionId);
  const scale = await evaluate(cdp, sessionId, `window.visualViewport?.scale || 1`);
  assert(Number(scale) >= 1.9, "CDP ~200% page-scale smoke approximation was not applied", { scale });
  assertLayout(snapshot, 375, "case-15-incomplete-scale-approx", "Incomplete");
  await cdp.send("Emulation.setPageScaleFactor", { pageScaleFactor: 1 }, sessionId);
}

async function runBrowserSmoke() {
  const version = process.versions.node;
  assert(typeof fetch === "function" && typeof WebSocket === "function", `Node ${version} lacks required built-in fetch/WebSocket`);

  const chrome = await findChrome();
  const chromeVersion = spawnSync(chrome, ["--version"], { encoding: "utf8" });
  assert(chromeVersion.status === 0, "Chrome version check failed", chromeVersion.stderr);

  const server = await startStaticServer();
  const profileDir = await mkdtemp(path.join(RUNNER_TEMP, "royal-browser-smoke-"));
  const stderr = { value: "" };
  let chromeProcess;
  let cdp;
  let targetId;

  try {
    chromeProcess = spawn(chrome, [
      "--headless=new",
      "--remote-debugging-address=127.0.0.1",
      "--remote-debugging-port=0",
      `--user-data-dir=${profileDir}`,
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-background-networking",
      "--disable-component-update",
      "--disable-default-apps",
      "--disable-sync",
      "--metrics-recording-only",
      "--mute-audio",
      "about:blank",
    ], { stdio: ["ignore", "ignore", "pipe"] });

    chromeProcess.stderr.on("data", (chunk) => {
      stderr.value = `${stderr.value}${String(chunk)}`.slice(-12_000);
    });

    const devtools = await waitForDevTools(profileDir, chromeProcess, stderr);
    cdp = await CdpConnection.connect(devtools.websocketUrl);
    const target = await cdp.send("Target.createTarget", { url: "about:blank" });
    targetId = target.targetId;
    const attached = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
    const sessionId = attached.sessionId;

    const runtimeExceptions = [];
    const requestedUrls = [];
    cdp.on((message) => {
      if (message.sessionId !== sessionId) return;
      if (message.method === "Runtime.exceptionThrown") runtimeExceptions.push(message.params?.exceptionDetails?.text || "uncaught runtime exception");
      if (message.method === "Network.requestWillBeSent") requestedUrls.push(message.params?.request?.url || "");
    });

    await cdp.send("Page.enable", {}, sessionId);
    await cdp.send("Runtime.enable", {}, sessionId);
    await cdp.send("Network.enable", {}, sessionId);
    await cdp.send("Accessibility.enable", {}, sessionId);

    let responsiveCases = 0;
    for (const width of VIEWPORTS) {
      await setViewport(cdp, sessionId, width, 1);
      for (const [scenario, expectedText] of SCENARIOS) {
        runtimeExceptions.length = 0;
        await navigateScenario(cdp, sessionId, server.origin, scenario);
        const snapshot = await layoutSnapshot(cdp, sessionId);
        assertLayout(snapshot, width, scenario, expectedText);
        assert(runtimeExceptions.length === 0, `${scenario} @ ${width}px raised uncaught runtime JavaScript`, runtimeExceptions);
        responsiveCases += 1;
      }
    }

    await setViewport(cdp, sessionId, 320, 1);
    await navigateScenario(cdp, sessionId, server.origin, "case-15-incomplete");
    await runLongContentStress(cdp, sessionId);
    await runKeyboardSmoke(cdp, sessionId, server.origin);
    await runAccessibilitySmoke(cdp, sessionId);
    await runScaleSmoke(cdp, sessionId, server.origin);

    const externalRequests = requestedUrls.filter((url) => url && !url.startsWith(`${server.origin}/`) && !url.startsWith("about:") && !url.startsWith("data:"));
    assert(externalRequests.length === 0, "prototype made an external network request during browser smoke", externalRequests);
    assert(runtimeExceptions.length === 0, "uncaught runtime JavaScript occurred during browser smoke", runtimeExceptions);

    console.log(`Browser smoke: PASS (${responsiveCases} responsive scenario/viewport cases)`);
    console.log(`Chrome: ${chromeVersion.stdout.trim()}`);
    console.log(`Node: ${version}`);
    console.log("Keyboard/focus: PASS");
    console.log("Accessibility tree: PASS");
    console.log("Long-content containment: PASS");
    console.log("CDP ~200% page-scale approximation: PASS (not a human browser-zoom conformance claim)");
    console.log("External page requests: 0");
  } finally {
    if (cdp && targetId) {
      try { await cdp.send("Target.closeTarget", { targetId }); } catch { /* cleanup only */ }
    }
    if (cdp) {
      try { await cdp.close(); } catch { /* cleanup only */ }
    }
    if (chromeProcess && chromeProcess.exitCode === null) {
      chromeProcess.kill("SIGTERM");
      const stopped = await waitForProcessExit(chromeProcess, 1_500);
      if (!stopped) {
        chromeProcess.kill("SIGKILL");
        await waitForProcessExit(chromeProcess, 1_500);
      }
    }
    await server.close();
    await rm(profileDir, {
      recursive: true,
      force: true,
      maxRetries: 8,
      retryDelay: 125,
    });
  }
}

runBrowserSmoke().catch((error) => {
  console.error(`Browser smoke: FAIL — ${error.message}`);
  if (error.detail !== undefined) console.error(JSON.stringify(error.detail));
  process.exitCode = 1;
});

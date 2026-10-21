// Drives headless Chrome over the DevTools protocol for tests/test_dashboard_browser.py (Node 22+, no packages).
//   node dashboard_probe.js CHROME_PATH URL WIDTH [light|dark] [WAIT_MS] [SCREENSHOT.png|-] [JS_TO_RUN_AFTER_LOAD] [JS_QUERY]
// Prints one JSON object: layout facts, the page text, and every console error / exception / CSP violation.
// JS_TO_RUN_AFTER_LOAD (optional) is evaluated once the page has rendered, e.g. to click a legend button. JS_QUERY
// (optional) is evaluated after that and its value comes back as "query" (e.g. a count of table rows).
const { spawn } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const [chromePath, url, widthArg, scheme, waitArg, shotArg, afterJs, queryJs] = process.argv.slice(2);
const shotPath = shotArg && shotArg !== "-" ? shotArg : null;
const width = parseInt(widthArg || "1366", 10);
const waitMs = parseInt(waitArg || "4000", 10);
const port = 9600 + Math.floor(Math.random() * 300);
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "kpm-probe-"));
const chrome = spawn(chromePath, ["--headless=new", "--disable-gpu", "--no-sandbox", `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`, "--hide-scrollbars", "about:blank"], { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  let targets = null;
  for (let i = 0; i < 60 && !targets; i++) {
    try { targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json(); } catch (e) { await sleep(250); }
  }
  const page = targets.find((t) => t.type === "page");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0;
  const pending = new Map();
  const problems = [];
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    const p = m.params || {};
    if (m.method === "Runtime.exceptionThrown") {
      const d = p.exceptionDetails || {};
      problems.push("exception: " + ((d.exception && d.exception.description) || d.text));
    } else if (m.method === "Runtime.consoleAPICalled" && (p.type === "error" || p.type === "warning")) {
      problems.push("console." + p.type + ": " + (p.args || []).map((a) => a.value || a.description).join(" "));
    } else if (m.method === "Log.entryAdded" && (p.entry.level === "error" || p.entry.level === "warning")) {
      problems.push("log." + p.entry.level + ": " + p.entry.text + " " + (p.entry.url || ""));
    }
  };
  const send = (method, params) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params: params || {} })); });
  const evaluate = async (expression) => (await send("Runtime.evaluate", { expression, returnByValue: true })).result.result.value;
  await send("Page.enable"); await send("Runtime.enable"); await send("Log.enable");
  await send("Emulation.setDeviceMetricsOverride", { width, height: 900, deviceScaleFactor: 1, mobile: width < 700 });
  await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: scheme === "dark" ? "dark" : "light" }] });
  await send("Page.navigate", { url });
  // Ready when the first poll has rendered (the badge leaves CONNECTING); then a moment for the research block.
  const deadline = Date.now() + Math.max(waitMs, 8000);
  while (Date.now() < deadline) {
    const ready = await evaluate("(document.getElementById('mode-text') || {}).textContent");
    if (ready && ready !== "CONNECTING") break;
    await sleep(100);
  }
  await sleep(500);
  if (afterJs) {
    await evaluate(afterJs);
    await sleep(300);
  }
  const layout = JSON.parse(await evaluate(
    "JSON.stringify({scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth, " +
    "scrollHeight: document.documentElement.scrollHeight, title: document.title, " +
    "overflowing: Array.from(document.querySelectorAll('body *')).filter(e => !e.closest('.table-wrap') && " +
    "e.getBoundingClientRect().right > window.innerWidth + 1).slice(0, 8).map(e => e.tagName + '#' + e.id + '.' + e.className)})"));
  const text = await evaluate("document.body.innerText");
  const hidden = JSON.parse(await evaluate(
    "JSON.stringify(Array.from(document.querySelectorAll('[id]')).filter(e => e.hidden).map(e => e.id))"));
  if (shotPath) {
    await send("Emulation.setDeviceMetricsOverride", { width, height: Math.min(layout.scrollHeight, 8000), deviceScaleFactor: 1, mobile: width < 700 });
    const shot = await send("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(shotPath, Buffer.from(shot.result.data, "base64"));
  }
  const query = queryJs ? await evaluate(queryJs) : null;
  console.log(JSON.stringify({ layout, text, hidden, problems, query }));
  ws.close();
}

// Stop Chrome with its child processes, then delete the throw-away profile (about 10 MB per run would pile up in %TEMP%).
function cleanup() {
  try {
    if (process.platform === "win32") require("child_process").spawnSync("taskkill", ["/PID", String(chrome.pid), "/T", "/F"], { stdio: "ignore" });
    else chrome.kill("SIGKILL");
  } catch (e) { /* already gone */ }
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 800);          // let the dying processes release their files
  for (let attempt = 0; attempt < 15; attempt++) {
    try { fs.rmSync(profile, { recursive: true, force: true }); return; } catch (e) {
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 200);
    }
  }
}

main().catch((e) => { console.error("PROBE FAILED", e); process.exitCode = 1; })
  .finally(() => { cleanup(); process.exit(); });

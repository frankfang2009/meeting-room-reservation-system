import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { pathToFileURL } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const appSource = fs.readFileSync(path.join(root, "src/App.jsx"), "utf8");
const calendarCss = fs.readFileSync(path.join(root, "src/styles/calendar.css"), "utf8");
const styleFiles = fs.readFileSync(path.join(root, "src/styles.css"), "utf8")
  .split("\n")
  .map((line) => line.match(/@import\s+"\.\/styles\/([^"]+)"/)?.[1])
  .filter(Boolean);

const longCase = `TEST-2026-${"0123456789".repeat(5)}`;
const spacedCase = "甲乙丙丁 ".repeat(12).trim();
const longName = `验收用户${"名字".repeat(16)}`;

function escapeHtml(value) {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function card({ id, kind, span, short = false, countdown = false, name, caseNumber, column, row }) {
  const title = `${name} · 已预约`;
  const caseText = `案号 ${caseNumber}`;
  const ownership = kind === "mine" ? "我的预约" : "他人预约";
  const aria = `笔录室 ${column + 1}，预约者${name}，${ownership}，当事人张女士，案号${caseNumber}`;
  const classes = [
    "slot booked-slot",
    kind === "mine" ? "booking-mine" : "booking-other",
    short ? "booking-short" : "",
    countdown ? "slot-countdown" : "",
  ].filter(Boolean).join(" ");
  const decoration = kind === "mine"
    ? `<span class="booking-card" aria-hidden="true"><span class="booking-sheet"></span></span><span class="booking-fold" aria-hidden="true"></span>`
    : "";
  const chip = countdown ? `<span class="booking-countdown" aria-hidden="true">15 分后</span>` : "";
  const dot = kind === "mine" ? "<i></i>" : "";
  return `<button class="${classes}" data-fixture="${id}" data-calendar-row="${row}" data-calendar-column="${column}" style="--booking-span:${span};--tag-color:#7B9275;--tag-surface:#EEF1EA;--tag-line:#C9D3C4;--tag-fold:#C5D2C1" aria-label="${escapeHtml(aria)}">${decoration}<span class="booking-copy"><span class="booking-title">${dot}<span title="${escapeHtml(title)}">${escapeHtml(title)}</span></span><span class="booking-case" title="${escapeHtml(caseText)}">${escapeHtml(caseText)}</span></span>${chip}</button>`;
}

function fixtureHtml() {
  const css = styleFiles.map((name) => fs.readFileSync(path.join(root, "src/styles", name), "utf8")).join("\n");
  const row = (time, cells) => `<div class="schedule-row"><div class="time-label">${time}</div>${cells}</div>`;
  const occupied = (column, index) => `<div class="slot occupied-slot" data-calendar-row="${index}" data-calendar-column="${column}" aria-hidden="true"></div>`;
  const empty = (column, index) => `<button class="slot available-slot" data-calendar-row="${index}" data-calendar-column="${column}"></button>`;
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <style>${css}</style>
  <style>html, body { width: 1024px; height: 720px; }</style>
</head>
<body>
  <div class="app-shell">
    <main class="main-canvas calendar-canvas">
      <section class="calendar-section">
        <div class="schedule-viewport">
          <div class="schedule" style="--room-count: 3" role="grid">
            <div class="schedule-head"><div></div><div class="room-heading">笔录室 1</div><div class="room-heading">笔录室 2</div><div class="room-heading">笔录室 3</div></div>
            <div class="schedule-body">
              ${row("09:00", [
                card({ id: "mine-long-case", kind: "mine", span: 2, name: "验收用户", caseNumber: longCase, column: 0, row: 0 }),
                card({ id: "other-long-case", kind: "other", span: 2, name: longName, caseNumber: longCase, column: 1, row: 0 }),
                empty(2, 0),
              ].join(""))}
              ${row("09:30", [occupied(0, 1), occupied(1, 1), empty(2, 1)].join(""))}
              ${row("10:00", [
                card({ id: "mine-short", kind: "mine", span: 1, short: true, name: longName, caseNumber: spacedCase, column: 0, row: 2 }),
                card({ id: "other-short", kind: "other", span: 1, name: longName, caseNumber: spacedCase, column: 1, row: 2 }),
                card({ id: "mine-countdown", kind: "mine", span: 1, short: true, countdown: true, name: longName, caseNumber: longCase, column: 2, row: 2 }),
              ].join(""))}
            </div>
          </div>
        </div>
      </section>
    </main>
  </div>
</body>
</html>`;
}

function chromePath() {
  const candidates = [
    process.env.CHROME_PATH,
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  ].filter(Boolean);
  return candidates.find((candidate) => fs.existsSync(candidate));
}

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

async function measureLayout() {
  const chrome = chromePath();
  assert.ok(chrome, "Chrome is required to measure calendar text overflow");
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "calendar-overflow-"));
  const htmlPath = path.join(directory, "index.html");
  fs.writeFileSync(htmlPath, fixtureHtml());
  const port = await freePort();
  const profile = path.join(directory, "profile");
  const child = spawn(chrome, [
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    `--user-data-dir=${profile}`,
    `--remote-debugging-port=${port}`,
    "--window-size=1024,720",
    "--force-device-scale-factor=1",
    "about:blank",
  ], { stdio: ["ignore", "pipe", "pipe"] });
  let logs = "";
  child.stdout.on("data", (chunk) => { logs += chunk; });
  child.stderr.on("data", (chunk) => { logs += chunk; });
  try {
    const targets = await waitForTargets(port);
    const page = targets.find((target) => target.type === "page");
    assert.ok(page?.webSocketDebuggerUrl, logs || "Chrome did not expose a page target");
    const ws = new globalThis.WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      ws.addEventListener("open", resolve, { once: true });
      ws.addEventListener("error", () => reject(new Error(logs || "Chrome debugger socket failed")), { once: true });
    });
    let sequence = 0;
    const pending = new Map();
    ws.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      const resolve = pending.get(message.id);
      if (!resolve) return;
      pending.delete(message.id);
      resolve(message);
    });
    const send = (method, params = {}) => new Promise((resolve, reject) => {
      const id = ++sequence;
      pending.set(id, (message) => {
        if (message.error) reject(new Error(`${method}: ${JSON.stringify(message.error)}`));
        else resolve(message.result);
      });
      ws.send(JSON.stringify({ id, method, params }));
    });
    await send("Page.enable");
    await send("Emulation.setDeviceMetricsOverride", { width: 1024, height: 720, deviceScaleFactor: 1, mobile: false });
    const loaded = new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("calendar fixture did not load")), 10000);
      ws.addEventListener("message", (event) => {
        const message = JSON.parse(event.data);
        if (message.method === "Page.loadEventFired") {
          clearTimeout(timeout);
          resolve();
        }
      });
    });
    await send("Page.navigate", { url: pathToFileURL(htmlPath).href });
    await loaded;
    await send("Runtime.evaluate", { expression: "document.fonts ? document.fonts.ready : null", awaitPromise: true });
    const evaluated = await send("Runtime.evaluate", {
      expression: measureExpression,
      returnByValue: true,
    });
    ws.close();
    if (evaluated.exceptionDetails) {
      throw new Error(evaluated.exceptionDetails.text || JSON.stringify(evaluated.exceptionDetails));
    }
    return evaluated.result.value;
  } finally {
    child.kill("SIGKILL");
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

async function waitForTargets(port) {
  const deadline = Date.now() + 15000;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/list`);
      if (response.ok) {
        const targets = await response.json();
        if (targets.some((target) => target.type === "page" && target.webSocketDebuggerUrl)) return targets;
      }
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(lastError ? String(lastError) : "Chrome debugger did not start");
}

const measureExpression = `(() => {
  const box = (element) => {
    if (!element) return null;
    const rect = element.getBoundingClientRect();
    return { left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, width: rect.width, height: rect.height };
  };
  const intersects = (a, b) => a && b && a.left < b.right - 0.5 && a.right > b.left + 0.5 && a.top < b.bottom - 0.5 && a.bottom > b.top + 0.5;
  return [...document.querySelectorAll("[data-fixture]")].map((button) => {
    const title = button.querySelector(".booking-title > span");
    const dot = button.querySelector(".booking-title i");
    const caseText = button.querySelector(".booking-case");
    const fold = button.querySelector(".booking-fold");
    const card = button.querySelector(".booking-card");
    const chip = button.querySelector(".booking-countdown");
    const column = Number(button.dataset.calendarColumn);
    const row = button.dataset.calendarRow;
    const neighbor = document.querySelector('[data-calendar-row="' + row + '"][data-calendar-column="' + (column + 1) + '"]');
    const caseBox = box(caseText);
    const probeX = button.getBoundingClientRect().right + 4;
    const probeY = (caseBox.top + caseBox.bottom) / 2;
    const probed = document.elementFromPoint(probeX, probeY);
    return {
      id: button.dataset.fixture,
      button: box(button),
      title: box(title),
      dot: box(dot),
      caseText: caseBox,
      fold: box(fold),
      chip: box(chip),
      neighbor: box(neighbor),
      titleClient: title.clientWidth,
      titleScroll: title.scrollWidth,
      caseClient: caseText.clientWidth,
      caseScroll: caseText.scrollWidth,
      titleOverflow: getComputedStyle(title).textOverflow,
      caseOverflow: getComputedStyle(caseText).textOverflow,
      caseWhiteSpace: getComputedStyle(caseText).whiteSpace,
      buttonOverflow: getComputedStyle(button).overflowY,
      cardFilter: card ? getComputedStyle(card).filter : "",
      foldWidth: fold ? getComputedStyle(fold).width : "",
      titleAttr: title.getAttribute("title"),
      caseAttr: caseText.getAttribute("title"),
      aria: button.getAttribute("aria-label"),
      probeIsOwnText: probed === title || probed === caseText || button.contains(probed) && probed !== button,
      foldHitsTitle: intersects(box(title), box(fold)),
      foldHitsDot: intersects(box(dot), box(fold)),
      chipHitsTitle: intersects(box(title), box(chip)),
      chipHitsCase: intersects(box(caseText), box(chip)),
    };
  });
})()`;

test("calendar cards keep long names and case numbers inside the card", () => {
  assert.equal(longCase.length, 60);
  assert.match(appSource, /className="booking-copy"/);
  assert.match(appSource, /<span title=\{titleText\}>\{titleText\}<\/span>/);
  assert.match(appSource, /className="booking-case" title=\{caseText\}/);
  assert.match(appSource, /案号\$\{booking\.caseNumber\}/);
  assert.match(calendarCss, /\.booked-slot\.booking-mine \{[\s\S]*?overflow: visible;/);
  assert.match(calendarCss, /\.booking-copy \{[\s\S]*?min-width: 0;[\s\S]*?overflow: hidden;/);
  assert.match(calendarCss, /\.booking-title > span \{[\s\S]*?text-overflow: ellipsis;[\s\S]*?white-space: nowrap;/);
  assert.match(calendarCss, /\.booking-case \{[\s\S]*?text-overflow: ellipsis;[\s\S]*?white-space: nowrap;/);
  assert.match(calendarCss, /\.booking-card \{[\s\S]*?drop-shadow\(0 3px 7px rgba\(20, 20, 19, 0\.04\)\)/);
});

test("a 1024px three-room calendar clips long case numbers and names without losing the fold", async () => {
  const cards = await measureLayout();
  assert.equal(cards.length, 5);
  for (const card of cards) {
    assert.ok(card.caseText.right <= card.button.right + 0.5, `${card.id} case ${card.caseText.right} > ${card.button.right}`);
    assert.ok(card.title.right <= card.button.right + 0.5, `${card.id} title overflows`);
    assert.ok(card.caseText.left >= card.button.left - 0.5, `${card.id} case starts outside`);
    assert.ok(card.title.bottom <= card.button.bottom + 0.5, `${card.id} title exceeds the card height`);
    assert.ok(card.caseText.bottom <= card.button.bottom + 0.5, `${card.id} case exceeds the card height`);
    assert.equal(card.caseOverflow, "ellipsis", card.id);
    assert.equal(card.titleOverflow, "ellipsis", card.id);
    assert.equal(card.caseWhiteSpace, "nowrap", card.id);
    assert.ok(card.caseScroll > card.caseClient + 8, `${card.id} case was not truncated`);
    assert.ok(card.titleAttr.includes("已预约"), card.id);
    assert.ok(card.caseAttr.startsWith("案号 "), card.id);
    assert.match(card.aria, /案号/);
    assert.equal(card.probeIsOwnText, false, `${card.id} text paints past the card`);
    if (card.neighbor) {
      assert.ok(card.caseText.right <= card.neighbor.left + 0.5, `${card.id} enters the next room`);
      assert.ok(card.title.right <= card.neighbor.left + 0.5, `${card.id} name enters the next room`);
    }
  }
  const mine = cards.find((item) => item.id === "mine-long-case");
  const short = cards.find((item) => item.id === "mine-short");
  const countdown = cards.find((item) => item.id === "mine-countdown");
  const other = cards.find((item) => item.id === "other-long-case");
  assert.equal(mine.buttonOverflow, "visible");
  assert.match(mine.cardFilter, /drop-shadow/);
  assert.equal(mine.foldWidth, "12px");
  assert.equal(short.foldWidth, "8px");
  assert.equal(mine.foldHitsTitle, false);
  assert.equal(mine.foldHitsDot, false);
  assert.equal(short.foldHitsTitle, false);
  assert.equal(short.foldHitsDot, false);
  assert.ok(short.button.height <= 76, short.button.height);
  assert.ok(other.titleScroll > other.titleClient + 8);
  assert.equal(countdown.chipHitsTitle, false);
  assert.equal(countdown.chipHitsCase, false);
  assert.match(mine.aria, new RegExp(longCase));
  assert.match(mine.caseAttr, new RegExp(longCase));
  assert.match(short.titleAttr, new RegExp(longName));
  assert.match(short.aria, new RegExp(longName));
});

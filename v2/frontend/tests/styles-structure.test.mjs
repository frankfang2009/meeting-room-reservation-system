import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoot = path.join(root, "src");
const stylesRoot = path.join(sourceRoot, "styles");
const manifestPath = path.join(sourceRoot, "styles.css");
const expectedFiles = [
  "foundation.css",
  "login.css",
  "setup.css",
  "shell.css",
  "help-center.css",
  "dashboard.css",
  "history.css",
  "rooms.css",
  "calendar.css",
  "drawer-shell.css",
  "booking-forms.css",
  "settings.css",
  "system.css",
  "users.css",
  "reports.css",
  "public-display.css",
  "runtime-states.css",
  "responsive.css",
  "production-flows.css",
  "system-extensions.css",
  "idle-states.css",
  "accessibility.css",
];
// V2.2.0 评审修复：数据中心标签四槽位改为与 ui/presentation.js 的 TAG_COLORS
// 完全同值（此前槽位 2/3 颜色互换了）。
// V2.2.1：系统状态页新增 macOS 版“软件更新”徽标与检查按钮样式。
// V2.2.x UX 打磨：日历来源时段描边与日期切换入场动画（calendar.css）、
// 我的预约/预约记录/数据中心读取骨架（dashboard/history/reports.css）、
// 成功确认条轻落入场（runtime-states.css）。
// V2.3.0 提醒重做：日历倒计时角标与「今天」圆点（calendar.css）、
// 变更通知居中弹窗与排队提示条（production-flows.css）。
// V2.4.0 工作交接：交接请求弹窗区块与人员选择抽屉（production-flows.css）、
// 独立工作交接页（dashboard.css）。
// V2.4.1：通知弹窗层级高于普通到达提醒（production-flows.css）。
// V2.4.1：交接页隐藏空分组并改为紧凑三列操作区（dashboard.css）。
// V2.4.1：交接页全空状态使用居中的图标、标题与说明（dashboard.css）。
// V2.5.0：管理员与员工共用紧凑单列导航节奏，业务/帮助/个人入口统一图标系统（shell.css / responsive.css）。
// V2.5.0：数据中心默认态收束为指标与趋势，分析/筛选/导出渐进展开（reports.css / responsive.css）。
// V2.5.0：帮助入口合一；个人偏好与系统安全能力按需展开（help-center/settings/system-extensions.css）。
// V2.5.0：所有真实空状态共用克制的居中图标、说明和单一恢复动作（idle-states.css）。
const frozenSourceSha256 = "d515b0c489d711dc3823b1f3df130df828331af7c06077882aa5463df8e83c3c";

function luminance(hex) {
  const channels = hex.match(/[0-9a-f]{2}/gi).map((value) => Number.parseInt(value, 16) / 255);
  const linear = channels.map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(left, right) {
  const values = [luminance(left), luminance(right)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

function importedFiles() {
  const manifest = fs.readFileSync(manifestPath, "utf8");
  return [...manifest.matchAll(/^@import "\.\/styles\/([^"]+)";$/gm)]
    .map((match) => match[1]);
}

test("global CSS manifest preserves the frozen cascade order", () => {
  assert.deepEqual(importedFiles(), expectedFiles);
  const manifestLines = fs.readFileSync(manifestPath, "utf8").trim().split("\n");
  assert.equal(manifestLines.length, expectedFiles.length + 1);
});

test("every feature stylesheet is registered exactly once without nested imports", () => {
  const actualFiles = fs.readdirSync(stylesRoot)
    .filter((name) => name.endsWith(".css"))
    .sort();
  assert.deepEqual(actualFiles, [...expectedFiles].sort());
  for (const name of actualFiles) {
    assert.doesNotMatch(fs.readFileSync(path.join(stylesRoot, name), "utf8"), /@import\s/);
  }
});

test("split styles reconstruct the frozen source byte for byte", () => {
  const source = expectedFiles
    .map((name) => fs.readFileSync(path.join(stylesRoot, name)))
    .reduce((chunks, content) => Buffer.concat([chunks, content]), Buffer.alloc(0));
  assert.equal(crypto.createHash("sha256").update(source).digest("hex"), frozenSourceSha256);
});

test("React keeps one global CSS entrypoint", () => {
  const main = fs.readFileSync(path.join(sourceRoot, "main.jsx"), "utf8");
  assert.match(main, /import "\.\/styles\.css";/);
  assert.doesNotMatch(main, /import "\.\/styles\//);
});

test("compact production actions retain at least a 44px hit target", () => {
  const dashboard = fs.readFileSync(path.join(stylesRoot, "dashboard.css"), "utf8");
  const drawer = fs.readFileSync(path.join(stylesRoot, "drawer-shell.css"), "utf8");
  const flows = fs.readFileSync(path.join(stylesRoot, "production-flows.css"), "utf8");
  const system = fs.readFileSync(path.join(stylesRoot, "system.css"), "utf8");
  const bookingForms = fs.readFileSync(path.join(stylesRoot, "booking-forms.css"), "utf8");
  const users = fs.readFileSync(path.join(stylesRoot, "users.css"), "utf8");
  assert.match(drawer, /\.drawer-back \{[\s\S]*?width: 44px;[\s\S]*?height: 44px;/);
  assert.match(flows, /\.reminder-toast > button \{[\s\S]*?min-height: 44px;/);
  assert.match(flows, /\.notice-item-actions button \{[\s\S]*?min-height: 44px;/);
  assert.match(flows, /\.handover-defer-button \{[\s\S]*?min-height: 44px;/);
  assert.match(flows, /\.handover-picker-actions button \{[\s\S]*?min-height: 44px;/);
  assert.match(flows, /\.handover-picker-actions \.primary-button \{[\s\S]*?min-height: 52px;/);
  assert.match(flows, /\.handover-booking-button \{[\s\S]*?min-height: 52px;/);
  assert.match(dashboard, /\.handover-ledger-actions button,[\s\S]*?min-height: 44px;/);
  assert.match(system, /\.system-copy-address \{[\s\S]*?min-height: 44px;/);
  assert.match(bookingForms, /\.room-delete-button \{[\s\S]*?min-height: 44px;/);
  assert.match(bookingForms, /\.copy-reminder-button,[\s\S]*?height: 52px;/);
  assert.match(users, /\.users-create-button \{[\s\S]*?min-height: 46px;/);
});

test("handover rows use a stable three-column action grid and a themed pending status", () => {
  const dashboard = fs.readFileSync(path.join(stylesRoot, "dashboard.css"), "utf8");
  const idleStates = fs.readFileSync(path.join(stylesRoot, "idle-states.css"), "utf8");
  assert.match(dashboard, /\.handover-ledger-actions \{[\s\S]*?display: grid;[\s\S]*?grid-template-columns: 88px 96px 120px;/);
  assert.match(dashboard, /\.handover-waiting \{[\s\S]*?color: var\(--terracotta-text\);[\s\S]*?background: var\(--terracotta-soft\);[\s\S]*?border: 1px solid var\(--terracotta-line\);/);
  assert.match(idleStates, /\.idle-state \{[\s\S]*?display: grid;[\s\S]*?place-items: center;[\s\S]*?text-align: center;/);
  assert.match(idleStates, /\.handover-page-empty\.idle-state \{[\s\S]*?min-height: clamp\(320px, calc\(100vh - 390px\), 520px\);/);
  assert.match(idleStates, /\.idle-state--accent \.idle-state__icon \{[\s\S]*?color: var\(--terracotta-text\);/);
  assert.match(idleStates, /\.idle-state__action \{[\s\S]*?min-height: 46px;/);
  assert.doesNotMatch(dashboard, /\.handover-summary/);
  assert.doesNotMatch(dashboard, /\.handover-ledger-empty/);
});

test("a primary idle action keeps its button treatment inside page-specific empty shells", () => {
  const idleStates = fs.readFileSync(path.join(stylesRoot, "idle-states.css"), "utf8");
  assert.match(idleStates, /\.idle-state button\.idle-state__action \{[\s\S]*?color: var\(--paper-raised\);[\s\S]*?border: 1px solid var\(--ink\);[\s\S]*?background: var\(--ink\);/);
});

test("the handover empty state is centered within its full-width ledger canvas", () => {
  const idleStates = fs.readFileSync(path.join(stylesRoot, "idle-states.css"), "utf8");
  assert.match(idleStates, /\.handover-page-empty\.idle-state \{[^}]*margin: 0 auto;/);
});

test("stacked notices use one bounded scroll body and a responsive combined footer", () => {
  const flows = fs.readFileSync(path.join(stylesRoot, "production-flows.css"), "utf8");
  assert.match(flows, /\.notice-modal-body \{[\s\S]*?overflow-y: auto;[\s\S]*?scrollbar-gutter: stable;/);
  assert.doesNotMatch(flows, /\.notice-modal-list \{[^}]*overflow-y: auto;/);
  assert.match(flows, /\.notice-modal\.mixed \{[\s\S]*?width: 760px;/);
  assert.match(flows, /\.notice-modal-combined-foot \{[\s\S]*?justify-content: space-between;/);
  assert.match(flows, /@media \(max-width: 680px\)[\s\S]*?\.notice-modal-combined-foot \{[\s\S]*?flex-direction: column;/);
});

test("rail reminder count sits outside the reservation icon", () => {
  const shell = fs.readFileSync(path.join(stylesRoot, "shell.css"), "utf8");
  assert.match(shell, /\.rail-reminder-badge \{[\s\S]*?top: -3px;[\s\S]*?right: -5px;[\s\S]*?width: 20px;[\s\S]*?height: 20px;/);
  assert.match(shell, /\.rail-reminder-badge \{[\s\S]*?font-variant-numeric: tabular-nums;/);
});

test("the three-room calendar can shrink to the 1024px workspace", () => {
  const calendar = fs.readFileSync(path.join(stylesRoot, "calendar.css"), "utf8");
  assert.match(calendar, /\.schedule \{\s*min-width: min\(860px, 100%\);/);
  assert.match(calendar, /grid-template-columns: 70px repeat\(var\(--room-count\), minmax\(220px, 1fr\)\);/);
});

test("low-frequency controls keep semantic styles and readable cancelled status", () => {
  const history = fs.readFileSync(path.join(stylesRoot, "history.css"), "utf8");
  const flows = fs.readFileSync(path.join(stylesRoot, "production-flows.css"), "utf8");
  const settings = fs.readFileSync(path.join(stylesRoot, "settings.css"), "utf8");
  assert.match(history, /\.history-filter-popover input\[type="radio"\]/);
  assert.match(flows, /\.booking-events-error button \{[\s\S]*?min-height: 44px;/);
  assert.match(settings, /\.settings-save-button:disabled/);
  const cancelledColor = history.match(/\.history-cancelled-status,\s*\.history-handover-status \{[\s\S]*?color: (#[0-9a-f]{6});/i)?.[1];
  assert.ok(cancelledColor);
  assert.ok(contrastRatio(cancelledColor, "#f5f4ed") >= 4.5);
});

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const app = fs.readFileSync(path.join(root, "src/App.jsx"), "utf8");
const authFlow = fs.readFileSync(path.join(root, "src/auth-flow.js"), "utf8");
const setupRestart = fs.readFileSync(path.join(root, "src/setup-restart.js"), "utf8");

test("production entry contains no demo credentials or query-state router", () => {
  for (const forbidden of ["demo123", "demo1234", "URLSearchParams", "loginState=", "bookingState=", "feedbackState=", "displayState=", "dataState="]) {
    assert.equal(app.includes(forbidden), false, "forbidden production source: " + forbidden);
  }
});

test("production entry does not generate business ids in the browser", () => {
  assert.equal(app.includes("crypto.randomUUID"), false);
  assert.doesNotMatch(app, /(?:booking|reservation)Id\s*:\s*[^\n]*Date\.now\(\)/);
  assert.equal(app.includes("booking-${"), false);
});

test("public display consumes the dedicated server projection", () => {
  assert.match(app, /api\.getPublicDisplay\(controller\.signal\)/);
  assert.equal(app.includes("maskDisplayName"), false);
  assert.equal(app.includes("derivePublicDisplayRows"), false);
});

test("shared-computer users have a visible logout action in personal settings", () => {
  assert.match(app, /className="settings-logout-button"[^>]*onClick=\{logout\}/);
  assert.equal(app.includes('className="sr-only" onClick={logout}'), false);
});

test("revision conflicts rebase the drawer baseline while preserving the draft", () => {
  assert.match(app, /rebaseBookingEdit\(bookingForm, error\.current\)/);
  assert.match(app, /setDrawer\(\(current\) => \(\{ \.\.\.current, booking: rebased\.baseline \}\)\)/);
  assert.match(app, /setBookingForm\(rebased\.draft\)/);
});

test("change notifications start the same due-reminder poller", () => {
  assert.match(app, /bookingReminder && !bootstrap\?\.preferences\?\.bookingChangeNotifications/);
});

test("acknowledging a change refreshes affected views and fetches the next notice", () => {
  assert.match(app, /acknowledged\.kind === "change"/);
  assert.match(app, /Promise\.all\(\[loadCalendar\(\), loadUpcoming\(\), loadHistory\(\)\]\)/);
  assert.match(app, /const result = await api\.getDueReminders\(\)/);
  assert.match(app, /reminderDisplayMessage\(dueReminder\)/);
});

test("the frozen empty-state action consumes a valid default room", () => {
  assert.match(app, /defaultRoomId\) \|\| activeRooms\[0\]/);
  assert.match(app, /onClick=\{openDefaultCreate\}>前往预约日历/);
  assert.match(app, /openCreate\(preferredRoom\.id, start, dayKey\)/);
});

test("system recovery has a dedicated gate with recovery and request references", () => {
  assert.match(app, /function RecoveryScreen/);
  assert.match(app, /SYSTEM_RECOVERY_REQUIRED/);
  assert.match(app, /error\?\.recoveryCode/);
  assert.match(app, /error\.requestId/);
  assert.match(app, /phase === "recovery"/);
});

test("reservation and history views consume opaque cursor pages", () => {
  assert.match(app, /fetchAllReservations/);
  assert.match(app, /page\?\.nextCursor/);
  assert.match(app, /pageSize: 100, cursor/);
  assert.match(app, /loadHistory\(\{ append: true, cursor: historyPage\.nextCursor \}\)/);
  assert.match(app, /history-count">\{historyPage\.total\} 场/);
});

test("reservation details separate edit and cancellation capabilities", () => {
  assert.match(app, /canEdit=\{!drawer\.readOnly && canManage && booking\.canEdit === true\}/);
  assert.match(app, /canCancel=\{!drawer\.readOnly && canManage && booking\.canCancel === true\}/);
  assert.match(app, /\{canEdit && <button className="edit-booking-button"/);
  assert.match(app, /\{canCancel && <button className="cancel-booking-button"/);
});

test("booking details load the authenticated event timeline", () => {
  assert.match(app, /api\.getReservationEvents\(booking\.id\)/);
  assert.match(app, /className="booking-event-timeline"/);
  assert.match(app, /role === "admin" \|\| booking\.ownerId === currentUser\.id/);
  assert.match(app, /event\.occurredAt \|\| event\.occurredAtUtc/);
});

test("system audit exposes every stable filter and refreshes on a timer", () => {
  for (const field of ["action", "outcome", "actorId", "targetType", "targetId", "dateFrom", "dateTo"]) {
    assert.match(app, new RegExp("auditFilters\\." + field));
  }
  assert.match(app, /api\.getAudit/);
  assert.match(app, /window\.setInterval\(\(\) => Promise\.all\(\[loadSystem\(true\), loadAudit\(\{ silent: true, preserveLoaded: true \}\), loadTokens\(true\)\]\), 30000\)/);
  assert.match(app, /item\.occurredAtUtc/);
  assert.match(app, /auditPage\.total/);
});

test("public display advances server time and distinguishes stale from offline", () => {
  assert.match(app, /projectServerClock\(clockAnchor, clockTick\)/);
  assert.match(app, /setClockAnchor\(\{ serverDate: next\.serverDate, serverTime: next\.serverTime, receivedAt \}\)/);
  assert.match(app, /failureCountRef\.current >= 3/);
  assert.match(app, /age >= 90000/);
  assert.match(app, /controller\.abort\(\), 10000/);
});

test("diagnostic download uses generated UTC time and delayed URL release", () => {
  assert.match(app, /diagnostic\.generatedAtUtc/);
  assert.match(app, /window\.setTimeout\(\(\) => URL\.revokeObjectURL\(url\), 1000\)/);
});

test("token revocation waits for the server before refreshing or removing UI", () => {
  assert.match(app, /await api\.revokeToken\(token\.id\);\s+await Promise\.all\(\[loadTokens\(true\), loadAudit/);
  assert.match(app, /正在等待服务器确认/);
});

test("filter races cannot let stale history or audit responses win", () => {
  assert.match(app, /historyRequestRef\.current !== requestNumber/);
  assert.match(app, /auditRequestRef\.current !== requestNumber/);
});

test("logout only leaves the authenticated shell after server confirmation", () => {
  assert.match(app, /await api\.logout\(\);\s+onLoggedOut\(\)/);
  assert.match(app, /退出失败，请确认网络后重试/);
  assert.match(app, /SYSTEM_RECOVERY_REQUIRED"\) onRecovery\(error\)/);
});

test("production drawers and history reuse the frozen visual contract classes", () => {
  for (const className of ["booking-form-scroll", "booking-create-summary", "booking-schedule-fields", "booking-information-section", "tag-choice-grid", "booking-form-footer", "history-date-anchor", "history-booking-summary", "history-row-end", "user-form", "room-form-actions"]) {
    assert.ok(app.includes(className), `missing frozen class ${className}`);
  }
  assert.match(app, /drawer\?\.type\?\.startsWith\("user"\) \? "user-drawer-open"/);
});

test("authenticated bootstrap data hydrates the frozen roster and room views immediately", () => {
  assert.match(app, /useState\(\(\) => initialBootstrap\?\.users \|\| \[\]\)/);
  assert.match(app, /useState\(\(\) => initialBootstrap\?\.rooms \|\| \[\]\)/);
  assert.match(app, /useState\(\(\) => initialBootstrap\?\.preferences \|\| null\)/);
});

test("mine, history, and users expose the frozen visual tools", () => {
  assert.match(app, /aria-label="筛选我的预约"/);
  assert.match(app, /className="booking-filter-popover"/);
  assert.match(app, /aria-label="搜索预约记录"/);
  assert.match(app, /className="history-search-popover"/);
  assert.match(app, /className="history-month-button"/);
  assert.match(app, /className="history-filter-section history-scope-section"/);
  assert.match(app, /className="history-scope-options"/);
  assert.match(app, /className="history-personal-helper"/);
  assert.match(app, /historyUserSelectRef\.current\?\.focus\(\)/);
  assert.match(app, /加载\{previousHistoryMonth\.label\}的记录/);
  assert.match(app, /aria-label=\{userSearchOpen \? "关闭搜索" : "搜索用户"\}/);
});

test("first-run setup keeps the frozen safety and summary hierarchy without demo copy", () => {
  for (const className of ["setup-safety-list", "setup-password-section", "setup-rule-list", "setup-confirm-list"]) {
    assert.ok(app.includes(className), `missing setup visual contract class ${className}`);
  }
  assert.match(app, /不会读取或迁移旧版本账号、预约或数据库/);
  assert.doesNotMatch(app, /合成内存数据|不连接正式数据库/);
});

test("setup field errors return to and identify their visible wizard controls", () => {
  assert.match(app, /mapSetupFieldErrors\(error\.fields, error\.message\)/);
  assert.match(app, /if \(mapped\.step !== null\) setStep\(mapped\.step\)/);
  assert.ok(app.includes("errors[field]"));
  assert.ok(app.includes("errors.workEnd"));
  assert.ok(app.includes("setup-room-${index}-error"));
});

test("setup completion waits for the replacement listener and transitions automatically", () => {
  assert.match(app, /runSetupRestartTransition\(\{/);
  assert.match(app, /onReady: \(\) => \{ if \(!cancelled\) onComplete\(\); \}/);
  assert.match(app, /<SetupRestartStatus/);
  assert.match(setupRestart, /waitForSetupRestart\(\{/);
  assert.match(setupRestart, /onState\("failed"\)/);
  assert.match(setupRestart, /重新检查服务/);
});

test("reauthentication remounts scoped state only after session and bootstrap validation", () => {
  assert.match(app, /const context = await reauthenticateContext\(api, credentials\)/);
  assert.match(authFlow, /await client\.login/);
  assert.match(authFlow, /return readAuthenticatedContext\(client\)/);
  assert.match(app, /validateAuthenticatedContext\(context\?\.session, context\?\.bootstrap\)/);
  assert.match(app, /setScopeVersion\(\(current\) => current \+ 1\)/);
  assert.match(app, /key=\{scopedAppKey\(session, scopeVersion\)\}/);
  assert.match(app, /<SessionIsolationBoundary[\s\S]*blocked=\{sessionExpired\}/);
  assert.match(app, /reauthentication=\{<SessionExpired/);
  assert.match(app, /!sessionExpired && isDrawerAllowed\(drawer\?\.type, permissions\)/);
  assert.match(app, /catch \(caught\) \{[\s\S]*setError\(userFacingError\(caught/);
  assert.doesNotMatch(app, /catch \(caught\) \{[\s\S]{0,240}onRecovered/);
});

test("editing another user's booking resolves personal tags from the owner", () => {
  assert.match(app, /bookingTagContext\(\{/);
  assert.match(app, /booking: drawer\?\.type === "edit" \? drawer\.booking : null/);
  assert.match(app, /globalTags: bootstrap\?\.globalTags/);
  assert.match(app, /users,/);
  assert.match(app, /原预约者的个人标签暂不可用/);
});

test("resetting the signed-in administrator password enters reauthentication immediately", () => {
  assert.match(app, /result\?\.reauthenticate[\s\S]*setSessionExpired\(true\)/);
});

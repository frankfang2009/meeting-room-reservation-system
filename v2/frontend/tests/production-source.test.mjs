import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const app = fs.readFileSync(path.join(root, "src/App.jsx"), "utf8");
const productionSource = fs.readdirSync(path.join(root, "src"), { recursive: true })
  .filter((relative) => /\.(?:js|jsx)$/.test(relative))
  .map((relative) => fs.readFileSync(path.join(root, "src", relative), "utf8"))
  .join("\n");
const authFlow = fs.readFileSync(path.join(root, "src/auth-flow.js"), "utf8");
const setupRestart = fs.readFileSync(path.join(root, "src/setup-restart.js"), "utf8");
const adminForms = fs.readFileSync(path.join(root, "src/features/admin/AdminForms.jsx"), "utf8");
const historyStyles = fs.readFileSync(path.join(root, "src/styles/history.css"), "utf8");

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
  assert.match(productionSource, /className="settings-logout-button"[^>]*onClick=\{onLogout\}/);
  assert.equal(app.includes('className="sr-only" onClick={logout}'), false);
});

test("personal center keeps a concise real summary without heatmap clutter", () => {
  assert.match(app, /api\.getActivity\(\)/);
  assert.match(app, /activeView === "settings" && profileTab === "activity"/);
  assert.match(productionSource, /<h1>个人中心<\/h1>/);
  assert.match(productionSource, />我的活动<\/button>/);
  assert.match(productionSource, />偏好设置<\/button>/);
  assert.match(productionSource, /<h2 id="profile-overview-heading">活动概览<\/h2>/);
  assert.match(productionSource, /<h2 id="profile-data-heading">活动数据<\/h2>/);
  assert.doesNotMatch(productionSource, /预约活动|profile-heatmap|activityMonths|getActivityDay|本人当天已完成的预约/);
});

test("personal center exposes preferences through one tab entry", () => {
  assert.equal(productionSource.match(/>偏好设置<\/button>/g)?.length, 1);
  assert.doesNotMatch(productionSource, /personal-center-preference-link/);
});

test("drawers isolate the background and focus their first visible field", () => {
  assert.match(app, /useLayoutEffect\(\(\) => \{/);
  assert.match(app, /backgroundRef\?\.current/);
  assert.match(app, /background\?\.setAttribute\("inert", ""\)/);
  assert.match(app, /window\.requestAnimationFrame\(\(\) => first\?\.focus/);
  assert.match(app, /first\?\.focus\(\{ preventScroll: true \}\)/);
  assert.match(app, /backgroundRef=\{mainRef\}/);
});

test("personal preferences expose real scoped defaults and server-backed personal tags", () => {
  assert.match(app, /readUiPreferences\(initialBootstrap\?\.currentUser\?\.id \|\| session\.currentUser\?\.id\)/);
  assert.match(app, /writeUiPreferences\(currentUser\.id, uiPreferencesDraft\)/);
  assert.match(productionSource, />登录后默认打开</);
  assert.doesNotMatch(productionSource, />活动图显示范围</);
  assert.match(productionSource, />个人标签</);
  assert.match(productionSource, /onChange\("personalTags"/);
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
  assert.match(app, /Promise\.all\(\[loadCalendar\(\), loadUpcoming\(\), loadHistory\(\), loadRooms\(\{ silent: true \}\)\]\)/);
  assert.match(app, /const result = await api\.getDueReminders\(\)/);
  assert.match(app, /reminderDisplayMessage\(dueReminder\)/);
});

test("upcoming reminders use the mine navigation clock badge instead of a bottom toast", () => {
  assert.match(app, /id === "mine" && dueReminder\?\.kind === "upcoming"/);
  assert.match(app, /className="rail-reminder-badge"/);
  assert.match(app, /<Clock size=\{11\} weight="fill"/);
  assert.match(app, /openMineAndAcknowledgeReminder/);
  assert.match(app, /dueReminder\?\.kind === "change" && <div className="toast visible reminder-toast"/);
  assert.doesNotMatch(app, /\{dueReminder && <div className="toast visible reminder-toast"/);
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
    assert.ok(productionSource.includes(className), `missing frozen class ${className}`);
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
  assert.match(app, /className="history-choice-options history-scope-options"/);
  assert.match(app, /className="history-choice-options history-status-options"/);
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

test("duration slider visual thumb shares the inset track coordinate system", () => {
  assert.match(app, /duration-slider-track[\s\S]{0,240}duration-slider-knob[\s\S]{0,160}<\/div><input className="duration-range-input"/);
});

test("room deletion is confirmed and blocking bookings lead directly to adjustment", () => {
  assert.match(adminForms, /className="room-delete-button"/);
  assert.match(adminForms, /先调整预约，再删除/);
  assert.match(adminForms, /调整预约/);
  assert.match(app, /useFocusTrap\(ref, open, onClose, true, heading, backgroundRef\)/);
  assert.match(app, /api\.getRoomDeletionImpact\(room\.id\)/);
  assert.match(app, /roomDeletionDrawer\(room, impact\)/);
  assert.match(app, /error\.code === "ROOM_HAS_FUTURE_BOOKINGS"/);
  assert.match(app, /bookings: error\.conflicts/);
  assert.match(app, /booking\.canEdit \? openEdit\(booking, returnTo\) : openDetails\(booking, false, returnTo\)/);
  assert.match(app, /aria-label="返回待处理预约"/);
  assert.match(app, /onBack=\{drawer\?\.returnTo \? \(\) => setDrawer\(drawer\.returnTo\) : null\}/);
  assert.match(app, /refreshRoomDeletionFlow\(returnTo\.room, returnTo\)/);
});

test("past server-local slots cannot open or submit a create flow", () => {
  assert.match(app, /hasBookingStarted\(\{[\s\S]{0,180}serverDate: businessClock\.date/);
  assert.match(app, /disabled=\{networkOffline \|\| slotStarted\}/);
  assert.match(app, /error\.code === "BOOKING_STARTED"[\s\S]{0,500}setPreservedDraft\(bookingForm\)/);
  assert.match(app, /drawer\.type === "edit"[\s\S]{0,180}预约已经开始，不能再修改/);
  assert.match(app, /预约内容已保留。请选择当前时间之后的空白时段/);
});

test("duration availability stops at the next room booking without shrinking the visual scale", () => {
  assert.match(app, /maximumAvailableDuration\(\{[\s\S]{0,260}excludeBookingId/);
  assert.match(app, /max=\{scaleMaximum\}/);
  assert.match(app, /Math\.min\(maximum, Number\(event\.target\.value\)\)/);
  assert.match(app, /--duration-available-progress/);
});

test("calendar time labels and the server-time line share the schedule coordinate system", () => {
  assert.match(app, /calendarTimeLineOffset\(\{/);
  assert.match(app, /className="current-time-line"/);
  assert.match(app, /aria-label=\{`当前时间/);
});

test("calendar supports direct date jumps and adaptive room-count layouts", () => {
  assert.match(app, /type="date"[^>]*aria-label="跳转到日期"/);
  assert.match(app, /value=\{dateKey\(currentDate\)\}/);
  assert.match(app, /className="calendar-day-navigation" role="group" aria-label="日期导航"/);
  assert.match(app, /className=\{`calendar-date-picker/);
  assert.match(app, /dateKey\(currentDate\)\.replaceAll\("-", "\/"\)/);
  assert.match(app, /calendar-room-count-\$\{activeRooms\.length\}/);
  assert.match(app, /room-count-\$\{orderedRooms\.length\}/);
});

test("cancelled history rows and details use a restrained Chinese status projection", () => {
  assert.match(app, /history-row \$\{booking\.status === "cancelled" \? "cancelled" : ""\}/);
  assert.match(app, /className="history-cancelled-status">已取消/);
  assert.match(app, /reservationStatusLabel\(booking\.status\)/);
  assert.doesNotMatch(app, /booking\.status === "active" \? "已预约" : booking\.status/);
  assert.doesNotMatch(historyStyles, /\.history-row\.cancelled\s*\{/);
  assert.doesNotMatch(historyStyles, /\.history-row\.cancelled \.history-(?:date-anchor|time|room|booking-summary)/);
  assert.match(app, /historyStatus === "active"/);
  assert.match(app, /setHistoryStatus\("cancelled"\)/);
  assert.match(app, /预约状态/);
  assert.match(app, /正常预约/);
});

test("configured LAN address has a trusted-HTTP copy fallback", () => {
  assert.match(app, /system\?\.bindMode === "lan" && Boolean\(system\?\.lanAddress\)/);
  assert.match(app, /aria-label="复制局域网地址"/);
  assert.match(app, /await copyText\(system\.lanAddress\)/);
  assert.match(app, /document\.execCommand\("copy"\)/);
  assert.match(app, /局域网地址已复制，可以直接发送给员工/);
});

test("administrator room metrics refresh after booking changes and while visible", () => {
  assert.match(app, /unwrapItems\(await api\.getRooms\(\)\)/);
  assert.match(app, /activeView !== "rooms"[\s\S]{0,260}window\.setInterval\(\(\) => loadRooms\(\{ silent: true \}\), 30000\)/);
  assert.match(app, /loadCalendar\(\), loadUpcoming\(\), loadHistory\(\), loadRooms\(\{ silent: true \}\)/);
});

test("security audit is Chinese, collapsible, and counts newly received rows", () => {
  assert.match(app, /"room\.created": "创建笔录室"/);
  assert.match(app, /auditOutcomeLabel\(outcome\)/);
  assert.doesNotMatch(app, /title=\{item\.action\}/);
  assert.doesNotMatch(app, /<small>\{item\.action\}<\/small>/);
  assert.match(app, /auditHidden \? "显示" : "隐藏"/);
  assert.match(app, /setAuditUnreadCount\(\(current\) => current \+ newlyReceived\.length\)/);
  assert.match(app, /action: hidden \? "" : auditFilters\.action\.trim\(\)/);
  assert.match(app, /auditHiddenRef\.current = nextHidden/);
  assert.match(app, /if \(!nextHidden\) \{[\s\S]{0,120}setAuditUnreadCount\(0\);[\s\S]{0,80}loadAudit\(\)/);
  assert.match(app, /system-audit-unread/);
});

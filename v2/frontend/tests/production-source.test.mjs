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
const dataCenter = fs.readFileSync(path.join(root, "src/features/reports/DataCenter.jsx"), "utf8");
const bookingFormStyles = fs.readFileSync(path.join(root, "src/styles/booking-forms.css"), "utf8");
const historyStyles = fs.readFileSync(path.join(root, "src/styles/history.css"), "utf8");
const responsiveStyles = fs.readFileSync(path.join(root, "src/styles/responsive.css"), "utf8");
const settingsStyles = fs.readFileSync(path.join(root, "src/styles/settings.css"), "utf8");
const systemStyles = fs.readFileSync(path.join(root, "src/styles/system.css"), "utf8");
const systemExtensionStyles = fs.readFileSync(path.join(root, "src/styles/system-extensions.css"), "utf8");
const designContract = fs.readFileSync(path.join(root, "DESIGN-CONTRACT.md"), "utf8");

test("production entry contains no demo credentials or query-state router", () => {
  for (const forbidden of ["demo123", "demo1234", "URLSearchParams", "loginState=", "bookingState=", "feedbackState=", "displayState=", "dataState="]) {
    assert.equal(app.includes(forbidden), false, "forbidden production source: " + forbidden);
  }
});

test("login uses the project-owned neutral illustration", () => {
  assert.match(app, /\/assets\/login\/schedule-portal\.svg/);
  assert.equal(fs.existsSync(path.join(root, "public/assets/login/schedule-portal.svg")), true);
  assert.doesNotMatch(app, /doorway-time\.png/);
});

test("production entry does not generate business ids in the browser", () => {
  assert.equal(app.includes("crypto.randomUUID"), false);
  assert.doesNotMatch(app, /(?:booking|reservation)Id\s*:\s*[^\n]*Date\.now\(\)/);
  assert.equal(app.includes("booking-${"), false);
});

test("public display consumes the dedicated server projection", () => {
  assert.match(app, /api\.getPublicDisplay\(controller\.signal\)/);
  assert.match(app, /payload && payload\.rooms\.length === 0/);
  assert.match(app, />当前暂无公开引导的笔录室</);
  assert.equal(app.includes("maskDisplayName"), false);
  assert.equal(app.includes("derivePublicDisplayRows"), false);
});

test("shared-computer users have a visible logout action in personal settings", () => {
  assert.match(productionSource, /className="settings-logout-button"[^>]*onClick=\{onLogout\}/);
  assert.equal(app.includes('className="sr-only" onClick={logout}'), false);
});

test("every authenticated main canvas is a keyboard-scrollable focus target", () => {
  const canvases = productionSource.match(/<main className="main-canvas[^>]+>/g) || [];
  assert.ok(canvases.length >= 8);
  for (const canvas of canvases) assert.match(canvas, /tabIndex=\{0\}/);
});

test("login validation returns keyboard focus to the first correctable field", () => {
  assert.match(app, /const usernameRef = useRef\(null\)/);
  assert.match(app, /const passwordRef = useRef\(null\)/);
  assert.match(app, /errors\.username \? usernameRef\.current : errors\.password \? passwordRef\.current/);
  assert.match(app, /target\?\.focus\(\{ preventScroll: true \}\)/);
  assert.match(app, /aria-describedby=\{errors\.password \? "login-password-error"/);
  assert.match(app, /id="login-password-error"[^>]*role="alert"/);
});

test("personal center delegates all activity analysis to the data center", () => {
  assert.match(productionSource, /<h1>个人中心<\/h1>/);
  assert.match(productionSource, /<p>管理个人资料与工作偏好<\/p>/);
  assert.match(productionSource, /<form id="personal-settings-form" className="settings-layout" aria-label="个人设置"/);
  assert.doesNotMatch(app, /api\.getReportOverview|profileTab|loadActivity/);
  assert.doesNotMatch(productionSource, /我的活动|本月服务概览|活动数据|查看数据中心|profile-activity|profile-heatmap|activityMonths|getActivityDay/);
});

test("data center separates each analysis task into a focused page and removes the personnel list", () => {
  assert.match(dataCenter, /\{ id: "overview", label: "概览" \}, \{ id: "time", label: "时段分布" \}, \{ id: "rooms", label: "笔录室" \}, \{ id: "tags", label: "标签" \}/);
  assert.match(dataCenter, /role="tablist" aria-label="数据中心页面"/);
  assert.match(dataCenter, /<TimeDistribution items=\{report\?\.weekdayTimeDistribution\}/);
  assert.match(dataCenter, /<RoomDistribution items=\{report\?\.roomWorkload\}/);
  assert.match(dataCenter, /<TagComposition items=\{isOverall \? report\?\.globalTagDistribution : report\?\.tagDistribution\}/);
  assert.match(dataCenter, /report-time-cell/);
  assert.match(dataCenter, /cell\.count >= 2 \? cell\.count/);
  assert.match(dataCenter, /最高峰/);
  assert.match(dataCenter, /reportTrendModel\(report \|\| \{\}, report\?\.filters \|\| applied\)/);
  assert.match(dataCenter, /预约\{trend\.granularity === "month" \? "月" : "周"\}趋势/);
  assert.match(dataCenter, /const valueLabel = metric === "duration" \? duration\.value : item\.activeCount/);
  assert.match(dataCenter, /\{activeItem\.activeCount\}场 · \{activeDuration\.value\}\{activeDuration\.unit\}/);
  assert.doesNotMatch(dataCenter, /report-analysis-section|report-analysis-tabs/);
  assert.doesNotMatch(dataCenter, /personWorkload|工作负荷分布|report-person-workload/);
  assert.match(productionSource, /role !== "admin"\) return \{ scope: "self" \}/);
});

test("personal center uses a compact identity header above one settings surface", () => {
  assert.match(productionSource, /className="personal-center-header-side"/);
  assert.match(productionSource, /personal-center-header-side"><section className="personal-center-identity"/);
  assert.match(settingsStyles, /\.personal-center-canvas \.settings-layout \{[\s\S]*?margin-top: 32px;[\s\S]*?border-top: 1px solid var\(--hairline-strong\);/);
  assert.doesNotMatch(settingsStyles, /personal-center-tabs|profile-activity/);
  assert.doesNotMatch(responsiveStyles, /personal-center-tab-row|profile-activity/);
});

test("system work hours use a whole-row drawer entry with a separated footer", () => {
  assert.match(app, /<button type="button" className="system-status-row system-action-row system-work-hours-row"[^>]*onClick=/);
  assert.match(app, /system-work-hours-row"[\s\S]{0,500}<CaretRight size=\{17\}/);
  assert.doesNotMatch(app, /system-edit-settings/);
  assert.match(systemStyles, /\.system-action-row:focus-visible/);
  assert.match(systemExtensionStyles, /\.system-settings-form \.drawer-fixed-footer \{[\s\S]*?margin-top: auto;[\s\S]*?padding-top: 40px;/);
});

test("booking tag choices contain long custom labels without changing the compact row", () => {
  assert.match(app, /className=\{`tag-choice[\s\S]{0,260}title=\{tag\.label\}[\s\S]{0,160}<i \/><span>\{tag\.label\}<\/span><\/button>/);
  assert.match(bookingFormStyles, /\.booking-form \.tag-choice-grid \{[\s\S]*?grid-template-columns: repeat\(4, minmax\(0, 1fr\)\);/);
  assert.match(bookingFormStyles, /\.booking-form \.tag-choice span \{[\s\S]*?min-width: 0;[\s\S]*?overflow: hidden;[\s\S]*?text-overflow: ellipsis;/);
});

test("personal center opens settings directly without internal navigation", () => {
  assert.doesNotMatch(productionSource, /aria-label="个人中心页面"|personal-center-tab-row|personal-center-preference-link/);
  assert.match(productionSource, /form="personal-settings-form"/);
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
  assert.match(productionSource, />默认标签</);
  assert.match(app, /defaultBookingTagId\(\{[\s\S]{0,180}defaultTagSlot: bootstrap\.preferences\?\.defaultTagSlot,[\s\S]{0,100}draft/);
  assert.match(productionSource, /onChange\("personalTags"/);
});

test("revision conflicts rebase the drawer baseline while preserving the draft", () => {
  assert.match(app, /rebaseBookingEdit\(bookingForm, error\.current\)/);
  assert.match(app, /setDrawer\(\(current\) => \(\{ \.\.\.current, booking: rebased\.baseline \}\)\)/);
  assert.match(app, /setBookingForm\(rebased\.draft\)/);
});

test("conflict panels expose busy rechecks and explicit results", () => {
  assert.match(app, /async function recheckSlotConflict\(\)/);
  assert.match(app, /occupied \? "该时段仍被占用" : "该时段已可用，可以返回日历重新选择"/);
  assert.match(app, /booking-conflict-recheck" disabled=\{conflictCheck\.busy\} onClick=\{recheckSlotConflict\}/);
  assert.match(app, /booking-conflict-check-result[\s\S]{0,160}conflictCheck\.message/);
  assert.match(app, /async function recheckRevisionConflict\(\)/);
  assert.match(app, /await api\.getReservation\(drawer\.booking\.id\)/);
  assert.match(app, /预约仍有新的变化，已更新最新内容/);
  assert.match(app, /booking-modified-recheck" type="button" disabled=\{conflictCheck\.busy\}/);
  for (const action of ["使用最新内容", "返回继续调整", "重新检查"]) assert.match(app, new RegExp(action));
});

test("preserved booking drafts stay visible and require explicit relocation", () => {
  assert.match(app, /className="calendar-draft-notice"[^>]*aria-label="待续预约草稿"/);
  assert.match(app, /选择空白时段后，系统会先确认是否迁移这份草稿/);
  assert.match(app, /type: "draft-relocation"/);
  assert.match(app, />使用草稿预约此时段</);
  assert.match(app, />清除草稿并新建</);
  assert.match(app, /beginCreate\(\{ \.\.\.drawer\.target, draft: drawer\.draft \}\)/);
  assert.doesNotMatch(app, /\.\.\.\(preservedDraft \|\| \{\}\)/);
});

test("calendar loading isolates the previous date and retains its schedule frame", () => {
  assert.match(app, /const visibleCalendarBookings = calendarDataDate === dateKey\(currentDate\) \? bookings : \[\]/);
  assert.match(app, /calendarDataDateRef\.current !== requestedDate\) setBookings\(\[\]\)/);
  assert.match(app, /calendarDataDateRef\.current = requestedDate;\s+setCalendarDataDate\(requestedDate\)/);
  assert.match(app, /calendarPending && activeRooms\.length \? <div className="calendar-loading-state"/);
  assert.match(app, /className="calendar-loading-head"[\s\S]{0,180}activeRooms\.map/);
  assert.match(app, /className="calendar-loading-row"[\s\S]{0,220}calendar-loading-time/);
});

test("calendar success notices expire and clear when their context changes", () => {
  assert.match(app, /window\.setTimeout\(\(\) => setSuccessNotice\(null\), 8000\)/);
  assert.match(app, /setSuccessNotice\(null\);\r?\n {2}}, \[currentDate\]\)/);
  assert.match(app, /if \(view !== "calendar"\) setSuccessNotice\(null\)/);
  assert.match(app, /function openCreate[\s\S]{0,180}setSuccessNotice\(null\)/);
  assert.match(app, /function openEdit[\s\S]{0,180}setSuccessNotice\(null\)/);
  assert.match(app, /error\.code === "SLOT_CONFLICT"[\s\S]{0,120}setSuccessNotice\(null\)/);
});

test("toast tones map success, information, and errors to honest icons", () => {
  assert.match(app, /tone === "success" \? CheckCircle : tone === "error" \? WarningCircle : Info/);
  assert.match(app, /className=\{`toast visible \$\{toast\.tone\}`\} role="status" aria-live="polite"/);
  assert.match(app, /<ToastIcon tone=\{toast\.tone\} \/><span>\{toast\.message\}<\/span>/);
  assert.match(app, /setToast\(userFacingError\(error, fallback\), "error"\)/);
  assert.match(app, /该时段已经开始，请选择当前时间之后的空白时段", "error"/);
  assert.match(app, /个人设置已保存", "success"/);
});

test("the due-reminder poller stays on for handover requests regardless of switches", () => {
  // V2.4.0：交接请求是待办而非通知，两项提醒开关全关也保持轮询。
  assert.match(app, /\/\/ 交接请求是待办而非通知：即使两项提醒开关全关也保持轮询。/);
  assert.doesNotMatch(app, /if \(!bootstrap\?\.preferences\?\.bookingReminder && !bootstrap\?\.preferences\?\.bookingChangeNotifications\) \{[\s\S]{0,200}return undefined/);
});

test("reminder lead preference controls both visible copy and editability", () => {
  assert.match(productionSource, /开始前 \{draft\.reminderLeadMinutes \|\| 30\} 分钟提醒我/);
  assert.match(productionSource, /value=\{draft\.reminderLeadMinutes \|\| 30\}/);
  assert.match(productionSource, /disabled=\{!draft\.bookingReminder\}/);
  assert.match(productionSource, /\[15, 30, 60\]\.map/);
  assert.match(productionSource, /onChange\("reminderLeadMinutes", Number\(event\.target\.value\)\)/);
  assert.doesNotMatch(productionSource, /开始前30分钟提醒我/);
});

test("external reminder copy stays manual, scoped, and recoverable", () => {
  assert.match(app, /const canCopyReminder = canManage && booking\.status === "active" && !hasBookingStarted/);
  assert.match(app, /canCopyReminder && <button className="copy-reminder-button"/);
  assert.match(app, /renderReminderTemplate\(bootstrap\?\.preferences\?\.reminderTemplate/);
  assert.match(app, /提醒信息已复制，可在微信中粘贴发送", "success"/);
  assert.match(app, /无法自动复制，请手动复制提醒信息", "error"/);
  assert.match(productionSource, />对外提醒模板</);
  assert.match(productionSource, /仅复制到剪贴板，由您自行发送/);
  assert.match(productionSource, /maxLength=\{200\}/);
  assert.match(app, /setPreferencesDraft\(bootstrap\?\.preferences \|\| \{\}\)/);
  assert.doesNotMatch(app, /已发送|跳转微信|window\.open\([^)]*微信/);
});

test("acknowledging change notices is event-scoped and refreshes affected views", () => {
  assert.match(app, /api\.acknowledgeChangeNotice\(item\.eventId\)/);
  assert.match(app, /await refreshDueReminders\(\);/);
  assert.match(app, /Promise\.all\(\[loadCalendar\(\), loadUpcoming\(\), loadHistory\(\), loadRooms\(\{ silent: true \}\)\]\)/);
  assert.doesNotMatch(app, /REMINDER_NOT_DUE/);
  assert.doesNotMatch(app, /reminderDisplayMessage/);
});

test("upcoming reminders live in the calendar with a counting badge and a one-time arrival toast", () => {
  // 临近提醒是状态而非待办：徽章显示计数，不再有确认动作。
  assert.match(app, /const badgeCount = id === "mine" \? dueReminders\.upcoming\.length : id === "handovers" \? handoverBoard\.incoming\.length : 0/);
  assert.match(app, /\{badgeCount > 9 \? "9\+" : badgeCount\}/);
  // 画进日历：预约块上的倒计时角标与紧急态。
  assert.match(app, /const countdown = countdownFor\(booking\)/);
  assert.match(app, /countdown && <span className="booking-countdown"/);
  assert.match(app, /slot-countdown-urgent/);
  // 浏览其他日期时「今天」按钮带小圆点。
  assert.match(app, /has-today-dot/);
  // 到达时刻只做一次性 toast（查看/知道了），自动消失。
  assert.match(app, /arrivalNotice && !drawer && <div className="toast visible reminder-toast arrival-toast"/);
  assert.match(app, /arrivalReminderText\(fresh\[0\]\)/);
  assert.match(app, /playArrivalChime\(\)/);
  // 变更通知不再是底部常驻 toast，也没有 upcoming 的确认语义。
  assert.doesNotMatch(app, /dueReminder\?\.kind === "change" && <div className="toast/);
  assert.doesNotMatch(app, /openMineAndAcknowledgeReminder/);
});

test("handover requests ride the action modal, the dedicated page, and the details drawer", () => {
  assert.match(app, /const visibleHandoverReminders = dueReminders\.handovers\.filter/);
  assert.match(app, /const noticeOnlyHandovers = noticeHasHandovers && dueReminders\.changes\.length === 0/);
  assert.match(app, /decideHandover\(item\.handoverRequestId, "accept"\)/);
  assert.match(app, /decideHandover\(item\.handoverRequestId, "decline"\)/);
  assert.match(app, /deferVisibleHandovers/);
  assert.match(app, /稍后处理/);
  assert.match(app, /const canHandover = !handoverPending && booking\.status === "active" && !hasBookingStarted/);
  assert.match(app, /drawer\.type === "handover"/);
  assert.match(app, /api\.getUserDirectory\(drawer\.booking\.id\)/);
  assert.match(app, /filter\(\(user\) => user\.id !== drawer\.booking\.ownerId\)/);
  assert.match(app, /aria-pressed=\{drawer\.selectedUserId === user\.id\}/);
  assert.match(app, /disabled=\{!selectedUser \|\| handoverActionBusy\}/);
  assert.match(app, /sendHandover\(booking\.id, selectedUser\.id\)/);
  assert.match(app, /toUserId === currentUser\.id \? "已指派，预约已转入您名下"/);
  assert.doesNotMatch(app, /onClick=\{\(\) => void sendHandover\(booking\.id, user\.id\)\}/);
  assert.match(app, /className="handover-picker-summary"/);
  assert.match(app, /交给谁？/);
  assert.match(app, /确认后将立即完成指派，无需对方确认/);
  assert.match(app, /返回预约详情/);
  assert.match(app, /\{ id: "handovers", label: "工作交接", Icon: ArrowsLeftRight \}/);
  assert.match(app, /function renderHandovers\(\)/);
  assert.match(app, /className="main-canvas handover-canvas"/);
  assert.match(app, /activeView === "handovers" && renderHandovers\(\)/);
  assert.doesNotMatch(app, /className="handover-board" aria-label="工作交接"/);
  assert.match(app, /handoverPending/);
  assert.match(app, /withdrawHandoverRequest\(request\.id\)/);
  assert.match(app, /reservationEventLabel/);
  assert.match(productionSource, /handover: "预约已交接"/);
  assert.match(productionSource, /预约者由 \$\{from\} 交接给 \$\{to\}/);
  assert.match(app, /booking\.handoverState === "pending" \? "交接中" : "已交接"/);
  assert.match(app, /booking\.status === "cancelled" && <span className="history-cancelled-status">已取消<\/span>/);
  assert.match(app, /booking\.status === "active" && booking\.handoverState && <span className=\{`history-handover-status/);
});

test("change notices require an explicit centered modal with field diffs and drawer deferral", () => {
  assert.match(app, /const noticeModalOpen = \(dueReminders\.changes\.length > 0 \|\| noticeHasHandovers\) && !drawer && !sessionExpired;/);
  assert.match(app, /useFocusTrap\([\s\S]{0,100}noticeModalRef,[\s\S]{0,420}mainRef,/);
  assert.match(app, /<\/div>\s*\{drawer && \(dueReminders\.changes\.length > 0[\s\S]*\{noticeModalOpen && <div className="notice-modal-layer"><section ref=\{noticeModalRef\}/);
  assert.match(app, /role="alertdialog" aria-modal="true" aria-labelledby="notice-modal-heading" aria-describedby="notice-modal-hint" aria-busy=\{noticeAckBusy\}/);
  assert.match(app, /noticeDiffRows\(item\.diffs\)/);
  assert.match(app, /noticeIdentitySummary\(item\)/);
  assert.match(app, />当事人<\/dt><dd>\{identity\.partyName\}/);
  assert.match(app, />事项<\/dt><dd>\{identity\.purpose\}/);
  assert.match(app, />原预约<\/dt><dd>\{identity\.originalSchedule/);
  assert.match(app, /你的预约发生了 <em>\{diffRows\.length\}<\/em> 项变更/);
  assert.match(app, /item\.changeType === "cancelled"/);
  assert.match(app, /if \(!noticeAckBusy\) void acknowledgeChangeNotices\(dueReminders\.changes\)/);
  assert.match(app, /disabled=\{noticeAckBusy\}[\s\S]{0,220}>\{noticeAckBusy \? "正在确认…" : "我知道了"\}/);
  // 抽屉打开期间排队，关闭后立即出现；排队期间只有安静的信息条。
  assert.match(app, /\{drawer && \(dueReminders\.changes\.length > 0 \|\| noticeHasHandovers\) && <div className="notice-queue-chip" role="status" aria-live="polite"/);
  assert.match(app, /关闭预约详情后自动打开/);
});

test("mixed handover and change notices share one scroll body and keep actions independent", () => {
  assert.match(app, /const noticeMixed = noticeHasHandovers && dueReminders\.changes\.length > 0/);
  assert.match(app, /className="notice-modal-body"/);
  assert.match(app, /id="notice-handover-section">\u5de5\u4f5c\u4ea4\u63a5/);
  assert.match(app, /id="notice-change-section">\u9884\u7ea6\u53d8\u66f4/);
  assert.match(app, /className="notice-modal-combined-foot"/);
  assert.match(app, />\u4ea4\u63a5\u7a0d\u540e\u5904\u7406<\/button>/);
  assert.match(app, /"\u786e\u8ba4\u5168\u90e8\u53d8\u66f4"/);
  assert.match(app, /data-initial-focus disabled=\{noticeAckBusy\} onClick=\{deferVisibleHandovers\}/);
  assert.match(app, /const noticeQueueLabel = dueReminders\.changes\.length > 0 && noticeHasHandovers/);
  assert.match(app, /\$\{dueReminders\.changes\.length\} 条预约变更、\$\{visibleHandoverReminders\.length\} 条工作交接待处理/);
});

test("the frozen empty-state action consumes a valid default room", () => {
  assert.match(app, /defaultRoomId\) \|\| activeRooms\[0\]/);
  assert.match(app, /onClick=\{openDefaultCreate\}>\{!activeRooms\.length/);
  assert.match(app, /openCreate\(preferredRoom\.id, start, dayKey\)/);
});

test("low-frequency UX safeguards remain explicit and testable", () => {
  assert.match(app, /<div ref=\{mainRef\} className="app-main-region">[\s\S]*<\/div>\s*\{drawer && \(dueReminders\.changes\.length > 0[\s\S]*\{noticeModalOpen/);
  assert.match(app, /<Drawer[\s\S]{0,500}backgroundRef=\{mainRef\}/);
  assert.match(app, /historyMonth <= historyMonths\.at\(-1\)\.id/);
  assert.match(app, /nextMonth < earliestMonth \|\| nextMonth > latestMonth/);
  assert.match(app, /min=\{calendarDateMinimum\} max=\{calendarDateMaximum\}/);
  assert.match(app, /name="history-room"/);
  assert.match(app, /name="history-tag"/);
  assert.doesNotMatch(app, /name="history-(?:room|tag)"[^>]*type="checkbox"/);
  assert.match(productionSource, /className="settings-save-button"[^>]*disabled=\{saving\}/);
  assert.match(productionSource, /name="profile-name"[^>]*aria-invalid=\{Boolean\(errors\.name\)\}/);
  assert.match(app, /booking-events-error[^>]*>暂时无法读取变更记录<button type="button" onClick=\{onRetryEvents\}>重新读取/);
  assert.match(app, /validateBookingForm\(bookingForm, settings\.slotMinutes\)/);
  assert.match(app, /当前没有可用笔录室，请联系管理员启用后再预约/);
  assert.match(app, /if \(!preferredRoom\) \{[\s\S]{0,420}return;[\s\S]{0,80}navigate\("calendar"\)/);
  assert.match(app, /calendarAutoScrollRef = useRef\(\{ inCalendar: false, day: "", requested: 0, handled: 0 \}\)/);
  assert.match(app, /if \(!state\.requested \|\| state\.handled === state\.requested\) return undefined/);
  assert.match(app, /state\.handled = state\.requested/);
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
  assert.match(app, /loadHistory\(\{ append: true, cursor: section\.nextCursor, month: section\.id \}\)/);
  assert.match(app, /history-count">\{historyPage\.total\} 场/);
});

test("history appends older months in place with the same filters and dividers", () => {
  assert.match(app, /api\.getHistory\(\{ month, ownerId:[\s\S]{0,260}roomId: historyRoom, status: historyStatus, tagId: historyTag, query: historyQuery\.trim\(\), pageSize: 50, cursor \}\)/);
  assert.match(app, /loadHistory\(\{ append: true, month: monthKey\(new Date\(year, month - 2, 1\)\) \}\)/);
  assert.match(app, /className="history-month-divider" role="separator">\{section\.label\}/);
  assert.match(app, /historySections\.map\(\(section, sectionIndex\)/);
  assert.match(app, /onClick=\{loadPreviousHistoryMonth\}/);
  assert.doesNotMatch(app, /onClick=\{\(\) => setHistoryMonth\(previousHistoryMonth\.id\)\}/);
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
  assert.match(app, /await api\.logout\(\);[\s\S]{0,120}clearSessionBookingDraft[\s\S]{0,120}onLoggedOut\(\)/);
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

test("non-modal popovers share escape, outside-click, and focus-return behavior", () => {
  assert.match(app, /function useDismissiblePopover\(active, onClose\)/);
  assert.match(app, /document\.addEventListener\("pointerdown", handlePointerDown\)/);
  assert.match(app, /event\.key !== "Escape"/);
  assert.match(app, /triggerRef\.current\?\.focus\(\{ preventScroll: true \}\)/);
  for (const name of ["bookingFilterPopover", "calendarFilterPopover", "historySearchPopover", "historyFilterPopover", "historyMonthPopover", "userSearchPopover", "auditFilterPopover"]) {
    assert.match(app, new RegExp(`ref=\\{${name}\\.triggerRef\\}`));
    assert.match(app, new RegExp(`ref=\\{${name}\\.popoverRef\\}`));
  }
  assert.ok((app.match(/aria-haspopup="true"/g) || []).length >= 7);
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

test("expired sessions preserve and restore an account-scoped booking draft", () => {
  assert.match(app, /本标签页内的预约草稿已保留，使用同一账号重新登录后可以恢复/);
  assert.match(app, /writeSessionBookingDraft\(window\.sessionStorage, latest\?\.userId/);
  assert.match(app, /consumeSessionBookingDraft\([\s\S]{0,80}window\.sessionStorage/);
  assert.match(app, /setToast\("已恢复未保存的预约草稿", "info"\)/);
  assert.match(app, /clearSessionBookingDraft\(window\.sessionStorage, currentUser\?\.id\)/);
});

test("approved F19 decisions keep production UI and the frozen contract aligned", () => {
  assert.match(app, /<p>\{dateSubtitle \|\| dateLabel\(booking\.date\)\}<\/p>\{booking\.status && <span className=\{`drawer-status \$\{booking\.status\}`\}/);
  assert.match(app, /const withRelativeDay = \(dateText\) =>/);
  assert.match(app, /relativeDayLabel\(dateText, businessClock\.date\)/);
  assert.match(designContract, /adjacent status badge remains visible/);
  for (const width of [960, 720, 620]) {
    assert.match(responsiveStyles, new RegExp(`@media \\(max-width: ${width}px\\)`));
  }
  assert.match(designContract, /960px, 720px, and 620px rules are defensive fallbacks/);
  assert.match(designContract, /本标签页内的预约草稿已保留，使用同一账号重新登录后可以恢复/);
});

test("editing another user's booking resolves personal tags from the owner", () => {
  assert.match(app, /bookingTagContext\(\{/);
  assert.match(app, /booking: drawer\?\.type === "edit" \? drawer\.booking : null/);
  assert.match(app, /globalTags: bootstrap\?\.globalTags/);
  assert.match(app, /users,/);
  assert.match(app, /原预约者的个人标签暂不可用/);
});

test("resetting the signed-in administrator password enters reauthentication immediately", () => {
  assert.match(app, /result\?\.reauthenticate[\s\S]*expireSession\(\)/);
});

test("duration slider visual thumb shares the inset track coordinate system", () => {
  assert.match(app, /duration-slider-track[\s\S]{0,1800}duration-slider-knob[\s\S]{0,160}<\/div><input className="duration-range-input"/);
});

test("duration slider renders four interior stops with dynamic availability states", () => {
  assert.match(app, /DURATION_STEPS\.slice\(1, -1\)\.map/);
  assert.match(app, /step > maximum \? "unavailable" : step <= Number\(form\.duration\) \? "reached" : "available"/);
  assert.match(app, /className=\{`duration-slider-stop \$\{state\} \$\{step === Number\(form\.duration\) \? "selected" : ""\}`\}/);
  assert.match(app, /disabled=\{busy \|\| step > maximum\}/);
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
  assert.match(app, /const unavailable = slotStarted \|\| outsideWorkHours/);
  assert.match(app, /disabled=\{networkOffline \|\| unavailable\}/);
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

test("system status offers a quiet administrator work-hours editor", () => {
  assert.match(app, /system-work-hours-row/);
  assert.match(app, /type: "system-settings"/);
  assert.match(app, /api\.updateSystemSettings/);
  assert.match(app, /validateSystemSettingsForm\(drawer\.form, settings\.slotMinutes\)/);
  assert.match(app, /已有预约保持不变/);
  assert.match(app, /outside-work-slot/);
  assert.match(app, /slots: workingTimeSlots/);
});

test("macOS edition update notice stays notice-only and administrator-visible", () => {
  assert.match(app, /checkForUpdate/);
  assert.match(app, /api\.checkForUpdate/);
  assert.match(app, /<strong>正在检查更新…<\/strong>/);
  assert.match(app, /system-update-badge/);
  assert.match(app, /有新版本 \$\{system\.updateCheck\.latestVersion\}/);
  assert.match(app, /system\?\.updateCheck\?\.enabled &&/);
  assert.match(app, /rel="noreferrer"/);
  assert.match(app, /system-update-check/);
  // 提示止步于“告知”：没有自动下载、自动安装或任何外发动作。
  assert.doesNotMatch(app, /downloadUpdate|installUpdate|autoUpdate/);
  assert.match(systemStyles, /\.system-update-badge/);
  assert.match(systemStyles, /\.system-update-check/);
  // 陈旧知识规则：只有服务端 status=current 才显示“已是最新版本”；
  // 检查过但状态未知时显示中性文案，绝不用成功语义掩盖失败。
  assert.match(app, /status === "current" \? <strong>已是最新版本<\/strong>/);
  assert.match(app, /<strong>暂时无法确认版本<\/strong>/);
  assert.match(app, /<strong>尚未检查更新<\/strong>/);
});

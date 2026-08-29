import assert from "node:assert/strict";
import test from "node:test";
import {
  bookingTagContext,
  bookingPayload,
  calendarFocusTarget,
  calendarTimeSlots,
  calendarTimeLineOffset,
  canManageBooking,
  canViewBookingDetails,
  clampDurationToWorkday,
  dateKey,
  defaultBookingTagId,
  findFirstAvailableStart,
  generateTimeSlots,
  hasBookingStarted,
  isDrawerAllowed,
  isWithinWorkingHours,
  isSameBooking,
  maximumAvailableDuration,
  projectServerClock,
  rebaseBookingEdit,
  reservationConflictDifferences,
  arrivalReminderText,
  bookingCountdownMinutes,
  noticeDiffRows,
  noticeIdentitySummary,
  reservationEventLabel,
  mapSetupFieldErrors,
  setupStepForField,
  shiftDateByYears,
  userFacingError,
  validateAuthenticatedContext,
  validateBookingForm,
  validateSetupUsername,
  waitForSetupRestart,
} from "../src/domain.js";
import {
  handoverLedgerSections,
  relativeDayLabel,
  reservationEventSummary,
  reservationStatusLabel,
} from "../src/ui/presentation.js";

test("reservation statuses are always projected as Chinese UI copy", () => {
  assert.equal(reservationStatusLabel("active"), "已预约");
  assert.equal(reservationStatusLabel("cancelled"), "已取消");
  assert.equal(reservationStatusLabel("unexpected"), "状态未知");
});

test("handover event copy distinguishes the reservation owner from the party", () => {
  assert.equal(reservationEventSummary({
    type: "handover",
    before: { ownerName: "林晨" },
    after: { ownerName: "周宁" },
  }), "预约者由 林晨 交接给 周宁");
});

test("handover ledger omits empty groups and preserves incoming-first order", () => {
  const incoming = [{ id: "incoming-1", fromUser: { name: "刘敏" } }];
  const outgoing = [{ id: "outgoing-1", toUser: { name: "王芳" } }];

  assert.deepEqual(handoverLedgerSections(), []);
  assert.deepEqual(
    handoverLedgerSections({ incoming }).map(({ id, eyebrow }) => ({ id, eyebrow })),
    [{ id: "incoming", eyebrow: "待我确认 · 来自 刘敏" }],
  );
  assert.deepEqual(
    handoverLedgerSections({ outgoing }).map(({ id, eyebrow }) => ({ id, eyebrow })),
    [{ id: "outgoing", eyebrow: "我发起的 · 等待 王芳确认" }],
  );
  assert.deepEqual(
    handoverLedgerSections({ incoming, outgoing }).map(({ id }) => id),
    ["incoming", "outgoing"],
  );
});

test("handover ledger names one counterparty and summarizes multiple requests", () => {
  const sections = handoverLedgerSections({
    incoming: [
      { id: "incoming-1", fromUser: { name: "刘敏" } },
      { id: "incoming-2", fromUser: { name: "周宁" } },
    ],
    outgoing: [
      { id: "outgoing-1", toUser: { name: "王芳" } },
      { id: "outgoing-2", toUser: { name: "林晨" } },
    ],
  });

  assert.equal(sections[0].eyebrow, "待我确认 · 2 条");
  assert.equal(sections[1].eyebrow, "我发起的 · 2 条处理中");
});

test("relative day labels follow the server business date across month and year boundaries", () => {
  assert.equal(relativeDayLabel("2026-12-31", "2026-12-31"), "今天");
  assert.equal(relativeDayLabel("2027-01-01", "2026-12-31"), "明天");
  assert.equal(relativeDayLabel("2027-01-02", "2026-12-31"), "后天");
  assert.equal(relativeDayLabel("2027-01-03", "2026-12-31"), "");
  assert.equal(relativeDayLabel("2026-12-30", "2026-12-31"), "");
});

test("generates adjacent working-hour slots", () => {
  assert.deepEqual(generateTimeSlots("08:30", "10:00"), [
    ["08:30", "09:00"], ["09:00", "09:30"], ["09:30", "10:00"],
  ]);
  assert.throws(() => generateTimeSlots("08:30", "09:10"), /divide evenly/);
});

test("default tag applies only to a genuinely blank new booking", () => {
  const tags = [
    { id: "tag-1", slot: 1, label: "单位标签" },
    { id: "tag-3", slot: 3, label: "个人标签" },
  ];
  assert.equal(defaultBookingTagId({ tags, defaultTagSlot: null }), "");
  assert.equal(defaultBookingTagId({ tags, defaultTagSlot: 3 }), "tag-3");
  assert.equal(defaultBookingTagId({ tags, defaultTagSlot: 1 }), "tag-1");
  assert.equal(
    defaultBookingTagId({ tags, defaultTagSlot: 3, draft: { tagId: "tag-1" } }),
    "tag-1",
  );
  assert.equal(
    defaultBookingTagId({ tags, defaultTagSlot: 3, draft: { tagId: "" } }),
    "",
  );
});

test("calendar keeps existing bookings outside updated work hours without enabling new slots", () => {
  const slots = calendarTimeSlots({
    workStart: "10:00",
    workEnd: "16:00",
    slotMinutes: 30,
    bookings: [{ start: "09:00", end: "10:00", status: "active" }],
  });
  assert.deepEqual(slots[0], ["09:00", "09:30"]);
  assert.deepEqual(slots.at(-1), ["15:30", "16:00"]);
  assert.equal(isWithinWorkingHours("09:00", "09:30", "10:00", "16:00"), false);
  assert.equal(isWithinWorkingHours("10:00", "10:30", "10:00", "16:00"), true);
  assert.equal(calendarTimeLineOffset({
    selectedDate: "2026-08-10",
    serverDate: "2026-08-10",
    serverTime: "10:00",
    workStart: "10:00",
    workEnd: "16:00",
    visibleStart: slots[0][0],
    slotMinutes: 30,
    rowHeight: 76,
  }), 152);
  assert.equal(calendarTimeLineOffset({
    selectedDate: "2026-08-10",
    serverDate: "2026-08-10",
    serverTime: "09:30",
    workStart: "10:00",
    workEnd: "16:00",
    visibleStart: slots[0][0],
  }), null);
});

test("calendar arrow navigation preserves grid coordinates around disabled cells", () => {
  const cells = [
    { row: 0, column: 0, enabled: false },
    { row: 0, column: 1, enabled: false },
    { row: 0, column: 2, enabled: false },
    { row: 1, column: 0, enabled: false },
    { row: 1, column: 1, enabled: true },
    { row: 1, column: 2, enabled: true },
    { row: 2, column: 0, enabled: true },
    { row: 2, column: 1, enabled: true },
    { row: 2, column: 2, enabled: true },
  ];
  assert.deepEqual(
    calendarFocusTarget(cells, { row: 1, column: 1 }, "ArrowDown"),
    { row: 2, column: 1, enabled: true },
  );
  assert.deepEqual(
    calendarFocusTarget(cells, { row: 2, column: 1 }, "ArrowUp"),
    { row: 1, column: 1, enabled: true },
  );
  assert.deepEqual(calendarFocusTarget(cells, null, "ArrowDown"), {
    row: 1,
    column: 1,
    enabled: true,
  });
});

test("calendar navigation skips occupied continuations without wrapping rows", () => {
  const cells = [
    { row: 0, column: 0, enabled: true },
    { row: 0, column: 1, enabled: true },
    { row: 0, column: 2, enabled: true },
    { row: 1, column: 0, enabled: false },
    { row: 1, column: 1, enabled: true },
    { row: 1, column: 2, enabled: true },
  ];
  assert.deepEqual(
    calendarFocusTarget(cells, { row: 1, column: 1 }, "ArrowLeft"),
    { row: 1, column: 1, enabled: true },
  );
  assert.deepEqual(calendarFocusTarget(cells, null, "End"), {
    row: 1,
    column: 2,
    enabled: true,
  });
});

test("clamps the default duration to the remaining working day", () => {
  assert.equal(clampDurationToWorkday({ desired: 60, start: "17:00", workEnd: "17:30" }), 30);
  assert.equal(clampDurationToWorkday({ desired: 180, start: "16:00", workEnd: "17:30" }), 90);
  assert.equal(clampDurationToWorkday({ desired: 60, start: "09:00", workEnd: "17:30" }), 60);
});

test("treats the current and earlier server-local slots as already started", () => {
  const clock = { serverDate: "2026-08-10", serverTime: "15:20:45" };
  assert.equal(hasBookingStarted({ date: "2026-08-09", start: "17:00", ...clock }), true);
  assert.equal(hasBookingStarted({ date: "2026-08-10", start: "15:00", ...clock }), true);
  assert.equal(hasBookingStarted({ date: "2026-08-10", start: "15:20", ...clock }), true);
  assert.equal(hasBookingStarted({ date: "2026-08-10", start: "15:30", ...clock }), false);
  assert.equal(hasBookingStarted({ date: "2026-08-11", start: "08:30", ...clock }), false);
  assert.equal(hasBookingStarted({ date: "0001-01-01", start: "00:00", ...clock }), true);
  assert.equal(hasBookingStarted({ date: "9999-12-31", start: "23:59", ...clock }), false);
  assert.throws(() => hasBookingStarted({ date: "2026-02-30", start: "09:00", ...clock }), /out of range/);
});

test("caps duration at the next active booking in the selected room", () => {
  const bookings = [
    { id: "next", roomId: "room-1", date: "2026-08-11", start: "09:30", end: "10:30", status: "active" },
    { id: "other-room", roomId: "room-2", date: "2026-08-11", start: "09:00", end: "12:00", status: "active" },
    { id: "cancelled", roomId: "room-1", date: "2026-08-11", start: "09:00", end: "09:30", status: "cancelled" },
  ];
  const input = { bookings, roomId: "room-1", date: "2026-08-11", workEnd: "17:30" };
  assert.equal(maximumAvailableDuration({ ...input, start: "09:00" }), 30);
  assert.equal(maximumAvailableDuration({ ...input, start: "10:30" }), 180);
  assert.equal(maximumAvailableDuration({ ...input, start: "09:00", excludeBookingId: "next" }), 180);
  assert.equal(maximumAvailableDuration({ ...input, start: "17:00" }), 30);
});

test("positions the current-time line from the server-local working-day scale", () => {
  const input = { selectedDate: "2026-08-11", serverDate: "2026-08-11", workStart: "08:30", workEnd: "17:30", slotMinutes: 30, rowHeight: 76 };
  assert.equal(calendarTimeLineOffset({ ...input, serverTime: "08:30:00" }), 0);
  assert.equal(calendarTimeLineOffset({ ...input, serverTime: "09:00:00" }), 76);
  assert.equal(calendarTimeLineOffset({ ...input, serverTime: "09:15:00" }), 114);
  assert.equal(calendarTimeLineOffset({ ...input, selectedDate: "2026-08-12", serverTime: "09:00:00" }), null);
  assert.equal(calendarTimeLineOffset({ ...input, serverTime: "18:00:00" }), null);
});

test("revision conflicts expose field-level draft and server differences", () => {
  const differences = reservationConflictDifferences(
    { roomId: "room-1", date: "2026-08-10", start: "09:00", duration: 60, partyName: "张三", caseNumber: "A-1", purpose: "询问", notes: "", tagId: "tag-1" },
    { roomId: "room-2", date: "2026-08-10", start: "09:30", end: "10:00", partyName: "张三", caseNumber: "A-1", purpose: "询问", notes: "已更新", tagId: "tag-2" },
    { rooms: [{ id: "room-1", name: "一室" }, { id: "room-2", name: "二室" }], tags: [{ id: "tag-1", label: "初审" }, { id: "tag-2", label: "复核" }] },
  );
  assert.deepEqual(differences.map((item) => item.label), ["笔录室", "开始时间", "预约时长", "标签", "备注"]);
  assert.equal(differences[0].localValue, "一室");
  assert.equal(differences[0].serverValue, "二室");
});

test("shares authenticated details while mutation follows owner identity", () => {
  const own = { id: "booking-own", ownerId: "user-1" };
  const other = { id: "booking-other", ownerId: "user-2" };
  assert.equal(canViewBookingDetails({ role: "employee", booking: other }), true);
  assert.equal(canManageBooking({ role: "employee", currentUserId: "user-1", booking: own }), true);
  assert.equal(canManageBooking({ role: "employee", currentUserId: "user-1", booking: other }), false);
  assert.equal(canManageBooking({ role: "admin", currentUserId: "user-1", booking: other }), true);
  assert.equal(canManageBooking({ role: "staff", currentUserId: "user-1", booking: own }), false);
});

test("reauthentication validates the complete user scope before remounting", () => {
  const adminSession = { authenticated: true, currentUser: { id: "admin-1", role: "admin" } };
  const adminBootstrap = {
    currentUser: { id: "admin-1", role: "admin" },
    permissions: { manageRooms: true, manageUsers: true, manageSystem: true, viewReports: true, viewOverallReports: true, viewOtherUserReports: true },
    serverDate: "2026-08-10",
    serverTime: "08:00:00",
  };
  assert.equal(validateAuthenticatedContext(adminSession, adminBootstrap).scopeKey, "admin-1:admin");
  const employeeSession = { authenticated: true, currentUser: { id: "employee-1", role: "employee" } };
  const employeeBootstrap = {
    currentUser: { id: "employee-1", role: "employee" },
    permissions: { manageRooms: false, manageUsers: false, manageSystem: false, viewReports: true, viewOverallReports: false, viewOtherUserReports: false },
    serverDate: "2026-08-10",
    serverTime: "08:00:00",
  };
  assert.equal(validateAuthenticatedContext(employeeSession, employeeBootstrap).scopeKey, "employee-1:employee");
  const downgradedSession = { authenticated: true, currentUser: { id: "admin-1", role: "employee" } };
  const downgradedBootstrap = {
    currentUser: { id: "admin-1", role: "employee" },
    permissions: { manageRooms: false, manageUsers: false, manageSystem: false, viewReports: true, viewOverallReports: false, viewOtherUserReports: false },
    serverDate: "2026-08-10",
    serverTime: "08:00:00",
  };
  assert.equal(validateAuthenticatedContext(downgradedSession, downgradedBootstrap).scopeKey, "admin-1:employee");
  assert.notEqual("admin-1:admin", validateAuthenticatedContext(downgradedSession, downgradedBootstrap).scopeKey);
  assert.throws(() => validateAuthenticatedContext(adminSession, {
    currentUser: { id: "employee-1", role: "employee" },
    permissions: { manageRooms: false, manageUsers: false, manageSystem: false, viewReports: true, viewOverallReports: false, viewOtherUserReports: false },
    serverDate: "2026-08-10",
    serverTime: "08:00:00",
  }), /身份/);
  assert.throws(() => validateAuthenticatedContext(adminSession, {
    currentUser: { id: "admin-1", role: "employee" },
    permissions: { manageRooms: false, manageUsers: false, manageSystem: false, viewReports: true, viewOverallReports: false, viewOtherUserReports: false },
    serverDate: "2026-08-10",
    serverTime: "08:00:00",
  }), /身份/);
  assert.throws(() => validateAuthenticatedContext({ authenticated: false }, adminBootstrap), /身份/);
  assert.throws(() => validateAuthenticatedContext(adminSession, {
    ...adminBootstrap,
    serverDate: "2026-02-30",
  }), /服务端时间/);
});

test("administrator drawers are denied again at render time", () => {
  const employee = { manageRooms: false, manageUsers: false, manageSystem: false };
  assert.equal(isDrawerAllowed("details", employee), true);
  assert.equal(isDrawerAllowed("token-created", employee), false);
  assert.equal(isDrawerAllowed("user-edit", employee), false);
  assert.equal(isDrawerAllowed("room-delete-blocked", employee), false);
  assert.equal(isDrawerAllowed("room-delete-confirm", { manageRooms: true }), true);
  assert.equal(isDrawerAllowed("token-created", { manageSystem: true }), true);
});

test("administrator edits use the booking owner's personal tag semantics", () => {
  const context = bookingTagContext({
    booking: { ownerId: "employee-1" },
    role: "admin",
    currentUserId: "admin-1",
    globalTags: [{ id: "tag-1", slot: 1 }, { id: "tag-2", slot: 2 }],
    currentPersonalTags: [{ id: "tag-3", slot: 3, label: "管理员标签" }],
    users: [{
      id: "employee-1",
      personalTags: [
        { id: "tag-3", slot: 3, label: "员工标签三" },
        { id: "tag-4", slot: 4, label: "员工标签四" },
      ],
    }],
  });
  assert.equal(context.ownerTagsAvailable, true);
  assert.deepEqual(context.tags.map((tag) => tag.label), [undefined, undefined, "员工标签三", "员工标签四"]);
  const missing = bookingTagContext({
    booking: { ownerId: "missing-user" }, role: "admin", currentUserId: "admin-1",
    globalTags: [{ id: "tag-1", slot: 1 }], currentPersonalTags: [{ id: "tag-3", slot: 3 }], users: [],
  });
  assert.equal(missing.ownerTagsAvailable, false);
  assert.deepEqual(missing.tags.map((tag) => tag.id), ["tag-1"]);
});

test("setup waits for a stable LAN listener and reports restart failure", async () => {
  const states = [
    { ok: true, status: "ready", setup_complete: true, bind_mode: "loopback" },
    new Error("listener switching"),
    { ok: true, status: "ready", setup_complete: true, bind_mode: "lan" },
    { ok: true, status: "ready", setup_complete: true, bind_mode: "lan" },
  ];
  const health = await waitForSetupRestart({
    probe: async () => {
      const state = states.shift();
      if (state instanceof Error) throw state;
      return state;
    },
    pause: async () => {}, attempts: 4, stableChecks: 2,
  });
  assert.equal(health.bind_mode, "lan");
  await assert.rejects(waitForSetupRestart({
    probe: async () => { throw new Error("offline"); },
    pause: async () => {}, attempts: 2,
  }), (error) => error.code === "SERVICE_RESTART_TIMEOUT");
});

test("common failures include an action and safe support reference", () => {
  assert.match(userFacingError({ code: "NETWORK_ERROR" }), /① 启动系统/);
  assert.match(userFacingError({ code: "INVALID_RESPONSE", requestId: "req-1" }), /请求编号 req-1/);
  assert.match(userFacingError({ code: "BACKUP_FAILED", status: 500 }), /备份没有完成/);
  assert.doesNotMatch(userFacingError({ code: "BACKUP_FAILED", status: 500 }), /SELECT|Traceback|\.db/);
  assert.match(userFacingError({ code: "SYSTEM_RECOVERY_REQUIRED", requestId: "req-2" }), /⑥ 从备份恢复/);
  assert.match(userFacingError({ status: 403, message: "C:\\secret\\server.py" }), /联系管理员/);
  assert.match(userFacingError({ status: 503, requestId: "req-3" }), /① 启动系统/);
  assert.doesNotMatch(
    userFacingError({ status: 500, message: "SELECT * FROM users at C:\\secret\\app.py" }, "保存失败"),
    /SELECT|secret|app\.py/,
  );
});

test("matches stable reservation ids only", () => {
  assert.equal(isSameBooking({ id: "a" }, { id: "a" }), true);
  assert.equal(isSameBooking({ id: "a" }, { id: "b" }), false);
  assert.equal(isSameBooking({ caseNumber: "same" }, { caseNumber: "same" }), false);
});

test("builds the API reservation shape and requires expected revision for edits", () => {
  const form = {
    roomId: "room-1", date: "2026-08-10", start: "09:00", duration: 60,
    partyName: " 张女士 ", caseNumber: " 2026-001 ", purpose: " 工伤笔录 ",
    notes: " 备注 ", tagId: "tag-1",
  };
  assert.deepEqual(bookingPayload(form, 3), {
    roomId: "room-1", date: "2026-08-10", start: "09:00", duration: 60,
    partyName: "张女士", caseNumber: "2026-001", purpose: "工伤笔录",
    notes: "备注", tagId: "tag-1", expectedRevision: 3,
  });
  assert.deepEqual(validateBookingForm(form), {});
  assert.ok(validateBookingForm({ ...form, partyName: "" }).partyName);
  assert.equal(validateBookingForm({ ...form, start: "09:15" }, 30).start, "开始时间必须按 30 分钟对齐");
  assert.equal(validateBookingForm({ ...form, start: "09:30" }, 30).start, undefined);
});

test("calendar year bounds clamp leap-day dates", () => {
  assert.equal(dateKey(shiftDateByYears(new Date(2024, 1, 29), 2)), "2026-02-28");
  assert.equal(dateKey(shiftDateByYears(new Date(2026, 7, 15), -2)), "2024-08-15");
});

test("rebases an edit onto the latest revision without changing its draft", () => {
  const draft = { roomId: "room-1", partyName: "保留的用户草稿", notes: "尚未保存" };
  const current = { id: "booking-1", revision: 8, roomId: "room-2", partyName: "服务端最新内容" };
  const rebased = rebaseBookingEdit(draft, current);
  assert.deepEqual(rebased.draft, draft);
  assert.notEqual(rebased.draft, draft);
  assert.equal(rebased.baseline.revision, 8);
  assert.equal(rebased.baseline.partyName, "服务端最新内容");
});

test("uses the preferred room's first available slot for a generic create flow", () => {
  const start = findFirstAvailableStart({
    roomId: "preferred-room",
    slots: [["09:00", "09:30"], ["09:30", "10:00"], ["10:00", "10:30"]],
    notBefore: "09:10",
    bookings: [{ roomId: "preferred-room", start: "09:30", end: "10:00", status: "active" }],
  });
  assert.equal(start, "10:00");
});

test("computes upcoming countdown minutes against the projected server clock", () => {
  assert.equal(bookingCountdownMinutes({
    date: "2026-08-10", start: "09:30", serverDate: "2026-08-10", serverTime: "09:00:00",
  }), 30);
  assert.equal(bookingCountdownMinutes({
    date: "2026-08-10", start: "09:00", serverDate: "2026-08-10", serverTime: "09:00:30",
  }), -0.5);
  assert.equal(bookingCountdownMinutes({
    date: "2026-08-11", start: "00:15", serverDate: "2026-08-10", serverTime: "23:45",
  }), 30);
  assert.throws(() => bookingCountdownMinutes({ date: "2026-08-10", start: "09:00", serverDate: "bad", serverTime: "09:00" }), TypeError);
});

test("projects change-notice diffs onto user-facing fields only", () => {
  const rows = noticeDiffRows([
    { field: "roomName", from: "笔录室 1", to: "笔录室 2" },
    { field: "end", from: "10:00", to: "11:00" },
    { field: "roomId", from: "room-1", to: "room-2" },
    { field: "revision", from: 1, to: 2 },
    { field: "start", from: "09:00", to: "10:00" },
  ]);
  assert.deepEqual(rows, [
    { key: "start", label: "开始时间", from: "09:00", to: "10:00" },
    { key: "end", label: "结束时间", from: "10:00", to: "11:00" },
    { key: "roomName", label: "笔录室", from: "笔录室 1", to: "笔录室 2" },
  ]);
  assert.deepEqual(noticeDiffRows(undefined), []);
});

test("builds an unambiguous change-notice identity from the event snapshot", () => {
  assert.deepEqual(noticeIdentitySummary({
    noticeIdentity: {
      partyName: "王芳",
      purpose: "工伤笔录",
      date: "2026-08-18",
      start: "12:00",
      end: "13:00",
      roomName: "第一笔录室",
    },
  }), {
    partyName: "王芳",
    purpose: "工伤笔录",
    originalSchedule: "2026/8/18 · 12:00–13:00 · 第一笔录室",
  });
  assert.equal(noticeIdentitySummary({ partyName: "当前值不能冒充事件快照" }), null);
});

test("arrival reminder text names the party and room without case data", () => {
  assert.equal(
    arrivalReminderText({ partyName: "王芳", roomName: "第一笔录室", start: "14:00" }),
    "您的预约「王芳 · 第一笔录室」 14:00 开始，倒计时已标在日历上",
  );
  assert.equal(
    arrivalReminderText({ start: "09:00" }),
    "您的预约 09:00 开始，倒计时已标在日历上",
  );
});

test("projects the server wall clock without applying the browser timezone", () => {
  assert.deepEqual(projectServerClock({
    serverDate: "2026-08-09",
    serverTime: "23:59:58",
    receivedAt: 1_000,
  }, 5_000), {
    date: "2026-08-10",
    time: "00:00:02",
  });
  assert.deepEqual(projectServerClock({
    serverDate: "2026-08-09",
    serverTime: "14:32",
    receivedAt: "2026-08-09T06:32:10Z",
  }, "2026-08-09T06:32:15Z"), {
    date: "2026-08-09",
    time: "14:32:05",
  });
  assert.throws(() => projectServerClock({
    serverDate: "2026-02-30",
    serverTime: "14:32",
    receivedAt: 0,
  }, 0), /out of range/);
});

test("labels every reservation event type from the stable contract", () => {
  assert.equal(reservationEventLabel("created"), "预约已创建");
  assert.equal(reservationEventLabel("updated"), "预约已更新");
  assert.equal(reservationEventLabel("cancelled"), "预约已取消");
  assert.equal(reservationEventLabel("future-event"), "预约有变更");
});

test("setup username validation mirrors the server contract", () => {
  assert.equal(validateSetupUsername("ab"), "用户名至少 3 个字符且不能包含空格");
  assert.equal(validateSetupUsername("admin user"), "用户名至少 3 个字符且不能包含空格");
  assert.equal(validateSetupUsername("  admin  "), "");
});

test("setup API field errors return the wizard to the earliest visible control", () => {
  assert.deepEqual(mapSetupFieldErrors({
    "admin.username": "用户名无效",
    "rooms.1.name": "名称重复",
    workEnd: "结束时间无效",
  }, "请检查输入"), {
    errors: {
      username: "用户名无效",
      "rooms.1.name": "名称重复",
      workEnd: "结束时间无效",
    },
    step: 1,
  });
  assert.equal(mapSetupFieldErrors({ "rooms.0.name": "请输入名称" }).step, 2);
  assert.equal(setupStepForField("workEnd"), 3);
  assert.equal(setupStepForField("admin.password"), 1);
  assert.deepEqual(mapSetupFieldErrors({ unsupported: "未知字段错误" }), {
    errors: { submit: "未知字段错误" },
    step: null,
  });
});

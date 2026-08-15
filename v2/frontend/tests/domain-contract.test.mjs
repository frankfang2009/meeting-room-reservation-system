import assert from "node:assert/strict";
import test from "node:test";
import {
  bookingTagContext,
  bookingPayload,
  calendarTimeLineOffset,
  canManageBooking,
  canViewBookingDetails,
  clampDurationToWorkday,
  findFirstAvailableStart,
  generateTimeSlots,
  hasBookingStarted,
  isDrawerAllowed,
  isSameBooking,
  maximumAvailableDuration,
  projectServerClock,
  rebaseBookingEdit,
  reservationConflictDifferences,
  reminderDisplayMessage,
  reservationEventLabel,
  mapSetupFieldErrors,
  setupStepForField,
  userFacingError,
  validateAuthenticatedContext,
  validateBookingForm,
  validateSetupUsername,
  waitForSetupRestart,
} from "../src/domain.js";
import { reservationStatusLabel } from "../src/ui/presentation.js";

test("reservation statuses are always projected as Chinese UI copy", () => {
  assert.equal(reservationStatusLabel("active"), "已预约");
  assert.equal(reservationStatusLabel("cancelled"), "已取消");
  assert.equal(reservationStatusLabel("unexpected"), "状态未知");
});

test("generates adjacent working-hour slots", () => {
  assert.deepEqual(generateTimeSlots("08:30", "10:00"), [
    ["08:30", "09:00"], ["09:00", "09:30"], ["09:30", "10:00"],
  ]);
  assert.throws(() => generateTimeSlots("08:30", "09:10"), /divide evenly/);
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
    permissions: { manageRooms: true, manageUsers: true, manageSystem: true },
    serverDate: "2026-08-10",
    serverTime: "08:00:00",
  };
  assert.equal(validateAuthenticatedContext(adminSession, adminBootstrap).scopeKey, "admin-1:admin");
  const employeeSession = { authenticated: true, currentUser: { id: "employee-1", role: "employee" } };
  const employeeBootstrap = {
    currentUser: { id: "employee-1", role: "employee" },
    permissions: { manageRooms: false, manageUsers: false, manageSystem: false },
    serverDate: "2026-08-10",
    serverTime: "08:00:00",
  };
  assert.equal(validateAuthenticatedContext(employeeSession, employeeBootstrap).scopeKey, "employee-1:employee");
  const downgradedSession = { authenticated: true, currentUser: { id: "admin-1", role: "employee" } };
  const downgradedBootstrap = {
    currentUser: { id: "admin-1", role: "employee" },
    permissions: { manageRooms: false, manageUsers: false, manageSystem: false },
    serverDate: "2026-08-10",
    serverTime: "08:00:00",
  };
  assert.equal(validateAuthenticatedContext(downgradedSession, downgradedBootstrap).scopeKey, "admin-1:employee");
  assert.notEqual("admin-1:admin", validateAuthenticatedContext(downgradedSession, downgradedBootstrap).scopeKey);
  assert.throws(() => validateAuthenticatedContext(adminSession, {
    currentUser: { id: "employee-1", role: "employee" },
    permissions: { manageRooms: false, manageUsers: false, manageSystem: false },
    serverDate: "2026-08-10",
    serverTime: "08:00:00",
  }), /身份/);
  assert.throws(() => validateAuthenticatedContext(adminSession, {
    currentUser: { id: "admin-1", role: "employee" },
    permissions: { manageRooms: false, manageUsers: false, manageSystem: false },
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

test("formats change and upcoming reminder summaries without hiding server copy", () => {
  assert.equal(reminderDisplayMessage({ message: "服务端明确文案", kind: "change" }), "服务端明确文案");
  assert.equal(reminderDisplayMessage({
    kind: "change", changeType: "cancelled", date: "2026-08-10", start: "09:00", roomName: "笔录室 1",
  }), "2026-08-10 · 09:00 · 笔录室 1：预约已取消");
  assert.equal(reminderDisplayMessage({ kind: "upcoming", roomName: "笔录室 2" }), "笔录室 2：预约即将开始");
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

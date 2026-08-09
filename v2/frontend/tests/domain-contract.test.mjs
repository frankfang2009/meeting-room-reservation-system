import assert from "node:assert/strict";
import test from "node:test";
import {
  bookingPayload,
  canManageBooking,
  canViewBookingDetails,
  findFirstAvailableStart,
  generateTimeSlots,
  isSameBooking,
  rebaseBookingEdit,
  reminderDisplayMessage,
  validateBookingForm,
} from "../src/domain.js";

test("generates adjacent working-hour slots", () => {
  assert.deepEqual(generateTimeSlots("08:30", "10:00"), [
    ["08:30", "09:00"], ["09:00", "09:30"], ["09:30", "10:00"],
  ]);
  assert.throws(() => generateTimeSlots("08:30", "09:10"), /divide evenly/);
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

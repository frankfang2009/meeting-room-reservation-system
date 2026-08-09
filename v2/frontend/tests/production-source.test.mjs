import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const app = fs.readFileSync(path.join(root, "src/App.jsx"), "utf8");

test("production entry contains no demo credentials or query-state router", () => {
  for (const forbidden of ["demo123", "demo1234", "URLSearchParams", "loginState=", "bookingState=", "feedbackState=", "displayState=", "dataState="]) {
    assert.equal(app.includes(forbidden), false, "forbidden production source: " + forbidden);
  }
});

test("production entry does not generate business ids in the browser", () => {
  assert.equal(app.includes("Date.now()"), false);
  assert.equal(app.includes("booking-${"), false);
});

test("public display consumes the dedicated server projection", () => {
  assert.match(app, /api\.getPublicDisplay\(\)/);
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

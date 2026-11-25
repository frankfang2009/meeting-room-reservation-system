import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  clearSessionBookingDraft,
  consumeSessionBookingDraft,
  retryDeferredDraftClears,
  serializeSessionBookingDraft,
  SessionIsolationBoundary,
  writeSessionBookingDraft,
} from "../src/session-isolation.js";

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    removeItem: (key) => values.delete(key),
    setItem: (key, value) => values.set(key, value),
  };
}

const bookingDraft = {
  partyName: "张晓燕",
  caseNumber: "TEST-2026-007",
  purpose: "补充笔录",
  notes: "携带材料",
  tagId: "tag-3",
  roomId: "room-1",
  date: "2026-08-17",
  start: "09:30",
  duration: 90,
};

function render(blocked, message = "请重新登录") {
  return renderToStaticMarkup(createElement(
    SessionIsolationBoundary,
    {
      blocked,
      reauthentication: createElement("section", { role: "dialog" }, message),
    },
    createElement("div", null,
      createElement("code", null, "ADMIN_TOKEN_PLAINTEXT"),
      createElement("div", null, "管理员审计和用户列表"),
      createElement("textarea", { defaultValue: "未保存的管理员预约草稿" }),
    ),
  ));
}

test("expired-session render removes old administrator state from the document", () => {
  const markup = render(true);
  assert.match(markup, /data-session-blocked="true"/);
  assert.match(markup, /请重新登录/);
  assert.doesNotMatch(markup, /ADMIN_TOKEN_PLAINTEXT|管理员审计|管理员预约草稿/);
});

test("failed reauthentication remains isolated and successful scope remount renders only new state", () => {
  const failed = render(true, "重新登录失败，请核对账号后重试");
  assert.match(failed, /重新登录失败/);
  assert.doesNotMatch(failed, /ADMIN_TOKEN_PLAINTEXT|管理员审计|管理员预约草稿/);

  const verifiedNewScope = renderToStaticMarkup(createElement(
    SessionIsolationBoundary,
    { blocked: false, reauthentication: null },
    createElement("div", null, "普通员工工作台"),
  ));
  assert.match(verifiedNewScope, /普通员工工作台/);
  assert.doesNotMatch(verifiedNewScope, /ADMIN_TOKEN_PLAINTEXT|管理员审计/);
});

test("expired booking drafts serialize only the approved fields", () => {
  const serialized = serializeSessionBookingDraft("user-1", {
    bookingForm: { ...bookingDraft, secret: "never-store" },
    preservedDraft: null,
  });
  const payload = JSON.parse(serialized);
  assert.equal(payload.userId, "user-1");
  assert.deepEqual(payload.bookingForm, bookingDraft);
  assert.equal(payload.preservedDraft, null);
  assert.doesNotMatch(serialized, /never-store|secret/);
});

test("session draft storage is isolated by user and consumed after recovery", () => {
  const storage = memoryStorage();
  assert.equal(writeSessionBookingDraft("user-1", {
    bookingForm: bookingDraft,
    preservedDraft: { ...bookingDraft, partyName: "待续草稿" },
  }, storage), "saved");
  assert.equal(consumeSessionBookingDraft("user-2", storage), null);
  assert.deepEqual(consumeSessionBookingDraft("user-1", storage), {
    bookingForm: bookingDraft,
    preservedDraft: { ...bookingDraft, partyName: "待续草稿" },
  });
  assert.equal(consumeSessionBookingDraft("user-1", storage), null);
});

test("active logout clears only the current user's stored draft", () => {
  const storage = memoryStorage();
  writeSessionBookingDraft("user-1", { bookingForm: bookingDraft }, storage);
  writeSessionBookingDraft("user-2", { bookingForm: bookingDraft }, storage);
  clearSessionBookingDraft("user-1", storage);
  assert.equal(consumeSessionBookingDraft("user-1", storage), null);
  assert.ok(consumeSessionBookingDraft("user-2", storage));
});

test("no pending form clears an old draft without claiming a save", () => {
  const storage = memoryStorage();
  writeSessionBookingDraft("user-1", { preservedDraft: bookingDraft }, storage);
  assert.equal(writeSessionBookingDraft("user-1", {}, storage), "empty");
  assert.equal(consumeSessionBookingDraft("user-1", storage), null);
});

test("a rejected write does not offer a stale draft or clear a different account", () => {
  const storage = memoryStorage();
  for (const user of ["user-1", "user-2"]) {
    writeSessionBookingDraft(user, { preservedDraft: bookingDraft }, storage);
  }
  storage.setItem = () => { throw new DOMException("full", "QuotaExceededError"); };
  assert.equal(writeSessionBookingDraft("user-1", { bookingForm: bookingDraft }, storage), "failed");
  assert.equal(consumeSessionBookingDraft("user-1", storage), null);
  assert.deepEqual(consumeSessionBookingDraft("user-2", storage).preservedDraft, bookingDraft);
});

test("denied access to the storage property still permits expiry, login and logout", () => {
  const old = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage");
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    get() { throw new DOMException("unavailable", "SecurityError"); },
  });
  try {
    assert.equal(writeSessionBookingDraft("user-1", { bookingForm: bookingDraft }), "failed");
    assert.equal(writeSessionBookingDraft("user-1", {}), "empty");
    assert.equal(consumeSessionBookingDraft("user-1"), null);
    assert.doesNotThrow(() => clearSessionBookingDraft("user-1"));
  } finally {
    if (old) Object.defineProperty(globalThis, "sessionStorage", old);
    else delete globalThis.sessionStorage;
    clearSessionBookingDraft("user-1", memoryStorage());
  }
});

test("an explicitly discarded draft cannot return when storage becomes available again", () => {
  const storage = memoryStorage();
  const blocked = { ...storage, removeItem() { throw new Error("removal denied"); } };
  writeSessionBookingDraft("owner", { bookingForm: bookingDraft }, storage);
  writeSessionBookingDraft("other", { bookingForm: bookingDraft }, storage);
  clearSessionBookingDraft("owner", blocked);
  assert.equal(retryDeferredDraftClears(blocked), false);
  assert.equal(consumeSessionBookingDraft("owner", blocked), null);
  assert.deepEqual(consumeSessionBookingDraft("other", storage).bookingForm, bookingDraft);
  assert.equal(consumeSessionBookingDraft("owner", storage), null);
  assert.equal(retryDeferredDraftClears(storage), true);
});

test("empty or malformed stored data does not count as a recovery", () => {
  const storage = memoryStorage();
  assert.equal(writeSessionBookingDraft("user-1", { bookingForm: {} }, storage), "empty");
  const key = "meeting-room-v2:expired-booking-draft:user-1";
  storage.setItem(key, JSON.stringify({ version: 1, userId: "user-1", bookingForm: {} }));
  assert.equal(consumeSessionBookingDraft("user-1", storage), null);
  storage.setItem(key, "{");
  assert.equal(consumeSessionBookingDraft("user-1", storage), null);
});

test("a reload retries outstanding deletion without putting booking content in history", async () => {
  const history = { state: { page: "example" }, replaceState(next) { this.state = next; } };
  const original = Object.getOwnPropertyDescriptor(globalThis, "history");
  Object.defineProperty(globalThis, "history", { configurable: true, value: history });
  const storage = memoryStorage();
  try {
    writeSessionBookingDraft("reload-user", { bookingForm: bookingDraft }, storage);
    clearSessionBookingDraft("reload-user", { removeItem() { throw new Error("blocked"); } });
    assert.deepEqual(history.state, {
      page: "example",
      meetingRoomDraftsToClear: ["meeting-room-v2:expired-booking-draft:reload-user"],
    });
    const reloaded = await import("../src/session-isolation.js?after-logout");
    assert.equal(reloaded.consumeSessionBookingDraft("reload-user", storage), null);
    assert.deepEqual(history.state, { page: "example" });
  } finally {
    clearSessionBookingDraft("reload-user", storage);
    if (original) Object.defineProperty(globalThis, "history", original);
    else delete globalThis.history;
  }
});

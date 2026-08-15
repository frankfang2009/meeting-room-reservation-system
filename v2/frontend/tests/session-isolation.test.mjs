import assert from "node:assert/strict";
import test from "node:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  clearSessionBookingDraft,
  consumeSessionBookingDraft,
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
  assert.equal(writeSessionBookingDraft(storage, "user-1", {
    bookingForm: bookingDraft,
    preservedDraft: { ...bookingDraft, partyName: "待续草稿" },
  }), true);
  assert.equal(consumeSessionBookingDraft(storage, "user-2"), null);
  assert.deepEqual(consumeSessionBookingDraft(storage, "user-1"), {
    bookingForm: bookingDraft,
    preservedDraft: { ...bookingDraft, partyName: "待续草稿" },
  });
  assert.equal(consumeSessionBookingDraft(storage, "user-1"), null);
});

test("active logout clears only the current user's stored draft", () => {
  const storage = memoryStorage();
  writeSessionBookingDraft(storage, "user-1", { bookingForm: bookingDraft });
  writeSessionBookingDraft(storage, "user-2", { bookingForm: bookingDraft });
  clearSessionBookingDraft(storage, "user-1");
  assert.equal(consumeSessionBookingDraft(storage, "user-1"), null);
  assert.ok(consumeSessionBookingDraft(storage, "user-2"));
});

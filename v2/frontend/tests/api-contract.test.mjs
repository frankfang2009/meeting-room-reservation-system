import assert from "node:assert/strict";
import test from "node:test";
import { API_BASE, ApiError, api, getCsrfToken, request, setCsrfToken } from "../src/api.js";
import { bookingPayload, rebaseBookingEdit } from "../src/domain.js";

test("uses API schema v1", () => assert.equal(API_BASE, "/api/v1"));

test("write requests require CSRF before reaching the network", async () => {
  setCsrfToken("");
  await assert.rejects(request("/session", { method: "POST", body: {} }), (error) => error instanceof ApiError && error.code === "CSRF_MISSING");
});

test("write requests use same-origin credentials and CSRF header", async () => {
  const previousFetch = globalThis.fetch;
  let captured;
  globalThis.fetch = async (url, options) => {
    captured = { url, options };
    return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  try {
    setCsrfToken("csrf-test");
    await request("/session", { method: "POST", body: { username: "user", password: "secret" } });
    assert.equal(captured.url, "/api/v1/session");
    assert.equal(captured.options.credentials, "same-origin");
    assert.equal(captured.options.headers["X-CSRF-Token"], "csrf-test");
    assert.equal(getCsrfToken(), "csrf-test");
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("maps the uniform API error shape", async () => {
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response(JSON.stringify({ error: { code: "REVISION_CONFLICT", message: "changed", current: { id: "booking-1", revision: 2 } } }), { status: 409, headers: { "Content-Type": "application/json" } });
  try {
    await assert.rejects(request("/reservations/booking-1", { method: "PATCH", body: {}, headers: {} }), (error) => error.code === "REVISION_CONFLICT" && error.current.revision === 2);
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("acknowledges change and upcoming reminders with revision and kind", async () => {
  const previousFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    return new Response(JSON.stringify({ acknowledged: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    setCsrfToken("csrf-reminder");
    await api.acknowledgeReminder("reservation/1", 7, "change");
    assert.equal(requests[0].url, "/api/v1/reminders/reservation%2F1/ack");
    assert.deepEqual(JSON.parse(requests[0].options.body), {
      revision: 7,
      kind: "change",
    });
    assert.equal(requests[0].options.headers["X-CSRF-Token"], "csrf-reminder");
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("a rebased reservation PATCH sends the latest expected revision", async () => {
  const previousFetch = globalThis.fetch;
  let requestBody;
  globalThis.fetch = async (_url, options) => {
    requestBody = JSON.parse(options.body);
    return new Response(JSON.stringify({ id: "booking-1", revision: 9 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    setCsrfToken("csrf-rebase");
    const draft = {
      roomId: "room-1", date: "2026-08-10", start: "09:00", duration: 60,
      partyName: "保留草稿", caseNumber: "2026-001", purpose: "工伤笔录",
      notes: "保留备注", tagId: "tag-1",
    };
    const rebased = rebaseBookingEdit(draft, { id: "booking-1", revision: 8 });
    await api.updateReservation(rebased.baseline.id, bookingPayload(rebased.draft, rebased.baseline.revision));
    assert.equal(requestBody.expectedRevision, 8);
    assert.equal(requestBody.partyName, "保留草稿");
  } finally {
    globalThis.fetch = previousFetch;
  }
});

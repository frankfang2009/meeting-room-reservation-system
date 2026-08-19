import assert from "node:assert/strict";
import test from "node:test";
import { API_BASE, ApiError, api, getCsrfToken, request, setCsrfToken } from "../src/api.js";
import { bookingPayload, rebaseBookingEdit } from "../src/domain.js";

test("uses API schema v1", () => assert.equal(API_BASE, "/api/v1"));

test("service readiness probes the root health endpoint", async () => {
  const previousFetch = globalThis.fetch;
  let captured = "";
  globalThis.fetch = async (url) => {
    captured = url;
    return new Response(JSON.stringify({ ok: true, status: "ready", setup_complete: true, bind_mode: "lan" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    await api.getServiceHealth();
    assert.equal(captured, "/healthz");
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("unused bootstrap-duplicating API helpers stay removed", () => {
  for (const name of ["getUsers", "getPreferences"]) {
    assert.equal(Object.hasOwn(api, name), false, name);
  }
});

test("room metrics use the dedicated administrator refresh endpoint", async () => {
  const previousFetch = globalThis.fetch;
  let captured = "";
  globalThis.fetch = async (url) => {
    captured = url;
    return new Response(JSON.stringify({ items: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  try {
    await api.getRooms();
    assert.equal(captured, "/api/v1/rooms");
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("administrator work-hour updates use the dedicated settings endpoint", async () => {
  const previousFetch = globalThis.fetch;
  let captured;
  globalThis.fetch = async (url, options) => {
    captured = { url, options };
    return new Response(JSON.stringify({ workStart: "09:00", workEnd: "17:00", slotMinutes: 30, maxDurationMinutes: 180 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    setCsrfToken("csrf-settings");
    await api.updateSystemSettings({ workStart: "09:00", workEnd: "17:00" });
    assert.equal(captured.url, "/api/v1/admin/settings");
    assert.equal(captured.options.method, "PUT");
    assert.equal(captured.options.body, JSON.stringify({ workStart: "09:00", workEnd: "17:00" }));
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("personal activity uses the current-user aggregate endpoint", async () => {
  const previousFetch = globalThis.fetch;
  let captured = "";
  globalThis.fetch = async (url) => {
    captured = url;
    return new Response(JSON.stringify({ summary: {}, overview: {} }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    await api.getActivity();
    assert.equal(captured, "/api/v1/activity");
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("data center overview preserves explicit scope and filters", async () => {
  const previousFetch = globalThis.fetch;
  let captured = "";
  globalThis.fetch = async (url) => {
    captured = url;
    return new Response(JSON.stringify({ summary: {}, filters: {} }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    await api.getReportOverview({ scope: "person", ownerId: "employee/1", dateFrom: "2026-08-01", query: "A B" });
    assert.equal(captured, "/api/v1/reports/overview?scope=person&ownerId=employee%2F1&dateFrom=2026-08-01&query=A+B");
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("CSV download returns report metadata with the blob", async () => {
  const previousFetch = globalThis.fetch;
  let captured = "";
  globalThis.fetch = async (url) => {
    captured = url;
    return new Response("\ufeff办件编号\r\n", {
      status: 200,
      headers: {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": "attachment; filename*=UTF-8''report.csv",
        "X-Report-Field-Version": "v1",
        "X-Report-Row-Count": "12",
      },
    });
  };
  try {
    const result = await api.downloadReportCsv({ scope: "self", status: "active" });
    assert.equal(captured, "/api/v1/reports/reservations.csv?scope=self&status=active");
    assert.deepEqual([...new Uint8Array(await result.blob.arrayBuffer()).slice(0, 3)], [0xef, 0xbb, 0xbf]);
    assert.equal(result.contentDisposition, "attachment; filename*=UTF-8''report.csv");
    assert.equal(result.fieldVersion, "v1");
    assert.equal(result.rowCount, 12);
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("room deletion preflight uses a read-only impact endpoint", async () => {
  const previousFetch = globalThis.fetch;
  let captured = "";
  globalThis.fetch = async (url) => {
    captured = url;
    return new Response(JSON.stringify({ room: { id: "room-1", name: "笔录室 1" }, total: 0, items: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    await api.getRoomDeletionImpact("room/1");
    assert.equal(captured, "/api/v1/rooms/room%2F1/deletion-impact");
  } finally {
    globalThis.fetch = previousFetch;
  }
});

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

test("reservation conflict recheck reads the latest record by encoded id", async () => {
  const previousFetch = globalThis.fetch;
  let capturedUrl = "";
  globalThis.fetch = async (url) => {
    capturedUrl = url;
    return new Response(JSON.stringify({ id: "booking/1", revision: 3 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const latest = await api.getReservation("booking/1");
    assert.equal(capturedUrl, "/api/v1/reservations/booking%2F1");
    assert.equal(latest.revision, 3);
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("rejects non-JSON success responses instead of treating them as empty data", async () => {
  const previousFetch = globalThis.fetch;
  const responses = [
    new Response("<html>proxy error</html>", {
      status: 200,
      headers: { "Content-Type": "text/html", "X-Request-Id": "req-html" },
    }),
    new Response("", { status: 200, headers: { "Content-Type": "application/json" } }),
  ];
  globalThis.fetch = async () => responses.shift();
  try {
    await assert.rejects(request("/session"), (error) => (
      error instanceof ApiError
      && error.code === "INVALID_RESPONSE"
      && error.requestId === "req-html"
    ));
    await assert.rejects(request("/session"), (error) => error.code === "INVALID_RESPONSE");
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("preserves request ids from both error envelopes and exposes recovery metadata", async () => {
  const previousFetch = globalThis.fetch;
  const responses = [
    {
      error: {
        code: "SYSTEM_RECOVERY_REQUIRED",
        message: "需要恢复",
        fields: { recoveryCode: "DB_INTEGRITY_FAILED" },
        requestId: "inner-request-id",
      },
      requestId: "top-level-request-id",
    },
    {
      error: {
        code: "VALIDATION_ERROR",
        message: "参数错误",
        requestId: "inner-only-request-id",
      },
    },
  ];
  globalThis.fetch = async () => new Response(JSON.stringify(responses.shift()), {
    status: 503,
    headers: { "Content-Type": "application/json" },
  });
  try {
    await assert.rejects(request("/session"), (error) => (
      error instanceof ApiError
      && error.requestId === "top-level-request-id"
      && error.recoveryCode === "DB_INTEGRITY_FAILED"
      && error.fields.recoveryCode === "DB_INTEGRITY_FAILED"
    ));
    await assert.rejects(request("/session"), (error) => error.requestId === "inner-only-request-id");
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("sends opaque pagination cursors for reservation and history pages", async () => {
  const previousFetch = globalThis.fetch;
  const urls = [];
  globalThis.fetch = async (url) => {
    urls.push(url);
    return new Response(JSON.stringify({ items: [], nextCursor: null, pageSize: 25, total: 0 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const cursor = "signed+/opaque=value";
    await api.getReservations("2026-08-10", "2026-08-16", { pageSize: 200, cursor });
    await api.getHistory({ month: "2026-08", ownerId: "user-1", roomId: "room-2", status: "cancelled", tagId: "tag-2", query: "案号 2026", pageSize: 25, cursor });

    const reservations = new URL(urls[0], "http://localhost");
    assert.equal(reservations.pathname, "/api/v1/reservations");
    assert.equal(reservations.searchParams.get("dateFrom"), "2026-08-10");
    assert.equal(reservations.searchParams.get("dateTo"), "2026-08-16");
    assert.equal(reservations.searchParams.get("pageSize"), "200");
    assert.equal(reservations.searchParams.get("cursor"), cursor);

    const history = new URL(urls[1], "http://localhost");
    assert.equal(history.pathname, "/api/v1/reservations/history");
    assert.equal(history.searchParams.get("month"), "2026-08");
    assert.equal(history.searchParams.get("ownerId"), "user-1");
    assert.equal(history.searchParams.get("roomId"), "room-2");
    assert.equal(history.searchParams.get("status"), "cancelled");
    assert.equal(history.searchParams.get("tagId"), "tag-2");
    assert.equal(history.searchParams.get("query"), "案号 2026");
    assert.equal(history.searchParams.get("pageSize"), "25");
    assert.equal(history.searchParams.get("cursor"), cursor);
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("supports audit filters, JSON diagnostics, and the admin token lifecycle", async () => {
  const previousFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    const pathname = new URL(url, "http://localhost").pathname;
    if (pathname.endsWith("/diagnostics")) {
      return new Response(JSON.stringify({ generatedAtUtc: "2026-08-09T08:00:00Z" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(JSON.stringify({ items: [], nextCursor: null, pageSize: 50, total: 0 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const auditFilters = {
      cursor: "audit+/cursor",
      pageSize: 50,
      action: "auth.login_succeeded",
      outcome: "succeeded",
      actorId: "user-1",
      targetType: "session",
      targetId: "target-1",
      dateFrom: "2026-08-01T00:00:00+08:00",
      dateTo: "2026-08-09T23:59:59+08:00",
    };
    await api.getAudit(auditFilters);
    const diagnostics = await api.getDiagnostics();
    await api.getTokens();
    setCsrfToken("csrf-admin-token");
    await api.createToken({
      name: "只读看板",
      scopes: ["health:read"],
      expiresAt: "2026-12-31T16:00:00Z",
    });
    await api.revokeToken("token/1");

    const auditUrl = new URL(requests[0].url, "http://localhost");
    for (const [key, value] of Object.entries(auditFilters)) {
      assert.equal(auditUrl.searchParams.get(key), String(value));
    }
    assert.deepEqual(diagnostics, { generatedAtUtc: "2026-08-09T08:00:00Z" });
    assert.equal(requests[1].options.method, "GET");
    assert.equal(requests[2].url, "/api/v1/admin/tokens");
    assert.equal(requests[2].options.method, "GET");
    assert.equal(requests[3].url, "/api/v1/admin/tokens");
    assert.equal(requests[3].options.method, "POST");
    assert.equal(requests[3].options.headers["X-CSRF-Token"], "csrf-admin-token");
    assert.deepEqual(JSON.parse(requests[3].options.body), {
      name: "只读看板",
      scopes: ["health:read"],
      expiresAt: "2026-12-31T16:00:00Z",
    });
    assert.equal(requests[4].url, "/api/v1/admin/tokens/token%2F1");
    assert.equal(requests[4].options.method, "DELETE");
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("handover endpoints hit the agreed URLs with CSRF", async () => {
  const previousFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({ url, options });
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    setCsrfToken("csrf-handover");
    await api.createHandover("res-1", "user-2");
    await api.acceptHandover("req-1");
    await api.declineHandover("req-1");
    await api.withdrawHandover("req-1");
    await api.getHandoverRequests();
    await api.getUserDirectory("res-1");
    assert.equal(requests[0].url, "/api/v1/reservations/res-1/handover");
    assert.deepEqual(JSON.parse(requests[0].options.body), { toUserId: "user-2" });
    assert.equal(requests[1].url, "/api/v1/handover-requests/req-1/accept");
    assert.equal(requests[2].url, "/api/v1/handover-requests/req-1/decline");
    assert.equal(requests[3].url, "/api/v1/handover-requests/req-1");
    assert.equal(requests[3].options.method, "DELETE");
    assert.equal(requests[4].url, "/api/v1/handover-requests");
    assert.equal(requests[5].url, "/api/v1/users/directory?reservationId=res-1");
    assert.equal(requests[0].options.headers["X-CSRF-Token"], "csrf-handover");
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("acknowledges a change notice by event id", async () => {
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
    await api.acknowledgeChangeNotice("event-1");
    assert.equal(requests[0].url, "/api/v1/reminders/ack");
    assert.equal(requests[0].options.method, "POST");
    assert.deepEqual(JSON.parse(requests[0].options.body), {
      eventId: "event-1",
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

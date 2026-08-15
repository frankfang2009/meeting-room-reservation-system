import { validatePublicDisplayPayload } from "./public-contract.js";

export const API_BASE = "/api/v1";

let csrfToken = "";

export class ApiError extends Error {
  constructor({
    status = 0,
    code = "NETWORK_ERROR",
    message = "无法连接系统服务",
    fields = {},
    conflicts = [],
    current = null,
    requestId = "",
    recoveryCode,
  } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = fields || {};
    this.conflicts = conflicts || [];
    this.current = current || null;
    this.requestId = typeof requestId === "string" ? requestId : "";
    this.recoveryCode = recoveryCode ?? this.fields.recoveryCode ?? null;
  }
}

export function setCsrfToken(value) {
  csrfToken = typeof value === "string" ? value : "";
}

export function getCsrfToken() {
  return csrfToken;
}

function captureCsrf(value) {
  if (value && typeof value.csrfToken === "string") setCsrfToken(value.csrfToken);
  return value;
}

function errorFromResponse(status, payload, headerRequestId = "") {
  const error = payload?.error || {};
  return new ApiError({
    status,
    code: error.code || `HTTP_${status}`,
    message: error.message || "请求未能完成",
    fields: error.fields,
    conflicts: error.conflicts,
    current: error.current,
    requestId: payload?.requestId || error.requestId || headerRequestId,
    recoveryCode: error.recoveryCode,
  });
}

export async function request(path, { method = "GET", body, signal, headers = {}, responseType = "json", rootPath = false } = {}) {
  const upperMethod = method.toUpperCase();
  const requestHeaders = { Accept: "application/json", ...headers };
  const options = {
    method: upperMethod,
    credentials: "same-origin",
    headers: requestHeaders,
    signal,
  };

  if (body !== undefined) {
    requestHeaders["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  if (!["GET", "HEAD", "OPTIONS"].includes(upperMethod)) {
    if (!csrfToken) throw new ApiError({ code: "CSRF_MISSING", message: "安全令牌尚未就绪，请刷新页面后重试" });
    requestHeaders["X-CSRF-Token"] = csrfToken;
  }

  let response;
  try {
    response = await fetch(`${rootPath ? "" : API_BASE}${path}`, options);
  } catch (cause) {
    if (cause?.name === "AbortError") throw cause;
    throw new ApiError({ code: "NETWORK_ERROR", message: "无法连接系统服务" });
  }

  if (responseType === "blob") {
    if (!response.ok) {
      let payload = null;
      try { payload = await response.json(); } catch { /* non-JSON error */ }
      throw errorFromResponse(response.status, payload, response.headers.get("X-Request-Id"));
    }
    return response.blob();
  }

  let payload = null;
  if (response.status !== 204) {
    try {
      payload = await response.json();
    } catch {
      throw new ApiError({
        status: response.status,
        code: "INVALID_RESPONSE",
        message: "系统返回了无法识别的响应",
        requestId: response.headers.get("X-Request-Id") || "",
      });
    }
  }
  if (!response.ok) throw errorFromResponse(response.status, payload, response.headers.get("X-Request-Id"));
  return captureCsrf(payload);
}

function query(path, values = {}) {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  });
  const suffix = params.size ? `?${params.toString()}` : "";
  return `${path}${suffix}`;
}

export const api = {
  completeSetup: (input) => request("/setup/complete", { method: "POST", body: input }),
  getServiceHealth: () => request("/healthz", { rootPath: true }),
  getSession: () => request("/session"),
  login: (username, password) => request("/session", { method: "POST", body: { username, password } }),
  logout: () => request("/session", { method: "DELETE" }),
  getBootstrap: () => request("/bootstrap"),

  getReservations: (dateFrom, dateTo = dateFrom, { pageSize, cursor, signal } = {}) => request(query("/reservations", {
    dateFrom,
    dateTo,
    pageSize,
    cursor,
  }), { signal }),
  getUpcoming: () => request("/reservations/upcoming"),
  createReservation: (input) => request("/reservations", { method: "POST", body: input }),
  updateReservation: (id, input) => request(`/reservations/${encodeURIComponent(id)}`, { method: "PATCH", body: input }),
  cancelReservation: (id, expectedRevision) => request(`/reservations/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
    body: { expectedRevision },
  }),
  getReservationEvents: (id) => request(`/reservations/${encodeURIComponent(id)}/events`),
  getHistory: (filters) => request(query("/reservations/history", filters)),
  getActivity: () => request("/activity"),

  getRooms: () => request("/rooms"),
  createRoom: (input) => request("/rooms", { method: "POST", body: input }),
  updateRoom: (id, input) => request(`/rooms/${encodeURIComponent(id)}`, { method: "PATCH", body: input }),
  getRoomDeletionImpact: (id) => request(`/rooms/${encodeURIComponent(id)}/deletion-impact`),
  deleteRoom: (id) => request(`/rooms/${encodeURIComponent(id)}`, { method: "DELETE" }),

  createUser: (input) => request("/users", { method: "POST", body: input }),
  updateUser: (id, input) => request(`/users/${encodeURIComponent(id)}`, { method: "PATCH", body: input }),
  resetUserPassword: (id, password) => request(`/users/${encodeURIComponent(id)}/reset-password`, {
    method: "POST",
    body: { password },
  }),

  updatePreferences: (input) => request("/preferences", { method: "PUT", body: input }),
  updateGlobalTags: (tags) => request("/tags/global", { method: "PUT", body: { tags } }),

  getDueReminders: () => request("/reminders/due"),
  acknowledgeReminder: (id, revision, kind) => request(`/reminders/${encodeURIComponent(id)}/ack`, {
    method: "POST",
    body: { revision, kind },
  }),

  getSystem: () => request("/admin/system"),
  createBackup: () => request("/admin/backups", { method: "POST" }),
  getDiagnostics: () => request("/admin/diagnostics"),
  getAudit: (filters = {}) => request(query("/admin/audit", filters)),
  getTokens: () => request("/admin/tokens"),
  createToken: (input) => request("/admin/tokens", { method: "POST", body: input }),
  revokeToken: (id) => request(`/admin/tokens/${encodeURIComponent(id)}`, { method: "DELETE" }),

  getPublicDisplay: async (signal) => validatePublicDisplayPayload(await request("/display/today", { signal })),
};

export function unwrapItems(payload) {
  if (Array.isArray(payload)) return payload;
  return Array.isArray(payload?.items) ? payload.items : [];
}

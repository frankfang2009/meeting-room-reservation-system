const VALID_ROLES = new Set(["admin", "employee"]);
const ADMIN_DRAWER_PERMISSIONS = new Map([
  ["room-create", "manageRooms"],
  ["room-edit", "manageRooms"],
  ["room-delete-confirm", "manageRooms"],
  ["room-delete-blocked", "manageRooms"],
  ["user-create", "manageUsers"],
  ["user-edit", "manageUsers"],
  ["user-reset", "manageUsers"],
  ["system-settings", "manageSystem"],
  ["backup", "manageSystem"],
  ["token-create", "manageSystem"],
  ["token-created", "manageSystem"],
  ["token-revoke", "manageSystem"],
]);

export function validateAuthenticatedContext(session, bootstrap) {
  const sessionUser = session?.authenticated ? session.currentUser : null;
  const bootstrapUser = bootstrap?.currentUser;
  if (
    !sessionUser?.id
    || !bootstrapUser?.id
    || !VALID_ROLES.has(sessionUser.role)
    || !VALID_ROLES.has(bootstrapUser.role)
    || sessionUser.id !== bootstrapUser.id
    || sessionUser.role !== bootstrapUser.role
  ) {
    throw new TypeError("登录身份与工作台权限不一致");
  }
  const permissions = bootstrap?.permissions || {};
  const expectedAdmin = sessionUser.role === "admin";
  if (["manageRooms", "manageUsers", "manageSystem"].some((key) => Boolean(permissions[key]) !== expectedAdmin)) {
    throw new TypeError("登录角色与工作台权限不一致");
  }
  if (
    permissions.viewReports !== true
    || Boolean(permissions.viewOverallReports) !== expectedAdmin
    || Boolean(permissions.viewOtherUserReports) !== expectedAdmin
  ) {
    throw new TypeError("登录角色与数据中心权限不一致");
  }
  try {
    projectServerClock({
      serverDate: bootstrap.serverDate,
      serverTime: bootstrap.serverTime,
      receivedAt: 0,
    }, 0);
  } catch {
    throw new TypeError("工作台缺少可信的服务端时间");
  }
  return {
    session,
    bootstrap,
    scopeKey: `${sessionUser.id}:${sessionUser.role}`,
  };
}

export function isDrawerAllowed(type, permissions = {}) {
  const required = ADMIN_DRAWER_PERMISSIONS.get(String(type || ""));
  return !required || permissions[required] === true;
}

export function bookingTagContext({
  booking = null,
  role,
  currentUserId,
  globalTags = [],
  currentPersonalTags = [],
  users = [],
} = {}) {
  const unitTags = globalTags.filter((tag) => Number(tag?.slot) <= 2);
  const editingAnotherUser = Boolean(
    booking?.ownerId && currentUserId && booking.ownerId !== currentUserId,
  );
  if (!editingAnotherUser) {
    return { tags: [...unitTags, ...currentPersonalTags], ownerTagsAvailable: true };
  }
  if (role !== "admin") {
    return { tags: unitTags, ownerTagsAvailable: false, reason: "FORBIDDEN_OWNER" };
  }
  const owner = users.find((user) => user?.id === booking.ownerId);
  const personalTags = Array.isArray(owner?.personalTags)
    ? owner.personalTags.filter((tag) => [3, 4].includes(Number(tag?.slot)))
    : [];
  const slots = new Set(personalTags.map((tag) => Number(tag.slot)));
  if (!slots.has(3) || !slots.has(4)) {
    return { tags: unitTags, ownerTagsAvailable: false, reason: "OWNER_TAGS_MISSING" };
  }
  return { tags: [...unitTags, ...personalTags], ownerTagsAvailable: true };
}

export async function waitForSetupRestart({
  probe,
  pause = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)),
  attempts = 60,
  intervalMs = 500,
  stableChecks = 2,
} = {}) {
  if (typeof probe !== "function") throw new TypeError("A service readiness probe is required");
  let consecutiveReady = 0;
  let lastError = null;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (attempt > 0) await pause(intervalMs);
    try {
      const health = await probe();
      if (
        health?.ok === true
        && health?.status === "ready"
        && health?.setup_complete === true
        && health?.bind_mode === "lan"
      ) {
        consecutiveReady += 1;
        if (consecutiveReady >= stableChecks) return health;
      } else {
        consecutiveReady = 0;
      }
    } catch (error) {
      consecutiveReady = 0;
      lastError = error;
    }
  }
  const error = new Error("服务未能在限定时间内以局域网模式重新上线");
  error.code = "SERVICE_RESTART_TIMEOUT";
  error.cause = lastError;
  throw error;
}

export function userFacingError(error, fallback = "请求未能完成") {
  const reference = error?.requestId ? `请求编号 ${error.requestId}` : "";
  const withReference = (message) => reference ? `${message}（${reference}）` : message;
  const messages = {
    NETWORK_ERROR: "无法连接系统服务。请确认服务器电脑上的“① 启动系统”已经运行后重试；仍失败请把 _程序文件\\logs 交给维护人员。",
    INVALID_RESPONSE: "系统返回了无法识别的响应。请重试；仍失败请把 _程序文件\\logs 和本提示中的请求编号交给维护人员。",
    FORBIDDEN: "当前账号没有执行此操作的权限。请返回可用页面，或联系管理员确认账号角色。",
    BACKUP_FAILED: "备份没有完成，现有数据未被当作已备份。请在系统状态中重试，并把 _程序文件\\logs 与请求编号交给维护人员。",
    RESTORE_FAILED: "恢复没有完成，系统不会把当前数据库当作已恢复。请保留现场，重新运行“⑥ 从备份恢复”，并把 _程序文件\\logs 与恢复代码交给维护人员。",
    SYSTEM_RECOVERY_REQUIRED: "数据库处于恢复保护状态，系统已停止业务写入。请在服务器电脑运行“⑥ 从备份恢复”，并提交恢复代码、请求编号和 _程序文件\\logs。",
    SERVICE_UNAVAILABLE: "系统服务暂时不可用。请在服务器电脑运行“① 启动系统”后重试；仍失败请提交 _程序文件\\logs 和请求编号。",
    SESSION_EXPIRED: "登录已过期。请在遮罩层重新登录；验证新账号权限前，原页面不会恢复。",
    SESSION_REQUIRED: "需要重新登录。验证新账号权限前，原页面不会恢复。",
  };
  if (error?.status === 403) return withReference(messages.FORBIDDEN);
  if (messages[error?.code]) return withReference(messages[error.code]);
  if (error?.status === 503) return withReference(messages.SERVICE_UNAVAILABLE);
  if (error?.status >= 500) {
    return withReference(`${fallback}。请重试；仍失败请把 _程序文件\\logs 和请求编号交给维护人员。`);
  }
  return withReference(fallback);
}

function parseTime(value) {
  if (typeof value !== "string" || !/^\d{2}:\d{2}$/.test(value)) {
    throw new TypeError(`Invalid time: ${String(value)}`);
  }
  const [hours, minutes] = value.split(":").map(Number);
  if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59) {
    throw new RangeError(`Invalid time: ${value}`);
  }
  return hours * 60 + minutes;
}

function formatTime(totalMinutes) {
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

export function generateTimeSlots(start, end, step = 30) {
  const startMinutes = parseTime(start);
  const endMinutes = parseTime(end);
  if (!Number.isInteger(step) || step <= 0) {
    throw new RangeError("Time-slot step must be a positive integer");
  }
  if (endMinutes <= startMinutes) {
    throw new RangeError("End time must be later than start time");
  }
  if ((endMinutes - startMinutes) % step !== 0) {
    throw new RangeError("Working hours must divide evenly into time slots");
  }
  const slots = [];
  for (let cursor = startMinutes; cursor < endMinutes; cursor += step) {
    slots.push([formatTime(cursor), formatTime(cursor + step)]);
  }
  return slots;
}

export function calendarTimeSlots({ workStart, workEnd, slotMinutes = 30, bookings = [] } = {}) {
  const step = Number(slotMinutes || 30);
  let visibleStart = parseTime(workStart);
  let visibleEnd = parseTime(workEnd);
  for (const booking of bookings) {
    if (booking?.status === "cancelled" || !booking?.start || !booking?.end) continue;
    const bookingStart = parseTime(booking.start);
    const bookingEnd = parseTime(booking.end);
    visibleStart = Math.min(visibleStart, Math.floor(bookingStart / step) * step);
    visibleEnd = Math.max(visibleEnd, Math.ceil(bookingEnd / step) * step);
  }
  return generateTimeSlots(formatTime(visibleStart), formatTime(visibleEnd), step);
}

export function isWithinWorkingHours(start, end, workStart, workEnd) {
  return parseTime(start) >= parseTime(workStart) && parseTime(end) <= parseTime(workEnd);
}

export function defaultBookingTagId({ tags = [], defaultTagSlot = null, draft = null } = {}) {
  if (draft !== null) return typeof draft?.tagId === "string" ? draft.tagId : "";
  const preferred = tags.find((tag) => Number(tag?.slot) === Number(defaultTagSlot));
  return preferred?.id || "";
}

export function calendarFocusTarget(cells = [], current = null, key = "") {
  const normalized = cells
    .map((cell) => ({
      row: Number(cell?.row),
      column: Number(cell?.column),
      enabled: Boolean(cell?.enabled),
    }))
    .filter((cell) => Number.isInteger(cell.row) && Number.isInteger(cell.column));
  const enabled = normalized
    .filter((cell) => cell.enabled)
    .sort((left, right) => left.row - right.row || left.column - right.column);
  if (!enabled.length) return null;
  if (key === "Home") return enabled[0];
  if (key === "End") return enabled.at(-1);
  if (!current || !Number.isInteger(current.row) || !Number.isInteger(current.column)) {
    return enabled[0];
  }
  const direction = {
    ArrowLeft: { row: 0, column: -1 },
    ArrowRight: { row: 0, column: 1 },
    ArrowUp: { row: -1, column: 0 },
    ArrowDown: { row: 1, column: 0 },
  }[key];
  if (!direction) return null;
  const maximumRow = Math.max(...normalized.map((cell) => cell.row));
  const maximumColumn = Math.max(...normalized.map((cell) => cell.column));
  let row = current.row + direction.row;
  let column = current.column + direction.column;
  while (row >= 0 && row <= maximumRow && column >= 0 && column <= maximumColumn) {
    const candidate = normalized.find(
      (cell) => cell.row === row && cell.column === column,
    );
    if (candidate?.enabled) return candidate;
    row += direction.row;
    column += direction.column;
  }
  return enabled.find(
    (cell) => cell.row === current.row && cell.column === current.column,
  ) || null;
}

export function canManageBooking({ role, currentUserId, booking } = {}) {
  if (!VALID_ROLES.has(role) || !booking) return false;
  if (role === "admin") return true;
  return Boolean(currentUserId && booking.ownerId && currentUserId === booking.ownerId);
}

export function canViewBookingDetails({ role, booking } = {}) {
  return Boolean(VALID_ROLES.has(role) && booking?.id);
}

export function isSameBooking(left, right) {
  return Boolean(left?.id && right?.id && left.id === right.id);
}

export function dateKey(date) {
  const value = date instanceof Date ? date : new Date(date);
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function shiftDate(date, days) {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

export function shiftDateByYears(date, years) {
  const next = new Date(date);
  const originalMonth = next.getMonth();
  next.setFullYear(next.getFullYear() + Number(years));
  if (next.getMonth() !== originalMonth) next.setDate(0);
  return next;
}

export function durationFromRange(start, end) {
  return parseTime(end) - parseTime(start);
}

export function endFromDuration(start, duration) {
  return formatTime(parseTime(start) + Number(duration));
}

export function hasBookingStarted({ date, start, serverDate, serverTime } = {}) {
  const parseDay = (value) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ""));
    if (!match) throw new TypeError("A valid booking date is required");
    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    const parsed = new Date(0);
    parsed.setUTCFullYear(year, month - 1, day);
    parsed.setUTCHours(0, 0, 0, 0);
    if (
      parsed.getUTCFullYear() !== year
      || parsed.getUTCMonth() !== month - 1
      || parsed.getUTCDate() !== day
    ) {
      throw new RangeError("Booking date is out of range");
    }
    return parsed.getTime();
  };

  const bookingDay = parseDay(date);
  const currentDay = parseDay(serverDate);
  if (bookingDay !== currentDay) return bookingDay < currentDay;
  return parseTime(start) <= parseTime(String(serverTime || "").slice(0, 5));
}

export function clampDurationToWorkday({
  desired,
  start,
  workEnd,
  maxDuration = 180,
  slotMinutes = 30,
} = {}) {
  const step = Number(slotMinutes) || 30;
  const configuredMaximum = Number(maxDuration) || 180;
  const requested = Number(desired) || step;
  if (!start || !workEnd) return Math.max(step, Math.min(requested, configuredMaximum));
  const remaining = durationFromRange(start, workEnd);
  const availableMaximum = Math.floor(Math.min(configuredMaximum, remaining) / step) * step;
  return availableMaximum >= step ? Math.min(Math.max(step, requested), availableMaximum) : 0;
}

export function maximumAvailableDuration({
  bookings = [],
  roomId,
  date,
  start,
  workEnd,
  maxDuration = 180,
  slotMinutes = 30,
  excludeBookingId = "",
} = {}) {
  const step = Number(slotMinutes) || 30;
  const configuredMaximum = Number(maxDuration) || 180;
  if (!roomId || !date || !start || !workEnd) return configuredMaximum;
  const startMinutes = parseTime(start);
  let boundary = Math.min(startMinutes + configuredMaximum, parseTime(workEnd));

  for (const booking of bookings) {
    if (
      booking?.id === excludeBookingId
      || booking?.status === "cancelled"
      || booking?.roomId !== roomId
      || booking?.date !== date
      || !booking?.start
      || !booking?.end
    ) continue;
    const bookingStart = parseTime(booking.start);
    const bookingEnd = parseTime(booking.end);
    if (bookingStart < startMinutes && bookingEnd > startMinutes) return 0;
    if (bookingStart >= startMinutes) boundary = Math.min(boundary, bookingStart);
  }

  return Math.max(0, Math.floor((boundary - startMinutes) / step) * step);
}

export function calendarTimeLineOffset({
  selectedDate,
  serverDate,
  serverTime,
  workStart,
  workEnd,
  visibleStart = workStart,
  slotMinutes = 30,
  rowHeight = 76,
} = {}) {
  if (selectedDate !== serverDate) return null;
  const current = parseTime(String(serverTime || "").slice(0, 5));
  const start = parseTime(workStart);
  const end = parseTime(workEnd);
  if (current < start || current > end) return null;
  return ((current - parseTime(visibleStart)) / Number(slotMinutes || 30)) * Number(rowHeight || 76);
}

export function reservationConflictDifferences(draft, latest, { rooms = [], tags = [] } = {}) {
  if (!draft || !latest) return [];
  const roomName = (id) => rooms.find((room) => room.id === id)?.name || id || "未选择";
  const tagName = (id) => tags.find((tag) => tag.id === id)?.label || id || "未选择";
  const latestDuration = latest.start && latest.end
    ? durationFromRange(latest.start, latest.end)
    : Number(latest.duration || 0);
  const fields = [
    ["笔录室", roomName(draft.roomId), roomName(latest.roomId)],
    ["日期", draft.date || "未选择", latest.date || "未选择"],
    ["开始时间", draft.start || "未选择", latest.start || "未选择"],
    ["预约时长", `${Number(draft.duration) || 0} 分钟`, `${latestDuration} 分钟`],
    ["预约对象", draft.partyName || "未填写", latest.partyName || "未填写"],
    ["案号", draft.caseNumber || "未填写", latest.caseNumber || "未填写"],
    ["事项", draft.purpose || "未填写", latest.purpose || "未填写"],
    ["标签", tagName(draft.tagId), tagName(latest.tagId)],
    ["备注", draft.notes || "未填写", latest.notes || "未填写"],
  ];
  return fields
    .filter(([, localValue, serverValue]) => localValue !== serverValue)
    .map(([label, localValue, serverValue]) => ({ label, localValue, serverValue }));
}

export function overlaps(booking, start, end) {
  return parseTime(booking.start) < parseTime(end) && parseTime(booking.end) > parseTime(start);
}

export function findFirstAvailableStart({ bookings = [], roomId, slots = [], notBefore = "" } = {}) {
  const match = slots.find(([start, end]) => (
    (!notBefore || start >= notBefore)
    && !bookings.some((booking) => (
      booking.roomId === roomId
      && booking.status !== "cancelled"
      && overlaps(booking, start, end)
    ))
  ));
  return match?.[0] || null;
}

export function bookingPayload(form, expectedRevision) {
  const payload = {
    roomId: form.roomId,
    date: form.date,
    start: form.start,
    duration: Number(form.duration),
    partyName: form.partyName.trim(),
    caseNumber: form.caseNumber.trim(),
    purpose: form.purpose.trim(),
    notes: form.notes.trim(),
    tagId: form.tagId,
  };
  if (expectedRevision !== undefined && expectedRevision !== null) {
    payload.expectedRevision = Number(expectedRevision);
  }
  return payload;
}

export function validateBookingForm(form, slotMinutes = 30) {
  const errors = {};
  if (!form.roomId) errors.roomId = "请选择笔录室";
  if (!form.date) errors.date = "请选择日期";
  if (!form.start) errors.start = "请选择开始时间";
  if (form.start) {
    const match = /^(\d{2}):(\d{2})$/.exec(String(form.start));
    const step = Number(slotMinutes) || 30;
    const minutes = match ? Number(match[1]) * 60 + Number(match[2]) : Number.NaN;
    if (!match || Number(match[1]) > 23 || Number(match[2]) > 59 || minutes % step !== 0) {
      errors.start = `开始时间必须按 ${step} 分钟对齐`;
    }
  }
  if (!form.partyName?.trim()) errors.partyName = "请输入预约对象";
  if (!form.caseNumber?.trim()) errors.caseNumber = "请输入案号";
  if (!form.purpose?.trim()) errors.purpose = "请输入预约用途";
  if (![30, 60, 90, 120, 150, 180].includes(Number(form.duration))) {
    errors.duration = "预约时长必须为 30 至 180 分钟";
  }
  if (!form.tagId) errors.tagId = "请选择标签";
  return errors;
}

export function validateSetupUsername(value) {
  const username = String(value || "").trim();
  if (username.length < 3 || /\s/.test(username)) {
    return "用户名至少 3 个字符且不能包含空格";
  }
  return "";
}

export function setupStepForField(sourceField) {
  const field = String(sourceField || "").replace(/^admin\./, "");
  if (["username", "name", "department", "password", "confirmPassword"].includes(field)) return 1;
  if (field === "rooms" || /^rooms\.\d+\.name$/.test(field)) return 2;
  if (["workStart", "workEnd"].includes(field)) return 3;
  return null;
}

/**
 * Translate API field paths into the setup wizard's visible controls. The API
 * may prefix administrator fields with `admin.` while the form keeps those
 * values in a dedicated object. Dotted room paths stay intact so the matching
 * row can expose and focus its own error.
 */
export function mapSetupFieldErrors(fields = {}, fallbackMessage = "") {
  const mapped = {};
  const unplaced = [];
  let earliestStep = null;

  for (const [sourceField, value] of Object.entries(fields || {})) {
    const field = String(sourceField).replace(/^admin\./, "");
    const message = typeof value === "string" ? value : String(value || "请检查此项");
    let targetField = field;
    const targetStep = setupStepForField(field);
    if (targetStep === null) targetField = "";

    if (targetField) {
      mapped[targetField] = message;
      earliestStep = earliestStep === null ? targetStep : Math.min(earliestStep, targetStep);
    } else {
      unplaced.push(message);
    }
  }

  if (unplaced.length || earliestStep === null) {
    mapped.submit = unplaced.join("；") || fallbackMessage || "请检查输入内容";
  }
  return { errors: mapped, step: earliestStep };
}

/**
 * Move an optimistic edit onto the newest server baseline without changing
 * any user-entered field. The next PATCH must read expectedRevision from the
 * returned baseline, while "use latest" remains a separate explicit action.
 */
export function rebaseBookingEdit(draft, current) {
  if (!draft || typeof draft !== "object") throw new TypeError("Booking draft is required");
  if (!current?.id || !Number.isInteger(Number(current.revision))) {
    throw new TypeError("Current booking revision is required");
  }
  return {
    draft: { ...draft },
    baseline: { ...current, revision: Number(current.revision) },
  };
}

/**
 * 临近提醒倒计时：按服务器投影时钟计算距离预约开始还有多少分钟（浮点，
 * 已开始为负）。与 hasBookingStarted 一样使用 UTC 日期算术，避免浏览器
 * 时区干扰。
 */
export function bookingCountdownMinutes({ date, start, serverDate, serverTime } = {}) {
  const parseDay = (value) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ""));
    if (!match) throw new TypeError("A valid booking date is required");
    const parsed = new Date(0);
    parsed.setUTCFullYear(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    parsed.setUTCHours(0, 0, 0, 0);
    return parsed.getTime();
  };
  const timeParts = /^(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(String(serverTime || ""));
  if (!timeParts) throw new TypeError("A valid server time is required");
  const dayDeltaMinutes = (parseDay(date) - parseDay(serverDate)) / 60000;
  const startTotal = parseTime(start);
  const nowTotal = Number(timeParts[1]) * 60 + Number(timeParts[2]) + Number(timeParts[3] || 0) / 60;
  return dayDeltaMinutes + startTotal - nowTotal;
}

/**
 * 变更通知的字段级对比：只保留面向用户的字段，其余（roomId、revision 等
 * 技术字段）不进入弹窗。
 */
const NOTICE_DIFF_LABELS = {
  date: "日期",
  start: "开始时间",
  end: "结束时间",
  roomName: "笔录室",
  partyName: "当事人",
  caseNumber: "案号",
  purpose: "用途",
  notes: "备注",
  tagLabel: "标签",
};
const NOTICE_DIFF_ORDER = Object.keys(NOTICE_DIFF_LABELS);

export function noticeDiffRows(diffs) {
  if (!Array.isArray(diffs)) return [];
  return diffs
    .filter((diff) => diff && Object.prototype.hasOwnProperty.call(NOTICE_DIFF_LABELS, diff.field))
    .sort((left, right) => NOTICE_DIFF_ORDER.indexOf(left.field) - NOTICE_DIFF_ORDER.indexOf(right.field))
    .map((diff) => ({
      key: diff.field,
      label: NOTICE_DIFF_LABELS[diff.field],
      from: String(diff.from ?? ""),
      to: String(diff.to ?? ""),
    }));
}

export function noticeIdentitySummary(item) {
  const identity = item?.noticeIdentity;
  if (!identity || typeof identity !== "object") return null;
  const date = String(identity.date || "").replace(
    /^(\d{4})-(\d{2})-(\d{2})$/,
    (_, year, month, day) => `${year}/${Number(month)}/${Number(day)}`,
  );
  const range = identity.start && identity.end ? `${identity.start}–${identity.end}` : "";
  return {
    partyName: String(identity.partyName || "未填写当事人"),
    purpose: String(identity.purpose || "未填写事项"),
    originalSchedule: [date, range, identity.roomName].filter(Boolean).join(" · "),
  };
}

export function arrivalReminderText(item) {
  const summary = [item?.partyName, item?.roomName].filter(Boolean).join(" · ");
  const lead = summary ? `「${summary}」` : "";
  return `您的预约${lead} ${item?.start || ""} 开始，倒计时已标在日历上`;
}

/**
 * Advance the public-display clock from the server-local date/time captured at
 * receipt. UTC date arithmetic is intentional: these components describe the
 * server's wall clock and must not be reinterpreted in the browser timezone.
 */
export function projectServerClock(anchor, nowMs = Date.now()) {
  const dateMatch = /^(\d{4})-(\d{2})-(\d{2})$/.exec(anchor?.serverDate || "");
  const timeMatch = /^(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(anchor?.serverTime || "");
  if (!dateMatch || !timeMatch) throw new TypeError("A valid server date and time are required");

  const year = Number(dateMatch[1]);
  const month = Number(dateMatch[2]);
  const day = Number(dateMatch[3]);
  const hours = Number(timeMatch[1]);
  const minutes = Number(timeMatch[2]);
  const seconds = Number(timeMatch[3] ?? 0);
  const projected = new Date(Date.UTC(year, month - 1, day, hours, minutes, seconds));
  if (
    projected.getUTCFullYear() !== year
    || projected.getUTCMonth() !== month - 1
    || projected.getUTCDate() !== day
    || hours > 23
    || minutes > 59
    || seconds > 59
  ) {
    throw new RangeError("Server date or time is out of range");
  }

  const receivedAt = typeof anchor.receivedAt === "number"
    ? anchor.receivedAt
    : Date.parse(anchor.receivedAt);
  const now = typeof nowMs === "number" ? nowMs : Date.parse(nowMs);
  if (!Number.isFinite(receivedAt) || !Number.isFinite(now)) {
    throw new TypeError("Valid clock timestamps are required");
  }
  projected.setTime(projected.getTime() + Math.max(0, now - receivedAt));

  return {
    date: `${projected.getUTCFullYear()}-${String(projected.getUTCMonth() + 1).padStart(2, "0")}-${String(projected.getUTCDate()).padStart(2, "0")}`,
    time: `${String(projected.getUTCHours()).padStart(2, "0")}:${String(projected.getUTCMinutes()).padStart(2, "0")}:${String(projected.getUTCSeconds()).padStart(2, "0")}`,
  };
}

export function reservationEventLabel(type) {
  return ({
    created: "预约已创建",
    updated: "预约已更新",
    cancelled: "预约已取消",
    handover: "预约已交接",
  })[type] || "预约有变更";
}

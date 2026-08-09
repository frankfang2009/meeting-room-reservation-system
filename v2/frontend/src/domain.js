const VALID_ROLES = new Set(["admin", "employee"]);

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

export function durationFromRange(start, end) {
  return parseTime(end) - parseTime(start);
}

export function endFromDuration(start, duration) {
  return formatTime(parseTime(start) + Number(duration));
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

export function validateBookingForm(form) {
  const errors = {};
  if (!form.roomId) errors.roomId = "请选择笔录室";
  if (!form.date) errors.date = "请选择日期";
  if (!form.start) errors.start = "请选择开始时间";
  if (!form.partyName?.trim()) errors.partyName = "请输入预约对象";
  if (!form.caseNumber?.trim()) errors.caseNumber = "请输入案号";
  if (!form.purpose?.trim()) errors.purpose = "请输入预约用途";
  if (![30, 60, 90, 120, 150, 180].includes(Number(form.duration))) {
    errors.duration = "预约时长必须为 30 至 180 分钟";
  }
  if (!form.tagId) errors.tagId = "请选择标签";
  return errors;
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

export function reminderDisplayMessage(reminder) {
  if (typeof reminder?.message === "string" && reminder.message.trim()) {
    return reminder.message.trim();
  }
  const summary = [reminder?.date, reminder?.start, reminder?.roomName].filter(Boolean).join(" · ");
  const prefix = summary ? summary + "：" : "";
  if (reminder?.kind === "change") {
    return prefix + (reminder.changeType === "cancelled" ? "预约已取消" : "预约内容已更新");
  }
  return prefix + "预约即将开始";
}

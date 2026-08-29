export function defaultReportFilters(serverDate) {
  const value = /^\d{4}-\d{2}-\d{2}$/.test(String(serverDate || ""))
    ? String(serverDate)
    : new Date().toISOString().slice(0, 10);
  return {
    dateFrom: `${value.slice(0, 7)}-01`,
    dateTo: value,
    roomId: "",
    tagId: "",
    query: "",
  };
}

export function reportAnalysisPages(isOverall) {
  return isOverall
    ? [{ id: "time", label: "时段分布" }, { id: "rooms", label: "笔录室" }, { id: "tags", label: "标签" }]
    : [{ id: "time", label: "时段分布" }, { id: "tags", label: "标签" }];
}

export function defaultReportAnalysisPage(isOverall) {
  return isOverall ? "rooms" : "time";
}

export function resetReportAnalysisFilters(filters = {}) {
  return {
    ...filters,
    roomId: "",
    tagId: "",
    query: "",
  };
}

export function reportScope(role, currentUserId, view) {
  if (role !== "admin") return { scope: "self" };
  if (view === "overall") return { scope: "overall" };
  if (view === currentUserId) return { scope: "self" };
  return { scope: "person", ownerId: view };
}

// 槽位到色調的顺序必须与全应用 ui/presentation.js 的 TAG_COLORS 一致：
// 1 粘土、2 赭金、3 鼠尾草、4 石板，同一标签在任何界面颜色一致。
export function tagTone(item, index) {
  if (!item.tagId) return "stone";
  const slot = Number(String(item.tagId).replace("tag-", ""));
  return ["clay", "ochre", "sage", "slate"][(Number.isFinite(slot) ? slot - 1 : index) % 4];
}

export function formatReportDuration(minutes) {
  const value = Math.max(0, Number(minutes) || 0);
  if (value < 60) return { value, unit: "分钟" };
  const hours = Math.round((value / 60) * 10) / 10;
  return { value: Number.isInteger(hours) ? hours : hours.toFixed(1), unit: "小时" };
}

export function formatCancellationRate(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  return `${Math.round(Number(value) * 1000) / 10}%`;
}

export function filenameFromDisposition(value, fallback = "办件明细.csv") {
  const text = String(value || "");
  const encoded = text.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded);
    } catch {
      return fallback;
    }
  }
  const simple = text.match(/filename="?([^";]+)"?/i)?.[1];
  return simple || fallback;
}

export function reportExportCount(report, status) {
  if (!report) return 0;
  if (status === "active") return Number(report.summary?.activeCount || 0);
  if (status === "cancelled") return Number(report.summary?.cancelledCount || 0);
  return Number(report.exportRowCount || 0);
}

export function reportComposition(items = []) {
  const categories = (Array.isArray(items) ? items : [])
    .map((item, index) => ({
      ...item,
      key: String(item.tagId || `unassigned-${index}`),
      activeCount: Math.max(0, Number(item.activeCount) || 0),
    }))
    .filter((item) => item.activeCount > 0);
  const total = categories.reduce((sum, item) => sum + item.activeCount, 0);
  if (!total) return [];
  const allocated = categories.map((item, index) => {
    const exact = item.activeCount / total * 100;
    return {
      ...item,
      originalIndex: index,
      exact,
      dotCount: Math.floor(exact),
      remainder: exact - Math.floor(exact),
      shareLabel: `${Math.round(exact * 10) / 10}%`,
    };
  });
  const remaining = 100 - allocated.reduce((sum, item) => sum + item.dotCount, 0);
  const remainderOrder = [...allocated].sort((left, right) => (
    right.remainder - left.remainder || left.originalIndex - right.originalIndex
  ));
  for (let index = 0; index < remaining; index += 1) {
    remainderOrder[index % remainderOrder.length].dotCount += 1;
  }
  return allocated;
}

function parseIsoDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ""));
  if (!match) return null;
  return new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
}

function isoDate(value) {
  return value.toISOString().slice(0, 10);
}

function chineseShortDate(value) {
  return `${value.getUTCMonth() + 1}月${value.getUTCDate()}日`;
}

export function reportTrendModel(report = {}, filters = {}) {
  const selectedStart = parseIsoDate(filters.dateFrom);
  const selectedEnd = parseIsoDate(filters.dateTo);
  const inclusiveDays = selectedStart && selectedEnd
    ? Math.round((selectedEnd - selectedStart) / 86400000) + 1
    : 0;
  const granularity = inclusiveDays > 90 ? "month" : "week";
  const source = granularity === "month" ? report.monthlyTrend : report.weeklyTrend;
  const sameYear = selectedStart?.getUTCFullYear() === selectedEnd?.getUTCFullYear();
  const items = (Array.isArray(source) ? source : []).map((item) => {
    const rawStart = parseIsoDate(granularity === "month" ? item.monthStart : item.weekStart);
    if (!rawStart) return null;
    const rawEnd = granularity === "month"
      ? new Date(Date.UTC(rawStart.getUTCFullYear(), rawStart.getUTCMonth() + 1, 0))
      : new Date(rawStart.getTime() + 6 * 86400000);
    const periodStart = selectedStart && rawStart < selectedStart ? selectedStart : rawStart;
    const periodEnd = selectedEnd && rawEnd > selectedEnd ? selectedEnd : rawEnd;
    return {
      ...item,
      key: granularity === "month" ? item.monthStart : item.weekStart,
      periodStart: isoDate(periodStart),
      periodEnd: isoDate(periodEnd),
      intervalLabel: `${chineseShortDate(periodStart)}—${chineseShortDate(periodEnd)}`,
      axisLabel: granularity === "month"
        ? `${sameYear ? "" : `${rawStart.getUTCFullYear()}年`}${rawStart.getUTCMonth() + 1}月`
        : chineseShortDate(periodStart),
    };
  }).filter(Boolean);
  return { granularity, items };
}

export function reportTagOptions({ role, view, currentUser, users, globalTags, personalTags }) {
  const unit = Array.isArray(globalTags) ? globalTags : [];
  if (role === "admin" && view === "overall") return unit;
  const owner = (users || []).find((user) => user.id === view);
  const personal = owner?.personalTags
    || (view === currentUser?.id ? personalTags : [])
    || [];
  return [...unit, ...personal];
}

function minutesFromSlot(slot) {
  const [hours, minutes] = String(slot || "").split(":").map(Number);
  if (!Number.isFinite(hours) || !Number.isFinite(minutes)) return null;
  return hours * 60 + minutes;
}

function slotFromMinutes(value) {
  const hours = Math.floor(value / 60);
  const minutes = value % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

export function weekdaySlotDistribution(
  items = [],
  { workStart = "08:30", workEnd = "17:30", slotMinutes = 30 } = {},
) {
  const step = Number.isInteger(Number(slotMinutes)) && Number(slotMinutes) > 0
    ? Number(slotMinutes)
    : 30;
  const observed = items
    .map((item) => minutesFromSlot(item.slot))
    .filter((minute) => minute !== null);
  const configuredStart = minutesFromSlot(workStart) ?? 8 * 60 + 30;
  const configuredEnd = minutesFromSlot(workEnd) ?? 17 * 60 + 30;
  const start = Math.min(configuredStart, ...(observed.length ? observed : [configuredStart]));
  const end = Math.max(
    configuredEnd > start ? configuredEnd : start + step,
    ...(observed.length ? observed.map((minute) => minute + step) : [configuredEnd]),
  );
  const slots = [];
  for (let minute = start; minute < end; minute += step) {
    const showTime = minute === start || minute === end - step || (minute - start) % 60 === 0;
    slots.push({
      slot: slotFromMinutes(minute),
      minute,
      axisLabel: showTime ? slotFromMinutes(minute) : "",
    });
  }

  const counts = new Map();
  for (const item of items) {
    const weekday = Number(item.weekday);
    const minute = minutesFromSlot(item.slot);
    if (weekday < 1 || weekday > 7 || minute === null) continue;
    const key = `${weekday}:${slotFromMinutes(minute)}`;
    counts.set(key, Math.max(0, Number(item.count) || 0));
  }

  const rows = Array.from({ length: 7 }, (_, index) => ({
    weekday: index + 1,
    label: `周${"一二三四五六日"[index]}`,
    cells: slots.map((slot) => ({
      ...slot,
      count: counts.get(`${index + 1}:${slot.slot}`) || 0,
    })),
  }));
  const maximum = Math.max(0, ...rows.flatMap((row) => row.cells.map((cell) => cell.count)));
  let peak = null;
  if (maximum > 0) {
    for (const row of rows) {
      const cell = row.cells.find((candidate) => candidate.count === maximum);
      if (cell) {
        peak = {
          weekday: row.weekday,
          weekdayLabel: row.label,
          slot: cell.slot,
          end: slotFromMinutes(cell.minute + step),
          count: cell.count,
        };
        break;
      }
    }
  }
  return { maximum, peak, rows, slotMinutes: step, slots };
}

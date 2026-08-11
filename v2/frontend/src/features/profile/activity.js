const DAY_MS = 24 * 60 * 60 * 1000;

function parseUtcDate(value) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ""));
  if (!match) return null;
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  if (Number.isNaN(date.getTime()) || dateKey(date) !== String(value)) return null;
  return date;
}

function dateKey(date) {
  return date.toISOString().slice(0, 10);
}

export function emptyActivity() {
  return {
    range: { start: "", end: "" },
    summary: {
      currentMonthCompleted: 0,
      totalCompleted: 0,
      totalDurationMinutes: 0,
      activeDays: 0,
    },
    overview: {
      averageDurationMinutes: 0,
      favoriteRoom: null,
      favoriteTag: null,
    },
    days: [],
  };
}

export function buildActivityHeatmap(activity) {
  const start = parseUtcDate(activity?.range?.start);
  const end = parseUtcDate(activity?.range?.end);
  if (!start || !end || start > end) return [];
  const counts = new Map((activity?.days || []).map((item) => [item.date, Number(item.completed || 0)]));
  const mondayOffset = (start.getUTCDay() + 6) % 7;
  const gridStart = new Date(start.getTime() - mondayOffset * DAY_MS);
  const sundayOffset = (7 - end.getUTCDay()) % 7;
  const gridEnd = new Date(end.getTime() + sundayOffset * DAY_MS);
  const cells = [];
  for (let cursor = gridStart; cursor <= gridEnd; cursor = new Date(cursor.getTime() + DAY_MS)) {
    const key = dateKey(cursor);
    const inRange = cursor >= start && cursor <= end;
    const count = inRange ? counts.get(key) || 0 : 0;
    cells.push({
      date: key,
      count,
      inRange,
      level: count <= 0 ? 0 : Math.min(4, count),
    });
  }
  return cells;
}

export function activityForMonths(activity, requestedMonths = 12) {
  const source = activity || emptyActivity();
  const end = parseUtcDate(source?.range?.end);
  const sourceStart = parseUtcDate(source?.range?.start);
  const months = requestedMonths === 6 ? 6 : 12;
  if (!end || !sourceStart) return source;
  const requestedStart = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth() - months + 1, 1));
  const start = requestedStart > sourceStart ? requestedStart : sourceStart;
  const startKey = dateKey(start);
  return {
    ...source,
    range: { ...source.range, start: startKey },
    days: (source.days || []).filter((item) => item.date >= startKey && item.date <= source.range.end),
  };
}

export function activityMonthLabels(activity) {
  const start = parseUtcDate(activity?.range?.start);
  const end = parseUtcDate(activity?.range?.end);
  if (!start || !end || start > end) return [];
  const labels = [];
  let year = start.getUTCFullYear();
  let month = start.getUTCMonth();
  while (year < end.getUTCFullYear() || (year === end.getUTCFullYear() && month <= end.getUTCMonth())) {
    labels.push(`${month + 1}月`);
    month += 1;
    if (month === 12) {
      month = 0;
      year += 1;
    }
  }
  return labels;
}

export function activityRangeLabel(activity) {
  const start = parseUtcDate(activity?.range?.start);
  const end = parseUtcDate(activity?.range?.end);
  if (!start || !end) return "近一年";
  return `${start.getUTCFullYear()}年${start.getUTCMonth() + 1}月 – ${end.getUTCFullYear()}年${end.getUTCMonth() + 1}月`;
}

export function activityDuration(totalMinutes) {
  const minutes = Math.max(0, Number(totalMinutes || 0));
  if (minutes < 60) return { value: minutes, unit: "分钟" };
  const hours = minutes / 60;
  return { value: Number.isInteger(hours) ? hours : Number(hours.toFixed(1)), unit: "小时" };
}

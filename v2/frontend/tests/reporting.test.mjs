import assert from "node:assert/strict";
import test from "node:test";

import {
  defaultReportFilters,
  filenameFromDisposition,
  formatCancellationRate,
  formatReportDuration,
  reportComposition,
  reportAnalysisPages,
  defaultReportAnalysisPage,
  reportExportCount,
  resetReportAnalysisFilters,
  reportScope,
  reportTagOptions,
  reportTrendModel,
  tagTone,
  weekdaySlotDistribution,
} from "../src/features/reports/reporting.js";

test("tag tones follow the app-wide slot palette order", () => {
  assert.equal(tagTone({ tagId: "tag-1" }, 0), "clay");
  assert.equal(tagTone({ tagId: "tag-2" }, 1), "ochre");
  assert.equal(tagTone({ tagId: "tag-3" }, 2), "sage");
  assert.equal(tagTone({ tagId: "tag-4" }, 3), "slate");
  assert.equal(tagTone({ tagId: "" }, 0), "stone");
});

test("report scopes never grant employees a broader view", () => {
  assert.deepEqual(reportScope("employee", "employee-1", "overall"), { scope: "self" });
  assert.deepEqual(reportScope("admin", "admin-1", "overall"), { scope: "overall" });
  assert.deepEqual(reportScope("admin", "admin-1", "admin-1"), { scope: "self" });
  assert.deepEqual(reportScope("admin", "admin-1", "employee-1"), { scope: "person", ownerId: "employee-1" });
});

test("report defaults use the current server month", () => {
  assert.deepEqual(defaultReportFilters("2026-08-16"), {
    dateFrom: "2026-08-01",
    dateTo: "2026-08-16",
    roomId: "",
    tagId: "",
    query: "",
  });
});

test("report presentation keeps duration and cancellation semantics explicit", () => {
  assert.deepEqual(formatReportDuration(30), { value: 30, unit: "分钟" });
  assert.deepEqual(formatReportDuration(90), { value: "1.5", unit: "小时" });
  assert.equal(formatCancellationRate(null), "—");
  assert.equal(formatCancellationRate(0.126), "12.6%");
});

test("analysis stays progressively revealed and keeps the global date range", () => {
  assert.deepEqual(reportAnalysisPages(true), [
    { id: "time", label: "时段分布" },
    { id: "rooms", label: "笔录室" },
    { id: "tags", label: "标签" },
  ]);
  assert.deepEqual(reportAnalysisPages(false), [
    { id: "time", label: "时段分布" },
    { id: "tags", label: "标签" },
  ]);
  assert.equal(defaultReportAnalysisPage(true), "rooms");
  assert.equal(defaultReportAnalysisPage(false), "time");
  assert.deepEqual(resetReportAnalysisFilters({
    dateFrom: "2026-08-01",
    dateTo: "2026-08-16",
    roomId: "room-1",
    tagId: "tag-2",
    query: "王某",
  }), {
    dateFrom: "2026-08-01",
    dateTo: "2026-08-16",
    roomId: "",
    tagId: "",
    query: "",
  });
});

test("trend model keeps short ranges weekly and exposes exact clipped intervals", () => {
  const model = reportTrendModel({
    weeklyTrend: [
      { weekStart: "2026-06-15", activeCount: 4, activeDurationMinutes: 300 },
      { weekStart: "2026-08-17", activeCount: 2, activeDurationMinutes: 120 },
    ],
  }, { dateFrom: "2026-06-18", dateTo: "2026-08-17" });
  assert.equal(model.granularity, "week");
  assert.deepEqual(model.items.map((item) => item.intervalLabel), [
    "6月18日—6月21日",
    "8月17日—8月17日",
  ]);
  assert.deepEqual(model.items.map((item) => item.axisLabel), ["6月18日", "8月17日"]);
});

test("trend model switches long ranges to calendar months and clips the last month", () => {
  const model = reportTrendModel({
    monthlyTrend: [
      { monthStart: "2026-01-01", activeCount: 14, activeDurationMinutes: 720 },
      { monthStart: "2026-08-01", activeCount: 25, activeDurationMinutes: 960 },
    ],
  }, { dateFrom: "2026-01-01", dateTo: "2026-08-17" });
  assert.equal(model.granularity, "month");
  assert.deepEqual(model.items.map((item) => item.intervalLabel), [
    "1月1日—1月31日",
    "8月1日—8月17日",
  ]);
  assert.deepEqual(model.items.map((item) => item.axisLabel), ["1月", "8月"]);
});

test("CSV metadata is converted into a safe user-facing filename and count", () => {
  assert.equal(filenameFromDisposition("attachment; filename*=UTF-8''%E5%8A%9E%E4%BB%B6.csv"), "办件.csv");
  assert.equal(filenameFromDisposition("", "fallback.csv"), "fallback.csv");
  const report = { exportRowCount: 7, summary: { activeCount: 5, cancelledCount: 2 } };
  assert.equal(reportExportCount(report, ""), 7);
  assert.equal(reportExportCount(report, "active"), 5);
  assert.equal(reportExportCount(report, "cancelled"), 2);
});

test("overall tags stay global while personal views can include owner tags", () => {
  const globalTags = [{ id: "tag-1", slot: 1 }];
  const users = [{ id: "employee-1", personalTags: [{ id: "tag-3", slot: 3 }] }];
  assert.deepEqual(reportTagOptions({ role: "admin", view: "overall", globalTags }), globalTags);
  assert.deepEqual(reportTagOptions({ role: "admin", view: "employee-1", users, globalTags }), [globalTags[0], users[0].personalTags[0]]);
});

test("weekday slot distribution preserves half-hour detail and identifies the first peak", () => {
  const model = weekdaySlotDistribution([
    { weekday: 1, slot: "08:30", count: 2 },
    { weekday: 1, slot: "09:00", count: 3 },
    { weekday: 1, slot: "13:30", count: 4 },
    { weekday: 5, slot: "16:30", count: 6 },
  ], { workStart: "08:30", workEnd: "17:30", slotMinutes: 30 });
  assert.equal(model.slots.length, 18);
  assert.deepEqual(model.slots.filter((slot) => slot.axisLabel).map((slot) => slot.axisLabel), [
    "08:30", "09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30", "16:30", "17:00",
  ]);
  assert.equal(model.rows[0].cells.find((cell) => cell.slot === "09:00").count, 3);
  assert.equal(model.rows[1].cells.find((cell) => cell.slot === "09:00").count, 0);
  assert.equal(model.maximum, 6);
  assert.deepEqual(model.peak, {
    weekday: 5,
    weekdayLabel: "周五",
    slot: "16:30",
    end: "17:00",
    count: 6,
  });
});

test("weekday slot distribution extends beyond current work hours for historical slots", () => {
  const model = weekdaySlotDistribution([
    { weekday: 7, slot: "07:30", count: 2 },
    { weekday: 7, slot: "18:00", count: 1 },
  ], { workStart: "08:30", workEnd: "17:30", slotMinutes: 30 });
  assert.equal(model.slots.at(0).slot, "07:30");
  assert.equal(model.slots.at(-1).slot, "18:00");
});

test("tag composition allocates exactly one hundred percentage dots", () => {
  const composition = reportComposition([
    { tagId: null, label: "未使用单位标签", activeCount: 32 },
    { tagId: "tag-1", label: "首次办理", activeCount: 19 },
    { tagId: "tag-2", label: "补充材料", activeCount: 14 },
  ]);
  assert.equal(composition.reduce((sum, item) => sum + item.dotCount, 0), 100);
  assert.deepEqual(composition.map((item) => item.dotCount), [49, 29, 22]);
  assert.deepEqual(composition.map((item) => item.shareLabel), ["49.2%", "29.2%", "21.5%"]);
});

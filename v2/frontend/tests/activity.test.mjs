import assert from "node:assert/strict";
import test from "node:test";
import {
  activityDuration,
  activityForMonths,
  activityMonthLabels,
  activityRangeLabel,
  buildActivityHeatmap,
  emptyActivity,
} from "../src/features/profile/activity.js";

test("builds a Monday-aligned heatmap with bounded activity levels", () => {
  const activity = {
    range: { start: "2026-07-01", end: "2026-08-11" },
    days: [
      { date: "2026-07-01", completed: 1 },
      { date: "2026-08-11", completed: 7 },
    ],
  };
  const cells = buildActivityHeatmap(activity);
  assert.equal(cells[0].date, "2026-06-29");
  assert.equal(cells.at(-1).date, "2026-08-16");
  assert.equal(cells.find((cell) => cell.date === "2026-07-01").level, 1);
  assert.equal(cells.find((cell) => cell.date === "2026-08-11").level, 4);
  assert.equal(cells.find((cell) => cell.date === "2026-06-30").inRange, false);
});

test("limits the heatmap to a saved six-month display range without changing server totals", () => {
  const source = {
    range: { start: "2025-09-01", end: "2026-08-11" },
    summary: { totalCompleted: 9 },
    days: [
      { date: "2026-02-28", completed: 2 },
      { date: "2026-03-01", completed: 3 },
      { date: "2026-08-11", completed: 4 },
    ],
  };
  const limited = activityForMonths(source, 6);
  assert.deepEqual(limited.range, { start: "2026-03-01", end: "2026-08-11" });
  assert.deepEqual(limited.days, source.days.slice(1));
  assert.equal(limited.summary.totalCompleted, 9);
  assert.equal(activityRangeLabel(limited), "2026年3月 – 2026年8月");
});

test("rejects rolled-over calendar dates when building activity cells", () => {
  assert.deepEqual(buildActivityHeatmap({ range: { start: "2026-02-30", end: "2026-08-11" }, days: [] }), []);
});

test("formats the activity period, months, and duration without a legend dependency", () => {
  const activity = { range: { start: "2025-09-01", end: "2026-08-11" } };
  assert.equal(activityRangeLabel(activity), "2025年9月 – 2026年8月");
  assert.deepEqual(activityMonthLabels(activity), ["9月", "10月", "11月", "12月", "1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月"]);
  assert.deepEqual(activityDuration(9480), { value: 158, unit: "小时" });
  assert.deepEqual(activityDuration(90), { value: 1.5, unit: "小时" });
  assert.deepEqual(activityDuration(30), { value: 30, unit: "分钟" });
  assert.deepEqual(buildActivityHeatmap(emptyActivity()), []);
});

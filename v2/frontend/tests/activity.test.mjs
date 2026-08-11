import assert from "node:assert/strict";
import test from "node:test";
import { activityDuration, emptyActivity } from "../src/features/profile/activity.js";

test("formats activity duration for the remaining summary metrics", () => {
  assert.deepEqual(activityDuration(9480), { value: 158, unit: "小时" });
  assert.deepEqual(activityDuration(90), { value: 1.5, unit: "小时" });
  assert.deepEqual(activityDuration(30), { value: 30, unit: "分钟" });
});

test("provides an empty summary without heatmap-only fields", () => {
  const activity = emptyActivity();
  assert.deepEqual(Object.keys(activity).sort(), ["overview", "summary"]);
  assert.equal(activity.summary.totalCompleted, 0);
  assert.equal(activity.overview.favoriteRoom, null);
});

import assert from "node:assert/strict";
import test from "node:test";
import { bookingDefaultsSummary } from "../src/features/profile/settings-presentation.js";

const rooms = [{ id: "room-1", name: "一号笔录室" }];
const tags = [{ id: "tag-1", slot: 1, label: "讯问" }];

test("new-booking defaults have one compact, resolvable summary", () => {
  assert.equal(
    bookingDefaultsSummary({ defaultDuration: 60, defaultRoomId: "room-1", defaultTagSlot: null }, rooms, tags),
    "60分钟 · 一号笔录室 · 不指定标签",
  );
  assert.equal(
    bookingDefaultsSummary({ defaultDuration: 90, defaultRoomId: "missing", defaultTagSlot: 1 }, rooms, tags),
    "90分钟 · 不指定笔录室 · 讯问",
  );
});

import assert from "node:assert/strict";
import test from "node:test";
import { validatePublicDisplayPayload } from "../src/public-contract.js";

const valid = {
  serverDate: "2026-08-10",
  serverTime: "14:32",
  lastUpdatedAt: "2026-08-10T14:32:05+08:00",
  status: "online",
  rooms: [{
    id: "room-1",
    name: "笔录室 1",
    current: { maskedPartyName: "张*士", start: "14:00", end: "15:00" },
    next: null,
  }],
};

test("accepts only the public display allowlist", () => {
  assert.deepEqual(validatePublicDisplayPayload(valid), valid);
});

for (const [label, addition] of [
  ["case number", { caseNumber: "2026-001" }],
  ["owner", { owner: { name: "李静" } }],
  ["notes", { notes: "private" }],
  ["department", { department: "工伤认定科" }],
  ["tag", { tagLabel: "首次笔录" }],
]) {
  test("rejects leaked " + label, () => {
    const payload = structuredClone(valid);
    Object.assign(payload.rooms[0], addition);
    assert.throws(() => validatePublicDisplayPayload(payload), /non-public fields/);
  });
}

test("rejects a full visitor name field even when a masked name is present", () => {
  const payload = structuredClone(valid);
  payload.rooms[0].current.partyName = "张女士";
  assert.throws(() => validatePublicDisplayPayload(payload), /non-public fields/);
});

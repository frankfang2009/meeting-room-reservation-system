import assert from "node:assert/strict";
import test from "node:test";
import {
  DEFAULT_UI_PREFERENCES,
  readUiPreferences,
  sanitizeUiPreferences,
  writeUiPreferences,
} from "../src/features/profile/ui-preferences.js";

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
}

test("stores interface preferences separately for each authenticated user", () => {
  const storage = memoryStorage();
  writeUiPreferences("admin", { defaultView: "calendar" }, storage);
  assert.deepEqual(readUiPreferences("admin", storage), { defaultView: "calendar" });
  assert.deepEqual(readUiPreferences("employee", storage), { ...DEFAULT_UI_PREFERENCES });
});

test("sanitizes unknown interface preference values and damaged storage", () => {
  assert.deepEqual(sanitizeUiPreferences({ defaultView: "system", activityMonths: 24 }), { defaultView: "mine" });
  const broken = { getItem: () => "{not-json", setItem: () => { throw new Error("blocked"); } };
  assert.deepEqual(readUiPreferences("user", broken), { ...DEFAULT_UI_PREFERENCES });
  assert.deepEqual(writeUiPreferences("user", { defaultView: "calendar" }, broken), { defaultView: "calendar" });
});

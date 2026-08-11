const STORAGE_PREFIX = "meeting-room-v2:ui-preferences:";

export const DEFAULT_UI_PREFERENCES = Object.freeze({
  defaultView: "mine",
});

export function sanitizeUiPreferences(value) {
  return {
    defaultView: value?.defaultView === "calendar" ? "calendar" : "mine",
  };
}

function storageKey(userId) {
  return `${STORAGE_PREFIX}${String(userId || "anonymous")}`;
}

function availableStorage(storage) {
  if (storage) return storage;
  try { return globalThis.localStorage; } catch { return null; }
}

export function readUiPreferences(userId, storage) {
  const target = availableStorage(storage);
  if (!target) return { ...DEFAULT_UI_PREFERENCES };
  try {
    return sanitizeUiPreferences(JSON.parse(target.getItem(storageKey(userId)) || "{}"));
  } catch {
    return { ...DEFAULT_UI_PREFERENCES };
  }
}

export function writeUiPreferences(userId, value, storage) {
  const next = sanitizeUiPreferences(value);
  const target = availableStorage(storage);
  if (target) {
    try { target.setItem(storageKey(userId), JSON.stringify(next)); } catch { /* storage can be unavailable */ }
  }
  return next;
}

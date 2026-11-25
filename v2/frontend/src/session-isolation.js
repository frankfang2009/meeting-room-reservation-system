import { createElement } from "react";

const DRAFT_STORAGE_PREFIX = "meeting-room-v2:expired-booking-draft:";
const DRAFT_FIELDS = [
  "partyName",
  "caseNumber",
  "purpose",
  "notes",
  "tagId",
  "roomId",
  "date",
  "start",
];

// If removal is denied, retain only the account keys to discard. History state
// survives a reload of this tab without putting draft content or IDs in the URL.
const DEFERRED_CLEAR_KEY = "meetingRoomDraftsToClear";
function readDeferredClears() {
  try {
    const keys = globalThis.history?.state?.[DEFERRED_CLEAR_KEY];
    if (!Array.isArray(keys)) return new Set();
    return new Set(keys.filter((key) =>
      typeof key === "string" && key.startsWith(DRAFT_STORAGE_PREFIX),
    ));
  } catch {
    return new Set();
  }
}
const deferredClears = readDeferredClears();

function setClearPending(key, pending) {
  if (deferredClears.has(key) === pending) return;
  if (pending) deferredClears.add(key);
  else deferredClears.delete(key);
  try {
    const history = globalThis.history;
    if (!history) return;
    const state = { ...history.state };
    if (deferredClears.size) state[DEFERRED_CLEAR_KEY] = [...deferredClears];
    else delete state[DEFERRED_CLEAR_KEY];
    history.replaceState(state, "");
  } catch {
    // If history is also restricted, the current page still honors the discard.
  }
}

function removeStoredDraft(key, storage) {
  try {
    (storage ?? globalThis.sessionStorage).removeItem(key);
    setClearPending(key, false);
  } catch {
    setClearPending(key, true);
  }
}

export function retryDeferredDraftClears(storage) {
  for (const key of deferredClears) removeStoredDraft(key, storage);
  return deferredClears.size === 0;
}

function storageKey(userId) {
  const id = String(userId || "").trim();
  return id ? `${DRAFT_STORAGE_PREFIX}${id}` : "";
}

function sanitizeDraft(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const draft = Object.fromEntries(
    DRAFT_FIELDS.map((field) => [field, String(value[field] || "")]),
  );
  if (DRAFT_FIELDS.every((field) => !draft[field])) return null;
  const duration = Number(value.duration);
  draft.duration = Number.isFinite(duration) && duration > 0 ? duration : 60;
  return draft;
}

export function serializeSessionBookingDraft(userId, { bookingForm, preservedDraft }) {
  const id = String(userId || "").trim();
  if (!id) return null;
  const payload = {
    version: 1,
    userId: id,
    bookingForm: sanitizeDraft(bookingForm),
    preservedDraft: sanitizeDraft(preservedDraft),
  };
  if (!payload.bookingForm && !payload.preservedDraft) return null;
  return JSON.stringify(payload);
}

export function writeSessionBookingDraft(userId, drafts, storage) {
  const key = storageKey(userId);
  if (!key) return drafts.bookingForm || drafts.preservedDraft ? "failed" : "empty";
  try {
    const serialized = serializeSessionBookingDraft(userId, drafts);
    if (!serialized) {
      clearSessionBookingDraft(userId, storage);
      return "empty";
    }
    (storage ?? globalThis.sessionStorage).setItem(key, serialized);
    setClearPending(key, false);
    return "saved";
  } catch {
    // A rejected write can leave an older draft at the same key.
    clearSessionBookingDraft(userId, storage);
    return "failed";
  }
}

export function consumeSessionBookingDraft(userId, storage) {
  retryDeferredDraftClears(storage);
  const key = storageKey(userId);
  if (!key || deferredClears.has(key)) return null;
  try {
    const target = storage ?? globalThis.sessionStorage;
    const raw = target.getItem(key);
    if (!raw) return null;
    const payload = JSON.parse(raw);
    if (payload?.version !== 1 || payload.userId !== String(userId)) return null;
    const drafts = {
      bookingForm: sanitizeDraft(payload.bookingForm),
      preservedDraft: sanitizeDraft(payload.preservedDraft),
    };
    if (!drafts.bookingForm && !drafts.preservedDraft) return null;
    target.removeItem(key);
    return drafts;
  } catch {
    return null;
  }
}

export function clearSessionBookingDraft(userId, storage) {
  const key = storageKey(userId);
  if (key) removeStoredDraft(key, storage);
}

/**
 * When a session is blocked, authenticated children are not rendered at all.
 * Cached administrator pages and secrets therefore cannot remain visible
 * underneath the reauthentication UI.
 */
export function SessionIsolationBoundary({ blocked, reauthentication, children }) {
  if (blocked) {
    return createElement(
      "main",
      { className: "session-isolation-boundary", "data-session-blocked": "true" },
      reauthentication,
    );
  }
  return children;
}

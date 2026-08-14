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

function storageKey(userId) {
  const id = String(userId || "").trim();
  return id ? `${DRAFT_STORAGE_PREFIX}${id}` : "";
}

function sanitizeDraft(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const draft = Object.fromEntries(
    DRAFT_FIELDS.map((field) => [field, String(value[field] || "")]),
  );
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

export function writeSessionBookingDraft(storage, userId, drafts) {
  const key = storageKey(userId);
  if (!key) return false;
  try {
    const serialized = serializeSessionBookingDraft(userId, drafts);
    if (serialized) storage.setItem(key, serialized);
    else storage.removeItem(key);
    return Boolean(serialized);
  } catch {
    return false;
  }
}

export function consumeSessionBookingDraft(storage, userId) {
  const key = storageKey(userId);
  if (!key) return null;
  try {
    const raw = storage.getItem(key);
    if (!raw) return null;
    const payload = JSON.parse(raw);
    if (payload?.version !== 1 || payload.userId !== String(userId)) return null;
    const drafts = {
      bookingForm: sanitizeDraft(payload.bookingForm),
      preservedDraft: sanitizeDraft(payload.preservedDraft),
    };
    if (!drafts.bookingForm && !drafts.preservedDraft) return null;
    storage.removeItem(key);
    return drafts;
  } catch {
    return null;
  }
}

export function clearSessionBookingDraft(storage, userId) {
  const key = storageKey(userId);
  if (!key) return;
  try {
    storage.removeItem(key);
  } catch {
    // Storage can be unavailable in hardened browser profiles; logout still proceeds.
  }
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

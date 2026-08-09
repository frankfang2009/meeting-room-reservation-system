const TOP_LEVEL_KEYS = new Set(["serverDate", "serverTime", "status", "lastUpdatedAt", "rooms"]);
const ROOM_KEYS = new Set(["id", "name", "current", "next"]);
const CALL_KEYS = new Set(["maskedPartyName", "start", "end"]);

function assertPlainObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new TypeError(`${label} must be an object`);
  }
}

function assertOnlyKeys(value, allowed, label) {
  const unexpected = Object.keys(value).filter((key) => !allowed.has(key));
  if (unexpected.length) {
    throw new TypeError(`${label} contains non-public fields: ${unexpected.join(", ")}`);
  }
}

function normalizeCall(value, label) {
  if (value === null || value === undefined) return null;
  assertPlainObject(value, label);
  assertOnlyKeys(value, CALL_KEYS, label);
  if (typeof value.maskedPartyName !== "string" || !value.maskedPartyName.trim()) {
    throw new TypeError(`${label}.maskedPartyName is required`);
  }
  if (typeof value.start !== "string" || typeof value.end !== "string") {
    throw new TypeError(`${label} time range is required`);
  }
  return { maskedPartyName: value.maskedPartyName, start: value.start, end: value.end };
}

/**
 * Validate the dedicated unauthenticated response. This intentionally rejects
 * extra fields instead of stripping them: the browser must never silently
 * accept an internal reservation serializer on the public route.
 */
export function validatePublicDisplayPayload(payload) {
  assertPlainObject(payload, "display payload");
  assertOnlyKeys(payload, TOP_LEVEL_KEYS, "display payload");
  if (!Array.isArray(payload.rooms)) throw new TypeError("display rooms must be an array");

  const rooms = payload.rooms.map((room, index) => {
    assertPlainObject(room, `display room ${index}`);
    assertOnlyKeys(room, ROOM_KEYS, `display room ${index}`);
    if (typeof room.id !== "string" || typeof room.name !== "string") {
      throw new TypeError(`display room ${index} identity is required`);
    }
    return {
      id: room.id,
      name: room.name,
      current: normalizeCall(room.current, `display room ${index}.current`),
      next: normalizeCall(room.next, `display room ${index}.next`),
    };
  });

  return {
    serverDate: String(payload.serverDate || ""),
    serverTime: String(payload.serverTime || ""),
    status: String(payload.status || "online"),
    lastUpdatedAt: String(payload.lastUpdatedAt || ""),
    rooms,
  };
}

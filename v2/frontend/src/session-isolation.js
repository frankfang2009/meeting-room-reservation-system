import { createElement } from "react";

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

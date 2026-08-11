# Repository working rules

- `v2/` is the active Meeting Room Reservation System V2.0.0 codebase.
- V2 is a fresh installation. Never read, migrate, modify, or delete V1 data.
- Preserve the roles `admin | employee`, the authenticated shared-calendar visibility rules,
  employee-owned history boundary, and the server-side public-display allowlist.
- Database generation, identity, and integrity failures must fail closed. Reservation records,
  occupied slots, and events must remain transactionally consistent.
- Production setup binds loopback first and opens the LAN listener only after committed setup.
- Do not add demo passwords, synthetic production records, client-generated business IDs, or
  private-to-public projection in the browser.
- Read `v2/docs/PRODUCT-CONTRACT.md`, `v2/frontend/DESIGN-CONTRACT.md`, and
  `v2/docs/RELEASE-CHECKLIST.md` before changing product behavior or release code.
- Use `v2/scripts/check.sh` for the repeatable local gate. Formal Windows acceptance remains a
  separate external requirement.
- Do not modify V1 directories while working on V2. Do not reset, clean, or overwrite unrelated
  working-tree changes.
- Keep the booking-duration slider on one fixed 30–180 minute visual scale. Its interactive ceiling
  is the earliest of the configured maximum, workday end, and the next active booking in that room.
- Calendar time labels share the horizontal-rule coordinate, and the today view shows a restrained
  server-time line only while the current time is inside configured working hours.
- Calendar and room-management layouts adapt deliberately for one, two, or three active rooms;
  do not leave one- and two-room views occupying only a three-column fraction of the canvas.
- Administrators can jump directly to a calendar date. Room-management metrics refresh from the
  dedicated admin rooms endpoint after booking changes and while the room page remains open.
- Security-audit UI copy is Chinese. Collapsing the audit list never stops polling or changes server
  records; newly received rows accumulate a user-facing unread badge until the list is shown again.
- Upcoming-booking reminders use a small clock badge on the My Reservations rail action instead of
  a persistent bottom toast; opening My Reservations acknowledges that reminder. Change notices
  remain distinct and must not be mislabeled as an upcoming-booking clock.
- Reservation status codes are never rendered directly. History keeps cancelled records visible
  with a restrained `已取消` treatment, while active records retain their tag-color marker.
- Keep the calendar header visually quiet: previous/today/next form one segmented navigation group,
  a compact adjacent date picker supports long jumps, and tag filtering remains separate.
- On a configured LAN service, show a copy action beside the real LAN URL. Clipboard fallback must
  still work over trusted HTTP; never render a copy action for a missing or loopback-only address.
- Deleting a room starts with a read-only server preflight and is confirmed only when no active
  unended bookings remain. Otherwise list the server-returned bookings, support direct adjustment,
  and preserve a visible back path to that blocking list; DELETE must still recheck transactionally.
- The bottom account action opens Personal Center rather than adding another main-navigation item.
  Its activity tab is strictly current-user scoped and counts only active reservations that have
  ended according to server-local time. Keep the approved heatmap free of a low-to-high legend;
  the lower-left overview stays unchanged and the lower-right area contains the four summary metrics
  instead of a recent-completions list. Preferences remain a separate tab in the same page.

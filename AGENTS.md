# Repository working rules

- `v2/` is the active Meeting Room Reservation System V2.1.0 codebase.
- The repository is prepared for public source collaboration under Apache-2.0. Never commit
  customer databases, backups, logs, credentials, private screenshots, or real personal data.
- Public source availability does not authorize publishing an unsigned Windows installer. Keep
  `formal_external_release_allowed=false` until every physical Windows and signing gate passes.
- Public-facing assets and copy must be project-owned or have documented redistribution rights;
  do not add third-party product mimicry, trademarks, or local/private source paths.
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
- The calendar today view scrolls the server-time line near the upper third of the viewport once per
  arrival (entering the view or switching to 今天); clock ticks and user scrolling never reposition it.
- While a booking drawer is open, its source time slot keeps a quiet graphite inset ring that clears
  when the drawer closes and never renders for a date other than the displayed one.
- Mine, history, and Data Center chart loading states use quiet warm-gray skeleton bars without
  shimmer, keeping their page shells and a role="status" announcement.
- Calendar date switches play one short directional slide and the success bar drops in once; the
  first load animates nothing and prefers-reduced-motion remains authoritative.
- Relative-day wording (今天/明天/后天) prefixes date subtitles using the server business date as the
  reference, never the client clock; the 我的预约 hero stays time-only.
- System Status keeps plain-language labels: 服务范围, 数据序号, 备份序号, 接口令牌, and audit times are
  described as converted to the local timezone.
- Calendar and room-management layouts adapt deliberately for one, two, or three active rooms;
  do not leave one- and two-room views occupying only a three-column fraction of the canvas.
- Administrators can jump directly to a calendar date. Room-management metrics refresh from the
  dedicated admin rooms endpoint after booking changes and while the room page remains open.
- Security-audit UI copy is Chinese. Collapsing the audit list never stops polling or changes server
  records; newly received rows accumulate a user-facing unread badge until the list is shown again.
- Upcoming-booking reminders are drawn into the calendar, never a banner or a persistent toast:
  the current user's in-window booking blocks carry a countdown chip (urgent style ≤2 minutes),
  the rail badge shows the in-window count, arrival is announced once via a short toast plus the
  synthesized chime (Personal Center switch, default on), and browsing another date shows a dot on
  the 今天 button. Upcoming reminders are state, not to-dos — they carry no acknowledgement
  action; change notices must never be mislabeled as upcoming-reminder countdowns.
- Change notices use a centered modal that requires explicit confirmation (Esc acknowledges all),
  aggregate every unacknowledged foreign change event, and show actor plus field-level
  before→after diffs from the event snapshots. They defer while any drawer is open (quiet dashed
  chip meanwhile) and are acknowledged per event id; notices survive the owner's own later edits,
  expire after 45 days unconfirmed, and receipts are pruned after 90 days.
- Copying external reminder text is a manual clipboard-only action and must never connect to an
  outbound channel, open WeChat, or claim/record a sent state. Its variable set is fixed to party
  name, date, start, end, and room; case number, purpose, and notes must never enter the template.
- Reservation status codes are never rendered directly. History keeps cancelled records visible
  with a restrained `已取消` treatment, while active records retain their tag-color marker.
- A draft preserved after a slot conflict remains visibly identified on the calendar. Selecting a
  different empty slot must ask whether to relocate that draft or clear it; never silently reuse a
  preserved party or case number after the short toast has disappeared.
- Successful backups are standalone non-WAL SQLite files. Success, failure, and retention cleanup
  must not leave backup `-wal`, `-shm`, `-journal`, or hidden `.part-*` companions behind.
- Keep the calendar header visually quiet: previous/today/next form one segmented navigation group,
  a compact adjacent date picker supports long jumps, and tag filtering remains separate.
- On a configured LAN service, show a copy action beside the real LAN URL. Clipboard fallback must
  still work over trusted HTTP; never render a copy action for a missing or loopback-only address.
- Deleting a room starts with a read-only server preflight and is confirmed only when no active
  unended bookings remain. Otherwise list the server-returned bookings, support direct adjustment,
  and preserve a visible back path to that blocking list; DELETE must still recheck transactionally.
- The bottom account action opens Personal Center rather than adding another main-navigation item.
  Its activity tab is strictly current-user scoped and counts only active reservations that have
  ended according to server-local time. Keep the activity tab concise: no heatmap, date range,
  month labels, daily drilldown, ranking, or recent-completions list. Keep the account identity in
  the page header; present the three overview values and four summary metrics as two quiet,
  full-width horizontal bands that collapse deliberately at narrow desktop widths. Preferences
  remain a separate tab in the same page and must not expose a heatmap-range setting.
- In System Status, work hours and recent backup are matching whole-row drawer entries with a
  trailing chevron. Do not place a standalone edit button inside the work-hours value cell, and
  keep the work-hours drawer action visibly separated from its final time field.
- Booking drawers keep all four tag choices in one compact row. Custom tag names that exceed their
  cell must stay inside the button with ellipsis while the full name remains available on hover and
  through the button's accessible name; never let label text overlap an adjacent tag or form field.

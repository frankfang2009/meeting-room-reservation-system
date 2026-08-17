# Design QA — calendar duration limit and time axis

## Scope

- Dynamic booking-duration ceiling at the next active booking in the same room.
- Calendar time labels aligned to horizontal rules.
- Current server-time line on today's calendar during configured working hours.

## Evidence

- Slider source: `codex-clipboard-a14b815d-64de-425e-9eff-a18aa019e337.png` (764×210).
- Calendar annotation source: `codex-clipboard-d154007b-2019-4383-a089-da524d64d226.png` (2494×992).
- Duration-limit implementation: `meeting-room-v2-duration-limit.png` (1424×890), room 2 at 08:30 with an active booking beginning at 09:00.
- Aligned-calendar implementation: `meeting-room-v2-aligned-calendar.png` (1424×890).
- Current-time implementation: `meeting-room-v2-current-time-line.png` (1280×720), isolated server clock fixed at 2026-08-10 10:15 +08:00.
- Combined comparison inputs: `meeting-room-v2-slider-comparison.png` and `meeting-room-v2-calendar-comparison.png`.

## Interaction checks

- The constrained slider exposes `aria-valuemax="30"` and `aria-valuetext="30 分钟，当前最多 30 分钟"`.
- Pressing End on the constrained slider keeps the value and output at 30 minutes.
- Other-room and cancelled bookings do not reduce the ceiling; the workday end still caps it.
- The current-time line appears only for the selected server-local date and only inside working hours.

## Visual comparison

- The duration control preserves the established full-width 30–180 minute track while the available and selected portions stop at the computed ceiling.
- Time-label centers now share the same vertical coordinate as each horizontal rule.
- The current-time rule is visible between ordinary grid rules without obscuring booking content or adding a conflicting time label.
- Existing typography, colors, room columns, card radii, and responsive structure remain unchanged.

## Findings

- P0: none.
- P1: none.
- P2: none.
- P3: none blocking; current-time rule emphasis can be tuned later from solo-user feedback without changing its coordinate logic.

## History

1. Compared the two supplied references with the live implementation in combined boards.
2. Verified the 30-minute ceiling through a real keyboard interaction.
3. Verified a 10:15 server-time line halfway between 10:00 and 10:30.
4. Ran the complete frontend lint, 85-test, and production-build gate.

## 2026-08-11 — room-count layouts, date jump, and audit disclosure

### Scope

- One-, two-, and three-room calendar widths and room-management composition.
- Direct calendar date input in addition to previous/today/next navigation.
- Live room metrics after reservation writes and while the room page remains open.
- Chinese security-audit projection, collapsible rows, and hidden-state unread badge.

### Evidence

- Calendar reference: `codex-clipboard-d2d04af7-6fd0-4d83-b209-564f89a23a16.png`.
- Room reference: `codex-clipboard-c456e13e-6918-4e52-a1a7-9871cf89a0af.png`.
- Audit reference: `codex-clipboard-63909e53-a8e0-49a9-a94b-811b1cd3e28c.png`.
- Implementation captures: `meeting-room-v2-one-room-calendar-final.png`, `meeting-room-v2-two-room-calendar-final.png`, `meeting-room-v2-one-room-management-final.png`, `meeting-room-v2-two-room-management-final.png`, `meeting-room-v2-audit-visible-final.png`, and `meeting-room-v2-audit-hidden-unread-final.png`.
- Combined comparison inputs: `meeting-room-v2-calendar-comparison-final.png`, `meeting-room-v2-rooms-comparison-final.png`, and `meeting-room-v2-audit-comparison-final.png`.

### Interaction checks

- Filling the native date input with `2026-08-15` updated both the input and the calendar heading without stepping day by day.
- An isolated two-room environment kept equal room columns; an isolated one-room environment used a centered compact calendar and a balanced editorial room summary.
- Creating a real isolated reservation for room 1 changed its future count from 1 to 2 immediately when the room page opened.
- While audit rows were hidden, a newly received audit row produced an accessible badge of 1; showing the rows cleared the badge.
- Audit action, target type, result, filter labels, and helper copy are Chinese while raw action codes remain available only as non-visible diagnostics.

### Visual comparison

- The implementation preserves the established warm paper palette, hairlines, typography, spacing rhythm, and icon family.
- One and two rooms no longer reserve an empty third column or stretch each calendar column to the former three-room width.
- The date control is visually subordinate to today navigation and uses one native calendar indicator.
- The collapsed audit state leaves a compact section boundary; the unread badge is noticeable without competing with system-health status.

### Findings

- P0: none.
- P1: none.
- P2: none blocking. Rail tooltips visible in some captured hover states are the existing navigation feedback, not persistent page content.

## 2026-08-11 — compact calendar controls, LAN sharing, and room deletion

### Scope

- Reduce the calendar header's apparent control count without removing date navigation or filtering.
- Copy the employee-facing LAN URL only when the running service reports a real LAN address.
- Replace the room deletion text zone with a restrained action, confirmation, and actionable booking blockers.

### Evidence

- Calendar-control source: `codex-clipboard-6b264a40-157d-4c85-8e90-c91d602c2fad.png`.
- Room-deletion source: `codex-clipboard-6d92f199-e36f-4011-95c5-3118877f092e.png`.
- 1280×890 implementation captures: `meeting-room-v2-calendar-toolbar-after.png`, `meeting-room-v2-lan-copy-final.png`, `meeting-room-v2-room-delete-button.png`, and `meeting-room-v2-room-delete-blocked.png`.
- Combined comparison inputs: `meeting-room-v2-calendar-toolbar-comparison.png` and `meeting-room-v2-room-delete-comparison.png`.

### Interaction checks

- The displayed calendar date accepts a direct `2026-08-15` jump; the grouped Today action returns to the server business date.
- The calendar exposes one accessible `日期导航` group and keeps tag filtering as a distinct action.
- A service reporting `http://192.168.2.106:8080` renders `复制局域网地址`; clicking it produced the success toast. Missing or loopback-only addresses remain without the action by render condition.
- Deleting room 1 first opens a confirmation. The server then returned its active blocking booking, the drawer displayed date/time/party/owner/case number, and `调整预约` opened the real booking edit form.

### Visual comparison

- Previous, Today, and Next now read as one segmented navigation object; the date picker moved into the displayed date, reducing the header from five separate bordered controls to two control groups.
- The LAN copy action is secondary to the URL and follows existing compact system-row controls.
- The former underlined danger heading and explanatory block are replaced by one quiet outlined button. Confirmation and blocker states reuse the existing drawer, icon family, paper palette, hairlines, and typography.

### Findings

- P0: none.
- P1: none.
- P2: none.

## 2026-08-11 — fused date navigation and room-deletion preflight

### Scope and state

- Authenticated administrator view at `http://127.0.0.1:8080/`.
- Calendar at 2026-09-15 after a direct cross-month jump.
- Room 1 deletion with two active unended bookings, its booking-adjustment screen, and Room 3 deletion with no active unended bookings.
- Browser viewport/CSS size: 1280×720 at device scale 1.

### Source visual truth

- Segmented navigation: `codex-clipboard-1f692450-ef8c-421d-8e4e-8703e7825c89.png` (512×184).
- Direct date picker: `codex-clipboard-1c912deb-a275-4828-b29b-734b756e9aa0.png` (626×310).
- Previous room-delete section: `codex-clipboard-6d92f199-e36f-4011-95c5-3118877f092e.png` (790×198).

### Implementation evidence

- Calendar toolbar: `meeting-room-v2-calendar-tools-v2.png` (1280×720).
- Blocking-booking list: `meeting-room-v2-room-delete-blocked-v2.png` (1280×720).
- Adjustment screen with back action: `meeting-room-v2-room-edit-back-v2.png` (1280×720).
- Clear-room confirmation: `meeting-room-v2-room-delete-clear-confirm-v2.png` (1280×720).
- Combined full-view comparisons: `meeting-room-v2-calendar-tools-comparison-v2.png` (1280×1060) and `meeting-room-v2-room-delete-comparison-v2.png` (1280×1500).
- Density normalization: source controls were proportionally contained without resampling the implementation; comparison judges composition and component language, not false pixel-for-pixel equality across different crops.

### Focused comparison and interactions

- The toolbar keeps previous/today/next as one segmented object, adds one adjacent bordered date picker, and leaves filtering as the final independent action. Filling the native date input with `2026-09-15` updated both the visible value and calendar in one interaction.
- Room 1 skipped the destructive confirmation and opened the server-returned blocking list. Opening the 11:00 booking exposed a top-left `返回待处理预约` action; activating it restored the same two-booking list.
- Room 3 had no blocking bookings and therefore opened the restrained confirmation state directly. The destructive action was not executed during QA.
- Focused regions were necessary for the compact toolbar, booking cards, drawer title/back affordance, and confirmation footer; the saved implementation captures keep each control readable at the tested viewport.

### Required fidelity surfaces

- Fonts and typography: existing Chinese system stack, optical weights, numeric alignment, headings, and helper-copy hierarchy are preserved.
- Spacing and layout rhythm: all three calendar controls share a 46px height and 9px radius; drawer padding, hairlines, card rhythm, and fixed destructive footer align with the existing system.
- Colors and tokens: warm paper surfaces, graphite text, hairlines, and restrained terracotta danger semantics reuse existing tokens without introducing a competing palette.
- Image and asset quality: the screens use the existing Phosphor icon library; no raster placeholder, CSS-drawn icon, handcrafted SVG, or generated asset was introduced.
- Copy and content: destructive copy now names the room, explains what is retained, distinguishes blocked and clear states, and gives a direct adjustment path.

### Findings

- P0: none.
- P1: none.
- P2: none.
- P3: the native date field shows a stronger browser focus ring while actively edited; this is acceptable accessibility feedback and not persistent visual noise.

### Comparison history

1. Earlier implementation hid the date picker inside the heading and always showed confirmation before discovering booking blockers.
2. The fix restored a compact independent date picker, added a read-only server preflight, split blocked/clear states, and preserved a return target while adjusting a booking.
3. Post-fix combined boards and real browser interactions showed no remaining actionable P0/P1/P2 mismatch.

### Implementation checklist

- [x] Direct cross-month date jump.
- [x] Read-only deletion preflight before destructive confirmation.
- [x] Blocking-booking list with direct adjustment actions.
- [x] Top-left return to the original blocking list.
- [x] Transactional DELETE recheck for races.
- [x] Clear-room confirmation in the established drawer style.

## 2026-08-11 — navigation reminder and cancelled-history status

### Scope and state

- Authenticated administrator at `http://127.0.0.1:8080/`, 1280×720 CSS viewport, device scale 1.
- Upcoming reminder present on My Reservations, then acknowledged by activating that rail action.
- August 2026 history with two active and two cancelled records; cancelled record details open.

### Source and implementation evidence

- History-list source: `codex-clipboard-050294a6-af78-45a6-bcfc-40079e0ba13e.png` (2848×1678).
- Cancelled-detail source: `codex-clipboard-fad8087a-5ffa-44e1-af5c-72e2ccb570b1.png` (756×1524).
- Navigation badge implementation: `meeting-room-v2-upcoming-rail-badge.png` (1280×720).
- History implementation: `meeting-room-v2-history-cancelled-zh.png` (1280×720).
- Cancelled details implementation: `meeting-room-v2-cancelled-details-zh.png` (1280×720).
- Combined visual input: `meeting-room-v2-history-status-comparison.png` (1600×1600).
- Density normalization: the source crops and full implementation captures were proportionally contained in one board. The comparison evaluates hierarchy and state treatment, not false pixel equality across different source crops.

### Interaction and focused comparison

- The upcoming reminder rendered as a small terracotta clock badge at the top-right of the My Reservations rail icon; the former bottom upcoming toast was absent.
- The accessible name became `我的预约，有预约即将开始`. Activating it kept the user on My Reservations, acknowledged the server reminder, and removed the badge.
- Change notifications remain a separate bottom notification path and are not represented by the upcoming clock.
- Cancelled history rows retain date, time, room, case number, and navigation affordance, but use lower-contrast primary text plus a thin `已取消` pill in place of the tag-color dot.
- Opening a cancelled row rendered `已取消`; the raw `cancelled` API status no longer appears in the interface.

### Required fidelity surfaces

- Fonts and typography: existing Chinese system stack, weights, line heights, and numeric alignment remain unchanged; the 11px status pill stays subordinate to the booking summary.
- Spacing and layout rhythm: the status pill fits the existing 86px trailing column, and the 18px rail badge stays inside the 52px navigation hit target.
- Colors and tokens: muted cancelled text and restrained terracotta outlines preserve the warm-paper palette without turning a historical record into an error state.
- Image and asset quality: the clock uses the existing Phosphor icon package; no fake, handcrafted, or rasterized icon was introduced.
- Copy and content: status projection is fully Chinese (`已预约`, `已取消`, `状态未知`) and cancelled records remain auditable.

### Findings

- P0: none.
- P1: none.
- P2: none.
- P3: the badge is intentionally icon-only; its tooltip and accessible name carry the full reminder text without widening the narrow rail.

### Comparison history

1. Earlier upcoming reminders occupied the bottom of the viewport, and cancelled rows were visually indistinguishable until details exposed the raw English status.
2. The fix moved upcoming state to the rail, added acknowledgement on My Reservations activation, introduced a restrained cancelled-row projection, and centralized Chinese status labels.
3. Post-fix browser interaction and the combined source/implementation board showed no actionable P0/P1/P2 mismatch. No product-specific console error was observed during these flows.

### Implementation checklist

- [x] Upcoming reminder clock badge.
- [x] Badge acknowledgement and disappearance on My Reservations activation.
- [x] Change-notification path remains separate.
- [x] Cancelled history list treatment.
- [x] Chinese detail status mapping with unknown-status fallback.
- [x] Keyboard/accessibility text preserved.

## 2026-08-11 restrained cancellation treatment and status filtering

### Evidence

- Source visual truth: `codex-clipboard-fc22e34b-e389-4eb9-bd02-fa3698288dfe.png`, 2730×1170 px. The user explicitly rejected the visible red left rule and requested a more restrained treatment.
- Rendered list: `meeting-room-v2-history-restrained-status.jpg`, 1280×720 px at a 1280×720 CSS viewport and device scale 1.
- Rendered filter: `meeting-room-v2-history-status-filter.jpg`, 1280×720 px at a 1280×720 CSS viewport and device scale 1.
- State: authenticated administrator, August 2026 history, four mixed-status records; filter was exercised in `全部`, `正常预约`, and `已取消` states.
- Density normalization: the source and implementation use different viewport sizes, so comparison was limited to the focused row treatment, typography hierarchy, trailing status control, and filter composition rather than claiming pixel-level full-page parity.

### Full-view and focused comparison

- The source, final list, and final filter were opened together in one comparison input. The rejected red rule is absent, and cancelled rows retain the same date, time, room, and case-number contrast as active rows.
- Cancellation is communicated only by the small `已取消` pill; the tag dot remains reserved for active records.
- The new three-way status control follows the existing scope-card radio language and fits the 404px filter panel without wrapping or changing surrounding sections.

### Interaction and contract verification

- `已取消` returned two cancelled records and updated both totals to 2.
- `正常预约` returned two active records and updated both totals to 2.
- `全部` restored all four records. Reset clears the status alongside the other filters.
- The backend accepts only `active` or `cancelled`, returns 422 for other values, and binds status to the signed pagination cursor context.
- Opening a cancelled record still shows the Chinese `已取消` detail and its change timeline.
- No product-specific console error was observed. A host-owned Statsig request to `ab.chatgpt.com` was blocked by the browser harness and is unrelated to the local application.

### Required fidelity surfaces

- Fonts and typography: unchanged between active and cancelled rows; no opacity or color reduction remains.
- Spacing and layout rhythm: removing the rule restores the original 4px row inset; the status filter reuses the established 44px choice targets.
- Colors and tokens: only the restrained terracotta status pill distinguishes cancellation; no new high-weight state color remains in the list.
- Image and asset quality: no new assets were introduced; existing Phosphor controls remain intact.
- Copy and content: status choices are `全部`, `正常预约`, and `已取消`; API values remain internal.

### Findings and comparison history

- P0: none.
- P1: none.
- P2: none.
- P3: none.
- Earlier iteration: a 3px terracotta left border was added to cancelled rows. Two consecutive cancelled rows visually merged into a heavy vertical block.
- Fix: the border and its padding compensation were removed; text contrast stayed unchanged and the status pill was retained.
- Post-fix evidence: the final restrained list and three functional status states show no actionable visual or interaction regression.

### Implementation checklist

- [x] Rejected red rule removed.
- [x] Active and cancelled row text contrast identical.
- [x] Restrained Chinese cancellation pill retained.
- [x] Server-backed `全部 / 正常预约 / 已取消` filtering.
- [x] Status-bound pagination cursor and invalid-value rejection.
- [x] Browser interaction, automated tests, and production build passed.

final result: passed

## 2026-08-17 adaptive trend granularity and duration labels

### Evidence

- Browser capture: `data-center-week-duration-numeric.png` at the local 1280×864 review viewport (local QA artifact, not committed).
- Authenticated administrator overall scope with the rich synthetic August database.
- Automated coverage includes clipped partial weeks, the `> 90` day monthly switch, clipped final months, and zero-filled calendar-month API buckets.

### Interaction and presentation verification

- Short date ranges render `预约周趋势`; long date ranges render `预约月趋势` with calendar-month labels.
- Hover, keyboard focus, and click-to-pin expose the exact selected interval plus both effective booking count and total duration.
- Duration mode keeps bar-top labels numeric-only (`9.5 / 29.5 / 23 / 4` in the exercised view); the selected summary retains the meaningful unit (`4场 · 4小时`).
- Screen-reader tables retain unambiguous ISO start/end dates and duration minutes for both grains.

### Verification

- Frontend tests: 152 passed.
- Backend report tests: 11 passed.
- Cross-layer release contract tests: 21 passed.
- ESLint, Ruff, production build, and full `v2/scripts/check.sh`: passed.

final result: passed

## 2026-08-17 personal-center settings-only consolidation

### Evidence and result

- Browser capture: `personal-center-settings-only.png` (local QA artifact).
- Authenticated administrator opened Personal Center from the bottom account action.
- The page opens directly to `个人资料`, `预约偏好`, `使用习惯`, `个人标签`, `通知`, and `对外提醒模板`.
- `我的活动`, internal tabs, activity summaries, and the duplicate Data Center link are absent.
- Identity, `退出登录`, and `保存更改` remain visible in the compact header; no browser console error was observed.
- The production entry no longer issues a report request when Personal Center opens. Data analysis remains in the role-scoped Data Center.

final result: passed

## 2026-08-17 data-center focused-page redesign

### Evidence

- Approved overview source: `exec-b2aa2746-cf6b-49ea-bd33-cbd7d8fee9b9.png` (local review artifact, not committed).
- Approved time source: `exec-d1f29ffc-0d69-4286-87de-24d8f0c4708d.png` (local review artifact, not committed).
- Approved room source: `exec-fdce97b4-7f20-4cb6-b9e7-948ce487d6fa.png` (local review artifact, not committed).
- Approved tag source: `exec-a651b0e5-a9fb-43cd-83b0-ef152190d7b2.png` (local review artifact, not committed).
- Implementation captures: `data-center-overview-implementation.png`, `data-center-time-rest-implementation.png`, `data-center-time-hover-implementation.png`, `data-center-room-implementation.png`, and `data-center-tags-implementation.png` (local QA artifacts).
- Combined comparison boards: `data-center-overview-comparison.png`, `data-center-time-comparison.png`, `data-center-room-comparison.png`, and `data-center-tags-comparison.png` (local QA artifacts).
- Responsive capture: `data-center-employee-1024.png` at a 1024×720 CSS viewport (local QA artifact).

### Visual and interaction verification

- The overview uses one compact metric strip and one trend chart; the former stacked icon/card hierarchy is absent.
- The time page has no crosshair or guide lines. Counts remain hidden at rest and appear in white inside the active dot only for values of two or more; the peak summary updates on hover.
- Room and tag analysis each have their own page. Room rows use a restrained lollipop comparison and the tag page uses a 100-dot composition rather than placing all charts on one canvas.
- The unassigned tag category is stone gray, while the two used categories are muted clay and sage. No warning-red treatment remains.
- Trend bars, time dots, room rows, and tag categories expose hover/focus and click-to-pin states. Screen-reader tables preserve the exact values.

### Scope and responsive verification

- Administrator overall scope exposes `概览 / 时段分布 / 笔录室 / 标签` and the user selector.
- Administrator person scope exposes only `概览 / 时段分布 / 标签` for the selected employee.
- Employee scope exposes `我的数据` without a user selector and only `概览 / 时段分布 / 标签`; the CSV action remains scoped by the server.
- Personal scope keeps the cancellation count but omits the redundant explanatory note beneath it.
- The 1440×1024 reference viewport and 1024×720 workspace both preserve readable hierarchy without page-level horizontal overflow.
- Browser console logs were empty during the exercised flows.

### Findings

- P0: none.
- P1: none.
- P2: none.
- P3: production typography and spacing are slightly denser than the exploratory mockups so the pages remain consistent with the existing 1240px application canvas.

### Automated verification

- Frontend tests: 152 passed.
- ESLint: passed.
- Production build: passed.

final result: passed

## 2026-08-17 data-center half-hour dot distribution

### Evidence

- Approved visual source: `exec-368c2045-63ab-440b-861b-60ca89700ba3.png`, 1703×923 px (local review artifact, not committed).
- Final full-page browser capture: `data-center-dot-matrix-final-1440.png`, 1440×900 px at a 1440×900 CSS viewport and device scale 1 (local QA artifact).
- Focused implementation crop: `data-center-dot-matrix-implementation-crop.png`, 1240×475 px (local QA artifact).
- Combined source/implementation board: `data-center-dot-matrix-comparison.png`, 1200×1126 px (local QA artifact).
- Responsive evidence: authenticated administrator, overall scope, rich synthetic August data at 1024×720 and 1440×900.

### Full-view and focused comparison

- The approved dot-matrix direction is preserved: weekday rows, continuous time columns, terracotta circles, restrained peak ring, compact peak summary, and a three-step density legend.
- The user-approved product correction removes the mockup's invented `午休` break. The final axis follows the configured continuous 08:30–17:30 work window and includes the final 17:00 half-hour slot.
- The production version uses the existing 1240px content canvas and a tighter vertical rhythm than the exploratory mockup. This keeps the data center consistent with its surrounding metrics and trend panel without weakening the chart hierarchy.

### Required fidelity surfaces

- Fonts and typography: existing PingFang/system stack retained; axis labels, weekday labels, summary, and legend use the established report hierarchy.
- Spacing and layout rhythm: seven rows remain immediately comparable; hourly labels reduce axis noise; the peak summary occupies the existing top-right chart space.
- Colors and tokens: existing warm-paper and terracotta report tokens are reused. Density is encoded through size and opacity rather than a rainbow heatmap.
- Image and asset quality: no decorative assets or handcrafted icons were introduced; the circles are native quantitative marks.
- Copy and content: `最高峰 / 当前时段`, weekday, exact half-hour range, count, and `少 / 中 / 多` are concise and data-backed.

### Interaction and accessibility verification

- Hovering or focusing a nonzero circle updates the chart summary to that weekday, time range, and count; leaving the circle restores the peak summary.
- Nonzero circles expose Chinese accessible names and keyboard focus. Empty cells stay out of the tab order, and the complete nonzero matrix is available in a screen-reader-only table.
- The 1440px viewport has no page or chart overflow. At 1024px, the page remains stable and the chart alone uses a small, deliberate horizontal scroll to protect legibility.
- `时段分布 / 笔录室 / 标签` tabs remained functional, and the browser console reported no warnings or errors.

### Findings

- P0: none.
- P1: none.
- P2: none.
- P3: exact counts are intentionally interaction-led so the default view stays quiet; the peak value remains visible without interaction.

### Comparison history

1. The first implementation used a floating pseudo-tooltip; it widened the chart and produced desktop horizontal overflow.
2. The tooltip was replaced with the stable top-right `当前时段` summary. Post-fix desktop chart metrics were 1240px client width and 1240px scroll width.
3. After user review, the mockup's lunch marker was removed and the configured workday became one continuous half-hour scale.
4. The final combined comparison and browser passes show no actionable P0/P1/P2 mismatch.

### Implementation checklist

- [x] Continuous half-hour weekday dot matrix.
- [x] Peak ring and concise peak summary.
- [x] Hover, keyboard focus, and accessible data table.
- [x] No invented lunch break or utilization percentage.
- [x] Responsive containment at 1440×900 and 1024×720.
- [x] Browser interaction, console, automated tests, and production build passed.

final result: passed

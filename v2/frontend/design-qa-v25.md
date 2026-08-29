# V2.5 frontend design QA

## Help Center

- Selected reference: approved Help Center entry mockup, option 1.
- Runtime checked: production frontend and Flask service at `http://127.0.0.1:18087/`
- Data boundary: copied synthetic preview database in an untracked temporary directory; no customer data used.
- Viewport inspected: 1280 x 720.

## Reference fidelity

- The Help Center uses the approved bottom utility placement directly above the avatar.
- Only the Help Center icon carries the terracotta active indicator while this view is open.
- The main canvas preserves the warm ivory, graphite, restrained terracotta, fine-rule visual language.
- The page uses one wide search surface, two low-emphasis role shortcuts, and a sparse three-column category index.
- The experimental label remains intentionally small and no longer wraps at compact heights.
- The 720px-height rail keeps both Help Center and avatar fully visible.

## Interaction checks

- Login page exposes a low-emphasis Help Center link.
- Authenticated Help Center opens inside the existing product shell.
- Search for `交接` returns the expected quick result and polite status text.
- Category and article reading remain inside the main canvas through a same-origin offline reader.
- Public `/help/` renders all 55 articles and 9 categories without authentication.
- Browser console: no warnings or errors on login, Help Center home, or public Help Center.

## Deviations from the generated reference

- Category counts use the real content inventory instead of decorative mock values.
- A compact offline/content-count note was retained to make the static boundary explicit.
- Full category/article reading adds a 64px return toolbar; the reference covered only the Help Center home state.

final result: passed

# V2.5 unified idle-state design QA — 2026-08-29

- Source visual truth: selected option 1（用户批准的生成设计稿；原始文件保存在本机私有目录，不随仓库分发）。
- Implementation evidence: `v2/frontend/qa/v25-idle/implementation-handover-968x796.png`.
- Review viewport: 968 × 797 CSS px in the local V2.5 preview at `http://127.0.0.1:8080/`; the browser reports DPR 2 and supplied a normalized 968 × 797 PNG. The generated source is 1487 × 1058 px with no declared CSS density, so the comparison is component-level rather than pixel-for-pixel.
- State reviewed: administrator “工作交接” with no incoming or outgoing requests. The source is the selected generic no-room template; its centered unboxed hierarchy is intentionally applied to the semantically different no-handover state, which has no recovery action.

## Full-view and focused comparison

- The source and rendered implementation were opened together for one comparison pass. The preserved rail, warm ivory canvas, centered icon/title/supporting-copy stack, and single-action treatment (where the state has an action) match at the component level.
- Focused review was limited to the idle stack and its relation to the empty canvas. The source does not provide a separate handover state, so room-specific wording/icon/action differences are intentional product semantics rather than fidelity defects.

## Required fidelity surfaces

- Typography: the implementation keeps the product system sans stack, strong dark title hierarchy, muted 14px supporting copy, and does not introduce display typography or wrapping pressure.
- Spacing and layout rhythm: the idle stack has one icon-to-title gap, one title-to-copy gap, and one optional action gap. It is centered in the available content canvas; no card, extra divider, or second column was introduced.
- Colors and tokens: warm ivory canvas, graphite text, muted gray icon/copy, black primary action, hairline outline action, and handover’s restrained terracotta icon all use existing product tokens or the established neutral palette.
- Image and asset fidelity: no raster artwork, custom SVG, emoji, CSS drawing, or placeholder was added. Existing Phosphor icons remain the only state visual.
- Copy and content: each state gives one precise reason and only a genuine recovery action. History filter zero remains compact (“没有符合条件的记录 / 清除筛选”), while the true-empty month state opens the existing month selector.

## Comparison history

- Pass 1 — [P1] Handover idle stack was offset left inside the full-width ledger canvas. Evidence: the icon/title centered inside a 460px box whose own left edge remained aligned to the content column. Fix: set `.handover-page-empty.idle-state` to `margin: 0 auto` and added a regression check.
- Pass 2 — no actionable P0/P1/P2 differences. The rendered handover stack is centered. “我的预约” verified the black 46px recovery action; its computed background is `rgb(20, 20, 19)` and minimum height is `46px`.

## Interaction and runtime checks

- “前往预约日历” opens the existing real booking drawer with the preferred active room and first available slot.
- “查看其他月份” opens the existing selectable month menu.
- The handover state remains action-free as intended.
- Browser console: no warnings or errors after the empty-state navigation and action checks.

## Residual scope

- The local synthetic preview currently contains active rooms, so the administrative no-room canvas and room-management no-room page were validated through component/contract tests, not a separate browser-rendered fixture. This is a coverage gap for the temporary preview data only; it is not an actionable product-fidelity defect.

final result: passed

# V2.5 role-aware icon rail design QA — 2026-08-28

- Source visual truth: `v2/frontend/qa/v25-rail/source-option-2.png`.
- Implementation screenshots: `v2/frontend/qa/v25-rail/implementation-admin-1280x720.png` and `v2/frontend/qa/v25-rail/implementation-employee-1280x720.png`.
- Combined comparison: `v2/frontend/qa/v25-rail/comparison-final.png`.
- Viewports checked: 1024 x 720, 1280 x 720, 1440 x 900, and 1920 x 1080.
- Pixel context: the source is a 1536 x 1024 generated component board; the final screenshots are 1280 x 720 CSS pixels and were compared at component level.

## Reference fidelity

- The existing 80px icon-only rail remains intact.
- Every business, Help Center, and Personal Center icon uses the same Phosphor regular 24px optical box inside a 46px action target.
- Administrator navigation contains eight permission-available business actions; employee navigation contains five and adds no placeholder gaps for unavailable actions.
- Business actions share one top anchor with 12px normal-height and 8px compact-height rhythm; Help Center and Personal Center remain bottom anchored.
- Business, Help Center, and Personal Center selected states use the same terracotta left marker and soft terracotta surface.
- Typography, copy, warm-neutral palette, and existing icon asset family are unchanged.

## Interaction checks

- Administrator and employee login, business navigation, Help Center, Personal Center, and logout were exercised.
- At every checked viewport, the rail had no horizontal overflow and all Help Center and Personal Center actions remained visible.
- Browser console: no warnings or errors after the navigation and role-switch checks.

## Comparison history

- Pass 1 found that the compact-height brand declaration was overridden by the later shared 46px action rule; the cascade order was corrected.
- Pass 2 found that hovering the active Personal Center action replaced its selected surface with neutral gray; the active-hover rule now preserves the selected treatment.
- Pass 3 compared the final administrator and employee implementation against the selected option 2 reference; no P0, P1, or P2 fidelity issues remained.

final result: passed

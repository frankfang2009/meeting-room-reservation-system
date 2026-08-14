# V2.0.0 代码审查修复清单（给 Codex）

> 来源：2026-08-14 三路深度审查（UX / 隐私安全 / 架构）+ 主审人逐行复核。
> 本清单是任务书：请逐项修复、逐项验收、逐项提交。未列出的问题不要顺手改动。

## 0. 工作范围与铁律（先读）

- 仓库：`03_当前V2项目/meeting-room-v2`，分支 `codex/v2.0.0-baseline`，当前 HEAD `f0fe7c1f`。
- 动手前必读：仓库 `AGENTS.md`、`v2/docs/PRODUCT-CONTRACT.md`、`v2/docs/API-CONTRACT.md`、
  `v2/docs/RELEASE-CHECKLIST.md`、`v2/frontend/DESIGN-CONTRACT.md`、`v2/frontend/src/styles/README.md`。
- 每个修复按此顺序闭环：**改代码 → 同步改测试 → 同步改受影响契约/文档 → 跑
  `v2/scripts/check.sh` 全绿 → 提交**。没有测试护住的修复不算完成。
- 提交信息用中文，格式建议 `fix: F01 预约详情越权读取已取消记录`；提交前 `git diff --check`。
- 样式改动必须按 `styles/README.md` 更新 `tests/styles-structure.test.mjs` 的冻结 SHA。
- 涉及行为变化时，同步更新 `v2/docs/RELEASE-CHECKLIST.md` 顶部"当前增量修复证据"表（编号续接 E13、E14……）。
- **禁止事项**（违反即返工）：动 V1 目录；引入演示数据/默认密码；在浏览器生成业务 ID；
  把私有字段投影到公开大屏；放宽任何 fail-closed 检查；改角色枚举；改安装器签名/六件套发布物；
  把 `formal_external_release_allowed` 改为 true（本轮不涉及外发）。
- 若某项修复与冻结契约（DESIGN-CONTRACT）冲突：**优先改实现对齐契约**；确需改契约的
  （清单已标注"需用户拍板"），停下来问用户，不要自行改契约。

## 1. 第一批：安全与数据边界（优先完成）

### F01 预约详情可越权读取他人"已取消"记录 【P0 · 安全】

- [x] 位置：`v2/backend/v2app/services/reservations.py:714-718`（`get_reservation`）；入口 `v2/backend/v2app/api/reservations.py:45-48`。
- [x] 现状：任何登录用户 GET `/api/v1/reservations/{id}` 可读取任意**已取消**预约的
  `partyName/caseNumber/purpose/notes` 全量（`serialize_reservation` 不分状态全输出，`:198-219`），
  绕过"预约记录仅本人"边界（PRODUCT-CONTRACT 第 2 节）。员工只要在预约 active 时见过共享日历中的 ID，
  取消后仍可长期读取。`list_events` 有本人/管理员校验（`:727-728`），本处不对称。
- [x] 期望：非 admin 且非 owner 时，仅允许读取 `status='active'` 的记录；`cancelled` 返回
  403 `FORBIDDEN`（与 list_events 一致）。owner 与 admin 行为不变。
- [x] **不得收窄**：employee 对他人 active 预约的完整详情仍要可见（共享日历契约）。
- [x] 验收：`v2/backend/tests/test_backend.py` 或 `test_hardening.py` 新增用例：
  employee 读他人 active 成功；employee 读他人 cancelled → 403；owner 读本人 cancelled 成功；
  admin 读任意 cancelled 成功。

### F02 笔录室页轮询豁免了 30 分钟空闲会话 【P1 · 安全契约】

- [x] 位置：`v2/backend/v2app/security.py:22-27`（`_PASSIVE_SESSION_PATHS`）。
- [x] 现状：管理员停在笔录室页时前端每 30 秒 `GET /api/v1/rooms`（`App.jsx:1127-1132`），
  该路径不在被动白名单，持续刷新 `_last_active_at`，空闲超时在该页事实上失效，
  违背 PRODUCT-CONTRACT 第 6 节"后台轮询不延长空闲会话"。
- [x] 期望：把 `/api/v1/rooms` 加入被动路径集合（已确认被动判定同时限定 `GET`）。
- [x] 验收：`test_hardening.py` 新增用例：在注入的 SESSION_TIME_PROVIDER 下，
  GET `/api/v1/rooms` 不更新 `_last_active_at`；非被动 GET（如 /reservations）仍更新。
  注意别破坏管理员房间统计刷新（E4 有相关测试，跑全量确认）。

### F03 备份失败时审计写入异常会吞掉 `BACKUP_FAILED` 错误码 【P1 · 可靠性】

- [x] 位置：`v2/backend/v2app/api/system.py:191-200`。
- [x] 现状：`with transaction(...): write_security_audit(...)` 若抛异常，跳到全局 500
  `INTERNAL_ERROR`，同一故障呈现两种错误码。
- [x] 期望：审计写入包独立 try/except（失败仅 `logger.exception`），
  `raise ApiError(500, "BACKUP_FAILED", ...)` 恒定执行。
- [x] 验收：测试注入"备份管道失败且审计写入也失败"（用现有 failpoint/mock 机制），
  断言响应仍是 `BACKUP_FAILED`。

### F04 slots 唯一约束冲突兜底映射为 409 而非 503 【P1 · 一致性】

- [x] 位置：`v2/backend/v2app/services/reservations.py:327-363`（create）与 `:462-472`（update）的
  `executemany`；参考已有先例 `v2/backend/v2app/api/admin.py:152-153`。
- [x] 现状：正常路径靠 `BEGIN IMMEDIATE` 串行化，但极端竞争下 IntegrityError 落到全局
  `sqlite3.Error` handler → 503 `DATABASE_UNAVAILABLE`，误导排障。
- [x] 期望：捕获 `sqlite3.IntegrityError` 且确认是 slots 冲突时 → 409 `SLOT_CONFLICT`（带 conflicts）；
  其余错误继续上抛。
- [x] 验收：测试先直插一条占用行再调用 create/update，断言 409 + SLOT_CONFLICT。

### F05 `/healthz` 的 `recovery_code` 仅回环可见 【P2 · 信息暴露】

- [x] 位置：`v2/backend/v2app/__init__.py:214-217`。
- [x] 现状：LAN 上任意机器可读到 `recovery_code`（`install_id` 已正确限制为回环）。
- [x] 期望：`recovery_code` 与 `install_id` 一样仅在 `remote_is_loopback()` 时输出。
- [x] 注意：确认 `service.py` 健康探测（本机回环）与 `frontend/src/setup-restart.js` 重启探测不受影响。
- [x] 验收：测试 LAN 来源无 recovery_code、回环来源有。

## 2. 第二批：UX 缺陷与契约漂移

### F06 键盘用户无法滚动长页面 【P1 · 无障碍】

- [x] 位置：`v2/frontend/src/styles/foundation.css:38`（`body { overflow: hidden }`）、
  `v2/frontend/src/styles/shell.css:156-168`（`.main-canvas` 滚动容器无 tabindex、滚动条隐藏）。
- [x] 期望：`.main-canvas` 成为可聚焦滚动容器（加 `tabindex="0"` + 可见 `:focus-visible` 样式），
  恢复可见滚动条（至少 thin 样式）；或放开 body 滚动。历史/审计/用户长页可用 PgUp/PgDn/空格滚动。
- [x] 验收：更新 `styles-structure.test.mjs` 冻结 SHA；`production-source.test.mjs` 增加
  main-canvas tabindex 断言；真实浏览器键盘滚动回归。

### F07 会话过期未保存内容静默丢失（与冻结契约相悖）【P1 · 数据保护】

- [x] 位置：`v2/frontend/src/App.jsx:746-780`（SessionExpired 文案"未保存内容将被清除"）、
  `v2/frontend/src/session-isolation.js:8-17`。
- [x] 契约：`DESIGN-CONTRACT.md:93` 要求"unsaved content is preserved"并在重新登录后恢复草稿。
- [x] 期望（默认方案）：会话过期时把未保存的预约表单（partyName/caseNumber/purpose/notes/tagId/
  roomId/date/start/duration 及 preservedDraft）写入 `sessionStorage`，**按登录用户 ID 键隔离**；
  重新认证成功且 session+bootstrap 校验通过后恢复草稿并提示"已恢复未保存的预约草稿"；
  主动登出时清空。文案同步改为"未保存内容已保留"。
- [x] 注意：不同账号互不可见；恢复必须发生在重新认证成功之后（沿用现有 scopedAppKey remount 机制）。
- [x] 验收：新增前端测试（序列化/隔离纯函数）；更新 `production-source.test.mjs` 文案断言；
  真实浏览器回归：填表→等会话过期→重登→草稿恢复。
- [x] 若用户选择"修订契约而非保留草稿"，先问用户，不要自行决定。

### F08 日历方向键导航在禁用格存在时坐标错位 【P1 · 无障碍】

- [x] 位置：`v2/frontend/src/App.jsx:1553-1568`（`moveCalendarFocus`）。
- [x] 现状：剔除 disabled 格后按 `indexOf + activeRooms.length` 步进，行不等长时上下键跳错
  （下午大量 past-slot 被禁用时必现），违背 DESIGN-CONTRACT:127 的方向键承诺。
- [x] 期望：按 (row, col) 网格坐标计算相邻格（每房间一列），或为禁用格保留等长占位。
- [x] 验收：把移动逻辑抽成纯函数进 `domain.js` 并加单测（含禁用格混合场景）；浏览器验证下午时段。

### F09 时长滑块缺 4 个内部刻度点 【P2 · 契约漂移】

- [x] 位置：`v2/frontend/src/App.jsx:812`（BookingForm）；样式 `.duration-slider-stop` 已存在未用
  （`booking-forms.css:190-242`）。
- [x] 契约：DESIGN-CONTRACT:84 要求 60/90/120/150 四个内部刻度可见，两端点无点。
- [x] 期望：渲染 4 个 stop，三态（已选/可用/不可用）与当前 duration 和动态上限联动。
- [x] 注意：不改 AGENTS.md 规定的固定 30–180 视觉比例尺与动态上限逻辑（上限=配置最大、
  工作时段结束、同室下一场预约三者最早）。
- [x] 验收：`production-source.test.mjs` 增加 stop 渲染断言；浏览器截图。

### F10 日历加载态无骨架屏、切日旧数据闪现 【P2 · 契约漂移】

- [x] 位置：`v2/frontend/src/App.jsx:1588`（整块 loading 态替换画布）；骨架样式已存在未用
  （`calendar.css:212-263`）。
- [x] 契约：DESIGN-CONTRACT:106 要求加载时保留日期控件、房间头、时间轴 + 安静骨架条。
- [x] 期望：加载态保留框架+骨架条；切换日期时先清空/隔离旧 bookings，避免旧日期数据在新日期头部下闪现。
- [x] 验收：production-source 断言 + 浏览器验证换日过程无旧数据闪现。

### F11 冲突面板"重新检查"无反馈；修订冲突缺第三个动作 【P2 · 契约漂移】

- [x] 位置：`v2/frontend/src/App.jsx:2002`（slot-conflict 重新检查直接 loadCalendar，无 busy/结果）、
  `App.jsx:828`（revision 面板只有两个动作）。样式 `.booking-conflict-check-result`
  （drawer-shell.css:267-279）与 `.booking-modified-recheck` 已存在未用。
- [x] 契约：DESIGN-CONTRACT:87-89（slot 冲突三动作+重检反馈）、:116（revision 三动作含"重新检查"）。
- [x] 期望：slot-conflict 重检按钮加 busy 态与结果提示（"该时段仍被占用/已可用"）；
  revision 面板补"重新检查"动作（重新拉取最新记录并更新基线）。
- [x] 验收：production-source 断言三动作与 busy/结果文案；浏览器回归。

### F12 历史"加载更早月份"应原地追加而非整表跳转 【P2 · 契约漂移】

- [x] 位置：`v2/frontend/src/App.jsx:1651`（按钮直接 setHistoryMonth → loadHistory append=false）、
  `App.jsx:1079-1103`（loadHistory/loadMoreHistory 已有 append 参数与游标）。
- [x] 契约：DESIGN-CONTRACT:77 要求底部原地追加上一月 + 淡月份分隔线（`.history-month-divider`
  样式已存在未用），月份步进器/下拉仍回到单月视图。
- [x] 期望：按钮走 append 路径 + 渲染分隔线；月份控件行为保持不变。
- [x] 验收：production-source 断言；游标分页测试确认 append 与筛选绑定不回归。

### F13 Toast 图标一律绿色对勾，错误消息语义矛盾 【P2 · 反馈一致性】

- [x] 位置：`v2/frontend/src/App.jsx:2053`。
- [x] 期望：toast 支持 tone（success/info/error），负面消息（权限提示、时段已开始、网络错误等）
  用 `WarningCircle`；保留 role=status/aria-live。
- [x] 验收：断言 tone→图标映射；浏览器抽查负面场景。

### F14 弹出层缺 Esc/外部点击关闭/焦点返回 【P2 · 一致性】

- [x] 位置：筛选弹层（`App.jsx:1542,1577,1641`）、历史搜索、月份菜单、用户搜索等。
- [x] 期望：与抽屉一致——Esc 关闭、点击外部关闭、关闭后焦点返回触发按钮。
  （可扩展 `useFocusTrap` 支持非模态 popover，或新增轻量 hook。）
- [x] 验收：浏览器键盘/鼠标回归；如本轮引入 React Testing Library 可加交互测试。

### F15 触控目标 < 44px 违约 【P2 · 契约 127 行】

- [x] 位置（实测）：抽屉返回钮 36×36（`drawer-shell.css:57-59`）；提醒"知道了" 34px
  （`production-flows.css:142`）；系统页复制地址 36px（`system.css:159`）；新建用户区 42px
  （`users.css:114`）；删除笔录室 42px（`booking-forms.css:533`）；`booking-forms.css:54` 36px。
- [x] 期望：全部 ≥44px（小图标可保留视觉尺寸、扩大命中区）。改完更新冻结 SHA。
- [x] 验收：样式审计 + 1024×720 浏览器点按回归。

### F16 1024×720 三房间日历 8px 横向溢出 【P2 · 契约四档】

- [ ] 位置：`v2/frontend/src/styles/calendar.css:298-301`（`.schedule { min-width: 860px }`），
  1024 档可用宽度 852px。
- [ ] 期望：1024 档下无横向滚动（`documentElement.scrollWidth === innerWidth`），三列仍可读。
- [ ] 验收：更新冻结 SHA；补 1024×720 截图证据。

### F17 低优先级 UX 项（批量小修，可一并处理）

- [ ] 抽屉打开时 toast 与提醒按钮移出交互范围（与 mainRef 一起 inert）；
- [ ] 历史月份步进无下界 vs 下拉只列 12 个月——两者范围对齐；
- [ ] 日历日期跳转限定业务范围（如今天 ±2 年）；
- [ ] 历史筛选的房间/标签是单选却用复选框外观——改 radio 语义；
- [ ] 个人中心"保存更改"加 busy/禁用，姓名必填校验；
- [ ] 变更记录加载失败加"重试"入口（`App.jsx:846`）；
- [ ] "已取消"徽章文字对比度 ≥4.5:1（`history.css:509-522`）；
- [ ] 开始时间 30 分钟对齐的客户端预校验（`App.jsx:816`）；
- [ ] 员工"前往预约日历"但无可用笔录室时给出提示而非静默落地（`App.jsx:1270-1271`）。
- [ ] 验收：每项一个小断言或浏览器证据即可，别把这一组做成大重构。

## 3. 第三批：文档与契约补齐

### F18 API-CONTRACT.md 补齐未覆盖路由 【P2 · 文档】

- [ ] 补 `GET /api/v1/reservations/upcoming`（`api/reservations.py:33-36`）；
- [ ] 补 `GET /api/v1/rooms/{id}/deletion-impact`（`api/admin.py:106-111`）与
  删除阻断投影字段（最多 50 条 + total）；
- [ ] 补 `/api/v1/integration/{rooms,availability,health}` 一节（`api/system.py:497-544`）：
  token scope 白名单（只读）、过期语义、错误码。
- [ ] 验收：文档与代码逐字段对照；可在 `tests/test_release_contract.py` 增加对应断言。

### F19 契约漂移裁决（需用户拍板，不要自行决定）

- [ ] 预约详情副标题含状态徽章，DESIGN-CONTRACT:75 说 subtitle 只含日期——问用户：
  保留实现并更新契约，还是移除徽章。
- [ ] 960/620/720px 响应式代码超出冻结四档——问用户：声明为防御性适配并写入文档，还是删除。
- [ ] 会话过期文案随 F07 一并定案。

## 4. 第四批：结构性改进（默认**本轮不做**，Windows 实机验收通过后另开工作）

- [ ] F20 App.jsx 拆分（按视图拆容器 + 抽屉状态 reducer/context + load* 抽 hooks）；
- [ ] F21 schema 迁移机制预留（迁移脚本目录 + schema_version 递增演练，V2.0.0 不启用）；
- [ ] F22 引入 React Testing Library 交互测试（焦点陷阱/草稿保留/会话隔离 10–15 例）；
- [ ] F23 后端用户名/密码校验去重（core.py 与 admin.py 两份几乎逐字重复）；
- [ ] F24 待评估项（**默认不做**，除非用户点名）：登录限流持久化（F-限流）；登录账号状态
  枚举统一 401 的取舍；服务端会话存储；备份/数据目录加密；会话绑定 IP 指纹；集成令牌
  接口速率限制与审计；Waitress 连接级资源限制；公开大屏输出 UUID 改为显示序号；两字名
  脱敏强度；密码复杂度校验。

## 5. 完成标准

1. 第一、二、三批全部勾选完成，每项都有对应测试/证据；
2. `v2/scripts/check.sh` 全绿（backend / installer / 跨层 / frontend / 构建）；
3. `git diff --check` 干净；每个修复独立提交、中文提交信息；
4. RELEASE-CHECKLIST 顶部新增本轮增量证据（E13 起），注明本轮已改变生产源码、
   `formal_external_release_allowed` 维持 `false`；
5. 最终回复按 F 编号汇总：改了哪些文件、新增哪些测试、哪些项问了用户、哪些项未做及原因。

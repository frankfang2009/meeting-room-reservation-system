# V2.0.0 前端视觉对齐 QA

日期：2026-08-10

## 对照来源

- 冻结原型：`/Users/frank/Desktop/projects/预约系统/output/prototypes/claude-calendar`
- 正式实现：`/private/tmp/meeting-room-v2/v2/frontend`
- 浏览器：Codex 应用内浏览器
- 正式数据：隔离的临时 V2 数据目录；所有房间、用户和预约均通过真实 API 创建
- 首次设置：使用另一隔离临时数据目录，仅推进到确认页，未提交配置

## 页面映射

| 原型页面 | 正式页面 | 结果 |
| --- | --- | --- |
| 登录 | 未认证入口 | 通过 |
| 首次设置 1–5 | Setup 向导 | 通过；生产文案替换原型合成数据说明 |
| 我的预约 | `mine` | 通过；真实预约、筛选与空状态 |
| 共享日历 | `calendar` | 通过；真实 API 日历数据 |
| 预约记录 | `history` | 通过；搜索、筛选、月份菜单与更早月份入口 |
| 笔录室 | `rooms` | 通过；真实笔录室数据 |
| 用户管理 | `users` | 通过；真实用户数据与搜索 |
| 系统状态 | `system` | 通过；保留正式诊断、令牌和审计能力 |
| 个人设置 | `settings` | 通过；保留正式登出能力 |
| 预约/用户抽屉 | 正式详情与编辑抽屉 | 通过 |
| 公共大屏 | `/display` | 通过；仅消费后端脱敏投影 |
| 错误/空状态 | 登录错误、筛选空、历史空 | 通过 |

## 初始差异与处置

- P0：0。
- P1：首次设置内容层级缺失；认证 bootstrap 已返回但 rooms/users/preferences 未立即水合；我的预约缺少冻结筛选与收起结构；历史缺少冻结搜索、月份菜单和更早月份入口。均已修复。
- P2：用户管理缺少搜索入口。已修复。
- 正式系统独有的手动刷新、安全审计、只读令牌与登出按钮继续保留；它们不是原型数据或业务逻辑的移植。

## 同视口截图证据

四个目标视口均记录了 `innerWidth × innerHeight`，且 `documentElement.scrollWidth === innerWidth`，无横向溢出。

| 状态 | 1024×720 | 1280×720 | 1440×900 | 1920×1080 |
| --- | --- | --- | --- | --- |
| 原型日历 | `qa/ui-alignment-2026-08-10/final-prototype-calendar-1024x720.png` | `qa/ui-alignment-2026-08-10/final-prototype-calendar-1280x720.png` | `qa/ui-alignment-2026-08-10/final-prototype-calendar-1440x900.png` | `qa/ui-alignment-2026-08-10/final-prototype-calendar-1920x1080.png` |
| 正式日历 | `qa/ui-alignment-2026-08-10/final-formal-calendar-1024x720.png` | `qa/ui-alignment-2026-08-10/final-formal-calendar-1280x720.png` | `qa/ui-alignment-2026-08-10/final-formal-calendar-1440x900.png` | `qa/ui-alignment-2026-08-10/final-formal-calendar-1920x1080.png` |
| 原型登录 | `qa/ui-alignment-2026-08-10/final-prototype-login-1024x720.png` | `qa/ui-alignment-2026-08-10/final-prototype-login-1280x720.png` | `qa/ui-alignment-2026-08-10/final-prototype-login-1440x900.png` | `qa/ui-alignment-2026-08-10/final-prototype-login-1920x1080.png` |
| 正式登录 | `qa/ui-alignment-2026-08-10/final-formal-login-1024x720.png` | `qa/ui-alignment-2026-08-10/final-formal-login-1280x720.png` | `qa/ui-alignment-2026-08-10/final-formal-login-1440x900.png` | `qa/ui-alignment-2026-08-10/final-formal-login-1920x1080.png` |
| 原型首次设置 | `qa/ui-alignment-2026-08-10/final-prototype-setup-1024x720.png` | `qa/ui-alignment-2026-08-10/final-prototype-setup-1280x720.png` | `qa/ui-alignment-2026-08-10/final-prototype-setup-1440x900.png` | `qa/ui-alignment-2026-08-10/final-prototype-setup-1920x1080.png` |
| 正式首次设置 | `qa/ui-alignment-2026-08-10/final-formal-setup-1024x720.png` | `qa/ui-alignment-2026-08-10/final-formal-setup-1280x720.png` | `qa/ui-alignment-2026-08-10/final-formal-setup-1440x900.png` | `qa/ui-alignment-2026-08-10/final-formal-setup-1920x1080.png` |
| 原型大屏 | `qa/ui-alignment-2026-08-10/final-prototype-display-1024x720.png` | `qa/ui-alignment-2026-08-10/final-prototype-display-1280x720.png` | `qa/ui-alignment-2026-08-10/final-prototype-display-1440x900.png` | `qa/ui-alignment-2026-08-10/final-prototype-display-1920x1080.png` |
| 正式大屏 | `qa/ui-alignment-2026-08-10/final-formal-display-1024x720.png` | `qa/ui-alignment-2026-08-10/final-formal-display-1280x720.png` | `qa/ui-alignment-2026-08-10/final-formal-display-1440x900.png` | `qa/ui-alignment-2026-08-10/final-formal-display-1920x1080.png` |

补充状态证据：

- 首次设置：`final-formal-setup-admin-1440x900.png`、`final-formal-setup-room-1440x900.png`、`final-formal-setup-hours-1440x900.png`、`final-formal-setup-confirm-1440x900.png`
- 我的预约与筛选：`postfix-formal-mine-1440x900.png`、`postfix-formal-mine-filter-1440x900.png`
- 历史工具：`final-prototype-history-filter-1440x900.png`、`final-formal-history-filter-1440x900.png`、`postfix-formal-history-search-1440x900.png`、`postfix-formal-history-month-1440x900.png`
- 抽屉：`postfix-formal-booking-drawer-1440x900.png`、`postfix-formal-user-drawer-1440x900.png`、`final-prototype-user-drawer-1024x720.png`、`final-formal-user-drawer-1024x720.png`
- 错误与空状态：`final-formal-login-error-1440x900.png`、`final-formal-session-expired-1440x900.png`、`final-formal-mine-filter-empty-1440x900.png`、`final-formal-history-empty-1440x900.png`
- 洁净页面：`final-formal-rooms-1440x900.png`、`final-formal-users-1440x900.png`、`final-formal-system-1440x900.png`、`final-formal-settings-1440x900.png`

## 交互与运行时检查

- 实际完成了登录、登出、重新认证、导航、我的预约筛选、历史搜索/筛选/月选择、用户抽屉、预约详情抽屉和公共大屏读取。
- 首次设置通过真实控件从第 1 步推进到第 5 步；未点击“完成配置”。
- 正式页与原型页控制台 `error` / `warn` 均为空。
- 原型的演示账号、演示密码、合成预约、假状态和前端生成 ID 未进入正式源码。

## 自动验证

- 基线：`npm test` 67/67；`npm run build` 通过。
- 最终：`npm test` 70/70；`npm run build` 通过。
- 最终产物：CSS 108.78 kB，JS 386.29 kB。
- `git diff --check` 通过；仅显示工作区既有 Windows 批处理文件的 CRLF 提示。

## 结论

- 残留 P0：0。
- 残留 P1：0。
- 残留 P2：正式系统页比原型多一个“立即刷新”，这是保留真实手动恢复入口的有意差异；离线、无权限、无笔录室、时段/版本冲突已有源码和自动化覆盖，但本轮未逐一生成正式截图，属于验收证据缺口。
- 本次未发布、未提交、未重置或覆盖工作区现有修改。

# V2.1.0 前端视觉对齐 QA

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
| 原型日历 | `../out/qa/ui-alignment-2026-08-10/final-prototype-calendar-1024x720.png` | `../out/qa/ui-alignment-2026-08-10/final-prototype-calendar-1280x720.png` | `../out/qa/ui-alignment-2026-08-10/final-prototype-calendar-1440x900.png` | `../out/qa/ui-alignment-2026-08-10/final-prototype-calendar-1920x1080.png` |
| 正式日历 | `../out/qa/ui-alignment-2026-08-10/final-formal-calendar-1024x720.png` | `../out/qa/ui-alignment-2026-08-10/final-formal-calendar-1280x720.png` | `../out/qa/ui-alignment-2026-08-10/final-formal-calendar-1440x900.png` | `../out/qa/ui-alignment-2026-08-10/final-formal-calendar-1920x1080.png` |
| 原型登录 | `../out/qa/ui-alignment-2026-08-10/final-prototype-login-1024x720.png` | `../out/qa/ui-alignment-2026-08-10/final-prototype-login-1280x720.png` | `../out/qa/ui-alignment-2026-08-10/final-prototype-login-1440x900.png` | `../out/qa/ui-alignment-2026-08-10/final-prototype-login-1920x1080.png` |
| 正式登录 | `../out/qa/ui-alignment-2026-08-10/final-formal-login-1024x720.png` | `../out/qa/ui-alignment-2026-08-10/final-formal-login-1280x720.png` | `../out/qa/ui-alignment-2026-08-10/final-formal-login-1440x900.png` | `../out/qa/ui-alignment-2026-08-10/final-formal-login-1920x1080.png` |
| 原型首次设置 | `../out/qa/ui-alignment-2026-08-10/final-prototype-setup-1024x720.png` | `../out/qa/ui-alignment-2026-08-10/final-prototype-setup-1280x720.png` | `../out/qa/ui-alignment-2026-08-10/final-prototype-setup-1440x900.png` | `../out/qa/ui-alignment-2026-08-10/final-prototype-setup-1920x1080.png` |
| 正式首次设置 | `../out/qa/ui-alignment-2026-08-10/final-formal-setup-1024x720.png` | `../out/qa/ui-alignment-2026-08-10/final-formal-setup-1280x720.png` | `../out/qa/ui-alignment-2026-08-10/final-formal-setup-1440x900.png` | `../out/qa/ui-alignment-2026-08-10/final-formal-setup-1920x1080.png` |
| 原型大屏 | `../out/qa/ui-alignment-2026-08-10/final-prototype-display-1024x720.png` | `../out/qa/ui-alignment-2026-08-10/final-prototype-display-1280x720.png` | `../out/qa/ui-alignment-2026-08-10/final-prototype-display-1440x900.png` | `../out/qa/ui-alignment-2026-08-10/final-prototype-display-1920x1080.png` |
| 正式大屏 | `../out/qa/ui-alignment-2026-08-10/final-formal-display-1024x720.png` | `../out/qa/ui-alignment-2026-08-10/final-formal-display-1280x720.png` | `../out/qa/ui-alignment-2026-08-10/final-formal-display-1440x900.png` | `../out/qa/ui-alignment-2026-08-10/final-formal-display-1920x1080.png` |

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

---

## 个人中心活动页 — 2026-08-11

### 对照与归一化

- 视觉真值：`qa/personal-center/source-personal-center-activity.png`。
- 浏览器实现截图：`qa/personal-center/implementation-personal-center-activity.png`。
- 同屏证据：`qa/personal-center/comparison-personal-center-activity.png`。
- 来源像素：1487×1058；实现像素：1280×890。
- CSS 视口：1280×890；浏览器报告 `devicePixelRatio=2`，截图接口输出 CSS 像素尺寸。
- 归一化：来源按 cover 缩放到 1280×911，垂直居中裁切为 1280×890；实现未缩放，
  两者以原始细节并排组成 2560×926 对照图。
- 状态：管理员 `frank`，隔离临时 V2 数据目录，通过真实 setup/login/reservation/activity
  API 生成的本人已完成预约活动；未读取或修改正式数据。

### 必查视觉面

- 字体与层级：沿用现有 PingFang/HarmonyOS/system sans；标题、身份、页签、分区标题、
  指标数字与小字层级和确认稿一致，无异常折行或截断。
- 间距与布局：80px 图标栏、内容左边线、身份区、页签、热力图及下方双栏对齐；
  下方右侧为 2×2 四项统计，左侧活动概览保持原结构。
- 色彩与令牌：个人中心使用现有 raised-ivory 画布、graphite 文本/细线和低饱和陶土色；
  没有新增卡片、渐变或高饱和状态色。
- 图片与资产：该页面不包含插画、品牌图或产品图片；导航继续使用现有 Phosphor 图标，
  没有用 CSS 图形、字符或临时占位物替代可见资产。
- 文案与内容：保留“我的活动 / 偏好设置 / 活动概览”，删除“最近完成”和热力图
  “少—多”图例；新增“活动数据”仅承载本月完成、累计完成、累计时长、活跃天数。

### 对照历史

- Pass 1：发现 P2 纵向节奏偏松，热力图和下方统计整体偏低；个人中心底色也比确认稿略沉。
- 修复：身份区和页签间距各收紧 8px，热力图区最小高度收紧 44px，并改用现有
  `--paper-raised` 令牌；未改动全局导航和其他页面。
- Pass 2：`comparison-personal-center-activity.png` 显示主要水平线、信息层级和下方统计
  区域已对齐；内容差异仅来自隔离测试账号的真实聚合数字，不属于设计漂移。
- 聚焦区域：不另做裁切。2560×926 原始对照中页签、月份、概览行和四项指标均可直接辨认，
  另通过浏览器语义快照核对了表单标签、开关与页签状态。

### 交互和运行时

- 实测登录、从底部账户入口打开个人中心、“我的活动 ↔ 偏好设置”双向切换；点击
  2026-08-10 活动格后，真实 `GET /api/v1/activity/days/2026-08-10` 返回本人预约，
  行项目可继续打开既有只读预约详情抽屉。
- 偏好页保留姓名、部门、默认时长、默认笔录室、两项网页通知、保存和退出登录；新增
  服务端保存的个人标签 3/4，以及按登录账号隔离在当前浏览器的默认首页和 6/12 个月
  活动范围。实测保存后刷新自动进入所选首页，活动范围和个人标签仍在。
- 活动热力图只让有记录的日期成为可聚焦按钮，空白格保持装饰节点；按钮具备日期、
  完成场次与“查看明细”说明，两个页签与对应 tabpanel 建立
  `aria-controls` / `aria-labelledby` 关系。
- 新增状态截图：`qa/personal-center/implementation-personal-center-activity-day.png`、
  `qa/personal-center/implementation-personal-center-preferences.png`。
- 浏览器控制台日志：0 条 error/warn。

### 功能化验收修正

- 首次切换为“近6个月”时发现月份标签仍占用固定 12 列，导致 3–8 月只铺在左半幅；
  已改为由实际月份数量驱动网格列数，并重新在浏览器确认 6 个月与 12 个月均铺满。
- 活动日接口只接受日期，服务端固定使用当前 session 用户并再次限定已结束、未取消；
  已取消日期、他人日期、非法日期和未登录均有后端测试。
- 个人标签保存后同步预约标签显示草稿；活动范围变化会清除不再可见的已选日期，避免
  旧明细跨范围残留。

### Findings

- P0：0。
- P1：0。
- P2：0。
- P3：测试数据的活动密度低于确认稿中的示意数据；这是数据状态差异，不改变组件层级、
  色阶映射或生产统计口径。

final result: passed

---

## 个人中心热力图删除 — 2026-08-11

### 对照与归一化

- 来源视觉真值：`/var/folders/7h/2l8d2s_d5_54l20rmssvyp2c0000gn/T/codex-clipboard-9c37b9bc-d0ed-4c62-8f6a-9cef895fe07e.png`（1874×522）；用户明确要求删除图中“预约活动”标题、日期范围、热力格与月份标记。
- 浏览器实现截图：`qa/personal-center/implementation-personal-center-no-heatmap.png`（1080×890 CSS 像素）。
- 同图对照：`qa/personal-center/comparison-personal-center-no-heatmap.png`（2160×890）；左侧将来源按 1080 宽等比缩放并置于同尺寸画布，右侧为未缩放实现。
- 状态：隔离 QA 管理员登录后，从底部账户入口打开“我的活动”；未读取或修改正式数据。

### 必查视觉面

- 字体与层级：身份、页签、活动概览和四项统计继续使用原有字体、字号与权重；删除区域没有留下空标题或重复说明。
- 间距与布局：页签分隔线下直接进入左右双栏，概览与统计整体上移；双栏边界、行高和 2×2 指标网格保持原对齐。
- 色彩与令牌：继续使用 raised-ivory、graphite 和低饱和陶土色，不新增卡片、阴影或替代装饰。
- 图片与资产：该区域没有图片资产；删除热力格后没有使用占位图形补空。
- 文案与内容：页面说明改为“查看工作概览与管理工作偏好”；“预约活动”、日期范围、月份标记和“活动图显示范围”均不可见。

### 交互与功能

- 浏览器实测登录、打开个人中心、“我的活动 ↔ 偏好设置”切换。
- “我的活动”仅展示服务端聚合的本人活动概览与四项统计；加载和失败回退仍保留。
- “偏好设置”保留账号隔离的默认首页、服务端个人标签和既有预约偏好；已移除失去对象的活动范围选项。
- 语义快照确认页签与 tabpanel 关系仍完整，删除区域不再产生可聚焦节点或不可达详情入口。

### 对照历史

- Pass 1：同图对照确认用户标记区域已完整删除，剩余双栏自然承接页签，没有 P0/P1/P2 视觉或交互问题。
- 聚焦区域：不另做裁切；对照目标是整块区域的删除，1080×890 实现图已可直接辨认所有剩余标题、行和指标。

### Findings

- P0：0。
- P1：0。
- P2：0。
- P3：0。

final result: passed

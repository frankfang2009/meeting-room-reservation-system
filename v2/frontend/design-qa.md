# V2 前端视觉对齐 QA 记录

日期：2026-08-10

## 对照来源

- 冻结原型：开源前的私有历史设计输入（不作为当前仓库依赖）
- 正式实现：本仓库 `v2/frontend`
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

## V2.2.0 数据中心简化布局（Option 2）— 2026-08-17

### 对照目标与归一化

- 来源视觉真值：`qa/data-center-simplification/source-option-2.png`，1487×1058 像素；
  用户明确选择第二版“一次看一件事”方案。
- 浏览器实现截图：`qa/data-center-simplification/implementation-final.png`，1243×890 像素；
  在 Codex 内置浏览器中使用当前 1243×890 CSS 视口、1×密度捕获。
- 同图对照：`qa/data-center-simplification/comparison-final.png`，2486×890 像素；
  来源图仅为对照等比关系归一到 1243×890，右侧实现保持原始浏览器像素。
- 状态：管理员 overall、2026-08-01 至 2026-08-17、真实合成数据库 65 场有效预约；
  筛选与导出面板收起，分析区停留在“时段分布”。

### 必查视觉面

- 字体与层级：沿用产品 PingFang/系统字体、既有字重和数字字体；标题、四项摘要、趋势、
  页签和分布条形成与来源一致的五级层次，没有引入新的展示字体。
- 间距与布局：标题与工具栏分两层；四项摘要为一条平面指标带；趋势保留大面积留白；
  下方一次只显示一个分析维度。人员长列表、分析卡片网格和大型 CSV 卡片均已移除。
- 色彩与令牌：继续使用原有暖白底、graphite 文本、hairline 分隔和低饱和陶土色；
  不新增阴影、渐变、红绿绩效语义或卡片墙。
- 图片与资产：目标和实现都不依赖内容图片；导航和操作继续使用现有 Phosphor 图标，
  没有用 CSS 绘图、emoji 或占位资源替代可见资产。
- 文案与内容：保留真实生成时间；时段标题由当前数据中的真实槽位推导，因此实现显示
  `08:30–12:30 / 13:00–17:30`，不机械照抄 mockup 的示意工作时段。

### 浏览器交互与权限

- 实测筛选按钮打开/关闭完整筛选表单；CSV 按钮打开/关闭状态选择与下载数量面板。
- 实测 overall 的“时段分布 / 笔录室 / 标签”三个页签均可切换，且同屏只有一个面板。
- 管理员切换到“测试冯员09”后只显示个人“时段分布 / 标签”，没有笔录室页签、
  全员工作负荷列表或任何其他员工数据入口；随后恢复 overall。
- 页面控制台 0 条日志；未触发下载写入，避免在视觉验收中产生多余文件。

### 对照历史

- Pass 1：`qa/data-center-simplification/implementation-pass-1.png`。发现 P2：顶部控件与标题
  同行，弱化了来源的两层层级；趋势柱按局部最大值顶满，缺少来源的阅读余量和轻量刻度。
- 修正：工具栏移到标题下方；趋势使用向上取整的展示上限，并增加克制的 0–40 横向刻度；
  同时缩短趋势区高度，让分析页签回到来源对应的首屏位置。
- Pass 2 后继续将生成时间收进工具栏，压缩标题间距和指标上方空隙。
- 最终同图对照确认上述 P2 均已消除；整体结构、主要区域比例、留白、字体、色彩和文案
  不再有可执行的 P0/P1/P2 差异。
- 聚焦区域：不另做裁切。最终 1243×890 同图中标题、控件、指标、坐标刻度、页签和
  时段标题均可直接辨认，局部裁切不会增加判断信息。

### Findings

- P0：0。
- P1：0。
- P2：0。
- P3：左侧导航保持生产系统既有 80px rail 和选中态，而非重画 mockup 的窄 rail；
  这是刻意保留现有产品设计系统，不影响数据中心层级或使用。

final result: passed

---

## V2.2.0 数据中心与个人摘要 — 2026-08-17

### 隔离状态与范围

- 使用独立临时数据目录、18080 回环服务、管理员与员工合成账号；未读取或
  修改正式数据，截图与下载只保存在临时验收目录，不进入发布包。
- 管理员 overall 实测有效 3 场、已结束 3 场、4.5 小时、取消 1 场/25%；切换李静后
  为有效 2 场、已结束 2 场、2.5 小时、取消 1 场。
- 员工导航包含数据中心但不包含人员选择器或管理入口；本人指标与管理员查看该员工
  完全一致。员工伪造 overall 和 person 请求均返回 `403 FORBIDDEN`。

### 视觉、交互与数据

- 1440×900 与 1024×720 下，`document/body/main` 均满足
  `scrollWidth = clientWidth`；1024 档筛选区有序变为两列，取消护栏换到主指标下一行，
  没有文档级横向滚动、裁切或控件重叠。
- 页面保持开放编辑式画布：范围与筛选在前，三项主指标和克制的取消护栏次之，再进入
  周趋势、人员/标签/笔录室分布、星期×时段热力图和独立 CSV 区；未变成卡片墙，未出现
  排名、分数、红黑榜、案件量或利用率文案。
- 管理员通过人员下拉切换个人视角；员工和管理员个人视角都显示单位及个人标签，
  overall 只显示单位标签 1/2。个人中心最终形态为纯偏好设置页：不含“我的活动”
  统计摘要，也没有数据中心入口，个人统计只由数据中心 self 视角提供。
- 管理员选择李静与员工本人各完成一次真实 CSV 下载；两份文件均为 UTF-8 BOM、15 列、
  3 条合成记录，且只包含该员工 `QA-EMP-001/002/CANCEL`。
- 正向流程控制台 0 error/0 warning；权限负向测试产生的两条 console error 均对应明确
  发起并得到预期结果的 403 请求，不属于页面运行错误。

### Findings

- P0：0。
- P1：0。
- P2：0。
- P3：0。

final result: passed

> 收口更正（2026-08-17 评审）：本节早先版本记录过“个人中心‘我的活动’复用本人
> 本月四项指标并可返回数据中心”与“管理员可从人员负荷行进入个人视角”，两者均在
> 后续简化中移除，最终合并代码以本节上文与 DESIGN-CONTRACT 为准。

---

## 个人中心热力图删除 — 2026-08-11

### 对照与归一化

- 来源视觉真值：开源前的私有历史截图（1874×522，不随仓库发布）；用户明确要求删除图中“预约活动”标题、日期范围、热力格与月份标记。
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

## V2.2.x 体验打磨第一批（不加功能）— 2026-08-17

### 变更范围

- 日历“今天”视图到达时（进入日历页或切到今天）安静滚动一次，把服务器时间线带到视口上三分之一处；后续时钟跳动与用户滚动不再重新定位。
- 预约抽屉（新建/编辑/冲突/草稿迁移/详情/取消）打开期间，其来源时段格保持 `.slot-origin` 石墨内描边；关闭即清除，非当前展示日期不出现。
- “我的预约”“预约记录”读取态由转轮文案改为与日历同语言的暖灰骨架条（hero + 行 / 记录行同构），数据中心图表读取态改为安静柱形骨架；均保留页面外壳与 `role="status"` 播报，无 shimmer。
- 日期切换按前后方向播放一次 190ms 轻位移（首次加载不播放）；预约成功确认条 200ms 自顶部轻落；全部处于既有 `prefers-reduced-motion` 全局降级之下；toast 维持既有滑入。
- 相对日期措辞以服务器业务日为基准：日历页副标题、“之后”行与预约记录行的星期位、详情抽屉与取消确认的日期副标题在 今天/明天/后天 时加前缀；“我的预约”hero 仍只显示时间。
- 系统状态文案人话化：绑定模式→服务范围、数据序列/备份序列→数据序号/备份序号、集成令牌→接口令牌、审计时间说明改为“时间显示已换算为本地时区”。已核实“会议室”仅出现于产品品牌名，笔录室命名全站一致，无需改动。

### 验证

- `v2/scripts/check.sh` 全绿（ruff、后端三套 unittest、compileall、前端 eslint、154 项契约测试、vite 构建、`git diff --check`）。
- `tests/production-source.test.mjs` F19 断言随 `dateSubtitle` 用法同步更新；`tests/styles-structure.test.mjs` 冻结 SHA-256 同步为 `6d7afc5e…7d7a8` 并注明变更清单。
- 本轮验证为源码级与自动化门禁；视觉实机走查（1024×720/1440×900 各状态截图）待 CI 与合并前人工确认，不据此宣称实机验收。

### Findings

- P0：0；P1：0；P2：0；P3：0（自动化门禁范围内）。

final result: passed (automated gates; on-device visual pass pending)

### 浏览器实测走查（2026-08-18 补记）

- 环境：worktree 内真实 `python server.py`（Flask + waitress，生产构建 dist），浏览器 1440×900，
  完整走过首次设置向导（管理员 + 三间笔录室 + 工作时间 08:30–17:30）→ 登录 → 建号验证。
- 实测通过：
  - 日历页副标题显示“今天 · 2026年8月18日 · 星期二”（相对日期拼接生效）；
  - 点击一号笔录室 10:00 空白格 → 新建抽屉打开，`.slot-origin` 全页恰 1 处且落在该格；
  - 填写表单保存 → 成功确认条出现（预约已创建 · 一号笔录室 · 10:00–11:00），日历块跨 10:00–11:00 渲染正确；
  - 抽屉关闭后描边清除（count=0）；点击预约块打开详情 → 副标题“今天 · 2026年8月18日 · 星期二”，
    描边转移到该预约块（恰 1 处）；
  - “我的预约”hero 正常显示下一场（时间 + 笔录室，维持只显示时间的冻结约束）。
- 走查时刻为本地凌晨（工作时间外）：today 时间线按契约不渲染，自动定位按设计不触发——
  行为符合“仅工作时间内定位”的约定，但**工作时间内的一次性滚动效果未在本轮实测**，
  留待工作时间内人工复核（机制为一次性 scrollTop 赋值，风险低）。
- 未逐帧捕获：日期切换方向动画、确认条入场动画（190/200ms 瞬时）、各视图读取骨架
  （本地接口毫秒级返回难以稳定捕获）；以上均由 CSS/源码审查与 154 项契约测试覆盖。
- 浏览器通道后期不稳定（IAB 点击/截图间歇超时），以 DOM 读取断言替代视觉断言完成验证；
  该不稳定为测试环境问题，非产品缺陷（同期 DOM 读取与页面行为全部正常）。

final result: passed (CI 9/9 green + on-server browser walkthrough; in-hours auto-scroll pending)

### Codex 最终集成冷审与工作时间内复核（2026-08-18）

- 体验分支已重放到 V2.2.2 修复版合并提交 `5cd6f1ae`，冲突处同时保留
  “服务范围/数据序号/备份序号/接口令牌”的体验文案，以及更新检查
  `current / available / unknown` 的可信状态语义；独立版本推进为 V2.2.3。
- 冷审发现原自动定位 effect 会在同一天后续 `loadCalendar()` 结束时再次执行，可能覆盖
  用户滚动；改为按“到达今天”的 request/handled 序号只处理一次，只有离开今天后再切回、
  或离开日历后再进入才产生新请求。补充相对日期跨月/跨年单测。
- `v2/scripts/check.sh` 集成全绿：backend 115、installer 84、跨层 29、frontend 155；
  Ruff、compileall、ESLint、`git diff --check` 与 Vite 生产构建均通过。
- 真实浏览器使用固定 `127.0.0.1:8080`、隔离临时 V2 数据完成首次设置、登录和日历走查。
  工作时间内切回今天后，时间线相对 845px 日历视口顶部为 281.9px（约上三分之一）；
  手动滚回 `scrollTop=0`，跨过一次 30 秒服务器时钟更新后仍为 0；切到明天再回今天后
  才重新定位到 `scrollTop=170`。一次性定位的待验收项关闭。
- 控制台日常交互 0 warning；唯一 error 为首次设置完成后监听器由回环切换到 LAN 模式时，
  健康探测产生的一次预期 `ERR_CONNECTION_REFUSED /healthz`，随后自动进入登录页。

final result: passed (V2.2.3 integrated gates + in-hours real-browser auto-scroll verification)

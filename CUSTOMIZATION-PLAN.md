# 个性化定制扩展计划（交付 Codex 实施）

> **历史任务书（已完结）**：本计划描述的 C1–C5 定制改造已全部随 V2.1.0 发布落地，
> 文中引用的分支名与本地 git 状态是当时的工作现场，与当前仓库无关；保留仅作
> 决策与验收口径的历史记录，不构成当前开发指令。

> 本计划承接 `codex/v2.0.0-baseline`，包含五项定制化改造：C1 管理员可修改
> 工作时间、C2 默认预约标签、C3 公开大屏按笔录室隐藏、C4 提醒提前量可选、
> C5 一键复制对外提醒信息（个人模板）。C5 是 C0–C4 启动后追加的独立小功能，
> 实施顺序见文末。
> 每项必须通过"频率 / 省动作 / 层级"三条自检后才落地，禁止为加而加。
> 第二梯队与明确否决项列于文末，实施时不得顺手实现。

## 0. 前置规则（每一项都必须遵守）

1. 动手前先读 `AGENTS.md`、`v2/docs/PRODUCT-CONTRACT.md`、`v2/docs/API-CONTRACT.md`、
   `v2/frontend/DESIGN-CONTRACT.md`、`v2/docs/RELEASE-CHECKLIST.md`。
2. 任何行为变更必须同步更新对应契约文档；`v2/scripts/check.sh` 每次提交前必须全绿
   （Ruff + compileall + backend/installer/跨层/frontend 测试 + Vite 生产构建）。
3. 保持既有安全不变量：`admin | employee` 角色、共享日历可见性、员工本人历史边界、
   服务端大屏白名单投影、事务一致性、fail-closed、安全审计、锁定至少一名启用管理员。
4. 每项独立提交，提交信息带编号，例如 `feat: C1 管理员可修改工作时间`；
   每项完成后在 `CHANGELOG.md` 记录。
5. 当前工作区存在未提交的 `CODE-REVIEW-FIX-LIST.md` 删除（`git status` 显示
   ` D CODE-REVIEW-FIX-LIST.md`）：本轮不得代为提交或恢复该文件，除非用户明确指示。
6. 建议在新分支 `codex/v2.1.0-customization`（从 `codex/v2.0.0-baseline` 切出）实施；
   版本号/VERSION 的提升留待全部项目落地后的最后一个提交处理，不要在中间提交改动。

## C0（公共依赖）：Schema v1 → v2 幂等迁移框架

C2、C3、C4 需要新列，必须先做迁移框架；C1 不依赖 C0，可先行。

### 现状

- `v2/backend/v2app/db.py`：`SCHEMA_VERSION = 1`；`classify_existing_database()` 对
  `schema_version != SCHEMA_VERSION` 直接抛 `DatabaseGenerationError`（fail-closed → 恢复状态）。
- 新鲜安装走 `_initialize_database()` 的 `SCHEMA_STATEMENTS`。

### 要求

- 新列写入对应 `CREATE TABLE`（新鲜安装直接得到 v2 结构）：
  - `rooms.show_on_display INTEGER NOT NULL DEFAULT 1 CHECK (show_on_display IN (0, 1))`（C3）
  - `user_preferences.default_tag_slot INTEGER CHECK (default_tag_slot BETWEEN 1 AND 4)`（C2，可空）
  - `user_preferences.reminder_lead_minutes INTEGER NOT NULL DEFAULT 30 CHECK (reminder_lead_minutes IN (15, 30, 60))`（C4）
  - `user_preferences.reminder_template TEXT NOT NULL DEFAULT '{系统默认模板}'`（C5，见 C5 章节的默认模板文案）
- `SCHEMA_VERSION` 提升为 `2`；`classify_existing_database()` 接受 `{1, 2}`：
  - v2：直接放行；
  - v1：标记"需迁移"，在服务可用前执行幂等迁移事务。
- 迁移函数：`BEGIN IMMEDIATE` 内先 `PRAGMA table_info` 逐列检查，缺失才执行
  `ALTER TABLE ... ADD COLUMN`（SQLite 约束限制：NOT NULL 必须带常量 DEFAULT，CHECK 允许）；
  全部完成后 `UPDATE app_meta SET value='2' WHERE key='schema_version'`；任何异常回滚，
  保持 fail-closed 恢复状态。绝不新建库、绝不静默跳过、绝不在迁移中碰业务数据。
- 恢复流程同步：`v2/backend/restore.py` 在预恢复校验通过、原子替换之后，对 v1 备份
  执行同一迁移函数再标记恢复完成；迁移失败按恢复失败回滚（保留现场），保证旧备份仍可恢复。
- 安装器、跨层测试（`v2/tests/`）中所有断言 `schema_version=1` 或依赖旧 schema 的
  用例必须同步更新，并在迁移测试中覆盖：v1 快照库迁移后数据保留且版本为 2；
  迁移中途注入失败 → 回滚后原库仍可按 v1 打开；新鲜安装直接为 v2 结构。

## C1：管理员可修改工作时间

**价值**：夏季/冬季作息调整是真实运营需求；当前 `system_settings.work_start/work_end`
只在首次设置写入（`v2/backend/v2app/api/core.py` 第 374 行是全库唯一 UPDATE），
设置完成后管理员只能查看，无法修改。

### 后端

- 新增 `PUT /api/v1/admin/settings`（仅 `admin_required`），请求体
  `{ "workStart": "08:30", "workEnd": "17:30" }`：
  - 校验 `HH:MM`、30 分钟对齐（与 `slot_minutes` 对齐）、`workEnd > workStart`，
    非法返回 `422 VALIDATION_ERROR` 与 `fields`；
  - 事务内 `UPDATE system_settings SET work_start=?, work_end=? WHERE id=1`；
  - `write_security_audit(..., action="settings.updated", details={"before": {...}, "after": {...}})`；
  - 响应复用 `_serialize_settings(db)`。
- 语义边界：只影响**未来**时段生成、日历滑块动态上限、today 时间线与集成
  availability；已有预约存具体日期时间，不做追溯改写（与 snapshot 哲学一致）。
- 测试：非管理员 403；格式/对齐/顺序校验；审计落库；事务回滚；修改后
  `GET /api/v1/bootstrap` 的 `settings` 与 `/api/v1/integration/availability`
  按新时段生成；已有预约落在新时段之外时日历与历史渲染不崩（前端测试）。
- 新增"已有预约跨出/跨入新工作时段"边界用例：时段列表包含预约时间但预约起点
  不在新工作时段内时，日历列与时间轴不得错位。

### 前端（`v2/frontend/src/App.jsx` 系统状态页）

- "运行环境"组新增"工作时间"行（现状未展示，仅首次设置页展示）；行内提供安静的
  outlined 编辑动作，打开既有抽屉形态（与备份/令牌抽屉同层）：
  两个 `type="time" step="1800"` 输入 + 行内校验 + busy 状态 + 保存后刷新
  `bootstrap.settings` 与系统状态轮询数据。
- 保持 DESIGN-CONTRACT 第 53 行"系统状态是安静编辑台账"的定位：不做成设置表单页、
  不引入指标卡或图表；非管理员不渲染该行。
- 提示沿用 toast 语义（`success | error`），失败不得显示成功对勾。

### 契约同步

- `API-CONTRACT.md` 管理段补 `PUT /api/v1/admin/settings`。
- `PRODUCT-CONTRACT.md` §6：首次设置写入不变，补"管理员可在系统状态页修改工作时间，
  仅影响未来时段生成，不追溯改写既有预约"。
- `DESIGN-CONTRACT.md` 系统状态目标处补一行编辑入口要求（安静、抽屉、非表单页）。

## C2：默认预约标签

**价值**：与"默认时长/默认笔录室"完全同构，工作类型固定的用户每次预约省一次标签选择。

### 后端

- `user_preferences.default_tag_slot`（`1..4` 或 `NULL`），存**槽位引用**不存文字：
  全局标签改名或用户改个人标签文案后，默认值仍跟随同一语义槽位
  （与 `tag_label_snapshot` 的"历史不回写"哲学一致；个人槽 3/4 只解析本人标签）。
- `_serialize_preferences()`（`core.py` 第 186 行）与 bootstrap 响应增加
  `defaultTagSlot: 1..4 | null`。
- `PUT /api/v1/preferences` 接受 `defaultTagSlot`（`null` 或 1..4，非法返回 422），
  写入并计入 `preferences.updated` 审计 details。
- 测试：校验边界（0/5/字符串/负数）；序列化；改名全局标签 1 后默认仍指向槽 1；
  员工 A 的个人槽 3 与员工 B 互不影响。

### 前端

- 个人中心 → 偏好设置 →"预约偏好"组新增"默认标签"选择行（复用
  `settings-choice-row` 结构）：选项为"不指定"+ 标签 1/2（全局文案）+ 标签 3/4（本人文案）。
- 新建预约抽屉打开且表单为**全新空白单**时预选默认标签，用户可改；
  会话恢复草稿、冲突保留草稿、跨页保留草稿路径一律保留草稿原值，不得被默认值覆盖
  （与 AGENTS.md 草稿规则一致）。
- 测试：预填只发生在空白新单；草稿恢复不覆盖；默认值改动后下次新单生效。

### 契约同步

- `API-CONTRACT.md` 偏好设置段补 `defaultTagSlot`。
- `PRODUCT-CONTRACT.md` §4 补默认标签语义（槽位引用、只影响新单预选）。
- `DESIGN-CONTRACT.md` 个人中心偏好区补该选择行描述。

## C3：公开大屏按笔录室隐藏

**价值**：特殊案件笔录室不应出现在公共电视；是"服务端白名单投影"隐私哲学的
自然延伸（管理员级设置，普通员工无感知）。

### 后端

- `rooms.show_on_display`（默认 1）。`PATCH /api/v1/rooms/{id}`（`admin.py` 第 158 行）
  接受 `showOnDisplay` 布尔并计入 `room.updated` 审计 details。
- `serialize_room` 仅对管理员场景序列化 `showOnDisplay`，不扩散进员工 bootstrap
  房间列表与任何公共投影（实施时按序列化器现状取最小暴露面）。
- `display.py` 的 `today_display()`：房间查询改为
  `WHERE is_active = 1 AND show_on_display = 1`；隐藏房间的当前/下一时段
  一律不出现在响应；全部隐藏时 `rooms: []`，前端渲染空态不崩。
- 集成 API `/integration/rooms`、`/integration/availability` **保持不动**
  （面向第三方工具的可用性数据，不属于公开大屏投影）。
- 大屏允许键集合测试（现有白名单锁定测试）**不得放宽**，只补充：隐藏房间及其
  预约不出现在 `rooms`；员工共享日历与员工可见性完全不受影响。
- 测试：投影过滤、空 rooms、审计、非管理员改房间 403 不变。

### 前端

- 笔录室管理右 inspector（房间编辑区）加安静开关"在公开大屏显示"，默认开；
  关闭后大屏随轮询（≤30 秒）自然消失该行，不需前端额外推送。
- 保持 DESIGN-CONTRACT 第 80–83 行房间管理布局不变：开关放进现有 inspector，
  不在房间列或概览区新增按钮。

### 契约同步

- `API-CONTRACT.md` 房间段补 `showOnDisplay`（管理员字段）。
- `PRODUCT-CONTRACT.md` §5 补："管理员可把个别笔录室移出公开大屏投影；
  投影仍由服务端白名单生成，隐藏房间的名称与预约不出现在响应。"

## C4：提醒提前量可选（15 / 30 / 60，默认 30）

**价值**：调卷、准备笔录材料的提前量需求不同；30 分钟一刀切对部分用户不够。

### 后端

- `user_preferences.reminder_lead_minutes`（默认 30，仅 `15|30|60`）。
- `PUT /api/v1/preferences` 接受 `reminderLeadMinutes`，非法返回 422。
- `reminders.py` 中两处硬编码 30 分钟窗口改为读取该偏好：
  - `due_reminders()` 第 72 行 `now + timedelta(minutes=30)`；
  - `acknowledge_reminder()` 第 144 行同款校验。
- **决策点（必须用测试固定行为）**：提醒送达后用户把提前量调小，导致旧提醒
  不再处于当前窗口内。规定行为：`due` 只按当前偏好扫描，ack 校验同样用当前偏好，
  窗口外的旧提醒 ack 返回 `409 REMINDER_NOT_DUE`，徽章随下一轮轮询自然消失。
  不得引入"送达时快照"等额外存储。测试覆盖：改大后新窗口生效、改小后旧提醒
  不可确认、两处窗口永远同源。
- 测试：三档校验、due/ack 窗口一致、开关关闭时无 upcoming 项（现状语义不变）。

### 前端

- 个人中心 → 通知区"预约提醒"行：开关右侧加"提前 15/30/60 分钟"选择，
  默认 30；开关关闭时选择禁用（灰色）。文案不再写死"开始前30分钟提醒我"，
  改为随选择显示；AGENTS.md 第 27–28 行钟形徽章机制不变。
- 测试：默认 30；切换后保存成功；关闭开关时选择禁用且保存关闭状态。

### 契约同步

- `API-CONTRACT.md` 偏好设置段补 `reminderLeadMinutes`；reminders 段补
  "due 与 ack 的提前窗口由该用户偏好决定，默认 30 分钟"。
- `PRODUCT-CONTRACT.md` §4 通知段同步。

## C5：一键复制对外提醒信息（个人模板）

**价值**：每次预约后工作人员通常要在微信/短信里通知当事人"时间、地点、请带材料"。
现在要人工抄姓名、日期时间、笔录室名再拼句子，一次复制省掉手工拼装，也消灭
"抄错时间"的风险。软件只做拼装 + 复制到剪贴板，发送由用户在自己的微信里完成——
不引入任何新的数据出界路径，也不属于 PRODUCT-CONTRACT §8 排除的"系统自动发送"通道。

**定位前提（实施前由用户确认）**：本功能价值成立依赖单位确实以微信/短信通知
当事人为主；若实际是前台电话统一通知或当场约定，此按钮使用率会很低。若用户
未确认，默认仍按计划实施（按钮不打扰不使用者：不点就没有任何存在感）。

### 后端

- 新列 `user_preferences.reminder_template TEXT NOT NULL DEFAULT '{系统默认模板}'`
  （列声明以 C0 的 `SCHEMA_V2_COLUMNS` 与 `CREATE TABLE` 为准；NOT NULL 需常量 DEFAULT）。
- `PUT /api/v1/preferences` 接受 `reminderTemplate`（`clean_text` 后 ≤200 字，
  超长返回 422；空字符串按"使用系统默认模板"处理——该行为用测试固定）。
  服务端把模板当纯文本存储，不做变量校验（变量只能由前端 chips 插入）。
- `_serialize_preferences()`（`core.py`）与 bootstrap 响应增加
  `reminderTemplate`；个人偏好只返回本人，无越权面。
- 审计：`preferences.updated` details 只记"模板已更新"+ 长度，不存模板全文。
- 拼装动作纯前端完成（模板 × 预约字段），不新增后端拼装端点；服务端永不接触成品文案。
- 测试：长度校验；空值回退默认；序列化；员工 B 的 bootstrap 不含员工 A 模板。

### 前端

- 预约详情抽屉（`BookingDetails`，`App.jsx` 第 881 行）操作区加安静 outlined 按钮
  "复制提醒信息"。显示条件：预约 `status=active` 且未开始，且当前用户可操作
  （与 `canEdit/canCancel` 的 `canManage` 同构——共享日历上他人未开始预约不显示该按钮，
  避免"替别人通知"的语义混乱）。
- 点击 → 本人模板替换 5 个变量 → `copyText`（复用 `App.jsx` 第 180–200 行的
  clipboard fallback）→ toast「提醒信息已复制，可在微信中粘贴发送」（success）；
  fallback 失败提示手动复制（error）。按钮文案保持渠道中立，不写死"微信"。
- 变量集合（最少 5 个，只来自详情页已展示字段）：`{当事人姓名} {日期} {开始时间}
  {结束时间} {笔录室}`。**不含案号、用途、备注**——微信聊天记录留案号敏感度高。
- 系统默认模板（示例，措辞待用户定稿）：「【笔录提醒】{当事人姓名}您好，您预约的
  笔录时间为{日期} {开始时间}，地点：{笔录室}，请提前到达。如有变动我们会再联系您。」
- 个人中心 → 偏好设置新增小节"对外提醒模板"：textarea（≤200 字）+ 5 个变量 chips
  点击插入 + 示例数据实时预览 + "恢复默认"按钮。**不改任何设置也能用**（零配置
  可用是硬要求）；小节附一行说明"仅复制到剪贴板，由您自行发送"。
- 测试：默认模板渲染正确；自定义模板渲染；chips 插入到光标位置；不保存时
  关闭不残留；复制失败 fallback 提示。

### 契约同步

- `API-CONTRACT.md` 偏好设置段补 `reminderTemplate`（说明：服务端存纯文本模板，
  变量由前端拼装，变量集不含案号）。
- `PRODUCT-CONTRACT.md` §4 补对外提醒模板小节，并明确"仅复制到剪贴板，系统不
  发送、不记录发送状态；不属于 §8 排除的自动通知通道"。
- `DESIGN-CONTRACT.md`：详情抽屉操作区按钮 + 偏好设置新小节描述。
- `AGENTS.md` 补一条：复制提醒信息不接入任何外发通道，案号/用途/备注永远不进模板。

### C5 明确不做（防蔓延）

- 不做自动发送、复制后跳转微信、渠道选择；
- 不做每房间/每预约级模板、多模板管理、详情抽屉内编辑模板；
- 不做"已发送"状态记录（软件无法知道用户发没发，守住诚实边界）；
- 案号/用途/备注永远不进变量集合。

### 与 C0 的协调（重要）

- C5 的新列与 C2/C4 同在 `user_preferences`，必须并入同一次 v1→v2 迁移批次：
  加入 `SCHEMA_V2_COLUMNS` 与 `CREATE TABLE user_preferences`（NOT NULL + 常量 DEFAULT，
  满足 SQLite `ADD COLUMN` 约束）。
- 若实施 C5 时 C0 已经提交：未发布前可回改 C0 批次并入（代价更小）；已发布则用
  同一幂等模式升 v2→v3。原则是"不给已部署库留半迁移状态"，由实施者按 C0 当时的
  提交状态选择并在提交信息里说明。
- 迁移测试需同步覆盖新列：v1 快照库迁移后 `reminder_template` 存在且为默认模板。

## 明确不做（防蔓延）

- 否决项：暗色模式/主题/换肤、更多个人标签槽（4 槽固定）、默认用途自动预填、
  默认开始时间、邮件/短信/微信通知、自定义快捷键、声音提示、预约后自动跳转。
- 第二梯队（留待以后单独裁决，本轮不得实现）：登录后默认页增加"预约记录"选项、
  记住上次日历标签筛选、界面字号放大（建议未来专项版本）。

## 实施顺序与完成定义

- 顺序：C1（不依赖 schema，可先行）→ C0 → C2 + C4 + C5（同表迁移，可同批）→ C3；
  每项独立提交并通过 `v2/scripts/check.sh`。
- 每项完成 = 后端 + 前端 + 契约同步 + 测试全绿 + `CHANGELOG.md` 记录 + 提交信息带编号。
- 全部完成后：在 `RELEASE-CHECKLIST.md` 以现有证据表风格续补 E32 起证据行
  （backend/frontend/跨层测试数量、`check.sh` 结果、真实浏览器回归、schema v1→v2
  迁移演练含恢复路径）；`formal_external_release_allowed` 保持 `false` 直到
  Windows 实机与签名门禁重新执行。

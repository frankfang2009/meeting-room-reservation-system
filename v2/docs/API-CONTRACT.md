# V2 API v1 契约

产品版本是 V2.5.1，API schema 的首个稳定版本仍使用 `/api/v1`。字段统一使用
camelCase。预约业务日期/时分使用服务器本地时间；带 `Utc` 后缀以及创建、修改、
事件和审计时间使用 UTC RFC3339。

## 通用流程

1. `GET /api/v1/session`：任何页面首次加载都调用；返回 setup、认证状态和 CSRF token。
2. 未设置时进入首次向导；已设置但未认证时进入登录。
3. 登录成功后调用 `GET /api/v1/bootstrap`。
4. 所有 `POST/PATCH/PUT/DELETE` 请求携带 `X-CSRF-Token`。

通用错误：

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "请检查输入内容",
    "fields": { "partyName": "请输入当事人姓名" }
  },
  "requestId": "7b4b7e6c70eb4cc6a4897f6da6034b7a"
}
```

所有 `/api/v1` 错误（包括 413 与未预期 500）都使用这一 JSON 外形，并在响应头
返回同值 `X-Request-Id`。恢复状态返回 503 `SYSTEM_RECOVERY_REQUIRED`；底层异常
只写服务日志，不进入响应。

`GET /healthz` 不需要登录。所有来源可读取通用健康状态；`install_id` 与
`recovery_code` 仅在请求来源为回环地址时返回，局域网来源不得获得这两个字段。

## Setup 与 session

`GET /api/v1/session`

```json
{
  "productVersion": "V2.5.1",
  "setupComplete": true,
  "authenticated": true,
  "csrfToken": "...",
  "currentUser": {
    "id": "...", "username": "lijing", "name": "李静",
    "department": "工伤认定科", "role": "employee", "enabled": true
  }
}
```

`POST /api/v1/setup/complete` 仅允许 setup 未完成且来源为 loopback；`Host` 还必须
是固定端口上的 `localhost` 或回环 IP 字面量，其他 Host 一律拒绝，防止浏览器
DNS rebinding 夺取首次管理员：

```json
{
  "admin": { "username": "admin", "password": "...", "name": "管理员", "department": "..." },
  "rooms": [{ "name": "笔录室 1" }],
  "workStart": "08:30", "workEnd": "17:30"
}
```

`POST /api/v1/session` 请求 `{ "username": "...", "password": "..." }`。`DELETE /api/v1/session` 退出。

## Bootstrap

`GET /api/v1/bootstrap`

```json
{
  "productVersion": "V2.5.1",
  "serverDate": "2026-08-10",
  "serverTime": "14:32:05",
  "currentUser": {},
  "rooms": [],
  "users": [],
  "globalTags": [],
  "personalTags": [],
  "preferences": {},
  "settings": {
    "workStart": "08:30", "workEnd": "17:30",
    "slotMinutes": 30, "maxDurationMinutes": 180
  },
  "permissions": {
    "manageRooms": false, "manageUsers": false, "manageSystem": false,
    "viewReports": true, "viewOverallReports": false,
    "viewOtherUserReports": false
  }
}
```

`serverDate/serverTime` 是认证工作台的业务时间真源；客户端只能以收到响应时的本地
单调经过时间推进这个服务端墙钟，不得用浏览器时区重新解释业务日期。

`users` 只对管理员包含全量用户；普通员工仍可从预约对象的 `owner` 摘要显示共享日历的预约人。

## 预约

`GET /api/v1/reservations?dateFrom=YYYY-MM-DD&dateTo=YYYY-MM-DD&pageSize=...&cursor=...`
返回认证用户可见的共享日历完整详情。

`GET /api/v1/reservations/upcoming` 返回当前登录用户本人 `status=active` 且尚未结束的
预约，按 `date/start` 升序排列。它不接受 owner、日期或分页参数，响应为
`{ "items": [预约对象] }`；管理员也只获得本人即将到来的预约，不扩展为全单位列表。

`GET /api/v1/reservations/history?month=YYYY-MM&ownerId=...&status=active|cancelled&tagId=...&query=...&pageSize=...&cursor=...`：
员工始终由服务端收窄为本人，忽略或拒绝扩权参数。
关键词最长 120 字，按字面量同时匹配当事人、案号、事项、备注、笔录室和标签快照。

日期接受 `0001-01-01` 至 `9999-12-31`，但查询跨度最多 366 天。历史月份接受
`0001-01` 至 `9999-11`；`9999-12` 无法安全形成右开区间，稳定返回 422 JSON，
不得进入 500。历史状态筛选只接受 `active` 或 `cancelled`；游标与首次请求的日期/月、owner、room、status、tag 和 query 筛选绑定；
把旧游标用于不同筛选会返回 `422 INVALID_CURSOR`。

两个列表都返回：

```json
{ "items": [], "nextCursor": null, "pageSize": 100, "total": 0 }
```

游标由服务器签名，客户端不得解析或构造；任何筛选条件变化都必须丢弃旧游标。

预约对象：

```json
{
  "id": "...", "date": "2026-08-10", "roomId": "...",
  "roomName": "笔录室 1", "start": "09:00", "end": "10:00",
  "partyName": "张女士", "caseNumber": "2026-001",
  "purpose": "工伤笔录", "notes": "...",
  "tagId": "tag-1", "tagLabel": "首次笔录",
  "ownerId": "...", "owner": { "id": "...", "name": "李静" },
  "status": "active", "revision": 1,
  "createdAt": "...", "updatedAt": "...",
  "canEdit": true, "canCancel": true
}
```

`POST /api/v1/reservations`：

```json
{
  "date": "2026-08-10", "roomId": "...", "start": "09:00",
  "duration": 60, "partyName": "张女士", "caseNumber": "2026-001",
  "purpose": "工伤笔录", "notes": "", "tagId": "tag-1"
}
```

`PATCH /api/v1/reservations/{id}` 使用同样字段并增加 `expectedRevision`。`POST /api/v1/reservations/{id}/cancel` 请求 `{ "expectedRevision": 2 }`。

`GET /api/v1/reservations/{id}` 对他人 `status=active` 的预约仍按共享日历契约
返回完整详情；对他人 `status=cancelled` 的预约，普通员工稳定返回
`403 FORBIDDEN`。预约本人与管理员仍可读取已取消详情。

`purpose` 在创建和修改时均为必填、去除首尾空白后不得为空；缺失或空白返回
`422 VALIDATION_ERROR`，`error.fields.purpose="请输入事项"`。服务端不会生成默认用途。

slot 冲突返回 `409 SLOT_CONFLICT`，`error.conflicts` 只含 `id/roomId/date/start/end`；
预检后才由 SQLite 槽位唯一约束发现的竞争冲突也使用相同响应，不得降级为数据库不可用。
revision 冲突返回 `409 REVISION_CONFLICT`，`error.current` 是最新预约对象。

预约开始后到结束前 `canEdit=false`、`canCancel=true`；结束后两者均为 false。
`GET /api/v1/reservations/{id}/events` 只允许预约本人或管理员读取追加式变更时间线。

## 管理与个人设置

- `PUT /api/v1/admin/settings` 仅管理员可用，请求
  `{ "workStart": "08:30", "workEnd": "17:30" }`。两个时间必须是 `HH:MM`、
  按 `slotMinutes` 对齐且结束晚于开始；非法返回 `422 VALIDATION_ERROR`
  及 `fields.workStart/workEnd`。成功返回完整 `settings`，只影响后续时段生成，
  不改写已有预约的日期或时间，并记录 `settings.updated` 安全审计。
- `GET|POST /api/v1/rooms`；`PATCH|DELETE /api/v1/rooms/{id}`。管理员房间响应与
  `POST/PATCH` 支持布尔字段 `showOnDisplay`，新房间默认 `true`；该字段只用于管理，
  不进入员工 bootstrap、公开大屏或只读集成响应。其中管理页的周期性
  `GET /api/v1/rooms` 是被动状态轮询，不刷新 30 分钟空闲会话计时。
- `GET /api/v1/rooms/{id}/deletion-impact` 仅管理员可用，返回目标
  `room: {id,name}`、所有未结束 active 预约计数 `total`，以及按日期/开始时间排序的
  前 50 个完整预约投影 `items`；没有阻断项时 `total=0, items=[]`，房间不存在返回
  `404 NOT_FOUND`。`DELETE /api/v1/rooms/{id}` 遇到阻断时返回
  `409 ROOM_HAS_FUTURE_BOOKINGS`，其中 `error.current={roomId,roomName,total}`，
  `error.conflicts` 与上述最多 50 条 `items` 一致；`total` 可能大于 conflicts 长度。
- `GET|POST /api/v1/users`；`PATCH /api/v1/users/{id}`；`POST /api/v1/users/{id}/reset-password`。
- `PUT /api/v1/tags/global` 更新槽 1、2。
- `GET|PUT /api/v1/preferences` 更新当前用户默认时长、默认房间、默认标签、三项提醒
  偏好（变更通知开关、临近提醒开关、提醒提示音开关）和个人标签 3、4。
  `defaultTagSlot` 只接受 `null | 1 | 2 | 3 | 4`，表示本人标签槽位引用，不存储标签文案；
  `reminderLeadMinutes` 只接受整数 `15 | 30 | 60`，新用户默认 `30`，关闭提醒不会清除该值；
  `reminderSound` 只接受布尔，新用户默认开启；
  `reminderTemplate` 是清理首尾空白后不超过 200 字的本人纯文本模板，空字符串回退系统
  默认模板。服务端只存储和返回模板，不拼装成品文案；前端变量白名单仅为
  `{当事人姓名} {日期} {开始时间} {结束时间} {笔录室}`，不含案号、用途或备注。
- `GET /api/v1/activity` 保留为兼容接口；新前端不再调用，统计口径唯一来源是下方
  reports 接口，个人中心不展示活动数据。

## 数据中心与 CSV

`GET /api/v1/reports/overview` 接受：

- `scope=self|overall|person`；`person` 必须同时提供 `ownerId`；
- `dateFrom/dateTo`，默认服务端当前自然月，含首尾日期且最长 366 天；
- 可选 `roomId/tagId/query`，关键词最长 120 字并按字面量匹配当事人、案号、事项、备注、
  笔录室和标签快照。

员工只允许 `scope=self` 且不得传 `ownerId`；管理员可使用全部三种视角。服务端先解析
角色与范围再构造查询，员工请求全单位、指定人员或伪造 owner 均返回 `403 FORBIDDEN`。
管理员全单位视角使用个人标签 3/4 时返回 `422 PERSONAL_TAG_OWNER_REQUIRED`。

响应固定包含 `resolvedScope`、`filters`、`metricVersion`、`generatedAtUtc`、`summary`、
`weeklyTrend`、`monthlyTrend`、`weekdayTimeDistribution` 和 `exportRowCount`。
`weeklyTrend` 按周一开始的自然周汇总，`monthlyTrend` 按自然月汇总，两者均补齐
查询范围内的零值周期。`summary` 包含
`activeCount`、`endedCount`、`activeDurationMinutes`、`cancelledCount` 和
`cancellationRate`；个人视角增加 `tagDistribution`，
全单位视角增加 `personWorkload/roomWorkload/globalTagDistribution`。`endedCount` 是有效
预约中结束时间已到的数量，不表示办件完成；`activeDurationMinutes` 是所有有效预约的
起止时长之和。

`GET /api/v1/reports/reservations.csv` 复用相同范围与筛选，另接受可选
`status=active|cancelled`。响应为带 UTF-8 BOM、CRLF 的 CSV，字段顺序固定为：预约日期、
开始时间、结束时间、预约时长（分钟）、笔录室、预约人、当事人、案号、事项、标签、
备注、状态、创建时间、最后修改时间、取消时间。文本若以可执行公式字符开头则加单引号；
超过 20,000 行返回 `422 EXPORT_TOO_LARGE`。响应头提供 `X-Report-Field-Version`、
`X-Report-Row-Count` 与安全文件名；成功和超限失败都写入不含业务内容的
`report.csv_exported` 审计。
- `GET /api/v1/reminders/due` 返回 `kind=upcoming|change|handover|assignment` 的全部待处理
  条目（普通交接请求携带 `handoverRequestId/fromName`；管理员指派结果携带
  `eventId/assignedByName/fromName`，两者都不受通知开关约束）（不再只取单条）；临近
  提醒和他人修改/取消通知各自受个人开关控制。临近提醒是状态而非待办：
  窗口以请求时的当前 `reminderLeadMinutes` 实时计算并返回窗口内全部条目，没有确认
  动作。变更通知以事件为维度，并按事件快照中的当时预约者投递，不随预约当前归属
  漂移：每项含 `eventId`、`changeType`、`actorName`、
  `occurredAt` 与 `diffs`（由事件快照计算的字段级对比，仅面向用户的字段），超过
  45 天未确认的变更事件过期不再返回。
- `POST /api/v1/reminders/ack` 提交 `{ "eventId": "…" }` 按事件确认（幂等）；修改/取消
  通知仅事件快照中的当时预约者可确认且受变更通知开关约束，管理员指派结果仅该
  handover 事件快照中的接收人可确认且不受开关约束，否则返回 `403/422/409`。确认动作只写回执、不改变
  预约归属，并顺带清理 90 天前的旧回执。
- `POST /api/v1/reservations/{id}/handover` 提交 `{ "toUserId": "…" }`：预约本人发起
  待确认交接请求（同一预约同时仅一个 pending，重复返回 `409 HANDOVER_REQUEST_EXISTS`；
  目标须为启用用户且非本人）；管理员对他人预约调用则直接指派、立即生效，并在同一
  事务撤回该预约旧 pending 请求（返回 `assigned: true` 与翻转后的预约，接收人随后
  通过 `kind=assignment` 知晓）。仅未开始的 active 预约可交接。
- `POST /api/v1/handover-requests/{id}/accept|decline` 仅接手人可调用；接受在
  `BEGIN IMMEDIATE` 事务内乐观锁翻转归属（revision+1、owner 姓名快照更新、追加
  `handover` 事件、审计 `handover.accepted`），预约已开始/取消返回
  `409 HANDOVER_EXPIRED`，发起后被编辑返回 `409 REVISION_CONFLICT`（请求均作废）。
- `DELETE /api/v1/handover-requests/{id}` 发起人或管理员撤回。
- `GET /api/v1/handover-requests` 返回当前用户 incoming/outgoing 的待处理请求
  （含预约摘要与双方姓名；详情仅收发双方与管理员可见）。
- `GET /api/v1/users/directory` 任何已登录用户可读的启用用户最小投影
  `{id, name, department}`（不含用户名），作为交接选择器数据源。
- `GET /api/v1/admin/system` 返回真实数据库、服务、备份追平状态以及
  `backupSequence/dataSequence/servicePort/bindMode/workStart/workEnd`；其 `databaseVersion`
  为当前 schema v5；`POST /api/v1/admin/backups`
  返回备份文件、UTC 时间、备份序列与源数据序列；备份管道失败时稳定返回
  `500 BACKUP_FAILED`，即使失败审计写入也失败；`GET /api/v1/admin/diagnostics`。
- 备份侧车的 `databaseSchemaVersion` 新建时为 `5`；恢复器只接受通过完整性和安装身份
  验证的 schema v1 或当前 schema v5 备份。schema v1 在恢复原子替换后复用与服务启动
  相同的数据库准备迁移链依次升级至 v5；schema v2/v3/v4 只属于已有产品代际 2 数据库的
  启动迁移输入，不是恢复侧车
  的受支持版本。迁移或复检失败不得标记恢复成功。
- `GET /api/v1/admin/audit` 支持 `cursor/pageSize/action/outcome/actorId/targetType/targetId/dateFrom/dateTo`，
  返回 `{items,nextCursor,pageSize,total}`，事件时间键为 `occurredAtUtc`。
- `GET|POST /api/v1/admin/tokens`；`DELETE /api/v1/admin/tokens/{id}`。明文 token 只在创建成功响应出现一次；`expiresAt` 必须带时区并规范化为 UTC。

## 只读集成 API

`/api/v1/integration/*` 不使用登录 Cookie 或 CSRF，必须携带
`Authorization: Bearer mr2_...`。管理员创建令牌时的 scope 白名单严格限定为
`rooms:read`、`availability:read`、`health:read`；接口全部为 GET，不提供预约内容写入能力。
每个接口只接受对应 scope，成功使用会更新令牌的 `lastUsedAt`：

- `GET /api/v1/integration/rooms` 需要 `rooms:read`，仅返回启用笔录室：
  `{ "items": [{ "id": "...", "name": "笔录室 1" }] }`。
- `GET /api/v1/integration/availability?date=YYYY-MM-DD` 需要 `availability:read`，返回：

  ```json
  {
    "date": "2026-08-10",
    "rooms": [{
      "roomId": "...", "roomName": "笔录室 1",
      "slots": [{ "start": "08:30", "available": true }]
    }]
  }
  ```

  日期缺失或非法时按通用 JSON 外形返回 `422 VALIDATION_ERROR`。
- `GET /api/v1/integration/health` 需要 `health:read`，只返回
  `{ "ok": true, "productVersion": "V2.5.1" }`。

令牌错误语义稳定为：缺少或不是 Bearer 认证时 `401 TOKEN_REQUIRED`；令牌未知或已撤销
时 `401 TOKEN_INVALID`；当前时间达到 `expiresAt` 时 `401 TOKEN_EXPIRED`；令牌存在但缺少
目标 scope 时 `403 TOKEN_SCOPE_FORBIDDEN`。过期令牌不会退化为无效令牌，也不会继续执行
目标查询。

## 公开大屏

`GET /api/v1/display/today` 是公开且只允许 loopback/私网来源的专用投影：

```json
{
  "serverDate": "2026-08-10",
  "serverTime": "14:32",
  "lastUpdatedAt": "2026-08-10T14:32:05+08:00",
  "status": "online",
  "rooms": [{
    "id": "...", "name": "笔录室 1",
    "current": { "maskedPartyName": "张*燕", "start": "14:00", "end": "15:00" },
    "next": { "maskedPartyName": "李*明", "start": "15:30", "end": "16:00" }
  }]
}
```

这一响应的允许键集合必须由服务端测试锁定，不能通过复用内部预约序列化器生成。
只有 `is_active=1 AND show_on_display=1` 的笔录室及其预约能进入服务端查询和投影；
全部隐藏时返回 `rooms: []`。`/integration/rooms` 与 `/integration/availability` 不受
公开大屏开关影响。

## 对话预约（V2.6.0，默认关闭）

`conversation_settings.enabled` 默认为 0。关闭时，下面三个已注册的写接口返回
`403 FEATURE_DISABLED`，`error.message` 为「对话预约未开启」，并且不编码、不调用模型。
`GET /bootstrap` 的 `permissions.useConversation` 仅在功能开启且（测试注入了
模型替身，或已配置模型名和服务器密钥）时为 true；响应里没有密钥。

第一期没有语音输入，也没有麦克风或系统听写接口。本增量不注册会话列表、
重命名、删除、管理员模型设置，也不在预约详情上返回 `fromConversation`。
这些未注册路径仍是 `404`，而不是 `403`。

已注册的只有 POST。对这三条路径发 GET 时，现有的未匹配路由仍返回 `404`。
已注册：

- `POST /api/v1/conversations/{id}/messages`，请求体 `{ "text": "..." }`，
  可选 `mentions`（`start`、`end`、`kind=party|case`、`value`）。`id` 为 `new`
  时由服务端新建会话。发送和预览先去掉首尾空白，再按 500 字拒绝。
  `POST /api/v1/conversations/preview` 使用同一入口，返回将要外发的编码。
  `@` / `#` 的下标按去掉的前缀回退。
- `POST /api/v1/quick-booking/prepare`，请求体 `{ "draftId", roomId?, duration?, caseNumber?, purpose?, partyName? }`。
  `duration` 可以是 30–180 的 30 倍数，或 `"window"`。此接口不调用模型。
- `POST /api/v1/quick-booking/commit`，请求体 `{ "token": "qb_..." }`。成功 `201`，
  正文是现有预约序列化结果加上 `conversationId`。

准备成功时响应为：

```json
{
  "conversation": { "id": "...", "title": "笔录室 1 · 8月10日 12:00", "status": "open" },
  "reply": {
    "kind": "prepared",
    "text": "帮你找到了：笔录室 1 明天下午空着，案号从你 8 月 8 日的预约里带出来了。",
    "prepared": {
      "token": "qb_...",
      "expiresAt": "2026-08-09T08:10:00+08:00",
      "booking": {
        "date": "2026-08-10", "weekday": "星期一", "start": "12:00", "duration": 60, "end": "13:00",
        "roomId": "...", "roomName": "笔录室 1",
        "partyName": "孙某", "caseNumber": "2026-087", "purpose": "询问笔录",
        "notes": "", "tagId": "tag-1"
      },
      "inferred": ["start", "roomId", "duration", "caseNumber", "purpose"],
      "missing": [],
      "relativeDay": "明天"
    }
  }
}
```

`inferred` 只列出程序补上的字段。`missing` 列出仍空着的 `party`、`case`、`purpose`。
事项只从请求者本人的历史预约，或句子里已经出现的事项词带回；不采用模型的 `purpose_hint`，也不接受模型自由文本。事项为空时仍签发凭证，但 `commit` 会走
现有创建校验，返回 `422 VALIDATION_ERROR` 且 `error.fields.purpose="请输入事项"`。
用户可再调 `prepare` 提交事项。相对日期以服务器业务日为准，回复正文不写案号。

每句话都走模型。模型只返回引用和证据：相对日或周几、开始时段或钟点、时长、
房间、事项列表中的一项、`refs` 和证据下标。证据下标必须存在，时间字段的证据
必须是时间类词元。`need_clarification` 为真时一律澄清。时段词只约束开始时间：
上午 08:30–12:00，中午 11:30–13:30，下午 12:00–17:30，晚上 18:00–23:00。开始
时间必须落在窗口与工作时间 08:30–17:30 的交集里；结束时间可以越过时段窗口，
不能越过工作时间。用户说出的时间、日期或时长，只要本机要改它（吸附、钳制或
挪走），就只给最近的合法选项并且不发凭证；卡片只为原话或点选结果准备。
例如 18:30 一小时提供 16:30–17:30，07:00 提供 08:30，14:15 提供 14:00。
时段窗口和工作时间没有交集时同样只给选项（晚上半小时给 17:00–17:30）。
没说钟点、只说了时段时，开始时间取该时段在工作时间内的起点。用户点选之后才
准备卡片。时长必须是 30–180 的 30 倍数，不能约过去。

当事人和案号只来自两处：请求者本人 `status=active` 的历史（含已结束的场次），
或用户自己句子里的占位符，由本机按原文回填。`@` 只能当人，`#` 只能当案号。
启用用户的显示名只参与编码，不进入提示词，也不用来带出别人的事项。未说明的
房间、时长、事项、当事人按本人历史补全。

多间空房且没有房间偏好时返回 `reply.kind="clarify"`。房间选项含
`type=room`、`roomId`、`roomName`、`date`、`windowStart`、`windowEnd`、
`maxDuration`，最多 3 间。时长不明确时另有
`duration.choices=[60, 120, "window"]`。澄清响应没有凭证。
选房顺序是：句子里的房间名、该案号或当事人最近用过的房间、用户的默认笔录室、
本人近 90 天使用最多的笔录室。空房只统计 `status=active` 的占用。

凭证是 `qb_` 加随机串，库里只存 SHA-256，10 分钟有效，并绑定用户和当时的
`session_version`。别人的凭证返回 `404 NOT_FOUND`。已使用返回
`409 PREPARED_USED`。过期返回 `410 PREPARED_EXPIRED`。会话版本不一致返回
`401 SESSION_EXPIRED`。`commit` 在同一个 `BEGIN IMMEDIATE` 事务里重新检查
权限和冲突，并调用与 `POST /reservations` 相同的创建路径；冲突返回
`409 SLOT_CONFLICT`，`conflicts` 只有 `id`、`roomId`、`date`、`start`、`end`。
失败会回滚，凭证保持未使用。预约审计仍是 `reservation_events` 的 `created`，
不另记原文。

离开本机的句子必须先经过 `v2app/services/deid.py` 的白名单编码器。编码器不联网。
所有带数字的词元共用一笔预算，中文数字按十进制位数计：每条消息最多 1 个日期、
2 个时刻、1 个时长、1 个日号（1–31）、1 个已公开的房间序号，合计不超过 16 个
数字字符。时刻的分钟只允许 00/15/30/45。时长必须是 15 的倍数且不超过 180 分钟。
四位年份只允许服务器当年或下一年，并且 `date()` 必须认这是真日期。不在词表里的
数字词，两边必须是词表词、标点、空白或字符串边界，否则并进占位符。
`13:38`、`13:81`、`6:7` 和超额的 `5点6点7点`、`一号三号` 不放行。2026-10-20
当天一句数字文法约 26.27 比特；换一句或改走词表选词都不在这笔上限里，它只挡住
偶然碎片。事项只发送代码里的类别，不发送历史自由文本。送给模型的只有编码句、
服务器日期与星期、当前钟点、启用房间名和事项类别。工作时间留在本机，不写进提示词。
字段值必须落在被引用词元的候选集里，对不上就澄清、不发凭证。
`confident=false`（空文本、过长或编码结果破坏不变式）、闸门拦截、解析失败、超时
或未配置模型时，`reply.kind="fallback"`。安全审计 `model.request` 只记录实际发出
的编码文本、供应商、模型名、占位符数量、耗时和结果；映射不入库。

验收拆开。S1 隐私不变式、S2 授权（凭证 10 分钟、用户与会话绑定、重放、过期、
跨用户、权限与冲突复核）和 S3 业务硬约束都必须 100%。Q 理解质量是冻结语料上的
指标，CI 用录制的模型输出回放，下限 0.9，不按缺陷阻断。
已知残余风险：名字里由词表词组成的部分（如「张会」→ `[P1] 会`，负责人 2026-10-20 确认）。
管理员房间名只检查长度。阿拉伯-印度数字、泰文数字不在这套归一化里。

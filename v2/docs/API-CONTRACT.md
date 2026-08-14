# V2 API v1 契约

产品版本是 V2.0.0，API schema 的首个稳定版本仍使用 `/api/v1`。字段统一使用
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

## Setup 与 session

`GET /api/v1/session`

```json
{
  "productVersion": "V2.0.0",
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
  "productVersion": "V2.0.0",
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
    "manageRooms": false, "manageUsers": false, "manageSystem": false
  }
}
```

`serverDate/serverTime` 是认证工作台的业务时间真源；客户端只能以收到响应时的本地
单调经过时间推进这个服务端墙钟，不得用浏览器时区重新解释业务日期。

`users` 只对管理员包含全量用户；普通员工仍可从预约对象的 `owner` 摘要显示共享日历的预约人。

## 预约

`GET /api/v1/reservations?dateFrom=YYYY-MM-DD&dateTo=YYYY-MM-DD&pageSize=...&cursor=...`
返回认证用户可见的共享日历完整详情。

`GET /api/v1/reservations/history?month=YYYY-MM&ownerId=...&status=active|cancelled&tagId=...&query=...&pageSize=...&cursor=...`：
员工始终由服务端收窄为本人，忽略或拒绝扩权参数。

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

slot 冲突返回 `409 SLOT_CONFLICT`，`error.conflicts` 只含 `id/roomId/date/start/end`。revision 冲突返回 `409 REVISION_CONFLICT`，`error.current` 是最新预约对象。

预约开始后到结束前 `canEdit=false`、`canCancel=true`；结束后两者均为 false。
`GET /api/v1/reservations/{id}/events` 只允许预约本人或管理员读取追加式变更时间线。

## 管理与个人设置

- `GET|POST /api/v1/rooms`；`PATCH /api/v1/rooms/{id}`。
- `GET|POST /api/v1/users`；`PATCH /api/v1/users/{id}`；`POST /api/v1/users/{id}/reset-password`。
- `PUT /api/v1/tags/global` 更新槽 1、2。
- `GET|PUT /api/v1/preferences` 更新当前用户默认时长、默认房间、两项网页通知和个人标签 3、4。
- `GET /api/v1/activity` 返回当前登录用户的只读活动聚合。服务端按本地时间只统计
  `status=active` 且已经结束的本人预约，响应仅包含四项汇总，以及平均时长、常用
  笔录室和常用标签概览。响应不得接受或返回 `ownerId`、按日分布或预约明细。
- `GET /api/v1/reminders/due` 返回 `kind=upcoming|change`；临近提醒和他人修改/取消通知各自受个人开关控制。
- `POST /api/v1/reminders/{reservationId}/ack` 提交 `{ "revision": 2, "kind": "change" }`；两种回执相互独立。
- `GET /api/v1/admin/system` 返回真实数据库、服务、备份追平状态以及
  `backupSequence/dataSequence/servicePort/bindMode`；`POST /api/v1/admin/backups`
  返回备份文件、UTC 时间、备份序列与源数据序列；`GET /api/v1/admin/diagnostics`。
- `GET /api/v1/admin/audit` 支持 `cursor/pageSize/action/outcome/actorId/targetType/targetId/dateFrom/dateTo`，
  返回 `{items,nextCursor,pageSize,total}`，事件时间键为 `occurredAtUtc`。
- `GET|POST /api/v1/admin/tokens`；`DELETE /api/v1/admin/tokens/{id}`。明文 token 只在创建成功响应出现一次；`expiresAt` 必须带时区并规范化为 UTC。

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

# V2 API v1 契约

产品版本是 V2.0.0，API schema 的首个稳定版本仍使用 `/api/v1`。字段统一使用 camelCase，所有时间字符串来自服务器所在本地时区。

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
  }
}
```

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

`POST /api/v1/setup/complete` 仅允许 setup 未完成且来源为 loopback：

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

`users` 只对管理员包含全量用户；普通员工仍可从预约对象的 `owner` 摘要显示共享日历的预约人。

## 预约

`GET /api/v1/reservations?dateFrom=YYYY-MM-DD&dateTo=YYYY-MM-DD` 返回认证用户可见的共享日历完整详情。

`GET /api/v1/reservations/history?month=YYYY-MM&ownerId=...&tagId=...&query=...`：员工始终由服务端收窄为本人，忽略或拒绝扩权参数。

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
  "createdAt": "...", "updatedAt": "..."
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

slot 冲突返回 `409 SLOT_CONFLICT`，`error.conflicts` 只含 `id/roomId/date/start/end`。revision 冲突返回 `409 REVISION_CONFLICT`，`error.current` 是最新预约对象。

## 管理与个人设置

- `GET|POST /api/v1/rooms`；`PATCH /api/v1/rooms/{id}`。
- `GET|POST /api/v1/users`；`PATCH /api/v1/users/{id}`；`POST /api/v1/users/{id}/reset-password`。
- `PUT /api/v1/tags/global` 更新槽 1、2。
- `GET|PUT /api/v1/preferences` 更新当前用户默认时长、默认房间、两项网页通知和个人标签 3、4。
- `GET /api/v1/reminders/due` 返回 `kind=upcoming|change`；临近提醒和他人修改/取消通知各自受个人开关控制。
- `POST /api/v1/reminders/{reservationId}/ack` 提交 `{ "revision": 2, "kind": "change" }`；两种回执相互独立。
- `GET /api/v1/admin/system`；`POST /api/v1/admin/backups`；`GET /api/v1/admin/diagnostics`。
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
    "current": { "maskedPartyName": "张*士", "start": "14:00", "end": "15:00" },
    "next": { "maskedPartyName": "李*生", "start": "15:30", "end": "16:00" }
  }]
}
```

这一响应的允许键集合必须由服务端测试锁定，不能通过复用内部预约序列化器生成。

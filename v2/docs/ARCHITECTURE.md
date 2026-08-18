# V2.2.3 技术架构

## 运行形态

生产环境只有一个同源 HTTP 服务：Waitress 运行 Flask，Flask 提供 JSON API 和预构建 React 静态资源，SQLite 保存全部业务状态。客户机器不安装 Node.js。

```text
桌面浏览器
    │ same-origin cookie + CSRF
    ▼
Waitress / Flask :8080
    ├── /api/v1/*        JSON API
    ├── /healthz         安装身份健康检查
    └── /*                React dist / SPA fallback
             │
             ▼
      SQLite (WAL + FK)
```

首次设置前，服务管理器以回环模式启动；完成设置后重启为局域网模式。`install_id` 同时出现在安装身份文件和健康响应中，安装器不能把端口上的其他服务误判成本系统。
正式布局为 `_程序文件/app`（代码和静态资源）、`runtime`、`data`、`backups`、
`logs` 五个同级目录。runtime 的 `python313._pth` 只显式加入受保护的
`..\app`，不得把整个 `_程序文件` 裸加入模块搜索路径，也不得启用用户
site-packages。生产入口仅以当前安装的 runtime、`app/service.py` 路径、
`install_id`、PID 与每进程随机令牌共同确认启停对象；Windows 存活探测只打开
查询句柄，不按进程名或端口结束程序。后台错误写入有限大小的
`logs/service.log` 轮转文件。

## 后端边界

- `v2app` 应用工厂：配置、数据库生命周期、会话、CSRF、安全响应头和蓝图注册。
- 数据库层：代际校验、schema 初始化、明确事务和备份。
- 认证层：服务端 session、密码哈希、账号+IP/IP 双层限流、固定耗时未知账号校验、
  启用状态、30 分钟空闲/12 小时绝对失效与安全审计。
- 预约服务：权限、slot 唯一占用、revision 乐观锁和追加式事件。
- 管理 API：用户、笔录室、全局标签、系统健康、诊断和备份。
- 用户 API：偏好、个人标签、提醒回执。
- 报表服务：先解析角色与服务端数据范围，再复用同一筛选、指标版本和 CSV 字段版本；
  个人统计的唯一消费方是数据中心（本人即 self 视角），个人中心不展示活动摘要，
  全应用不另写第二套聚合 SQL。
- 公开投影：单独查询和白名单序列化，不复用内部预约序列化器。
- 可选只读令牌：只保存摘要，范围白名单，不允许写操作。

## API 约定

- 基础路径：`/api/v1`。
- 成功响应可直接返回资源或 `{ "items": [...] }`；列表可增加
  `nextCursor` 和 `total`。错误统一为：

```json
{
  "error": {
    "code": "SLOT_CONFLICT",
    "message": "所选时段已被占用",
    "requestId": "server-generated-uuid",
    "fields": {},
    "conflicts": []
  }
}
```

- 认证 cookie 必须是 `HttpOnly`、`SameSite=Strict`；非安全本地 HTTP 环境不强制 `Secure`，部署到 TLS 后启用。
- 所有 session 写请求要求 `X-CSRF-Token`。
- JSON 请求设大小上限；字符串服务端裁剪并验证长度。
- 页面显示权限和服务端资源权限使用同一角色枚举，但必须独立执行。

## 数据库不变量

- 启动时首先读取安装镜像和数据库元数据，再执行 `quick_check` 与外键检查；非
  `product_generation=2`、未知 schema、疑似 V1、已设置后数据库缺失/为空/损坏时
  都进入 fail-closed 恢复状态，绝不创建新库或重新开放首次设置。
- `PRAGMA foreign_keys=ON`，写入使用显式事务；数据库文件与 secret 不进入版本库或候选 payload。
- 用户、笔录室、预约均使用稳定随机 ID；不能用时间戳或数组下标充当业务身份。
- 预约事件只追加；包含操作者、事件类型、发生时间以及最少必要的前后快照。
- 备份先做 SQLite 在线备份，再执行 `integrity_check`，最终用原子改名提交；每份
  数据库必须有带 `install_id`、schema、SHA-256 与单调序号的 sidecar。恢复在停服
  后执行预恢复快照、身份和完整性校验、原子替换及失败回滚。

## 前端边界

- 冻结 JSX/CSS 是视觉和交互合同，合成 seed、URL demo 状态和客户端权限判断不是业务真值。
- 应用启动依次获取 setup/session/bootstrap；会话过期进入不可绕过的重新登录状态。
- 预约冲突、离线、无权限、保存失败和成功使用冻结稿规定的不同层级反馈。
- 公开大屏只消费专门的公开响应类型，任何内部预约字段出现都应让契约测试失败。

## 安装与升级边界

- 安装包有顶层零参数 BAT、冻结 Python runtime、payload 和 manifest。
- 正式安装根固定为 `%ProgramFiles%\会议室预约系统V2`，提交前使用同卷 staging 和
  事务锁；测试模式才允许注入临时目录。
- 安装现场生成 `install_id` 与 `.secret_key`；两者绝不进入候选包。
- 安装器关闭继承并设置 DACL：程序允许 Users 读取执行，data/backups/logs 只允许
  SYSTEM 与 Administrators；安装、启动和更新都要复核。
- 每日 02:00 的 SYSTEM 任务执行在线备份，服务启动时对超过 24 小时的缺口补备份；
  本机 UAC 恢复工具是唯一恢复入口。
- V2.2.0 交付离线累计升级包与零参数 BAT，首个来源矩阵只接受冻结 V2.1.0。升级工具
  使用包内独立 runtime，严格校验 manifest、工具、runtime 和 payload 后，只读取明确
  登记的 V2 安装根；禁止扫描磁盘寻找旧版本或触碰 V1。
- 升级先停服并保留原任务、防火墙与服务状态，在线备份及全数据树哈希通过后才 staging；
  程序与 runtime 替换后必须通过健康检查，再恢复原运行状态并最后提交版本、manifest
  和 receipt。提交前任一步失败都回滚程序、runtime、根文件、数据与原运行状态；中断
  重跑按事务记录恢复，若目标版本已提交则只完成清理，不得回滚升级后新产生的数据。

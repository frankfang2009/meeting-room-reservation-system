# 会议室预约系统

这是原会议室预约系统的等功能重制版。业务功能保持 1:1，不增加审批、通知、统计、导出、注册或其他流程。

## 功能范围

- 登录与退出
- 单日会议室预约日历
- 新建预约和冲突检查
- 我的预约与取消
- 管理员预约管理
- 管理员用户管理
- 管理员会议室管理

## 开发运行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python server.py
```

默认访问地址为 `http://127.0.0.1:8080`。

首次启动会创建管理员 `admin`，随机初始密码保存在
`data/首次登录账号密码.txt`。首次登录后应立即修改密码。

## 数据

- 正式数据库：`data/reservation.db`
- 固定会话密钥：`data/.secret_key`
- 备份目录：`backups/`
- 日志目录：`logs/`

不要把 SQLite 数据库放到 NAS、SMB 或其他共享文件系统。

这是全新安装版。不要用旧项目的 `reservation.db` 直接覆盖新版数据库；
如需保留旧数据，应先由维护人员完成数据迁移。

## 版本与数据库迁移

- 当前程序版本读取 `版本.txt`。
- 数据库结构版本由 `app.py` 中的 `SCHEMA_VERSION` 管理，并记录在
  `app_meta.schema_version`。
- 新增结构迁移时必须按版本连续追加到 `MIGRATIONS`，不能修改已经发布的旧迁移。
- 手动检查可运行 `python migrate_check.py --precheck data/reservation.db`；执行当前版本
  迁移与完整性检查可运行 `python migrate_check.py --migrate`。

Windows 一键升级包的制作与验收步骤见相邻的 `升级包工具/README-出包说明.txt`。

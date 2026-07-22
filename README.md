# 会议室预约系统

单位局域网使用的轻量会议室预约系统，以及面向 Windows 10/11 的单文件累计升级机制。

当前仓库状态：V1.0.1 候选版。Mac 自动测试和 Windows GitHub Actions 用于研发验证；
完成真实客户 Windows 电脑上的人工验收前，不视为正式发布。

## 目录

- `02_开发工作区/源代码工作区`：Flask 应用、数据库迁移框架和业务测试。
- `02_开发工作区/升级包工具`：BAT 生成器、PowerShell 5.1 升级事务、累计负载和测试。
- `02_开发工作区/Windows部署目录-V1.0.0`：不含 runtime 和数据的旧版测试基线。
- `02_开发工作区/Windows部署目录-V1.0.1-待Windows验收`：不含 runtime 和数据的新版候选结构。
- `02_开发工作区/升级机制实施计划.md`：升级、回滚和验收规则。

## 测试

本地业务与迁移测试：

```bash
cd 02_开发工作区/源代码工作区
python -m unittest discover -s tests -v
```

升级包生成器测试：

```bash
cd 02_开发工作区/升级包工具
python -m unittest discover -s tests -v
```

GitHub Actions 的 Windows PowerShell 5.1 作业会重新生成升级 BAT，并在临时安装目录验证：

- V1.0.0 到 V1.0.1 成功升级；
- 用户、会议室、预约、会话密钥和数据库结构保留；
- 同一 BAT 重复运行安全退出；
- 故障 Payload 触发回滚，旧程序和旧数据恢复。

交互式 UAC、SmartScreen、真实开机计划任务和物理断电仍须在真实 Windows 10/11 电脑验收。

## 安全

仓库忽略所有 `data`、`logs`、`backups`、runtime、本机虚拟环境、数据库、首次登录凭据和
V1.0.0 正式交付归档。不要把客户安装目录直接复制到仓库。

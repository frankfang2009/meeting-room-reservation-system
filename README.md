# 会议室预约系统

当前开发主线是 **V2.0.0 全新安装基准**：React 冻结视觉稿、Flask JSON API、SQLite，以及面向 Windows 10/11 的完整离线安装链。

V2 不迁移 V1。旧账号、笔录室、预约、配置和数据库不会被读取、导入或删除；原 `01_版本归档/`、`02_开发工作区/` 和 V1 Git 历史继续作为只读发布证据。

## V2 入口

- [`v2/README.md`](v2/README.md)：目录、运行方式和核心不变量。
- [`v2/docs/PRODUCT-CONTRACT.md`](v2/docs/PRODUCT-CONTRACT.md)：产品、权限、标签、公开大屏和范围真值。
- [`v2/docs/API-CONTRACT.md`](v2/docs/API-CONTRACT.md)：React 与 Flask 的 `/api/v1` 契约。
- [`v2/docs/ARCHITECTURE.md`](v2/docs/ARCHITECTURE.md)：运行、数据库和安装架构。
- [`v2/docs/RELEASE-CHECKLIST.md`](v2/docs/RELEASE-CHECKLIST.md)：自动化与 Windows 实机发布门禁。

## 目录

- `v2/frontend/`：冻结视觉合同上的正式 React 前端；生产只交付预构建静态资源，客户机不需要 Node.js。
- `v2/backend/`：模块化 Flask API、V2 数据库、认证权限、预约事务、备份和运行服务。
- `v2/installer/`：V2 fresh-install、确定性候选 ZIP 和后续 V2-only 更新事务。
- `02_开发工作区/`：V1 源码、升级器和发布证据；V2 不在这里继续叠加。

## 核心边界

- 数据库必须标识 `product_generation=2`；V1、未知或更高 schema 数据库在任何写入前拒绝。
- `admin | employee` 是唯一角色枚举。
- 登录后的日历对两种角色展示全单位预约完整详情；员工只能改/取消本人，管理员可管理全单位。
- 公开大屏仅接收 Flask 服务端生成的白名单和脱敏投影。
- 首次设置前只绑定 `127.0.0.1:8080`，设置完成并重启后才绑定局域网。
- 全新安装器只处理用户明确选择的不存在或空目录，不搜索或删除 V1。

## 验证

各子项目的 README 记录了可执行命令。合并门禁至少包括后端 `unittest`、前端 Node tests + production build、安装器 `unittest`、跨层契约检查和候选包反向校验。

自动化通过不等于正式外发。普通用户 Windows 10/11 的 UAC、SmartScreen/EDR、真实重启、计划任务、防火墙、第二台局域网电脑和电视大屏验收完成前，V2.0.0 只能标记为候选版。

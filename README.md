# 会议室预约系统

[English](README.en.md) · [贡献指南](CONTRIBUTING.md) · [安全策略](.github/SECURITY.md) · [Apache-2.0](LICENSE)

[![CI](https://github.com/frankfang2009/meeting-room-reservation-system/actions/workflows/ci.yml/badge.svg)](https://github.com/frankfang2009/meeting-room-reservation-system/actions/workflows/ci.yml) [![CodeQL](https://github.com/frankfang2009/meeting-room-reservation-system/actions/workflows/codeql.yml/badge.svg)](https://github.com/frankfang2009/meeting-room-reservation-system/actions/workflows/codeql.yml)

一个面向可信局域网、可自托管的会议室/笔录室预约系统。当前主线 V2.1.0 使用 React、Flask 与 SQLite，提供共享日历、预约管理、公开大屏、用户与房间管理、备份恢复和 Windows 全新安装链。

> 当前公开的是源代码，不是正式 Windows 安装包。V2.1.0 尚未完成普通用户 Windows 10/11 实机验收和 Authenticode 签名，任何自动构建产物都只能作为内部候选，不能视为正式发布。

## 主要能力

- 全单位共享预约日历；员工只能修改或取消本人预约，管理员可管理全单位预约。
- revision 并发保护、时段冲突检测，以及预约、占用时段和事件的同事务提交。
- 预约记录、网页内提醒、全局/个人标签和手动复制对外提醒文本。
- 公开大屏使用服务端白名单投影和姓名脱敏，不返回案号、备注、标签或工作人员身份。
- 首次设置阶段只绑定回环地址；完成配置并重启后才开放可信局域网监听。
- 安装身份校验、诊断、每日备份与原子恢复；异常数据库默认 fail-closed。

## 安全与部署边界

V2.1.0 只面向受信任的 Windows Domain/Private 局域网，当前仍使用 HTTP。不要把服务直接暴露到互联网、访客网络或不受信任的 Wi-Fi；部署方需要自行保证终端、网络、防火墙和操作系统账户安全。生产或客户数据库、日志、备份、密钥和真实个人信息不得提交到仓库或 Issue。

详细要求见 [安全部署说明](v2/docs/SECURITY-DEPLOYMENT.md) 与 [漏洞报告策略](.github/SECURITY.md)。

## 快速开始（开发环境）

完整本地门禁固定使用 Python 3.13.14、Node.js 22.17.1、npm 和 [uv](https://docs.astral.sh/uv/)。

```bash
v2/scripts/bootstrap-dev.sh
v2/scripts/check.sh
```

前端开发：

```bash
cd v2/frontend
npm run dev
```

构建前端后，可从 `v2/backend` 运行本地首次设置服务：

```bash
cd v2/frontend && npm run build
cd ../backend && .venv/bin/python server.py
```

新数据库会以 V2 generation 2、`setup_complete=0` 创建，并在首次设置完成前只监听 `127.0.0.1:8080`。请只使用隔离的开发数据；不要把客户数据复制到开发环境。

## 项目结构

- `v2/frontend/`：React/Vite 正式前端。
- `v2/backend/`：Flask API、认证权限、SQLite 数据、备份和运行服务。
- `v2/installer/`：V2 全新安装、可复现候选包和未来 V2-only 更新安全基础。
- `v2/docs/`：产品、API、架构、安全和发布契约。
- `02_开发工作区/`：V1 历史源码与升级验证材料，仅作兼容维护和审计证据，不接受新功能。

V2 是全新安装代际，永远不读取、迁移、修改或删除 V1 数据。

## 文档

- [V2 概览](v2/README.md)
- [产品契约](v2/docs/PRODUCT-CONTRACT.md)
- [API 契约](v2/docs/API-CONTRACT.md)
- [架构说明](v2/docs/ARCHITECTURE.md)
- [发布门禁](v2/docs/RELEASE-CHECKLIST.md)
- [支持说明](SUPPORT.md)

## 参与贡献

欢迎提交 Issue 和 Pull Request。开始前请阅读 [贡献指南](CONTRIBUTING.md)；涉及权限、数据边界、公开大屏或安装/恢复流程的修改，应同时更新契约文档并补回归测试。

本项目采用 [Apache License 2.0](LICENSE)。第三方组件仍适用各自许可证，见 [第三方声明](THIRD_PARTY_NOTICES.md)。

# 安全策略

## 支持范围

| 版本 | 状态 |
| --- | --- |
| `main` 上的 V2.5.1 源码 | 接受安全报告 |
| V2.5.0 macOS arm64 GitHub Release（历史正式版） | 正式公开发布；未签名，须核对 Release SHA-256 |
| V2.5.1 Windows 安装/升级候选 | 非正式发布，仅用于内部验证 |
| V1.x | 历史兼容维护，不接受新功能 |

当前没有完成签名和普通用户 Windows 10/11 验收的正式 V2 Windows 安装包。
GitHub Actions 产物、分支 ZIP 或第三方重新打包文件都不应被视为项目正式发布。
macOS arm64 版仅以项目 GitHub Release 中的 DMG/ZIP 及其校验材料为正式分发物。

## 私下报告漏洞

请使用仓库 Security 页面中的 **Report a vulnerability**（GitHub Private Vulnerability Reporting）。不要为安全问题创建公开 Issue，也不要在报告中上传客户数据库、完整日志、备份、密码、密钥或真实个人信息。

报告请尽量包含：受影响提交/版本、风险与攻击前提、最小复现步骤、预期和实际结果，以及已经脱敏的证据。维护者会尽快确认收到并在评估后协调修复与披露；这是社区项目，不承诺固定 SLA 或漏洞奖励。

## 部署边界

当前 V2 使用 HTTP。Windows 版只支持可信的 Domain/Private 局域网；macOS 自托管版
建议仅在本机使用。直接暴露到互联网、访客网络或不受信任 Wi-Fi 不在支持范围。
部署方仍负责操作系统补丁、主机账户、网络隔离、防火墙、终端防护、备份和物理安全。

更多信息见 `v2/docs/SECURITY-DEPLOYMENT.md`。

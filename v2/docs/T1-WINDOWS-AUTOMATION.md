# T1 Windows 验收自动化

本文档定义 V2 发布门禁中的 T1 自动化验收层：在没有常驻 Windows 真机的情况下，
先在免费、一次性、可复跑的 Windows CI 环境里覆盖尽可能多的原“实机验收”项。
分层依据与背景见仓库讨论记录；T1 不改变 `formal_external_release_allowed=false`。

## 分层定义

- **T1 自动化层**（每次 PR 与 main，`v2-windows-acceptance.yml`）：在
  `windows-latest` runner 上用真实安装包执行完整安装与运行验收。runner 是
  管理员会话的一次性 Windows Server 虚拟机；仓库公开后标准 runner 免费。
  测试候选使用与正式候选一致的规范命名（`会议室预约系统-V<版本>-安装包.zip`），
  由 build_package 的产品契约保证，但只在 runner 内存活、绝不上传。
- **T2 单机人工层**（每次候选，一台真实 Windows 10/11 桌面约半天）：UAC 提权
  同意框、SmartScreen、真实重启、真实局域网第二设备、四档视觉复核、长时间运行。
- **T3 正式外发层**（一次性）：Authenticode 证书、签名验签、目标单位 EDR/
  组策略试点。签名与 EDR 不是测试问题，只能随真实部署完成。

## T1 工作流覆盖矩阵

| 验收项 | 实现方式 |
| --- | --- |
| 真实零参数 BAT 全新安装 | 解压测试候选后驱动顶层 `安装V<版本>.bat`，stdin 输入 `YES` |
| 固定 `%ProgramFiles%\会议室预约系统V2` 安装根 | 安装器生产模式默认路径，无需注入 |
| 回环 `/healthz` 契约 | 断言 `ok/product_generation/bind_mode=loopback/setup_complete/install_id` |
| 首次设置 + LAN 重启 | `POST /api/v1/setup/complete`（回环 + `localhost` Host）后轮询 `bind_mode=lan` |
| 登录、bootstrap、创建预约 | 真实 cookie + CSRF 会话走 `/api/v1` |
| 时段冲突 | 同槽位重复创建断言 `409 SLOT_CONFLICT` |
| 取消预约 | `expectedRevision` 取消断言 `200` |
| 公开大屏白名单投影 | 未认证读取 `/api/v1/display`，断言不含案号/用途/备注/当事人原字段 |
| 人工备份入口 | 驱动 `② 立即备份.bat`，断言新 `.db + .json`、`backup-status.json` 成功、无 `-wal/-shm/-journal/.part-*` 伴随文件 |
| 客户启停生命周期 | 驱动 `④ 停止` / `① 启动` BAT；`①` 内部完整校验 HKLM 登记、SYSTEM 计划任务、开机触发器、每日 02:00 备份任务与 LocalSubnet 防火墙 |
| 系统登记显式断言 | 脚本另行断言计划任务主体、HKLM 身份、防火墙 TCP/8080/LocalSubnet |
| 端口冲突拒绝 | 占用 8080 的探针监听器下 `① 启动` 必须失败，且占用进程存活 |
| 数据库损坏 fail-closed | 损坏主库后 `① 启动` 健康检查失败；`/healthz` 进入 `recovery` 且回环可见 `recovery_code`；首次设置不重开；库文件不被重建 |
| DACL 边界 | `icacls` 断言 app 树含 Users 只读执行；`data/backups/logs` 无 Users ACE |
| V2.1.0→当前版本累计升级 | 从冻结 `v2.1.0` 标签构建真实基线安装包，再用当前 `v2/VERSION` 的累计升级包验证版本、数据、安装身份、运行状态与替换后 DACL 保留 |
| 升级健康失败回滚 | 在当前累计升级的健康检查阶段注入失败，断言程序、runtime、数据与原运行状态回滚，且升级事务可安全收尾 |

## T1 的诚实边界

以下内容 T1 **不能**证明，仍属 T2/T3：

- runner 是 Windows Server 镜像且以管理员会话运行，无法覆盖标准用户双击 BAT
  时的 UAC 同意/取消交互，也无法覆盖 SmartScreen 对未签名包的行为；
- 无法真实重启操作系统验证开机任务与补跑（服务级停止/启动已覆盖）；
- runner 网络不是客户局域网，“第二台设备”与 LocalSubnet 的真实效果需 T2；
- 长时间运行（电视大屏数小时刷新）与单位 EDR/AppLocker/组策略不在 T1 范围；
- 测试候选与正式候选同名同源，但只在 runner 内存活，不构成任何发布物；
  正式候选仍只能由 `release-candidate.yml` 构建并保留 7 天。

## 唯一旧版本来源：V2.1.0 基线

1. 在包含 V2.1.0 源码的提交（`v2/VERSION=2.1.0`）上打 `v2.1.0` 标签；
2. 标签触发 `release-candidate.yml` 完成双重可复现构建、六件套上传与
   Windows 候选门禁；
3. 记录候选 ZIP 与 payload 的 SHA-256 作为升级来源基线指纹；同标签任意次
   重建必须逐字节一致；
4. 当前来源矩阵仍只接受 V2.1.0；不因目标版本更新而自动接受 V2.2/V2.3/V2.4 作为
   升级来源；
5. V2.1.0→当前版本的 T1 通过仍不能替代目标版本的 T2 真机验收（见
   `RELEASE-CHECKLIST.md` 与 `V2.5.0-WINDOWS-PHYSICAL-ACCEPTANCE-GUIDE.md`）。

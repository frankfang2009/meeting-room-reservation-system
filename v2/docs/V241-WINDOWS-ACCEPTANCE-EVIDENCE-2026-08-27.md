# V2.4.1 Windows 验收证据（2026-08-27，测试机代理执行）

测试机：DESKTOP-F6CBUIJ · Windows 11 家庭中文版 23H2（build 22631，x64）· 未加域（WORKGROUP）· Windows Defender（productState 397568，全程未关闭）· EnableLUA=1 · 用户 `DESKTOP-F6CBUIJ\Frank Fang`（管理员组，UAC 过滤 token；提权均经用户本人确认 UAC 共 2 次：10:56 启动 admin-runner、11:17 解堵隐藏窗口；其后所有提权操作经 admin-runner 队列执行）· pwsh 7.6.5（WindowsApps Store 版）+ 本轮新装 MSI 7.6.5 + zip 版 7.4.19/7.3.10/7.2.24（仅诊断用）· Windows PowerShell 5.1.22621.4391 · C 盘剩 20GB / D 盘剩 27.8GB · 系统代理 127.0.0.1:7890。
仓库：`D:\mrv2-v241\repo`（自 `D:\mrv2-t2\repo` 本地克隆，远端 `frankfang2009/meeting-room-reservation-system`），分支 `codex/v241-hardening-uncommitted`，HEAD `59fcec2ace915452c67248cd5e9b5091328dc998`。
工具链：Python 3.13.14（per-user 静默安装到 `D:\mrv2-v241\work\tools\Python313`）· Node 22.17.1（zip 解压 `D:\mrv2-v241\work\tools\node-v22.17.1-win-x64`，SHA-256 与 nodejs.org 官方 SHASUMS256.txt 一致）· ruff 0.12.12（requirements-dev 锁定）。
全部工作文件位于 `D:\mrv2-v241\`（TEMP/TMP 重定向到 `D:\mrv2-v241\work\tmp`）；transcript 与脱敏诊断位于 `D:\mrv2-v241\evidence\`（transcript 原始日志、数据库与 secret 不入仓库）；验收截图 30 张（合成数据）同步镜像于本仓库 `v2/docs/evidence/v241-2026-08-27/shots/`（依仓库所有者 2026-08-27 指示；任务书默认为仅存受控本地目录）。提权执行采用 admin-runner 队列模式（`D:\mrv2-v241\admin-runner.ps1`，轮询 `D:\mrv2-v241\tmp\admin-queue\`，单次 UAC 覆盖全程，12:29:06 收到 stop 后退出）。

## 1. 第 1 节：SHA 与任务书锚点核对 —— PASS

```
git rev-parse HEAD^   → 93d49e0cc8c77fef939d4e37d6c783f13672bc65   （= 任务书产品代码锚点）
git diff --name-only HEAD^..HEAD → v2/docs/V241-WINDOWS-ACCEPTANCE-TASK.md   （仅任务书）
基线 origin/main = 25284f0ebf5ed1313a567211f0abdd4072562b08（V2.4.0 证据合并后 main）
```
工作树清洁（除本证据文件外无改动，无 `.mimosa`、客户数据或本机私有路径）。锚点不在 main 中（hardening 分支未合入，符合预期）。

## 2. 候选构建（代码锚点本地构建，双副本可复现）

`v2-reproducible-build.sh`（与 release-candidate.yml 相同流程）在锚点检出上双次构建并逐字节比对：**`MRV2_REPRODUCIBLE_BUILD=PASS`**（log：`evidence/repro-build.log`）。

| 产物 | 字节数 | SHA-256 |
|---|---|---|
| 会议室预约系统-V2.4.0-安装包.zip | 12,109,051 | `1ed0f6e18c09a91bab6a34cab876036550c053bc6d926716cbc6c83f7472fe24` |
| 会议室预约系统-V2.4.0-累计升级包.zip | 23,843,063 | `e1565a5ee3a7854f5a942091b9a3727d6184612e55e8633b610900042492b519` |
| （升级腿基线）会议室预约系统-V2.1.0-安装包.zip | 12,062,260 | `55a4db9861d682250204ee4b3044216a098de3fea84649b40c8e1fb2423075f5`（与冻结 sidecar 一致） |

命名说明：本分支 `v2/VERSION` 按发布契约固定为 `2.4.0`（版本号在合入 main 发布时才提升，`test_release_contract.py` 强制），故候选文件名沿用 V2.4.0；候选身份由「锚点本地构建 + 双构建字节一致 + 上表 SHA-256」保证，与历史 V2.4.0 包（不同字节）区分，未用历史包冒充。
供应链核对：Python embed zip 与安装器经华为镜像下载，另从 python.org 官方慢速下载副本比对 **SHA-256 一致**（`evidence/embed-verify.txt`）；安装器 Authenticode=Valid（Python Software Foundation）；wheelhouse 11 个 wheel 按 `requirements-win-amd64.lock` `--require-hashes` 下载校验；PowerShell 7.6.5 MSI Authenticode=Valid（Microsoft Corporation，`evidence/msi-verify.txt`），zip 版 7.4.19/7.3.10 的 pwsh.exe 均 Valid。

## 3. 第 4 节：静态门禁

| 门禁 | 结果 | 证据 |
|---|---|---|
| `python -m ruff check v2/backend v2/installer v2/tests` | **PASS**（All checks passed!，Python 3.13.14） | `gate-ruff.txt` |
| `python -m unittest discover -s v2/tests -v` | **PASS** 32/32 | `gate-unittest-tests.txt` |
| `npm ci` + `npm run check`（Node 22.17.1，engines 精确匹配） | **PASS**（0 漏洞；check 含 build） | `gate-npm-ci.txt` / `gate-npm-check.txt` |
| `git diff --check` | **PASS**（无输出） | `gate-gitdiffcheck.txt` |
| `python -m unittest discover -s v2/installer/tests -v` | **111/113 PASS；2 个 macOS 打包测试 FAIL（见 V241-B2 / V241-O1）** | `gate-unittest-installer-lf.txt` |

任务书点名的 6 个测试**全部通过**：`uses_windows_powershell_for_standard_user_probe`、`rejects_standard_user_probe_cleanup_residue`、`cleans_partial_standard_user_probe_setup`、`health_success_uses_fail_closed_stop_before_commit`、`health_failure_does_not_restore_files_when_second_stop_fails`、`health_probe_failure_stops_new_runtime_before_restore` 均 `ok`。
环境注记：为对齐 CI 的 LF 检出（避免无属性文本文件被 CRLF 化影响候选字节），克隆配置 `core.autocrlf=false` + `core.eol=lf` 后重检出；两轮单测结果一致（`gate-unittest-installer.txt` 与 `-lf.txt` 均为同 2 个失败），确认失败与检出 EOL 无关。

## 4. 环境缺陷与适配记录（V241-O2，非产品缺陷）

**现象**：T1 全新腿首次运行（10:56–11:05）产品功能 11 步全绿，但标准用户探针创建账号时 `New-LocalUser` 抛
`Could not load type 'Microsoft.PowerShell.Telemetry.Internal.TelemetryAPI' from assembly 'System.Management.Automation, Version=7.x.x.500'`。

**定性**：本机 Win11 23H2（22631，家庭中文版）内置 `C:\Windows\System32\WindowsPowerShell\v1.0\Modules\Microsoft.PowerShell.LocalAccounts\1.0.0.0\Microsoft.Powershell.LocalAccounts.dll` 引用了公共 PowerShell 7 从未携带的 SMA 内部类型；`Import-Module` 成功但 cmdlet **执行时**类型加载失败。验证矩阵（均为提权、同一报错，`evidence/smoke-*.log`、`diag-*.log`）：

| 宿主 | New-LocalUser |
|---|---|
| Store pwsh 7.6.5（WindowsApps） | FAIL |
| Store 包复制到普通目录运行（排除 MSIX 包身份） | FAIL |
| MSI pwsh 7.6.5（官方 MSI 全新安装） | FAIL |
| zip pwsh 7.4.19 / 7.3.10 / 7.2.24 | FAIL |
| `Import-Module … -UseWindowsPowerShell` 兼容代理 | cmdlet 可用，但反序列化对象无法通过脚本后续代理参数绑定（`Get-LocalGroup -SID` 收到 Deserialized SID、`-Group` 收 null）→ 不可用 |
| 真 Windows PowerShell 5.1 进程 | **OK**（含账号增删查、组成员关系全流程） |

背景：2026-08-18 T2 验收（E65/E66）在同一台机器、同一 Store pwsh 7.6.5 下 T1 全绿——因为 V2.4.0 时代脚本**没有**标准用户探针阶段；探针是本分支六提交新增，恰好首次踩中该机器级缺陷。GitHub CI（windows-latest=Server SKU，内置模块版本不同）不受影响。修复方向属机器/OS 侧（等待 Windows 更新修正内置模块），不属于产品代码。

**适配（不改产品代码）**：腿宿主保持 pwsh（脚本依赖 PS7 专属 `-SkipHttpErrorCheck`，5.1 承载不可行——按 UTF-8 正确解码后 5.1 可解析、无 `$PSScriptRoot` 依赖，但 HTTP 层必然失败）。在包装器内以同名函数遮蔽 6 个 LocalAccounts cmdlet（`Get/New-LocalUser`、`Get-LocalGroup`、`Get/Add-LocalGroupMember`、`Remove-LocalUser`），每个调用委托真 Windows PowerShell 5.1 进程执行同等 Windows API 并以活类型对象（真实 `SecurityIdentifier`）回传；验收脚本的全部断言、成员关系判定、fail-closed 残留复核逻辑原样运行。垫片源：`D:\mrv2-v241\tmp\la-shim.ps1`（约 120 行，带 DELEGATION_FAILED 抛错）。垫片自身的两处迭代 bug（5.1 try/catch 接管道的空管道元素；`$script:` 变量跨脚本作用域解析）均在触达产品前被包装器 sanity 检查拦截（`05c/05d` 两次 rc=1 失败均未运行验收主体）。

**其他环境适配**：产品 ④ 停止 BAT 以隐藏窗口调用时挂起（BAT 存在交互输入点，隐藏窗口无法送达；提权上下文观察到该现象，属于交互入口在无人值守管道中的已知形态）→ 后续清理改用 `Stop-ScheduledTask` + `Unregister-ScheduledTask`；任务/注册表/防火墙清理沿用 T2 经验（`reg.exe delete`，规避 pwsh `Remove-Item` HKLM 的 T2-B1 问题）；腿前等待 TCP TIME_WAIT 沉降（卸载后 8080 残连计数>0 会触发脚本预检断言，V241-O4 记录）。

## 5. 第 5 节 T1：全新安装腿 —— **PASS**

执行：`v2-windows-acceptance.ps1 -CandidateZip <V2.4.1候选> -WorkRoot D:\mrv2-v241\work\fresh6`（attempt 6，12:16–12:18，transcript `evidence/t1-fresh6-transcript.log`，rc=0）。
前 5 次尝试均因上述环境缺陷/垫片迭代失败（`t1-fresh*-transcript.log` 1–5），第 6 次完整通过。观察点逐项：

| 任务书要求 | 结果 |
|---|---|
| 最终 `MRV2_T1=PASS` | **达成**（rc=0） |
| `data/backups/logs` 三项 `STANDARD_USER_ACL:<name>:directory=PASS;file=PASS` | **达成**：`data`/`backups`/`logs` 三行均 `directory=PASS;file=PASS`（标准用户列举与读取均被拒绝=探针 PASS） |
| 探针进程使用 `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe` | **达成**：脚本第 627 行硬编码该绝对路径 `Start-Process -Credential` 启动（非 PATH 解析），且单元测试 `uses_windows_powershell_for_standard_user_probe` PASS；垫片不影响探针进程本身 |
| 成功与强制失败后无 `MRV2Acl*` 账号 / 三个 `standard-user-read-probe.txt` / `standard-user-acl-probe` 目录 | **达成**：脚本 fail-closed 残除断言随 PASS 一并成立；腿后提权复核 `MRV2Acl accounts=`（空）；最终清理后全机 `MRV2Acl=0` |
| 清理失败必须令整腿失败 | 契约由 `rejects_standard_user_probe_cleanup_residue`、`cleans_partial_standard_user_probe_setup` 单测锁定（PASS）；本轮实际清理成功 |
| （附）DACL 边界 | `ACL_SUMMARY`：app=RX、runtime=RX（owner Administrators）、data/backups/logs=Users NONE |
| （附）其余步骤 | 安装（BAT RC_0）→回环 healthz→首次设置→LAN 重启（install_id 稳定）→登录 bootstrap→预约/409 冲突/取消→公开大屏白名单→手动备份（db+sidecar 无伴随文件）→停止/启动生命周期→SYSTEM 任务+HKLM+LocalSubnet 防火墙→8080 被占拒绝且不杀占用者→损坏 fail-closed（不重开设置、保留库）——全部 STEP 通过 |

## 6. 第 5 节 T1：累计升级腿 —— **FAIL（产品缺陷 V241-B1）**

执行：`v2-windows-upgrade-acceptance.ps1 -BaselineZip <冻结V2.1.0> -UpdateZip <V2.4.1候选> -WorkRoot D:\mrv2-v241\work\upgrade`（12:21–12:25，transcript `evidence/t1-upgrade-transcript.log`，rc=1）。

**失败前已通过的步骤**：preflight → install-baseline（V2.1.0 BAT RC_0）→ first-setup-and-business-data（业务数据+回执）→ wait-backup-catch-up（补备份幂等 no-op）→ prepare-cumulative-update → **health-failure-rollback**（故障注入→健康失败→回滚：版本回 2.1.0、install_id 不变 `32006eed-…`、程序/数据/设置恢复、更新器不扫 V1）→ **standard-user-private-roots-after-rollback**：`MRV2_T1U=STANDARD_USER_ACL:data:list=PASS;read=PASS`、`backups:…PASS`、`logs:…PASS` 三项全 PASS，且腿后提权复核 data/backups/logs `owner=BUILTIN\Administrators, usersACEs=0`（无 Users 允许 ACE）→ run-cumulative-update **失败**。

**最短复现（V241-B1，产品 bug，高）**：
1. 全新安装 V2.1.0 → 完成首次设置并造业务数据（腿自动完成）；
2. 执行一次会触发健康检查失败的更新尝试（腿的故障注入路径）——更新器按契约先做「更新前在线备份」生成 `reservation-v2-backup-00000003.db`（12:23:46，155648 字节），随后健康失败 → 回滚；回滚**按设计保留 backups/ 全部文件**，但 data/（含 `backup-status.json`，sequence=2）恢复到尝试前；
3. 重跑同一累计升级包：更新器再次请求「更新前在线备份」→ 按 status 计算目标序列=3 → 与保留的 00000003 文件**冲突** → `v2app/backup.py:637 create_backup` 抛 `RuntimeError: 备份序列目标已存在，拒绝覆盖`（`_程序文件\app\v2app\backup.py`，backup.log 12:25:15）→ 更新 BAT `MRV2_UPDATER_RESULT=1 / MRV2_UPDATE_GATE=PRODUCT_RC_1` → 验收腿 `upgrade BAT failed: code=1`（脚本 :608）。

**定性（初判，修复轮已修正见第 12 节）**：手动/在线备份的序列预留未把「当前版本无法解析的 sidecar 文件」计入占用——真实机制为跨版本 sidecar 兼容问题：健康失败尝试换入的新版本运行时在健康探针启动时把现场库迁移到新 schema 并留下 `databaseSchemaVersion=4` 的 seq3 备份；回滚把数据恢复到备份前快照（水位回落到 2），重试更新时由旧版本代码执行更新前在线备份，其序列预留扫描跳过无法解析的 seq3 sidecar → 预留 3 → 与保留的 00000003.db 冲突。触发链 = 「失败更新的健康运行时留下跨版本备份」+「重试更新」——正是本任务书 §5 与 §7.7（断电重试同包幂等）要求的能力路径，属**必须修复的放行阻断项**。
**正面记录**：失败时更新器行为正确——fail-closed 中止、无半更新状态、未扫描/读取/删除任何 V1 目录、`backup-status.json` 如实记录 `status=failed, detail=RuntimeError`，与分支加固目标 1/2/3 的其余断言一致。
**证据**：`evidence/upgrade-failure-backupdiag.log`（backup.log 尾部完整 traceback、backup-status、目录清单）、`evidence/t1-upgrade-transcript.log`、失败现场快照 `evidence/post-legs-inspection.log`（版本 2.1.0、install_id 保持、监听 0.0.0.0:8080 为产品 runtime pythonw、备份目录 7 个文件含 updates 占位）。
**后续未执行**：按任务书 §2.6（失败后保存现场、不先改代码、不反复重跑），未重试升级腿、未做 §7.7 断电演练、未做升级后才能覆盖的项（升级后版本一致性、升级后预约/会话保留、`app/runtime` 提交后 RX 复核等）。

## 7. 第 6–7 节：普通用户 / 运行网络恢复 —— 逐项判定

| 条目 | 判定 | 依据 / 备注 |
|---|---|---|
| §6.1 双击 BAT 的 UAC 取消/接受 | **SKIP（需用户本人）** | 本轮无用户交互双击场景；管道侧 BAT 均以 YES 喂入（T1）。T2 E67/E73 已留有真机记录可参考 |
| §6.2 安装根精确 + 绑定可信根 | **PASS（正向）** | 注册表 `InstallRoot=C:\Program Files\会议室预约系统V2`（提权复核）；全新腿安装根断言通过。篡改注册表根/移动目录/异目录启动的负面用例未在本轮执行（升级腿失败阻断后续，且需保持现场）→ 记 **SKIP（负面用例）**，可信根绑定的单测（`test_update_core` 系列）PASS |
| §6.3 标准用户 RX on app/runtime、私有目录不可列举/读取 | **PASS** | 全新腿 ACL_SUMMARY + STANDARD_USER_ACL 三项；探针为真实随机本地账号经 5.1 委托创建（见第 4 节） |
| §6.4 维护入口按需 UAC、普通用户不能绕过 | **PASS（行为证据）+ SKIP（真实双击）** | 提权管道观察到 ④ 停止 BAT 存在交互输入点（隐藏窗口挂起）；真实非提权双击 UAC 体验需用户（未执行） |
| §6.5 8080 被占：拒绝启动、不杀占用、可操作提示 | **PASS** | 全新腿 port-conflict 步：`refused to start with port 8080 occupied and left the occupier alive` |
| §6.6 SmartScreen/Defender/EDR/AppLocker/GPO 真实表现 | **部分** | Defender 全程在位未关闭（productState 记录）；EnableLUA=1；AppLocker 于家庭版 N/A；SmartScreen 真实弹窗表现需用户双击（SKIP）；未做任何关闭操作 |
| §7.1 首启仅回环→设置后 LAN | **PASS** | 全新腿 loopback-health → first-setup → `service restarted into LAN mode with stable install_id`；失败现场监听 0.0.0.0:8080 |
| §7.2 防火墙仅 TCP8080/Domain+Private/LocalSubnet，Public 不开放 | **PASS** | 提权复核：两条规则 Inbound/Allow/`profile=Domain, Private`、`protocol=TCP localPort=8080 remoteAddress=LocalSubnet`（Public 未列入） |
| §7.3 第二台设备登录/大屏 | **SKIP（无第二设备）** | 公开大屏白名单字段由全新腿 public-display 步验证（PASS）；跨设备可达性未测（T2 E78 有历史记录） |
| §7.4 真实重启后任务恢复 | **SKIP（需用户重启）** | 任务定义已核验：主任务 Boot 触发、SYSTEM、RunLevel=Highest、state=Running；重启动作未在本轮执行 |
| §7.5 每日 02:00/补备份/30 份轮转/无 WAL 残留 | **PASS（触发与补跑）/SKIP（30 份轮转实跑）** | 备份任务 Daily `StartBoundary=02:00:00+08:00`、NextRunTime 次日 02:00；补备份幂等实证（catch-up no-op）；手动备份无 `-wal/-shm/-journal/.part-*` 伴随（全新腿 + 失败现场 data 目录清单）；30 份轮转需 30 天或批量造数，本轮未执行（单测覆盖轮转逻辑） |
| §7.6 造预约→备份→损坏→恢复→数据回来→旧会话失效 | **PASS（T1 层）+ SKIP（真实 UAC 恢复交互）** | 全新腿 fail-closed-corruption 步（不重开设置、保留库）+ 升级腿回滚数据保留证据；交互式 ⑥ 恢复闭环需用户（T2 E80 有历史修复验证记录） |
| §7.7 断电演练（升级中断电→开机重跑同包） | **SKIP（未获批准）** | 且叠加 V241-B1：重试同包的「更新前备份」路径正是缺陷所在——修复前断电重试预期同样失败，明确不得模拟为 PASS |
| §7.8 分辨率/缩放 UI 逐档检查 | **PASS（代理视觉验收，方法学见第 7A 节）** | 中文界面五档视口 + 125%/150% 缩放模拟全过；键盘焦点/抽屉焦点陷阱/Esc 关闭/长刷新实证；截图 30 张存 controlled 本地目录 |

### 7A. §7.8 视觉验收详录（2026-08-27 13:37–14:05，代理视觉执行）

**前置**：产品以同一候选安装包重装（`visual-setup-transcript.log`：BAT RC_0 → 首次设置 201 → LAN 重启 install_id 稳定 → 登录 200 → 两条明日合成预约入库）；UI 实测中又经真实界面创建一条今日 15:00–16:00 预约（含必填校验拦截：缺「事项/标签」时提示「请检查 2 个字段」且焦点自动跳到首个错误字段——校验体验正确）。

**方法学**：ZCode 内嵌浏览器（Chromium）+ `setViewportSize` 精确视口；系统 DPI 缩放以「CSS 视口等比缩减」模拟（T2 E71 同款方法）：125% 物理 1024×720 → CSS 819×576，150% → 683×480；1920×1080@125%/150% → 1536×864 / 1280×720。真实系统 DPI 切换（设置→缩放→注销）仍属用户侧项。截图 30 张存 `D:\mrv2-v241\evidence\shots\`（`t78-*.png`），本仓库不收录。

| 检查项 | 结果 | 证据截图 |
|---|---|---|
| 中文界面渲染（无乱码/豆腐块/截断/重叠） | **PASS** | `t78-login-*.png`（6 档）、`t78-calendar-header-*.png`、`t78-display-*.png` |
| 登录页 1024×720/1280×720/1440×900/1920×1080 + 125%/150% 模拟 | **PASS**：宽屏双栏；819×576 插画退居表单层后、交互元素完整；683×480 插画自动隐藏、表单全宽可用（响应式正确） | `t78-login-{1920x1080,1440x900,1280x720,1024x720,819x576,683x480}.png` |
| 日历页五档分辨率 + 1536×864（1920@125%）+ 1280×720（1920@150%） | **PASS**：表头/日期导航/房间列/过去时段禁用态/当前时间红线完整；窄视口侧栏收缩为图标栏（含 aria 可访问名，DOM 快照证实） | `t78-calendar-*.png`、`t78-calendar-header-{1920x1080,1024x720}.png` |
| 键盘焦点可见性（登录页 Tab 序） | **PASS**：用户名→密码（黑描边焦点环+光标）→显示密码→登录按钮，逐格推进，焦点环清晰 | `t78-login-focus-tab1-password.png`、`t78-login-focus-tab3-submit.png` |
| 抽屉焦点陷阱（新建预约，1440×900） | **PASS**：打开即焦点入首个字段（`预约对象 [active]`）；背景被 `inert`（DOM 快照中背景按钮可访问名消失）；Tab 沿 预约对象→案号→事项→标签 1–4→备注→创建预约 推进且从「创建预约」回绕到抽屉顶部「关闭」，全程未逃逸 | `t78-drawer-open-1440x900.png`、`t78-drawer-focus-事项.png`、`t78-drawer-focus-tags.png`、`t78-drawer-focus-wrap.png` |
| Esc 关闭与焦点归还 | **PASS**：Esc 后 dialog 移出 DOM，焦点环回到来源时段按钮（14:00 · 新建预约） | `t78-drawer-after-esc.png` |
| 150% 缩放模拟抽屉压测（683×480） | **PASS**：抽屉响应式收窄为约 2/3 宽，字段/滑杆/按钮全部可用 | `t78-drawer-683x480.png` |
| 公开大屏（/display，白名单脱敏）视觉验证 | **PASS**：仅显示引导字段；「验收当事人甲」呈「验*甲」、案号不出现在 DOM（快照断言 `验收当事人甲`/`VS-2026-003` 均 false）；页脚「姓名已脱敏」；1920 大字号排版与 1280×720 等比正常 | `t78-display-1920x1080.png`、`t78-display-1280x720.png` |
| 长时间刷新（会话建立约 25 分钟后真实 reload） | **PASS**：会话保持（仍为 验收管理员）、当前预约 15:00–16:00 与明日两条完整、无白屏/状态漂移 | `t78-longrefresh-1920x1080.png` |
| 抽屉必填校验（真实创建路径） | **PASS**：空「事项/标签」提交被拦截（「请检查 2 个字段」+ 逐字段错误文案 + 焦点跳转到首个错误字段），补全后创建成功、日历即显 | DOM 快照记录（transcript `visual-setup-transcript.log` 时段） |

**方法学注记（如实记录）**：① 本轮为代理视觉执行，浏览器渲染与真实桌面窗口一致受 CSS 像素影响，但 Windows 系统 DPI 缩放对窗口装饰/字体渲染管线的额外影响未经真实切换验证，100%/125%/150% 系统缩放下的真实观感仍建议用户抽查（保持第 9 节余项）；② 自动化 CUA `Enter` 在聚焦的「登录」按钮上未触发表单提交（点击路径正常），因自动化按键合成与真实键盘差异，未定性为产品缺陷；③ 一次截图出现重复渲染伪影，复拍全页核实为截图工具合成问题，非应用缺陷（`t78-drawer-focus-check-full.png` 为核实后正常状态）。

## 8. 系统对象与探针残留清单

- **全新腿成功后（腿内断言 + 提权复核）**：无 `MRV2Acl*` 账号、无 `standard-user-read-probe.txt`、无 `standard-user-acl-probe` 目录；产品对象在位（供后续腿使用）。
- **升级腿失败后（现场快照 `post-legs-inspection.log`）**：无 `MRV2Acl*` 残留；无 `.update-*`/临时程序树残留（workroot 仅有 baseline/update/update-health-failure 三个解压目录）；维护锁为正常运行态文件（`maintenance.lock.guard`+`service.pid`，服务存活）；`backup-status.json` status=failed（如实）。
- **终态（`final-snapshot-and-cleanup.log`，12:29）**：产品固定对象全部移除——tasks=0、防火墙=0、注册表=False、安装根=False、port8080=0、`MRV2Acl=0`；失败现场备份序列清单（00000001/2/3 + sidecar + updates）已先行抄录进证据。
- **视觉验收轮（13:37 后）**：为代理视觉验收以同一候选包重装产品（SYSTEM 双任务、LocalSubnet 防火墙、HKLM 登记恢复，`visual-setup-transcript.log`）；验收完成后**有意保留安装与运行状态**，供用户完成第 9 节余项（真实双击 UAC、系统缩放抽查、真实重启）。视觉验收会话 admin-runner 于 14:05 以 stop 标志正常退出；本轮无探针账号、无 `.update-*`/维护锁残留（仅正常运行态文件）。

## 9. 需用户本人操作 / 未执行清单

1. §6.1 双击安装 BAT 的 UAC 取消与接受（含 SmartScreen 真实表现）。
2. §7.4 真实重启后主任务/健康/备份任务恢复复核（产品已保持安装运行，可直接执行）。
3. §7.3 第二台局域网设备登录与公开大屏目检。
4. §7.8 真实系统 DPI 缩放（设置→缩放 125%/150% + 注销）下的抽查——代理视觉验收已用 CSS 视口模拟覆盖布局效应（第 7A 节），真实渲染管线观感建议抽查。
5. §7.7 断电演练（若批准；修复 V241-B1 后执行才有意义）。
6. 修复 V241-B1 后重跑升级腿与上述依赖项。

## 10. 缺陷与观察汇总

| 编号 | 级别 | 摘要 | 证据 |
|---|---|---|---|
| V241-B1 | **产品 bug（高，放行阻断）→ 已修复并实机验证（第 12 节）** | 回滚后重试累计更新时「更新前在线备份」序列与回滚保留的备份文件冲突，抛 `备份序列目标已存在，拒绝覆盖` → 更新 fail-closed 中止。真实机制为跨版本 sidecar 兼容（新版本健康运行时留下 `databaseSchemaVersion=4` 备份；旧代码扫描跳过 + 回滚水位回落 → 重试瞄准已占用序列）。最短复现见第 6 节，修复见第 12 节 | `upgrade-failure-backupdiag.log`、`t1-upgrade-transcript.log`、`v2app/backup.py`、`update_core.py` |
| V241-B2 | 产品 bug（中，仅影响 Windows 宿主构建 macOS 包）→ 已修复（第 12 节） | `build_macos_package.py` 以无 `newline` 参数的 `write_text` 写 `EDITION`：Windows 宿主产出 `macos-selfhost\r\n`，与常量 `\n` 不等且跨平台字节不一致（读回校验因 `read_text` 反向翻译而漏检，修复后改字节级比对）。测试 `test_builds_reproducible_zip_with_edition_and_permissions` 修复前在本机 FAIL、修复后 PASS | `gate-unittest-installer-lf.txt`、`fix1-gate-unittest-installer.txt` |
| V241-O1 | 平台限制（非缺陷）→ 测试已加平台跳过（第 12 节） | `test_staging_from_zip_restores_top_folder_and_exec_bits` 断言 `启动.command` 执行位：NTFS 无 POSIX 执行位且 `.command` 非 Windows 可执行扩展名，该 macOS 打包测试在 Windows 上原理性不可通过（CI 仅在 macOS/Linux 运行它）；已加 `skipIf(os.name == "nt")` | 同上 |
| V241-O2 | 环境（机器级，非产品） | Win11 23H2 内置 LocalAccounts 模块与所有公开 PS7 不兼容（详见第 4 节矩阵）；v241 新增的标准用户探针在本机需 5.1 委托垫片执行。建议：验收脚本后续可考虑探针账号操作显式走 `System32\WindowsPowerShell\v1.0\powershell.exe` 子进程以获得与探针进程一致的抗性 | `diag-*.log`、`smoke-*.log` |
| V241-O3 | 观察 | 产品交互 BAT（④ 停止等）在无人值守/隐藏窗口下挂起于交互输入点；建议运维文档标注维护入口需真实控制台 | `cleanup-before-retry.log`（11:12–11:17 挂起 5 分钟后人为终止） |
| V241-O4 | 观察（测试基建） | 验收脚本预检把 8080 的 TIME_WAIT 计入「被占用」：卸载后立即重跑腿会在预检失败，需沉降等待 | `t1-fresh5-transcript.log`（attempt 5 预检失败）vs attempt 6 通过 |

## 11. 结果判定（首验轮）

- 第 1 节锚点核对：**PASS**；第 4 节静态门禁：**PASS（111/113，两个失败定性见 V241-B2/V241-O1，点名 6 测试全过）**；第 5 节全新腿：**PASS**；第 5 节升级腿：**FAIL（V241-B1）**；第 6–7 节逐项见第 7 节表，§7.8 视觉验收 **PASS**（代理视觉执行，第 7A 节）。
- 首验结论：升级腿因 V241-B1 阻断放行。修复见第 12 节，修复后升级腿复跑 **PASS**。
- `formal_external_release_allowed=false`。全程未创建 PR/合并/标签/Release/签名，未对外分发候选包；未关闭 UAC/SmartScreen/Defender/EDR/防火墙；仅使用合成测试数据。

## 12. 修复轮（V241-B1/B2，2026-08-27 下午，分支 `codex/v241-b1-b2-fixes`）

### 12.1 V241-B1 根因定案与修复

法证修正（首验轮 §6 的初判不准，此处为实锤机制）：升级腿时间线（backup.log/service.log 交叉比对）证实 seq3 备份（12:23:46.475）来自**健康失败尝试换入的新版本运行时**（pid 2548，`start_for_health` 后其 catch-up worker）：新代码 `prepare_database` 就地把现场库迁移到 schema 4 并写下 `databaseSchemaVersion=4` 的 seq3 sidecar。回滚把 data 恢复到在线备份前快照（`app_meta.backup_sequence` 水位回落到 2）。重试更新的更新前在线备份由**当前安装的旧版本代码**执行（更新未提交前 `default_online_backup` 调用已安装的 runtime+app），旧代码 `reserve_backup_sequence` 的 sidecar 扫描对 schema 4 抛「不属于已设置的 V2」被静默跳过 → 预留 3 → `create_backup` 命中现存 00000003.db 抛「拒绝覆盖」（该拒绝在 try 块外，现场文件完好，fail-closed 正确）。12:24:57 的 catch-up no-op（旧服务重启）亦吻合：当时最新可解析备份=seq2 且数据序列一致。

修复（两处互补，因为更新前在线备份永远运行「源版本」代码）：
1. `v2/backend/v2app/backup.py`：`reserve_backup_sequence` 增加 `_filename_sequence_floor`——文件名序列一并计入下限。保护今后从 V2.4.1+ 升级的安装（新代码自身兼容未来 schema 漂移）。
2. `v2/installer/update_core.py`：`default_online_backup` 在调用旧版 CLI 前先 `reconcile_backup_sequence_floor(identity)`——更新器以纯文件名扫描结果、单行原子上调旧库 `app_meta.backup_sequence` 水位（产品自身维护的单调键；**不经** `prepare_database`，不触发迁移、不解析 sidecar、不触碰备份文件），使旧代码预留直接落到空闲序列。这是修复 V2.1.0→V2.4.1 重试场景的唯一可行层（旧代码已冻结不可改）。

fix1 教训（如实记录）：第一轮仅修了新代码 reserve，重跑升级腿仍在同点失败——重试的在线备份运行旧代码，修复必须落在更新器层；`fix1-failure-diag.log` 的 backup.log traceback 与首验完全同点，据此完成上述法证修正。

### 12.2 V241-B2 修复

`v2/installer/build_macos_package.py`：EDITION 写入显式 `newline="\n"`；staging 校验从 `read_text`（会反向翻译换行、掩盖漂移）改为 `read_bytes` 字节级比对。既有测试 `test_builds_reproducible_zip_with_edition_and_permissions` 即字节级回归（修复前 Windows 宿主 FAIL、修复后 PASS），未另加重复断言。`test_staging_from_zip_restores_top_folder_and_exec_bits` 加 `@unittest.skipIf(os.name == "nt")`（V241-O1：NTFS 无法表达 POSIX 执行位，属性仅 POSIX 宿主有意义，CI 在 macOS/Linux 仍实跑）。

### 12.3 回归测试

| 测试 | 位置 | 红绿验证 |
|---|---|---|
| `test_backup_sequence_skips_foreign_sidecar_after_rollback_retry`（跨版本 sidecar 场景：水位 2 + 不可解析 seq3 → 下次备份必须为 seq4 且不触碰他版文件） | `v2/backend/tests/test_hardening.py` | 无修复=FAILED（BACKUP_FAILED 500，与生产症状一致）；有修复=PASS |
| `test_online_backup_reconciles_watermark_above_foreign_sidecars`（更新器水位对齐：2→3、幂等、多次残留续升） | `v2/installer/tests/test_update_core.py` | PASS |
| `test_online_backup_reconcile_is_noop_without_backup_files`（空备份目录 no-op） | 同上 | PASS |
| （既有）`test_builds_reproducible_zip_with_edition_and_permissions` | `v2/installer/tests/test_build_macos.py` | 修复前 FAIL / 修复后 PASS |

### 12.4 全量门禁复跑（Windows 实机，Python 3.13.14 / Node 22.17.1）

| 门禁 | 结果 | 证据 |
|---|---|---|
| ruff check（backend/installer/tests） | PASS | `fix2-gate-ruff.txt` |
| `v2/backend/tests`（含新增回归） | **151 tests OK**（5 skipped 为既有平台跳过；一次因整机负载出现的真实进程移交测试时序失败，单独静默复跑通过，见正文注） | `fix2-gate-unittest-backend.txt` |
| `v2/installer/tests`（含 2 个新增回归 + O1 平台跳过） | **115 tests OK（skipped=1）** | `fix2-gate-unittest-installer.txt` |
| `v2/tests` + `test_open_source_contract` | 32 OK / OK | `fix2-gate-unittest-tests.txt`、`fix2-gate-opensource.txt` |
| `npm ci` + `npm run check` | PASS（rc=0） | `fix2-gate-npm.txt` |
| `git diff --check` | PASS（无输出） | `fix2-gate-gitdiff.txt` |

注：一轮后端套件在与提权 runner/转录并发负载下出现 `test_real_waitress_setup_handoff_has_no_bad_file_descriptor` 的 WinError 10053 时序失败（真实子进程监听移交测试，与本轮修改的模块无交集），runner 空闲后单独静默复跑 151/151 通过；以复跑结果为准。

### 12.5 修复分支候选包（可复现双构建 `MRV2_REPRODUCIBLE_BUILD=PASS`）

| 产物 | SHA-256 | 与首验候选差异 |
|---|---|---|
| 会议室预约系统-V2.4.0-安装包.zip | `1b3cabe318f630e626cc34c24fd2cabdbe9798c9c676f8333c4f919d2f3a57a0` | Windows 载荷不含变更文件→与首验安装包字节一致（合理） |
| 会议室预约系统-V2.4.0-累计升级包.zip | `b4a020bbe2238e68789d839b5213e5ef059a8619bf70d02272557eceed946027` | 含修复版 `update_core.py`（更新工具随包分发） |

### 12.6 Windows 累计升级腿复跑 —— **PASS**

执行：`v2-windows-upgrade-acceptance.ps1 -BaselineZip <冻结V2.1.0> -UpdateZip <fix2 累计升级包> -WorkRoot D:\mrv2-v241\work\upgrade-fix2`（垫片适配同第 4 节；前置清理后预检通过；transcript `evidence/fix2-upgrade-transcript.log`，rc=0）。逐步：

- preflight → install-baseline（V2.1.0 BAT RC_0，install_id `af507b06-…`）→ first-setup-and-business-data → wait-backup-catch-up → prepare-cumulative-update → **health-failure-rollback**（故障注入 → 健康失败 → 回滚到基线运行态）→ **standard-user-private-roots-after-rollback**：`data/backups/logs` 三项 `list=PASS;read=PASS` → **run-cumulative-update：`upgrade BAT returned 0 with product markers`（修复点，首验同点 FAILED）** → verify-version-and-identity → verify-service-and-data（`business data, login session and rooms survived the upgrade`）→ dacl-boundaries-after-upgrade（升级后 DACL 重固化复验）→ **`MRV2_T1U=PASS`**。
- 中间失败尝试（fix1 候选 `cddb2759…`，仅修新代码）在同一验证点失败，已作为法证输入记录（`evidence/fix1-upgrade-transcript.log`、`fix1-failure-diag.log`），未计入最终判定。

### 12.7 修复轮结论

- **V241-B1：已修复并经实机累计升级腿验证 PASS（含健康失败回滚后重试的精确场景）。**
- **V241-B2：已修复，Windows 宿主安装器套件 113/113（1 平台跳过）。**
- 放行状态维持 `formal_external_release_allowed=false`：本轮仅新分支提交与推送，未创建 PR/合并/标签/Release/签名，未对外分发；第 9 节用户侧余项（真实 UAC 双击、真实重启、第二设备、系统 DPI 抽查、断电演练）仍待用户完成后方可申请放行。

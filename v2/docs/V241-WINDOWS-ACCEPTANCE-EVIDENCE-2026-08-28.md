# V2.4.1 Windows 验收证据（2026-08-28，测试机代理执行）

> 本文件为执行者证据记录，**未提交**（工作树未跟踪文件；按任务约束不创建 PR/合并/标签/Release）。
> 截图与 transcript 原件保存在受控本地证据目录（脱敏后仅摘要入本文件）。

## 0. 结论速览

| 项 | 判定 |
|---|---|
| 第 1 节 锚点核对 | **PASS** |
| 第 4 节 静态门禁 | **PASS（installer 套件 111/113，2 个已知失败为本分支未含的 V241-B2/V241-O1 修复对手态，见 §3）** |
| 第 5 节 T1 全新安装腿 | **PASS** |
| 第 5 节 T1 累计升级腿 | **FAIL（V241-B1，放行阻断项，本分支未含修复）** |
| 第 6 节 普通用户/UAC/安装根 | 见 §6 逐项（多为 PASS/SKIP-需用户） |
| 第 7 节 运行/网络/恢复 | 见 §7 逐项；§7.8 工作交接页四状态 **PASS** |
| 放行状态 | `formal_external_release_allowed=false` |

**本轮核心结论**：产品代码锚点 `2391ad6` 相对 2026-08-27 首验锚点 `93d49e0` 仅新增工作交接页前端两提交；后端/安装器零变化。**V241-B1（回滚后重试累计更新时更新前在线备份序列冲突）在本分支仍未修复**，升级腿在同一验证点以同一签名 FAIL，继续阻断放行。修复（`backup.py` `_filename_sequence_floor` + `update_core.py` `reconcile_backup_sequence_floor`）存在于姊妹分支 `codex/v241-b1-b2-fixes` 并已于 2026-08-27 在该分支实机验证 PASS；本验收轮未做任何代码修改。

## 1. 第 1 节：SHA 与任务书锚点核对 —— PASS

```
git rev-parse HEAD                 → 7aa541bb9688bb032d11978bbcb139190f17e66f（任务书文档提交）
git rev-parse HEAD^                → 2391ad66f7523757c0000d5073d7e2b025914738（= 任务书产品代码锚点）
git diff --name-only HEAD^..HEAD   → v2/docs/V241-WINDOWS-ACCEPTANCE-TASK.md（仅任务书）
git merge-base --is-ancestor 25284f0e 2391ad6 → 是（基线为 V2.4.0 证据合并后的 main）
```
工作树清洁（除本证据文件外无改动；无 `.mimosa`、客户数据或本机私有路径）。检出采用 `core.autocrlf=false + core.eol=lf` 后重检入（对齐 CI 字节，方法同 2026-08-27 轮）。

## 2. 机器档案与环境（脱敏）

- 测试机：Windows 11 家庭中文版 23H2（build 22631，x64）· 未加域（WORKGROUP）· 本地管理员账户（UAC 过滤令牌，EnableLUA=1）
- 杀软：Windows Defender（SecurityCenter2 productState=397568）+ 火绒安全软件（266240），**全程未关闭、未配置排除**；`Get-MpComputerStatus` RealTimeProtectionEnabled=False 为双 AV 并存时系统让位显示，非本轮操作所致（如实记录）
- AppLocker：家庭版 N/A；SmartScreen/UAC 保持默认
- PowerShell：Windows PowerShell 5.1.22621.4391；pwsh 7.6.5（MSI）
- 工具链：Python 3.13.14（per-user，D 盘 tools）+ ruff 0.12.12（requirements-dev 锁定）；Node 22.17.1（zip 版，SHA-256 与 nodejs.org SHASUMS256.txt 一致，2026-08-27 已核）
- 磁盘：C 剩 14GB / D 剩 24.3GB；全部工作文件位于 `D:\mrv2-v241\`（TEMP/TMP 重定向）
- 提权方式：admin-runner 队列（用户本人 UAC 批准 3 次：10:30 runner1、10:42 runner2、10:47 runner3；原因见 §8 执行过程记录）

## 3. 第 4 节：静态门禁 —— PASS（含 2 个已知携带失败）

工具链：Python 3.13.14 / Node 22.17.1，仓库锁定依赖。

| 门禁 | 结果 | 证据（evidence-20260828\） |
|---|---|---|
| `python -m ruff check v2/backend v2/installer v2/tests` | **PASS**（All checks passed!） | `gate-ruff.txt` |
| `python -m unittest discover -s v2/tests -v` | **PASS 32/32** | `gate-unittest-tests.txt` |
| `npm ci`（Node 22.17.1） | **PASS**（0 漏洞） | `gate-npm-ci.txt` |
| `npm run check`（lint+test+build） | **PASS**（前端 173/173） | `gate-npm-check.txt` |
| `git diff --check` | **PASS**（无输出） | `gate-gitdiffcheck.txt` |
| `python -m unittest discover -s v2/installer/tests -v` | **111/113 PASS；2 FAIL**（见下） | `gate-unittest-installer.txt` |
| （加测）`v2/backend/tests` | **150 OK（5 平台跳过）** | `gate-unittest-backend.txt` |

任务书点名的 6 个测试**全部 `ok`**：`windows_acceptance_uses_windows_powershell_for_standard_user_probe`、`…rejects_standard_user_probe_cleanup_residue`、`…cleans_partial_standard_user_probe_setup`、`health_success_uses_fail_closed_stop_before_commit`、`health_failure_does_not_restore_files_when_second_stop_fails`、`health_probe_failure_stops_new_runtime_before_restore`。

2 个失败（与 2026-08-27 首验完全相同，均为本分支**未含**姊妹分支修复的携带项，非本轮回归）：
1. `test_builds_reproducible_zip_with_edition_and_permissions`（V241-B2：`build_macos_package.py` EDITION 写入缺 `newline="\n"`，Windows 宿主构建 macOS 包字节含 `\r\n`；修复在 `codex/v241-b1-b2-fixes`）。**不影响本轮 Windows 候选包**（本轮构建仅产 Windows 安装包与累计升级包）。
2. `test_staging_from_zip_restores_top_folder_and_exec_bits`（V241-O1：NTFS 无 POSIX 执行位，原理性不可过；姊妹分支已加 `skipIf(os.name=="nt")`）。

## 4. 候选构建（代码锚点本地构建，双副本可复现）

`v2-reproducible-build.sh`（与 release-candidate.yml 同流程）在锚点检出上双次构建：**`MRV2_REPRODUCIBLE_BUILD=PASS`**（双副本字节一致）。证据：`repro-build.log`、`candidate-hashes-20260828.txt`。

| 产物 | 字节数 | SHA-256 |
|---|---|---|
| 会议室预约系统-V2.4.0-安装包.zip | 12,109,070 | `950f897989c1484f70d073da0905979a273f7180666d6daeca627b2bd3c573a0` |
| 会议室预约系统-V2.4.0-累计升级包.zip | 23,843,104 | `6d6d650569073adc7b1be0bf86a6246326323b63e0d624870550bca7e72790d1` |
| （升级腿基线）会议室预约系统-V2.1.0-安装包.zip | 12,062,260 | `55a4db9861d682250204ee4b3044216a098de3fea84649b40c8e1fb2423075f5`（与冻结记录一致） |

命名说明同 2026-08-27 轮：本分支 `v2/VERSION` 按发布契约固定 `2.4.0`（合入 main 发布时才提升），候选身份由「锚点本地构建 + 双构建字节一致 + 上表 SHA-256」保证，与历史 V2.4.0 包（不同字节）区分，未用历史包冒充。Python embed zip 沿用 2026-08-27 双源 SHA-256 核对结论（`evidence/embed-verify.txt`）；wheelhouse 11 wheel 按 `requirements-win-amd64.lock` `--require-hashes` 校验（沿用 2026-08-27 已核对的 approved-inputs）。

## 5. 第 5 节 T1：全新安装腿 —— PASS

执行：`v2-windows-acceptance.ps1 -CandidateZip <上表安装包> -WorkRoot D:\mrv2-v241\work\fresh-20260828`（新的不存在的 WorkRoot；前置清理后运行。10:30–10:34，transcript `21-fresh-transcript.log`，rc=0）。

| 任务书要求 | 结果 |
|---|---|
| 最终 `MRV2_T1=PASS` | **达成**（rc=0） |
| `data/backups/logs` 三项 `STANDARD_USER_ACL:<name>:directory=PASS;file=PASS` | **达成**：三行均 `directory=PASS;file=PASS` |
| 探针进程使用 `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe` | **达成**：脚本对该绝对路径硬编码（2026-08-27 已核对脚本行 + 单测 `uses_windows_powershell_for_standard_user_probe` 本轮 PASS） |
| 成功后无 `MRV2Acl*` 账号 / `standard-user-read-probe.txt` / `standard-user-acl-probe` 目录 | **达成**（腿内 fail-closed 残除断言随 PASS 成立；终清理后全机 `MRV2Acl=0`） |
| 清理失败必须令整腿失败 | 契约由 2 个残院单测锁定（本轮 PASS） |
| （附）DACL | `ACL_SUMMARY`：app/runtime=Users RX（owner=Administrators），data/backups/logs=Users NONE |
| （附）其余步骤 | 安装 BAT→回环 healthz→首次设置→LAN 重启（install_id 稳定）→登录→预约/409→公开大屏白名单→手动备份（无 WAL/SHM/journal/.part-* 伴随）→停止/启动生命周期→SYSTEM 双任务+HKLM+LocalSubnet 防火墙→8080 被占拒绝且不杀占用者→损坏 fail-closed——全部 STEP 通过 |

## 6. 第 5 节 T1：累计升级腿 —— **FAIL（V241-B1，放行阻断）**

执行：`v2-windows-upgrade-acceptance.ps1 -BaselineZip <冻结V2.1.0> -UpdateZip <上表累计升级包> -WorkRoot D:\mrv2-v241\work\upgrade-20260828`（新的不存在的 WorkRoot。10:47–10:49，transcript `22-upgrade-transcript.log`，**rc=1**）。

**失败前已通过**：preflight → install-baseline（V2.1.0 BAT RC_0）→ first-setup-and-business-data → wait-backup-catch-up（幂等 no-op）→ prepare-cumulative-update → **health-failure-rollback**（故障注入→健康失败→回滚）→ **standard-user-private-roots-after-rollback**：`data/backups/logs` 三项 `list=PASS;read=PASS` → **run-cumulative-update 失败**。

**失败签名（与 2026-08-27 首验 V241-B1 同点同因）**：
- 更新 BAT：`V2 更新没有完成：更新前在线备份失败：备份失败：没有生成可用备份…`（`MRV2_UPDATER` 失败，腿 rc=1）
- `backup-status.json`：`{"detail": "RuntimeError", "schema": 1, "sequence": 3, "status": "failed"}`
- 失败现场快照（`23-post-upgrade-snapshot.log`）：backups 内 `00000001/2.db`=135,168B（基线 schema），**`00000003.db`=155,648B**——健康失败尝试换入的新版本运行时迁移 schema 后写下的跨版本 sidecar；data 内现场库回滚后仍为 135,168B。重试更新的「更新前在线备份」由已安装的 V2.1.0 旧代码执行，序列预留跳过不可解析的 seq3 sidecar → 瞄准 3 → 命中现存文件抛 RuntimeError（fail-closed 正确：无半更新、未触碰 V1、状态如实记录）。

**定性**：本分支（锚点 `2391ad6`）未包含 V241-B1 修复（`git grep reconcile_backup_sequence_floor`/`_filename_sequence_floor` 于锚点树均 ABSENT）。修复位于 `codex/v241-b1-b2-fixes`（2026-08-27 下午实机复跑升级腿 PASS）。按任务书 §2.6，本轮未修改代码、未重跑腿；失败现场快照已存，随后终清理移除全部产品对象。
**证据缺口（如实记录）**：`_程序文件\logs\backup.log` 原始 traceback 未在终清理前单独抄录（脚本自带诊断已含 update.log ERROR 行、service.log 尾 60 行、backup-status.json、backups 清单；机制判定依据上述四项 + 2026-08-27 同点法证）。

## 7. 第 6–7 节：普通用户 / 运行网络恢复 —— 逐项判定

| 条目 | 判定 | 依据 / 备注 |
|---|---|---|
| §6.1 双击 BAT 的 UAC 取消/接受 | **SKIP（需用户本人）** | 本轮无真实双击场景；提权运行器的 3 次 UAC 均由用户本人批准（10:30/10:42/10:47）。T2 E67/E73 有历史真机记录 |
| §6.2 安装根精确 + 绑定可信根 | **PASS（正向）/ SKIP（篡改负面用例）** | 全新腿安装根与注册表 `InstallRoot` 断言通过；注册表根篡改/目录搬移/异目录启动负面用例本轮未执行（单测 `test_update_core` 系列覆盖锁定） |
| §6.3 标准用户 RX app/runtime、私有目录不可读 | **PASS** | 全新腿 ACL_SUMMARY + STANDARD_USER_ACL 三项；探针为真实随机本地账号（经 5.1 委托垫片，见 §8 注） |
| §6.4 维护入口按需 UAC、不能绕过 | **PASS（行为证据）+ SKIP（真实双击）** | 同 2026-08-27 结论；交互 BAT 在无人值守管道的已知形态（V241-O3） |
| §6.5 8080 被占：拒绝启动、不杀占用、可操作提示 | **PASS** | 全新腿 port-conflict 步通过 |
| §6.6 SmartScreen/Defender/EDR/AppLocker/GPO | **部分** | Defender+火绒全程在位未关闭（productState 记录）；EnableLUA=1；AppLocker 家庭版 N/A；SmartScreen 真实弹窗需用户双击（SKIP）；未做任何关闭操作 |
| §7.1 首启仅回环→设置后 LAN | **PASS** | 全新腿 loopback-health → first-setup → LAN 重启且 install_id 稳定 |
| §7.2 防火墙仅 TCP8080/Domain+Private/LocalSubnet | **PASS** | 全新腿 system-registration 步断言（规则形状与 2026-08-27 提权复核一致）；终清理复核 fw=0 |
| §7.3 第二台设备登录/大屏跨设备 | **SKIP（无第二设备）** | 公开大屏白名单脱敏由全新腿 public-display 步验证（PASS） |
| §7.4 真实重启后任务恢复 | **SKIP（需用户重启）** | 任务定义已由腿内断言核验（主任务 Boot 触发、SYSTEM、Highest） |
| §7.5 每日 02:00/补备份/30 份轮转/无 WAL 残留 | **PASS（触发与补跑）/ SKIP（30 份实跑）** | 升级腿 catch-up 幂等 no-op；手动备份无伴随文件；30 份轮转由单测覆盖、实跑未执行 |
| §7.6 造预约→备份→损坏→恢复→数据回来→旧会话失效 | **PASS（T1 层）+ SKIP（真实 UAC 交互恢复）** | 全新腿 fail-closed-corruption + 升级腿回滚数据保留证据；T2 E80 有历史交互修复验证 |
| §7.7 断电演练（升级中断电→重跑同包） | **SKIP（未获批准）** | 且 V241-B1 未修复前该路径预期失败，不得模拟成 PASS |
| §7.8 分辨率/缩放 UI + 工作交接页四状态 | **PASS（详见 §7A）** | 锚点前端生产构建 + 后端锚点源码本地实例（18080 端口、独立数据目录、纯合成数据） |

### 7A. §7.8 工作交接页四状态验证详录（2026-08-28 10:14–10:25）

**方法学（如实记录）**：今日新增的产品变化全部在工作交接页（`703e76a`+`2391ad6`，纯前端）。为使被测前端与锚点完全一致，采用「锚点源码 `npm run build` 生产构建（Node 22.17.1）+ 锚点后端源码直跑（Python 3.13.14，`MEETING_ROOM_V2_PORT=18080`、独立 `MEETING_ROOM_V2_DATA_DIR`）」，由后端直接服务 `dist/client`；浏览器为 Chromium 内嵌浏览器 + `setViewportSize` 精确视口。125%/150% 系统缩放以 CSS 视口等比模拟（819×576 / 683×480；1920@125%=1536×864），真实系统 DPI 切换属用户侧余项。**非安装候选包实测**——安装包内的前端与该构建同源同锚点，但安装态差异（如服务化路径）未在本节覆盖，如实注明。

**合成数据**：3 个合成账号（验收人甲/乙/丙）+ 5 条明日合成预约（SYN-H-001…005，同一笔录室错峰）+ 定向交接请求，经产品 API 造数；四状态经「造/撤/再发起」顺序雕刻后逐状态实测。无任何真实个人信息。

| 状态 | DOM/行为断言 | 视口矩阵（均无横向滚动、无按钮裁切） |
|---|---|---|
| 全空 | 无分组壳/零计数/概览带；`role=status` 容器居中显示陶土色柔和圆形交接图标 + 标题「暂无工作交接」+ 说明「收到确认请求或发起交接后，将在这里显示。」逐字一致；侧栏徽标空 | 1440×900、1024×720、683×480(150%)、1920×1080 |
| 仅待我确认 | 「我发起的」分组完全不渲染；动作顺序=查看预约/不接受/接受交接；徽标=条数 | 1440×900、819×576(125%)、1536×864(125%)、683×480(150%) |
| 仅我发起 | 「待我确认」分组完全不渲染；侧栏交接徽标为**空**；动作顺序=查看预约/**处理中**/撤回申请；「处理中」为 `<span>` 非交互胶囊，计算样式 `rgb(164,82,61)` 文字 + `rgb(250,237,231)` 底（产品陶土色系） | 1440×900、683×480(150%) |
| 两者并存 | 「待我确认」分组在上、「我发起的」在下；两分组各行动作区左缘 x 坐标一致（1920 宽下四行均 1216px，测量值）；「查看预约」打开详情抽屉、关闭后正常返回（分组保持）；徽标=待确认条数 | 1920×1080、1024×720、1280×720、1536×864(125%)、683×480(150%) |

**业务语义（未开始预约，既有语义回归）**：
- 接受交接（乙接受 SYN-H-003）：toast「已接受，预约已转入您名下」，预约 13:00 场次所有权转移至乙（API 终验 owner=乙）；甲的对应发起行消失
- 不接受（乙拒绝 SYN-H-004）：toast「已拒绝，预约仍归原预约者」，所有权保持甲，双侧行消失
- 撤回申请（甲撤回 SYN-H-001）：toast「已撤回交接请求」，双侧行消失，分组计数即时更新
- 终态 API 核对：5 条预约归属全部符合语义（001→甲、002→乙（待确认中）、003→乙、004→甲、005→乙）
- 待办弹窗（非本节新增但交界）：待确认数徽标与弹窗计数一致；「稍后处理」后徽标保留；接受/拒绝入口在弹窗内可用

**键盘焦点/抽屉**：画布 Tab 后焦点以清晰深色描边环落在行内首个「查看预约」；详情抽屉开关正常（昨日轮已对抽屉焦点陷阱/焦点归还做过全量检查，本轮抽屉结构未变化，仅复核开关路径）。
**控制台 error 的证据方式（如实记录）**：内嵌浏览器不暴露 console 捕获；以「全流程每个动作均产生预期 DOM/toast/所有权效果 + 后端进程零错误输出 + 无失败网络请求表现」作为近似证据；真实 DevTools console 抽查属用户侧余项。
**观察项（非本轮引入）**：从交接页打开的详情抽屉状态芯片显示既有回退文案「状态未知」（`ui/presentation.js:60`，由合入前提交 `9559e6a` 引入；交接列表载荷不含 status 字段时的回退表现）——建议后续随详情抽屉载荷补齐，不属本轮四状态验收口径。

## 8. 执行过程记录（执行者侧，如实）

1. **提权运行器（runner1，10:30 UAC）**：队列 20 清理 rc=0 → 21 全新腿 rc=0（10:33 完成）。随后 runner1 卡死于 `Start-Process -Wait`（PowerShell 7 已知等待形态：子进程已退出、transcript 已收尾，但 -Wait 未返回），22–24 未被调度。任务 21 的 `.rc` 由执行者按 transcript 记录补记为 0（真实结果以 transcript 为准）。
2. **runner2（10:42 UAC）**：直接串行执行 22–24，但**未设置工作目录**，升级腿脚本以相对路径读取 `v2/VERSION` 立即终止（rc=1，失败 transcript 存 `22-upgrade-transcript.cwd-failed.log`）；其后的 23 快照（绝对路径，记录的是全新腿后状态）与 24 终清理（清空产品对象）照常执行。此为**执行者操作失误**，非产品行为；经验教训：验收脚本必须在仓库根 CWD 下运行（首个运行器的 `-WorkingDirectory` 指向的是 2026-08-27 的旧检出，属同类隐患）。
3. **runner3（10:47 UAC）**：修正 CWD 后重跑 22（先删除 22/23/24 的过期 `.rc`，23/24 日志改名区分）。升级腿完整跑到 run-cumulative-update 失败（§6）；23 快照记录失败现场（§6 签名）；24 终清理复核零残留。runner3 输出：`runner3-remaining.log`。
4. **本机 LocalAccounts 适配（沿袭 2026-08-27 V241-O2）**：Win11 23H2 内置 LocalAccounts 模块与全部公开 PowerShell 7 不兼容（`New-LocalUser` 执行时类型加载失败），标准用户探针账号操作经 `la-shim.ps1` 委托真 Windows PowerShell 5.1 执行；验收脚本断言与 fail-closed 残留复核逻辑原样运行。探针**进程本身**仍为脚本硬编码的 System32 Windows PowerShell。该垫片只影响测试基建，不影响产品。
5. 卡死的 runner1 进程保留至 4 小时自动退出（无法跨完整性级别终止；无对象锁竞争）。

## 9. 系统对象与探针残留清单（终态，`24-final-cleanup.log`）

- 计划任务=0、防火墙规则=0、注册表键=False、安装根=False、8080 监听=0、`MRV2Acl*` 账号=0
- 无 `.update-*`/临时程序树/探针账号/canary/维护锁残留（腿内断言 + 终清理复核）
- UI 验证用本地实例（18080）已停止；其数据目录为 D 盘独立合成数据目录，与产品固定对象无关
- 验收产生的全部痕迹限于受控本地目录 `D:\mrv2-v241\`（evidence-20260828 共 22 个证据文件 + 16 张合成数据截图）与仓库内本未跟踪证据文件

## 10. 需用户本人操作 / 未执行清单

1. §6.1 真实双击安装 BAT 的 UAC 取消与接受（含 SmartScreen 真实弹窗表现）。
2. §7.3 第二台局域网设备登录与公开大屏目检。
3. §7.4 真实重启后主任务/健康/每日备份任务恢复复核。
4. §7.8 真实系统 DPI 缩放（设置→缩放→注销）抽查（代理视觉验收已用 CSS 视口模拟覆盖布局效应）；真实 DevTools console 抽查。
5. §7.7 断电演练（若批准；建议 V241-B1 修复进入分支后执行才有意义）。
6. **V241-B1 修复合入本分支后重跑累计升级腿及其依赖项**（当前为放行阻断项）。

## 11. 缺陷与观察汇总（本轮增量）

| 编号 | 级别 | 摘要 |
|---|---|---|
| V241-B1 | 产品缺陷（高，放行阻断） | **本分支仍未修复**：健康失败回滚后重试累计更新，更新前在线备份与回滚保留的跨版本 sidecar 序列冲突（RuntimeError, sequence=3）。修复已在 `codex/v241-b1-b2-fixes` 验证。本轮证据：`22-upgrade-transcript.log`、`23-post-upgrade-snapshot.log` |
| V241-B2/V241-O1 | 携带（非本轮回归） | installer 套件同 2 失败，修复/平台跳过均在姊妹分支；不影响 Windows 候选 |
| V241-O5 | 测试基建（新） | runner1 `Start-Process -Wait` 卡死形态：队列 runner 应改用 `& pwsh -File`（runner3 已验证可用）或 `-Wait` 超时兜底 |
| V241-O6 | 脚本人机工学（新） | 验收脚本以相对路径读取 `v2/VERSION`，必须在仓库根 CWD 运行，建议脚本内自行 `Set-Location` 到脚本仓库根（本轮 runner2 失败根因） |
| V241-O7 | 观察（既有，非本轮引入） | 交接页→详情抽屉状态芯片回退文案「状态未知」（`ui/presentation.js:60`，`9559e6a` 引入）；建议交接列表载荷补 status |
| V241-O8 | 环境（沿袭 V241-O2） | 本机 LocalAccounts×PS7 不兼容需 5.1 委托垫片；建议验收脚本将探针账号操作显式改走 System32 Windows PowerShell 子进程 |

## 12. 结果判定

- 第 1 节 PASS；第 4 节 PASS（2 个携带失败已定性）；第 5 节全新腿 **PASS**；第 5 节升级腿 **FAIL（V241-B1）**；第 6–7 节逐项见 §7；§7.8 交接页四状态 **PASS**。
- **放行阻断项**：V241-B1 修复未合入本分支。即便全部用户侧余项完成，在 V241-B1 合入并复跑升级腿 PASS 之前，不得申请放行。
- `formal_external_release_allowed=false`。本轮全程：未修改产品代码、未创建 PR/合并、未打标签、未发布/签名/分发；未关闭 UAC/SmartScreen/Defender/EDR/防火墙；仅使用合成测试数据。

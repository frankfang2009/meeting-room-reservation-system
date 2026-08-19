# T2 Windows 实机测试证据（2026-08-18 ~ 08-19，测试机代理执行）

测试机：DESKTOP-F6CBUIJ · Windows 11 家庭中文版 23H2（build 22631）· 未加域 · Windows Defender · 用户 Frank Fang（Administrators 组，UAC 过滤 token）· C 盘剩 22GB / D 盘剩 28.4GB · 代理 127.0.0.1:7890。
仓库：`D:\mrv2-t2\repo`，分支 `codex/t2-windows-task`，HEAD `e5c108b4`（任务书 commit，相对 `d4fb9adc` 仅新增任务书文档本身，产品代码零差异）。
工具：git（已有）+ winget 安装 PowerShell 7.6.5（MSIX）与 GitHub CLI 2.97.0；gh 设备码登录 frankfang2009。
全部工作文件位于 `D:\mrv2-t2\`（TEMP/TMP 重定向），transcript 位于 `D:\mrv2-t2\transcripts\`，截图位于 `D:\mrv2-t2\evidence\shots\`，B-4 复现实验位于 `D:\mrv2-t2\motw-repro\`。

## 产物清单（SHA-256）

| 产物 | 来源 | SHA-256 |
|---|---|---|
| 会议室预约系统-V2.1.0-安装包.zip | run 31999643262（2026-08-24 过期前抢救） | `55a4db9861d682250204ee4b3044216a098de3fea84649b40c8e1fb2423075f5`（与任务书要求一致） |
| 会议室预约系统-V2.2.3-安装包.zip | run 32089926336 | `9471099623b462e43b3d82f7ac330b8d0a21af4755d9af36c748dc09cd10acd0` |
| 会议室预约系统-V2.2.3-累计升级包.zip | run 32089926336 | `a12ce79288f97a10954bf9ba963528effb57c7b9eee84faf287deefb35ef8508` |
| 会议室预约系统-V2.3.0-安装包.zip | run 32143370343（main `d4fb9adc`，触发于本机） | `649f0df5e9c315358ce299a51bab64bb802f82df09daf8081e3e3b2ffe00e68f` |
| 会议室预约系统-V2.3.0-累计升级包.zip | run 32143370343 | `3f7fffc3706122f61e35b1b3fb29160ddbae9fb84dbfe4cde29ce2f5a8a5bea4` |

## E 条目（从 E64 起编号，E62/E63 已被 V2.2.3 段占用）

| E编号 | 测试事项 | 结果 |
|---|---|---|
| E64 | 机器档案与工具准备 | Win11 23H2 家庭中文版未加域、仅 Defender、管理员账户非提权会话；winget 装 pwsh 7.6.5 + gh 2.97.0（C 盘 22GB ≥ 2GB 前提成立）；gh 设备码登录成功 |
| E65 | T1 全新安装腿真机重放（管理员 pwsh 7 + Start-Transcript） | `v2-windows-acceptance.ps1` 对 V2.3.0 候选 12 步全绿 `MRV2_T1=PASS` rc=0（08-18 21:47–21:51，transcript `t1-fresh-214752.log`）：安装→回环 healthz→首次设置→LAN→预约/409/取消→公开大屏脱敏→手动备份无伴随文件→停止/启动生命周期→SYSTEM 任务+HKLM+LocalSubnet 防火墙→端口冲突拒绝且不杀占用者→损坏 fail-closed（不重开设置、保留损坏库）→DACL 边界 |
| E66 | T1 升级腿真机重放 | 前置两次尝试失败暴露环境问题（见 T2-B1）；第三次全绿 `MRV2_T1U=PASS` rc=0（08-18 22:00–22:05，transcript `combo3-fullcleanup-upgrade-*.log`）：v2.1.0 基线安装→业务数据→备份补跑→V2.3.0 累计升级→版本三处一致、install_id 不变、回执 complete、无 `.update-*` 残留→预约/会话/房间保留→升级后 DACL 重固化 |
| E67 | A1 UAC 弹窗（真客户双击安装 BAT，未签名） | 用户实测：弹窗显示**未知发布者**；UAC 同意后进入安装控制台 |
| E68 | A2 SmartScreen / MotW | 实验：给 zip 添加 Zone.Identifier(ZoneId=3) 后以资源管理器同款 Shell.Application 解压，**292/292 个文件全部被传播 MotW**（含 安装V2.3.0.bat、python.exe、全部 DLL/PYD）；用户双击 BAT 未被 SmartScreen 拦截（BAT 直接触发 UAC）；摩擦记录：1 次 UAC，0 次 SmartScreen（E 类签名后复测） |
| E69 | A3 DACL 负面测试（非提权会话） | 向 `_程序文件\app` 写文件 Permission denied；列/读 `_程序文件\data` Permission denied；读 app 树（RX）允许；普通用户浏览器访问 8080 返回 200；另实测非提权 `reg delete HKLM` 拒绝访问 |
| E70 | A4 首启仅回环→向导→切 LAN→防火墙 | 安装后监听 127.0.0.1、LAN IP 192.168.2.114 拒绝（000）、回环 200；浏览器走完 5 步首次设置向导；服务切 LAN：0.0.0.0 监听、LAN IP 200、install_id 前后一致 `26db23e1…`；防火墙弹窗**未出现**（安装器已预建程序+TCP8080+LocalSubnet 精确规则）；规则核验：手动=python.exe、后台=pythonw.exe，均 Inbound/Allow/TCP/8080/LocalSubnet |
| E71 | B2 DPI 渲染（方法学：IAB 内嵌浏览器不支持系统 DPI 与原生缩放，采用视口等比缩减 1/1.25、1/1.5 模拟 125%/150% 布局效应） | 100%/125%/150% 三档预约日历截图（`shots/b2-calendar-*.png`）目检：无错位、无截断、无乱码；150% 档日历时间轴竖排中文为设计样式 |
| E72 | D1 备份→破坏→恢复闭环（核心能力） | UI 创建预约（T2-D1-RECOVER-001，2026-08-19 08:30–09:30）；② 立即备份 BAT 成功；停服后破坏 db；**产品恢复核心成功**：`restore.py --backup …-00000002.db --expected-install-id` 返回 `restored:true`（含 pre-restore 快照），db 回 139264 字节，服务健康 install_id 不变，API 复核预约/房间/登录全部恢复；**⑥ BAT 入口被 T2-B4 完全阻断（见 bug 表）** |
| E73 | 真实客户安装交互（含 T2-B3） | 用户双击带 MotW 的 安装V2.3.0.bat → UAC 同意后**控制台黑屏**（安装确认提示被 BAT 重定向进临时日志，见 T2-B3），按 Enter（空输入≠YES）→ 安装取消 RC_3、无残留（取消路径安全）；被告知后输入 YES → 安装成功（约 100 秒），首启状态符合契约 |
| E74 | 破坏后 fail-closed 与 ③ 任务重建 | db 损坏态下 ③ 设置开机自动启动.bat 重建两个计划任务成功（主任务进入 Running）；服务保持 fail-closed `status=recovery, recovery_code=DATABASE_UNAVAILABLE, setup_complete=false`（不重开首次设置）；backup.log 记录损坏期 catch-up 备份正确拒绝（`数据库未就绪`）；③ 在管道环境输出中文乱码行（同 T2-B5 族观察） |
| E75 | B3 真实夜间备份（机器 8/18 18:22 起持续开机挂机，00:15 起 AC 睡眠=从不） | `reservation-v2-backup-00000003.db`（139264 字节）+ sidecar 于 **08-19 03:00:03** 落盘，`backup-status.json` status=succeeded sequence=3；backups/data 无 -wal/-shm/-journal/.part-/.tmp 残留；另捕获恢复后启动的 `backup idempotent no-op mode=catch-up`（补跑幂等设计实证） |
| E76 | 每日备份任务触发器核验（提权） | StartBoundary=`2026-08-18T02:00:00+08:00`（02:00 契约写入正确，DaysInterval=1）；**LastRunTime=08-19 03:00:00（首次延迟 1 小时，见 T2-O3）**；LastTaskResult=0；NextRunTime=08-20 02:00:00 |
| E77 | A5 真实重启（08-19 08:40 重启，用户在场） | 重启后：主任务**开机自启**（lastRun 08:40:40，boot 后 10 秒，state=Running，principal=SYSTEM）；备份任务 Ready lastResult=0；`/healthz` ok=true、lan、install_id 不变 `26db23e1…`；LAN IP 200、监听 0.0.0.0；db 139264 字节数据完好；transcript `combo13-post-reboot.log` |
| E78 | B1 第二设备（手机连同一 WLAN） | 手机浏览器打开 `http://192.168.2.114:8080` 成功，显示登录页（跨设备可达 ✓）；公开大屏脱敏白名单在 T1 已验证（`/api/v1/display/today` 不含 partyName/caseNumber/purpose/notes）；双端并发 409 由 T1 冒烟覆盖（`SLOT_CONFLICT`），未做真实双端对抢（诚实记录） |
| E79 | ⑥ 真实窗口人工路径 + T2-B4 根因定位 | 用户真实双击 ⑥ → UAC 同意 → 窗口显示 `Missing closing ')' in expression`（与管道路径一字不差，**第 4 次复现**）；后台健康监控证实全程服务零中断——⑥ 失败于解析层、未进入停服步骤，与 `:failed` 文案「服务和计划任务没有被修改」一致（失败前置无副作用）；随后最小复现实验（`motw-repro/`）二分定位：ASCII 版同样失败（排除中文/编码）、缩短至 229 字符仍失败（排除长度）、对照实验证实**提取行 `& ([ScriptBlock]::Create((expr))` 缺 `& (` 层的闭合括号**——补一个 `)` 即恢复正常输出，少一个即精确复现 `MissingEndParenthesisInExpression`；①③④⑤ 无此提取模式不受影响 |

## Bug 与观察清单（最终）

| 编号 | 级别 | 摘要 | 复现与证据 |
|---|---|---|---|
| T2-B1 | 测试方法 | 任务书 §6.2 腿间清理的 `Remove-Item -LiteralPath HKLM:\Software\MeetingRoomReservationV2 -Recurse` 在提权 pwsh 7.6.5/Win11 23H2 报 "Requested registry access is not allowed" 且键残留；`reg.exe delete` 正常。建议任务书改用 reg.exe | transcript `combo-cleanup-upgrade-215721.log`；残留注册表导致升级腿基线安装误判失败 |
| T2-B2 | 设计行为记录 | v2.1.0 基线安装在「HKLM 残留+安装根缺失」异常环境下走升级分支并以 RC_6 失败，按 RC_6 语义保留半成品安装现场；期间出现 `'…' is not recognized` （基线包内调用不存在的升级命令） | transcript `combo-cleanup-upgrade-215721.log` |
| T2-B3 | **产品 bug（高）** | 安装 BAT 将 install.py 全部 stdout/stderr 重定向到临时日志（`>"%TEMP%\…log" 2>&1`），「确认继续全新安装？请输入 YES：」（install.py:87）在控制台不可见，窗口黑屏；任意按键=空输入→取消安装。真客户几乎无法自行完成安装。CI 管道喂 YES 永不暴露。建议：确认提示写 CON 设备或 BAT 不做整体重定向 | 用户实测（本轮对话）；`安装V2.3.0.bat:16`；`_V2安装工具/app/install.py:87` |
| T2-B4 | **产品 bug（高，根因已定位）** | `⑥ 从备份恢复.bat:56` 提取内嵌 PS 的命令 `& ([ScriptBlock]::Create((…-join [Environment]::NewLine))` **缺 `& (` 层的闭合 `)`**（3 个开括号仅 2 个闭），Windows PowerShell 解析必然失败 `Missing closing ')' in expression`，恢复流程未进入主体即退出；**管道 3 次 + 用户真实窗口 1 次 = 4/4 复现**；客户侧恢复入口完全阻断（核心 `restore.py` 本身工作正常，E72）。修复：行尾 `))` → `)))`（或改落地临时 .ps1 执行）。CI 未抓到原因：T1 验收不含 ⑥ 执行 | `combo6/7/9` transcripts + 用户实测 + `motw-repro/` 对照实验（vC 229 字符复现、vD 84 字符正常、平衡/不平衡一行对照） |
| T2-B5 | 观察 | ③（及升级失败路径）BAT 输出在管道环境出现中文乱码 cmd 报错（`'…或防火墙规则。' is not recognized`、`The system cannot find the path specified.`）；核心功能未受影响；与 B-4 无关（独立现象，疑为 UTF-8 BAT 长/中文行经 cmd 解析的稳定性） | transcript `combo9-task-rebuild-restore.log` |
| T2-O1 | 观察（8/19 更正） | 昨晚疑似「计划任务意外消失」为**误报**：两任务带限制性安全描述符，**非提权会话（pwsh Get-ScheduledTask 与 cmd schtasks /query）均不可见返回空**，提权会话始终可见（count=2）。正面安全设计（普通用户不可见/不可篡改 SYSTEM 任务），同时是运维诊断陷阱；提权 TaskScheduler 事件日志（141/140/375）近 12h 无删除记录 | `combo11-b3-overnight.log`、`combo12-trigger-diag.log`、非提权/提权对照 |
| T2-O2 | 观察 | 切 LAN 时防火墙弹窗未出现：安装器已预建程序限定+TCP8080+LocalSubnet 规则，服务绑定非回环时直接命中，免摩擦正确设计 | E70 核验输出 |
| T2-O3 | 观察（8/19） | 每日备份首次真实夜间触发延迟 1 小时：契约 StartBoundary 02:00+08:00 正确，实际 LastRunTime 03:00:00（备份成功落盘），NextRunTime 次日回到 02:00。疑似 Modern Standby 空闲错过定点触发后由 StartWhenAvailable 补跑。CI 一次性虚拟机无法暴露；建议开发复核空闲/待机下的触发时效 | `combo12-trigger-diag.log`、`combo11-b3-overnight.log` |

## 2026-08-19 修复验证补充（E80–E82，分支 codex/t2-windows-installer-fixes）

| E编号 | 测试事项 | 结果 |
|---|---|---|
| E80 | T2-B4/B6/B7 修复实弹：⑥ 恢复全链路（修复版 ⑥ 部署至安装根，管道喂 RESTORE） | 三重修复后 **rc=0 完整通过**：选中最新备份 00000003 → RESTORE 确认 → 安全停服 → `restored:true`（pre-restore 快照）→ 服务恢复 →「V2 已从当前安装的最新有效备份恢复」；修复过程暴露三处独立缺陷（见 B6/B7）均为真机独有（CI 的 pwsh 7 行为差异/T1 从不执行 ⑥） |
| E81 | T2-B3 修复回归：install.py 交互改造（修复版部署进解压包 + manifest 哈希同步） | EOF（stdin 关闭）→ **干净 RC_3**「安装已取消：没有收到安装确认输入」无 traceback（旧版为 EOFError→RC_1）；YES → **RC_0**，输出含开场横幅/重要说明/新增「校验通过，开始安装（约 1–2 分钟…）」进度提示/完成 URL，服务首启 loopback 就绪（新 install_id 38715769）；「请输入 YES」提示走 CONOUT$ 不进日志为设计行为（真窗口可见，管道静默）；另实证产品防篡改：替换 install.py 未同步 manifest 时被哈希校验拒绝 RC_1 |
| E82 | 修复配套门禁 | 单测：BAT 全部 `-Command` 单行圆括号平衡断言（红绿验证：缺括号旧版精确报 `⑥:56 10 != 9`）+ install 入口 4 项交互测试；candidate gate：解压包与安装根全部内嵌 PS（-Command 行 + # MRV2-POWERSHELL-BEGIN 整段）真实 ScriptBlock 解析冒烟（本机红绿：修复版 18 行全过、旧版立即失败） |

**修复期间新发现（均已修复或记录）**：

| 编号 | 摘要 | 处置 |
|---|---|---|
| T2-B6 | ⑥ 配对校验硬编码 `databaseSchemaVersion -ne 1`，而 V2.3.0 备份 sidecar 为 schema 3 → 修复括号后 ⑥ 仍拒绝全部现有备份（"没有可恢复的配对备份"）；restore.py 本身接受 1..3 | 已修复：对齐 update_core.SUPPORTED_DATABASE_SCHEMA_VERSIONS（1..3）范围检查 + 注释同步关系 |
| T2-B7 | ⑥ 恢复后服务重启等待两层缺陷：a) 30 秒窗口 < 真机冷启动（冻结 runtime+Defender 扫描）；b) **Windows PowerShell 5.1 下 `$ErrorActionPreference='Stop'` 使原生命令 stderr 直接变 NativeCommandError 异常**——服务未起时 `service --check` 必写 stderr，等待循环首次迭代即中断（窗口加长也无效）；CI 用 pwsh 7 无此行为故从未暴露 | 已修复：窗口对齐 120 秒；两处原生调用局部放宽偏好；循环改为 TCP 快探 8080 就绪后才做身份终验 |
| T2-B8（观察） | ⑥ 恢复成功（rc=0、数据完好）后服务在数分钟内停止一次（service.log 09:21:00 stop 之后无 start 记录），`① 启动系统.bat` 一键救活；未复现/未定位（疑任务 Start 与 Disabled 时序或会话清理连带）；修复版 ⑥ 后续运行未再出现 | 记录待观察；如复现建议查 TaskScheduler 会话作业对象行为 |

## v2.3.0 标签产物定点复测（2026-08-19，任务书 `v2/docs/T2-V230-SPOT-TASK.md`@`codex/t2-v230-spot`）

编号说明：任务书写「E85 起编号」，但其后 RELEASE-CHECKLIST 已占用 E83–E87（发布与 V2.4.0 开发证据），本段顺延从 E88 起。

| E编号 | 测试事项 | 结果 |
|---|---|---|
| E88 | 标签产物核验与 T1 全新安装腿（tag run 32207285782 产物，检出于 `v2.3.0` 标签 `4aa3df3c`） | 安装包/累计升级包 SHA-256 与任务书要求逐字节一致（`c6c36f1b…dd80` / `e163f21a…d43c`）；机器四件套清理全净（见 T2-B1 升级注记）；`v2-windows-acceptance.ps1` 对标签安装包 **12 步全绿 `MRV2_T1=PASS` rc=0**（transcript `spot2-t1-*.log`） |
| E89 | ⑥ 恢复闭环（B4/B6/B7 在最终标签产物上的回归） | 两次实弹：a) 验收留下的损坏 db 现场，⑥ rc=0 `restored:true` 恢复至最新备份（服务未自动重启属设计——`serviceWasRunning=false` 时保持原停机状态，transcript `spot3/spot4`）；b) 完整闭环：UI 创建预约 V230-SPOT-001（2026-08-20 09:00 验收笔录室一）→ ② 备份 rc=0（backups 3→4）→ ④ 停服+破坏 db → ⑥ 喂 RESTORE **rc=0**（选中最新备份 00000004、pre-restore 快照）→ 四项验证全过：成功文案、`/healthz` ok、预约回归（API 复核）、install_id 前后一致 `469e0458…`（transcript `spot6-diag-rescue-loop-*.log`、`spot7-verify.log`） |
| E90 | 新发现 T2-B9（产品，高）：服务计划任务默认电池策略导致笔记本自停 | 任务设置实测 `DisallowStartIfOnBatteries=True`、`StopIfGoingOnBatteries=True`（注册时未显式禁用，任务计划程序默认值）；测试机为 DELL 笔记本（AC 在线 100% 时仍发生瞬时电源事件）——复测期间服务三次自停（spot5/6 两轮 STEP D 与 11:57 一次），与 T2-B8 观察合并定性：boot 触发+持续供电下驻留正常，电源波动即被任务计划程序终止。建议：注册任务时设置 `-AllowStartIfOnBatteries`+`-DontStopIfGoingOnBatteries`（服务器角色），并在 T1 验收补断言 |
| — | T2-B1 升级注记（测试方法） | PS 注册表 provider 在提权 pwsh 会话对 `HKLM\Software\MeetingRoomReservationV2` **间歇性 Test-Path=False 而键实际存在**（spot1 现场：if 守卫被跳过导致清理不净→安装 RC_6）；清理脚本已改为无条件 `reg.exe delete` + `reg.exe query` 循环验证（以 reg.exe 视角为准），复测 PHASE 1 `reg query confirms key gone` |

## V2.4.0 Windows 真机测试（2026-08-19，main `27e963f` 候选）

| E编号 | 测试事项 | 结果 |
|---|---|---|
| E91 | V2.4.0 候选构建与 T1 全新安装腿（run 32214468178，workflow_dispatch from main；检出于 main 使 VERSION=2.4.0 匹配产物） | 首次构建失败于 candidate-linux `test_deterministic_across_two_builds`（THIRD-PARTY-NOTICES.txt 两次构建 diff，**flaky**，重跑全绿）；产物 SHA-256：安装包 `1bfc925f…d7a4b`、累计升级包 `ba0e8b09…a66b`；机器复测后已清空，`v2-windows-acceptance.ps1` 对 V2.4.0 候选 **12 步全绿 `MRV2_T1=PASS` rc=0**（transcript `v240-t1-*.log`）；⑥ 恢复在 V2.4.0 产物上 rc=0（`restored:true`，服务停属设计：验收现场 `serviceWasRunning=false`） |
| E92 | 工作交接 API 全链路（管理员建员工 王五/赵六 → 员工视角） | 发起 `POST /reservations/{id}/handover`（HTTP 200，请求 pending、预约锁定 canEdit/canCancel=false）；重复发起同一预约 → **409 HANDOVER_REQUEST_EXISTS**（一预约一待处理请求）；接受（赵六）→ **owner 王五→赵六**、status active；拒绝（另一单）→ **owner 回退王五**；`GET /handover-requests` 双视角投影正确（赵六 incoming=待确认、王五 outgoing=我发起）；`GET /users/directory` 仅 id/name/department（无用户名，脱敏投影）；错误响应的 `error.requestId` 为追踪 ID 非交接请求 ID（测试脚本曾误用，已按台账定位真实 ID 复测） |
| E93 | 工作交接 UI（真实浏览器，员工 赵六 登录） | **交接弹窗**（alertdialog「工作交接」）：「王五 希望将这场预约交接给你」+ 当事人/事项/时间 + 归属说明文案，三操作 接受交接/不接受/稍后处理；**主导航徽标**「工作交接 · 1 条交接等待确认」；**台账页**：交接概览（待我确认 1 / 我发起的 0）+「待我确认」「我发起的」两段开放台账；员工导航无管理项（笔录室/用户管理/系统状态不可见）；截图 `shots/v240-handover-dialog.png`、`v240-handover-page.png` |
| — | T2-B9 状态核验（服务任务电池策略） | **V2.4.0（main `27e963f`）未修**：`installer_core.py:1330` 的 `New-ScheduledTaskSettingsSet` 仍无 `-AllowStartIfOnBatteries`/`-DontStopIfGoingOnBatteries`——笔记本部署电源波动仍会触发服务自停（E90 现场），建议随 V2.4.0 正式发布前修复 |

| E94 | V2.4.0 真机升级腿（v2.1.0 基线 → V2.4.0 累计升级包）与 C1 收口（2026-08-19 用户决定） | **`MRV2_T1U=PASS` rc=0**（transcript `v240-upgrade-*.log`）：基线安装→业务数据→备份补跑→累计升级→版本三处一致、install_id 不变、回执 complete、无 `.update-*` 残留→数据/会话/房间保留→升级后 DACL 重固化。C1 断电演练正式标记跳过（用户放行标准=稳定升级+功能正常，均已满足）；排障记录：安装根一度无法删除，元凶为救援时真窗口残留的 `cmd.exe`（① BAT 的 pause 挂起，工作目录锁住安装根）——杀残留 cmd 后删除成功，属测试操作残留非产品问题 |

| E95 | T2-B9 修复闭环（PR #33 合入 main `5e15b9f`，候选 run 32218052902） | 修复：主任务与每日备份任务注册时显式 `-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries`；T1 验收 system-registration 步骤新增电池策略断言（读回对象仅 DisallowStartIfOnBatteries/StopIfGoingOnBatteries 负面属性，首版断言属性名错误已在 CI 现行修正）；PR CI 全绿（含带断言的 v2-windows-acceptance）；**真机复验**：B9 修复候选（安装包 `679c551f…5e1`）T1 十二步全绿 PASS，电池断言通过（transcript `v240-b9-verify-*.log`）；排障注记：安装根删除受阻的元凶为真窗口残留 `cmd.exe`（① BAT pause 挂起锁目录），清理脚本已加残留持有者清除 |

## 六个 [人工] 动作状态

1. 点 UAC「是」：**已完成**（gh 工具组合、真客户安装、③/⑥ 提权、A5 重启后组合，共 8+ 次）
2. SmartScreen「仍要运行」：**未出现**（BAT 未被拦截，见 E68；E 类签名后复测）
3. 重启机器：**已完成**（08-19 08:40，A5 全通过）
4. 升级中途拔电源：**未执行**（用户选择跳过 C1；升级事务性已由 CI T1 层 windows-upgrade-integration 与真机升级腿覆盖大部分，断电场景留待后续）
5. 防火墙弹窗「允许」：**未出现**（规则预建，见 T2-O2）
6. 证书操作：本次无证书，跳过（E 类待签名）

## 环境恢复清单（收尾时已执行）

- AC 睡眠超时已由「从不」恢复为原值 600 秒（powercfg setacvalueindex 0x258 复核）
- 系统保留状态：V2.3.0 真客户安装 + 已恢复数据（install_id `26db23e1-…`）持续运行——如需完全清理请告知（任务书未要求测后卸载）
- 其余系统对象（计划任务/防火墙/注册表）均为本次测试合法创建

## 剩余风险

- **T2-B3/T2-B4 均为交付阻断级**：建议 V2.3.0 正式发布前修复（B-4 一字符修复；B-3 需调整 BAT 输出策略）并补「真窗口人工执行 ①/⑥」进 T1 或发布门禁
- C1 断电演练未执行：升级事务性证据链缺真实断电一环
- B1 未做真实双端对抢（并发 409 由 T1 API 层覆盖）
- E 类（signtool verify、SmartScreen 消失、发布者显示）待签名后补测

## V2.4.0 最终候选验收（2026-08-19）

> 任务书：`v2/docs/T2-V240-FINAL-TASK.md`（origin/codex/v240-final-verify `c024c47f`）。最终提交 main `3b5504a9`（含 #27 交接 + #33 电池修复 + #34 叠加状态修复 + #35 管理员指派结果通知）。证据归档：`D:\mrv2-t2\evidence\v240-final-20260819\`（43 文件 + `manifest.json` 含逐文件 SHA256；截图 10 张、transcripts、admin 队列脚本与输出）。全部账号为合成数据。

| E编号 | 测试事项 | 结果 |
|---|---|---|
| E96 | 最终候选核对与 T1 全新安装腿（run 32226602941，workflow「V2 release candidate」@ main `3b5504a9`，conclusion=success） | 产物下载后 SHA-256 **逐一相符**：安装包 `8ec0650c1d72cea0a5dec1fdbaa1b3a2445ffd0ad069aebf21342d7e68f72474`、累计升级包 `fa6d1f1d150e5a62589eb50500a0e2cfd6ed57ef04ee148a76c17d973296f3ef`；测前四件套清理（注册表 `reg.exe delete /f` + `reg.exe query` rc=1 验证，`Remove-Item` 对该键无效——任务书指定 reg.exe 的原因实证）；T1 全新安装腿 **13 个 STEP 标记全绿 `MRV2_T1=PASS` rc=0**，含 system-registration 的 T2-B9 电池断言（主/备份任务 DisallowStartIfOnBatteries/StopIfGoingOnBatteries 四条，读回全 false 才放行）；transcript `v240-t1-fresh-retry-203107.log`。操作注记：首次 T1 尝试在 preflight 因代理预建 WorkRoot 目录断言失败（测试方失误非产品问题），清目录重试全绿 |
| E97 | #34 叠加状态修复定点冒烟：同一员工（赵六）2 条变更通知 + 2 条待交接请求并存，真实浏览器（IAB）1440×900 与 1024×720 | 混合弹窗（alertdialog「待处理事项」）：标题汇总「2 条预约变更，2 条工作交接」+ 两分区各带计数（工作交接 2 条 / 预约变更 2 条，交接区内每条独立 不接受/接受交接）；**单一滚动区**量化验证：`.notice-modal-body` scrollHeight 1502 / clientHeight 464（@1024×720），滚动到底最后一条可达，弹窗整体在视口内（top 24 / bottom 696 ≤720），页头页脚全程可见、无嵌套滚动与遮挡；**固定底部**双动作「交接稍后处理」（初始焦点）+「确认全部变更」，提示语「交接请求可稍后处理；已生效指派和预约变更只能确认知晓。」；**动作隔离双向验证**：点「交接稍后处理」→ toast「交接请求已保留在『工作交接』，可稍后处理」+ 弹窗转纯变更模式、两条变更完好未确认（API due 证实），交接仍 2 条 pending；点「全部知道了」→ 仅变更被确认（due 清空），交接不受消费。截图 `01–04`（含 1024×720 滚动前后） |
| E98 | #34 指派目录与自指派（管理员对王五的 Q1/Q2，真实浏览器） | **指派目录**（「指派给谁？」）：候选 = 赵六 + 验收管理员，**排除当前预约者王五**、**排除停用钱七**；自指派可选，确认按钮「指派给 验收管理员」，提示语正确（「确认后即时更换预约者；当前预约者为 王五。」「确认后将立即完成指派，无需对方确认。」）；**实际自指派 Q2→验收管理员**：日历即时显示新预约者、revision 仅 +1（1→2）、**管理员 due=0（无自通知）**、Q2 上旧 pending 交接请求 H2 同事务收敛（C incoming 2→1）；补充变体：管理员操作自己的预约时按钮自动为「交接给同事」，目录排除自己（含王五/赵六、不含钱七），与回归清单 5.1 一致。截图 `05/06/09` |
| E99 | #35 管理员指派结果通知（Q1 王五→赵六；偏好关闭后 Q5 王五→赵六） | **S3**：指派即时生效（日历/我的预约即时刷新），Q1 旧 pending 交接请求 H1 收敛（C incoming→0）；C 登录弹「1 条管理员指派待知晓」，正文「**预约已经从 王五 转入你名下，无需接受**；请按时处理。」+ 管理员指派的预约信息（当事人/事项/时间），**仅「查看预约 / 我知道了」，无「不接受 / 接受交接」**；点「我知道了」→ 通知不再出现（due=0），Q1 仍 active 归赵六（我的预约可见）。**S4**：C 在 UI 关闭两项提醒偏好（保存 toast，API 复核 bookingChangeNotifications=False / bookingReminder=False）后——管理员改 C 的 Q3 → **变更通知被偏好正确拦截**（due 无此条）；管理员把 Q5 指派给 C → **指派结果通知仍然出现**（不受偏好开关影响），仍仅两操作；确认后 due=0、Q5 归赵六。截图 `07/08/10` |
| E100 | 控制台 0 error 核验与收尾清理 | **0 error 三方替代证据**（IAB 无控制台监听面，`performance` 只读评估被内核拒绝，如实注明）：① 全程 DOM 快照无 role=alert 与错误文案（0/0）；② 服务端 `service.log`/`backup.log`/`install-*.log` ERROR/Traceback 行数=0；③ 全部交互 API 均为 2xx/预期状态码。**四件套收尾**（`v240-final-teardown-205203.log`）：计划任务 0、防火墙规则 0、注册表 `reg.exe query` rc=1（不存在）、安装根已删、8080 无监听。排障注记（测试操作非产品）：④ 停止 BAT 在服务停止后挂起于 pause（E94 同款），杀挂起 cmd 后 teardown 完成，stop BAT rc=-1 但服务实际已停——无人值守清理建议喂 stdin 或设 `MEETING_ROOM_V2_INSTALL_NO_PAUSE` |

**结论：V2.4.0 最终候选（main `3b5504a9`，run 32226602941）全部验收项通过，READY FOR V2.4.0 TAG。**

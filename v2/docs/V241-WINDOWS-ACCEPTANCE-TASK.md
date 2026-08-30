# V2.4.1 Windows 修复后复验任务书（2026-08-28）

> **已完成并被 V2.5 流程取代的历史任务书，禁止作为当前执行入口。** 本文保留
> V2.4.1 hardening 复验范围与旧证据要求。文中的分支、产品锚点、SHA、候选文件名、
> 测试计数和 PowerShell 步骤不得用于 V2.5 当前候选，也不得作为当前放行证据。
> 当前 Windows 普通用户实机验收唯一入口是
> `V2.5.0-WINDOWS-PHYSICAL-ACCEPTANCE-GUIDE.md`，当前发布判定以
> `RELEASE-CHECKLIST.md` 为准；`formal_external_release_allowed=false` 继续有效。
>
> 本任务书用于真实 Windows 测试机复核 V2.4.1 hardening 分支。执行者只做验收和
> 证据记录，不修改产品代码，不创建标签或 Release，不把候选包对外分发。

## 1. 被测范围与代码锚点

- 仓库：`frankfang2009/meeting-room-reservation-system`
- 分支：`codex/v241-hardening-uncommitted`
- 产品代码锚点：`095675c0ead7aa3e3293b2cc5e9e10162101569b`
- 基线：`25284f0e`（V2.4.0 发布证据合并后的 main）
- 本任务书位于代码锚点之后的独立文档提交。检出远端分支后，必须确认：

  ```powershell
  git fetch origin
  git checkout -B codex/v241-hardening-uncommitted origin/codex/v241-hardening-uncommitted
  git rev-parse HEAD^
  git diff --name-only HEAD^..HEAD
  ```

  第一条结果必须是上述产品代码锚点；第二条只能包含
  `v2/docs/V241-WINDOWS-ACCEPTANCE-TASK.md`。不满足就停止，不要验收其他 SHA。

本分支在 2026-08-28 首轮 Windows 证据所测锚点 `2391ad66` 之上，已合入
V241-B1、V241-B2 与 V241-O1 修复。当前复验以本节的新产品代码锚点为唯一真值，
不得继续使用首轮候选包。重点变化如下：

1. 更新健康运行时在成功提交前和故障回滚前均使用 fail-closed 停止；无法确认停净时
   禁止提交或恢复文件。
2. 累计更新只接受注册表绑定的固定 `%ProgramFiles%` 安装根，拒绝不可信根与过期身份。
3. 程序替换和回滚后重新固化并验证 `app/runtime` 与 `data/backups/logs` 的 DACL。
4. 全新安装验收的标准用户探针固定调用系统 Windows PowerShell，不依赖 pwsh 的
   `$PSHOME`。
5. 标准用户探针使用随机本地账号，并对账号、私有目录 canary 文件及探针工作目录执行
   fail-closed 清理和残留复核；部分创建失败也必须进入清理。
6. 工作交接页移除概览指标带并按数据条件渲染分组；空分组不显示，双侧并存时“待我
   确认”在上，“处理中”使用产品陶土色状态胶囊，操作区保持固定顺序和响应式对齐；
   全空时在剩余画布居中显示主题色交接图标、“暂无工作交接”和一行说明。
7. V241-B1：更新健康失败回滚后重试同一累计更新时，更新器先按备份文件名把旧库
   `backup_sequence` 水位原子上调到已占用序列下限；新版备份代码也按文件名避开当前
   版本无法解析的跨版本 sidecar，禁止覆盖任何既有备份。
8. V241-B2：Windows 宿主构建 macOS 包时，`EDITION` 固定写入 LF 并做字节级校验，
   保持跨平台可复现。
9. V241-O1：NTFS 无法表达的 POSIX 执行位测试在 Windows 明确跳过；macOS/Linux 仍执行。

F3（既有事务恢复在 `ExclusiveLock` 外执行）不属于本轮，禁止顺手修改。

## 2. 硬性安全边界

1. 只使用合成测试数据，禁止复制客户数据库、备份、日志、账号、截图或个人信息到仓库。
2. 候选状态保持 `formal_external_release_allowed=false`；本任务不授权 PR、合并、标签、
   Release、签名或公开分发。
3. 不得关闭 UAC、SmartScreen、Defender、EDR 或防火墙来换取通过。
4. 只允许操作本产品固定对象：
   - 安装根 `C:\Program Files\会议室预约系统V2`
   - 注册表 `HKLM:\Software\MeetingRoomReservationV2`
   - 计划任务 `会议室预约系统 V2`、`会议室预约系统 V2 每日备份`
   - 防火墙规则 `会议室预约系统V2-手动`、`会议室预约系统V2-后台`
5. UAC 接受/取消、SmartScreen 放行、重启、断电和证书动作必须由用户本人操作。
6. 失败后先保存 transcript、截图、返回码和脱敏诊断；不要先改代码或反复重跑覆盖现场。

## 3. 环境与证据目录

在管理员 PowerShell 7 中建立独立目录；工作文件放非系统盘，应用仍必须安装到固定根：

```powershell
New-Item -ItemType Directory -Force D:\mrv2-v241\repo,D:\mrv2-v241\work,D:\mrv2-v241\evidence | Out-Null
$env:TEMP='D:\mrv2-v241\work\tmp'
$env:TMP='D:\mrv2-v241\work\tmp'
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
Start-Transcript -Path D:\mrv2-v241\evidence\windows-v241-transcript.txt
```

记录：Windows 版本/内部版本、Win10 或 Win11、家庭版或专业版、是否加域、当前用户权限、
杀软/EDR、PowerShell 7 与 Windows PowerShell 5.1 版本、C/D 盘剩余空间。

## 4. 源码与静态门禁

1. 完成第 1 节 SHA 核对。
2. 确认工作树只有执行者自己的证据文件；不得出现 `.mimosa`、客户数据或本机私有路径。
3. 使用 Python 3.13.14、Node 22.17.1 和仓库锁定依赖运行：

   ```powershell
   python -m ruff check v2/backend v2/installer v2/tests
   python -m unittest discover -s v2/backend/tests -v
   python -m unittest discover -s v2/installer/tests -v
   python -m unittest discover -s v2/tests -v
   Push-Location v2/frontend
   npm ci
   npm run check
   Pop-Location
   git diff --check
   ```

4. 安装器测试应至少包含并通过：
   - `test_windows_acceptance_uses_windows_powershell_for_standard_user_probe`
   - `test_windows_acceptance_rejects_standard_user_probe_cleanup_residue`
   - `test_windows_acceptance_cleans_partial_standard_user_probe_setup`
   - `test_health_success_uses_fail_closed_stop_before_commit`
   - `test_health_failure_does_not_restore_files_when_second_stop_fails`
   - `test_health_probe_failure_stops_new_runtime_before_restore`
   - `test_online_backup_reconciles_watermark_above_foreign_sidecars`
   - `test_online_backup_reconcile_is_noop_without_backup_files`
5. 后端测试应包含并通过
   `test_backup_sequence_skips_foreign_sidecar_after_rollback_retry`。
6. 预期结果：Ruff 退出 0；backend 151 项退出 0；installer 115 项退出 0（Windows
   允许且只允许 POSIX 执行位用例 1 项 skip）；cross-layer 32 项退出 0；frontend
   173 项与生产构建退出 0；`git diff --check` 无输出。任何 FAIL/ERROR 都必须把本节
   判为 FAIL，不得以“已知失败”为由写成 PASS。

## 5. T1 全新安装与累计升级自动验收

从产品代码锚点重新执行双副本可复现构建，必须得到
`MRV2_REPRODUCIBLE_BUILD=PASS`；记录安装包、累计升级包的文件名、字节数与 SHA-256。
若候选不是由该产品代码锚点构建，停止并报告；首轮锚点 `2391ad66` 的
`950f8979…` 安装包与 `6d6d6505…` 累计升级包均已失效，不得复用或冒充。
当前 `v2/VERSION` 在正式切版前仍为 `2.4.0`，所以本轮内部候选文件名可能仍显示
V2.4.0；必须用产品代码锚点、双构建结果和本轮 SHA-256 识别，不得只看文件名。

两条腿必须串行运行，并为每条腿使用新的不存在的 `WorkRoot`：

```powershell
.github/scripts/v2-windows-acceptance.ps1 `
  -CandidateZip <本锚点重建安装包绝对路径> `
  -WorkRoot D:\mrv2-v241\work\fresh

.github/scripts/v2-windows-upgrade-acceptance.ps1 `
  -BaselineZip <冻结V2.1.0安装包绝对路径> `
  -UpdateZip <本锚点重建累计升级包绝对路径> `
  -WorkRoot D:\mrv2-v241\work\upgrade
```

全新安装腿必须观察并记录：

- 最终 `MRV2_T1=PASS`。
- `data`、`backups`、`logs` 三项均输出
  `MRV2_T1=STANDARD_USER_ACL:<name>:directory=PASS;file=PASS`。
- 探针进程实际使用
  `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe`，不是 pwsh 目录下的
  `powershell.exe`。
- 成功与强制失败后都不存在 `MRV2Acl*` 本地账号、三个
  `standard-user-read-probe.txt` 文件或 `standard-user-acl-probe` 工作目录。
- 清理失败必须令整条验收腿失败，不得输出 PASS。

累计升级腿必须观察并记录：

- 最终 `MRV2_T1U=PASS`。
- `data`、`backups`、`logs` 三项均输出
  `MRV2_T1U=STANDARD_USER_ACL:<name>:list=PASS;read=PASS`。
- 健康检查故障回滚后，版本、install_id、账号、预约、设置、备份、日志、原运行状态和
  private-root canary 全部恢复。
- 程序替换或回滚后，`app/runtime` 仅向 Users 提供读取执行；
  `data/backups/logs` 不得包含 Users 允许 ACE。
- 无 `.update-*`、临时程序树、探针账号、canary 或维护锁残留。
- `health-failure-rollback` 后重跑累计更新必须越过 2026-08-28 首轮的
  `run-cumulative-update` 失败点，最终输出 `MRV2_T1U=PASS`；保存脱敏备份序列摘要，
  确认跨版本 seq3 sidecar 保留且新备份使用空闲序列，不得覆盖旧文件。

任何腿失败都阻断后续放行。保存完整 transcript 和脚本自带的脱敏 diagnostics；不要附加
数据库、secret、备份内容或原始日志。

## 6. 普通用户、UAC 与安装根实机验收

在普通非提权账户上完成以下项目：

1. 双击零参数安装 BAT，分别记录 UAC 取消和接受；取消不得创建安装根、注册表、任务或
   防火墙对象。
2. 接受后安装根必须精确为 `C:\Program Files\会议室预约系统V2`。篡改注册表根、移动
   安装目录或从另一目录启动更新均应 fail closed，不得搜索磁盘寻找 V2。
3. 普通用户可读取执行 `app/runtime`，但不能修改；不能列举或读取
   `data/backups/logs`、数据库、secret、PID、日志或备份。
4. 维护入口按需请求 UAC；普通用户不能绕过提权执行恢复或累计更新。
5. 8080 被未知进程占用时，产品不得杀进程或换端口，必须给出可操作提示。
6. 记录 SmartScreen、Defender/EDR、AppLocker 和单位组策略的真实表现，不得关闭它们。

## 7. 运行、网络与恢复验收

1. 首次设置前只有 loopback 可访问；设置完成后才切换 LAN。
2. 防火墙只允许 TCP 8080、Domain/Private、`LocalSubnet`；Public 不开放。
3. 第二台同局域网设备可登录和访问，公开大屏只显示白名单脱敏字段。
4. 用户本人执行一次真实重启；未登录前主任务恢复运行，健康接口正常，每日备份任务不重复。
5. 验证每日 02:00、超过一天启动补备份、30 份轮转及无 WAL/SHM/journal/`.part-*` 残留。
6. 完成“造合成预约 → 立即备份 → 损坏数据库 → UAC 恢复 → 数据回来 → 旧会话失效”闭环。
7. 如用户批准断电演练：累计升级中断电，开机后重跑同包，验证事务恢复与幂等；否则明确
   标为 SKIP，不得模拟成 PASS。
8. 在 1024×720、1280×720、1440×900、1920×1080 和 100%/125%/150% 缩放下完成
   中文界面、键盘焦点、抽屉焦点陷阱和长时间刷新检查；工作交接页必须用合成数据分别
   记录以下四种状态，且均不得出现横向滚动、按钮裁切或控制台 error：
   - 全空：不显示分组壳、零计数或概览带；剩余画布居中显示陶土色柔和圆形
     交接图标、标题“暂无工作交接”和说明“收到确认请求或发起交接后，将在这里显示。”；
   - 仅待我确认：完全不显示“我发起的”，动作顺序为“查看预约 / 不接受 / 接受交接”；
   - 仅我发起：完全不显示“待我确认”，左侧交接数量徽标为空，动作顺序为
     “查看预约 / 处理中 / 撤回申请”，且“处理中”为陶土色非交互状态；
   - 两者并存：“待我确认”在上、“我发起的”在下，两组动作列对齐；“查看预约”可打开
     详情抽屉并正常关闭返回，未开始预约的接受、拒绝和撤回仍按既有业务语义工作。

## 8. 结果判定与回报格式

最终报告必须包含：

- 代码锚点、候选 SHA-256、机器档案。
- 第 4–7 节逐项 `PASS / FAIL / SKIP`，不得只写“整体通过”。
- 每个 FAIL 的最短复现、返回码、关键脱敏输出及 transcript/截图路径。
- 成功和失败场景后的系统对象与探针残留清单。
- 哪些步骤需要用户操作、哪些未执行。
- `formal_external_release_allowed=false`；即使全部通过，也只能报告“Windows 验收证据完成”，
  不得自行合并、打标签、签名或发布。

证据写入新证据分支的
`v2/docs/V241-WINDOWS-ACCEPTANCE-EVIDENCE-2026-08-28-R2.md`，只提交脱敏 Markdown；
截图和 transcript 保存在受控本地目录，仓库仅记录相对证据说明和摘要。报告应明确区分
本轮新产品锚点结果与首轮失败记录，不得把姊妹分支或旧候选结果冒充本轮结果。

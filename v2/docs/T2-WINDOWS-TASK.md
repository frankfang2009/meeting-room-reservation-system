# T2 Windows 实机测试任务书（测试机代理执行版，2026-08-18）

> **已取代的历史任务书，禁止作为当前执行入口。** 本文只保留 V2.3.0 阶段的测试
> 设计与当时证据线索。文中的 run id、过期 artifact、分支、SHA、候选文件名、下载命令
> 和清理步骤都不得用于 V2.5 当前候选。当前 Windows 普通用户实机验收唯一入口是
> `V2.5.0-WINDOWS-PHYSICAL-ACCEPTANCE-GUIDE.md`，当前发布判定以
> `RELEASE-CHECKLIST.md` 为准；`formal_external_release_allowed=false` 继续有效。
>
> 本文件是给运行在真实 Windows 测试机上的编码代理（ZCode / Z.ai / Codex）的完整任务书。
> 你应当已经把本仓库克隆到 `D:\mrv2-t2\repo` 并 checkout 到分支 `codex/t2-windows-task`
> （基于 main `d4fb9adc` = V2.3.0，已合并未打标签）。先读完本文件再动手。

## 0. 背景与身份

- 产品：会议室预约系统 V2（局域网自托管：Python 后端 + 静态前端；Windows 上以
  BAT 安装器 + 冻结运行时 + 计划任务交付，固定安装根 `C:\Program Files\会议室预约系统V2`）。
- CI 上的自动化验收（T1）已全绿；你的任务 = **T1 脚本真机重放 + 只有真机才能测的
  T2 交互项**，全程记录证据。
- 所有安装包/升级包均为内部候选（`formal_external_release_allowed=false`）：
  只许在本机测试，不得外发。
- 你是测试执行者：**发现 bug 只记录证据，不修改产品源代码**。

## 1. 硬性约束（违反任何一条立即停下报告）

1. **C 盘空间紧张**：你的一切工作文件（产物、工作目录、transcript、截图、缓存）
   一律放 `D:\mrv2-t2\` 下。每个会话开始先执行
   `$env:TEMP='D:\mrv2-t2\tmp'; $env:TMP='D:\mrv2-t2\tmp'`（目录先建好）。
2. C 盘只允许两类写入：① 工具安装（git / gh / PowerShell 7，winget 默认装 C 盘，
   共几百 MB，先向用户确认 C 盘剩余 ≥2GB）；② 应用固定安装根
   `C:\Program Files\会议室预约系统V2`（产品安全契约硬编码，不能改到 D 盘，
   占用约几百 MB 并随备份增长）。其余 C 盘路径一律只读。
3. **权限纪律**：安装与验收脚本在管理员 PowerShell 7 里跑；日常探针与 DACL 验证
   必须在普通非提权会话执行。DACL 负面测试 = 以普通用户身份向 app 目录写文件并
   预期被拒——全程管理员会让这项测试白做。
4. 不得为了让测试通过而关闭 UAC / SmartScreen / Defender / 防火墙——这些摩擦
   本身就是测试数据。
5. 只允许创建/删除本产品的系统对象：计划任务「会议室预约系统 V2」
   「会议室预约系统 V2 每日备份」；防火墙规则「会议室预约系统V2-手动」
   「会议室预约系统V2-后台」；注册表 `HKLM:\Software\MeetingRoomReservationV2`；
   上述安装根目录。其余系统对象一律不碰。
6. **六个动作必须停下来叫用户做，不许自己代做**：点 UAC「是」、SmartScreen
   「仍要运行」、重启机器、升级中途拔电源、防火墙弹窗点「允许」、将来一切证书操作。

## 2. 第 0 步：环境与档案

- 记录机器档案：Windows 版本/内部版本/家庭版还是专业版、是否加域、杀软与 EDR、
  当前用户是否管理员、C/D 盘剩余空间。
- 缺什么装什么：`winget install Git.Git GitHub.cli Microsoft.PowerShell`。
  之后一律用 pwsh（PowerShell 7）跑脚本——验收脚本用了 `-SkipHttpErrorCheck`，
  5.1 跑不了。
- `gh auth login`（设备码流程，让用户用 frankfang2009 账号在手机确认）。
  下载 artifact 必须有它。

## 3. 第 1 步：抢救下载（有过期时间，登录后立刻做，排在一切之前）

```
gh run download 31999643262 -n meeting-room-v2-candidate -D D:\mrv2-t2\artifacts\v2.1.0-baseline
gh run download 32089926336 -n meeting-room-v2-candidate -D D:\mrv2-t2\artifacts\v2.2.3-tag
```

- 第一个是 v2.1.0 基线安装包（升级链路的唯一基线），**2026-08-24 过期**；
  第二个是 v2.2.3 标签候选，**2026-08-25 过期**。过期就没了。
- 校验：v2.1.0 的 安装包.zip SHA-256 必须等于
  `55a4db9861d682250204ee4b3044216a098de3fea84649b40c8e1fb2423075f5`，
  不符就报告，不要猜。所有产物下载后把 SHA-256 记入证据表。

## 4. 第 2 步：拿 V2.3.0 候选（主测版本，main 未打标签）

```
gh workflow run "V2 release candidate" --ref main
gh run list --workflow=release-candidate.yml -L 1     # 轮询到 success，约 5 分钟
gh run download <新 run id> -n meeting-room-v2-candidate -D D:\mrv2-t2\artifacts\v2.3.0-main
```

备选（用户不想触发 CI 时）：直接测 v2.2.3-tag 产物，并把仓库 checkout 到
`v2.2.3` 标签（被测产物版本必须与检出的 `v2/VERSION` 一致）。

## 5. 第 3 步：检出核对

- `git rev-parse HEAD` 应为 `d4fb9adc4fca4d3050e7c81c048539cd17c0e11f`。
- 通读 `.github/scripts/v2-windows-acceptance.ps1` 与
  `v2-windows-upgrade-acceptance.ps1`（你的自动化主力）和
  `v2/docs/RELEASE-CHECKLIST.md`（证据条目格式参考）。

## 6. 第 4 步：T1 真机重放（管理员 pwsh，Start-Transcript 落 D 盘）

两条腿都要求「无残留旧安装」（preflight 会断言 8080 空闲、安装根不存在），串行执行：

1. 全新安装腿：
   `.github/scripts/v2-windows-acceptance.ps1 -CandidateZip <V2.3.0 安装包.zip> -WorkRoot D:\mrv2-t2\work\fresh`
2. 腿间清理（管理员）：
   ```
   Unregister-ScheduledTask -TaskName '会议室预约系统 V2','会议室预约系统 V2 每日备份' -Confirm:$false
   Get-NetFirewallRule -DisplayName '会议室预约系统V2-手动','会议室预约系统V2-后台' | Remove-NetFirewallRule
   Remove-Item -LiteralPath HKLM:\Software\MeetingRoomReservationV2 -Recurse
   Remove-Item -LiteralPath 'C:\Program Files\会议室预约系统V2' -Recurse -Force
   ```
3. 升级腿：
   `.github/scripts/v2-windows-upgrade-acceptance.ps1 -BaselineZip <v2.1.0 安装包.zip> -UpdateZip <V2.3.0 累计升级包.zip> -WorkRoot D:\mrv2-t2\work\upgrade`

任何一步失败：完整保留 transcript 与诊断输出，那就是 bug 证据；不要试图修好再跑，
先记录再报告。

## 7. 第 5 步：T2 交互清单（真实桌面，先做一次腿间清理）

做一次「像真客户一样」的安装：解压安装包，普通用户双击 `安装V2.3.0.bat`
（这轮**不要**设 `MEETING_ROOM_V2_INSTALL_NO_PAUSE`，要真实的暂停与提示；
控制台里「请输入 YES」也由人在场输入）。

- **[人工] A1 UAC 弹窗**：记录文案与发布者显示（未签名会显示什么）。
- **A2 SmartScreen / Mark-of-the-Web**：逐个入口（安装 bat、①–⑥ bat）记录
  是否被拦、拦截文案、几次摩擦。
- **A3 DACL 负面测试（非管理员会话）**：向
  `C:\Program Files\会议室预约系统V2\_程序文件\app` 写文件预期被拒；
  列/读 `_程序文件\data` 预期被拒；普通用户浏览器访问 8080 预期可用。
- **A4 首启仅回环**（127.0.0.1 通、本机局域网 IP 不通）→ 走完首次设置向导 →
  切 LAN → **[人工]** 防火墙弹窗「允许」→ `netsh` 验证放行规则为
  TCP 8080 / LocalSubnet。
- **[人工] A5 真实重启** → 不登录直接验证：两个计划任务存在且主任务 Running、
  `/healthz` 返回 ok、局域网另一设备可打开页面。
- **B1 第二设备**（等用户提供手机/Mac 连入同一局域网）：页面可用、公开大屏脱敏
  （不含当事人/事项敏感字段）、双端并发抢同一时段预期一端 409。
- **B2 DPI 渲染**：中文界面在 100% / 125% / 150% 缩放下各截图一组存证
  （浏览器自动化），检查无错位截断。
- **B3 真实 02:00 备份**：机器挂机过夜（不睡眠），次日核对备份文件落盘且
  无 `.tmp` 残留。
- **C1 [人工] 升级中断电演练**：累计升级包 BAT 升级进行中拔电源 → 开机后重跑
  同包 → 应事务恢复成功；同包再跑一遍应幂等（版本三处一致、install_id 不变）。
- **D1 [人工] 从备份恢复闭环**：造一笔业务数据 → `② 立即备份.bat` → 破坏数据库
  → `⑥ 从备份恢复.bat` → 数据回来且服务健康。
- **E 类（签名后补测：signtool verify、SmartScreen 消失、发布者显示）**：
  本次无证书，跳过，证据表标注「待签名」。

## 8. 证据与收尾

- 证据写 `D:\mrv2-t2\evidence\T2-WINDOWS-2026-08-18.md`，沿用仓库 E 条目表格格式
  `| E编号 | 测试事项 | 结果 |`（写法参考 `v2/docs/RELEASE-CHECKLIST.md`，
  暂从 E62 起编号，合并时如冲突再顺延）；transcript 与截图按子目录引用；
  产物一律记 SHA-256。
- 结束后在 `D:\mrv2-t2\repo` 建分支 `codex/t2-windows-evidence-20260818` 提交证据
  文件（只加证据，不改产品代码），推送并开 PR，标题
  「docs: T2 Windows 实机测试证据 2026-08-18」；没有推送权限就把证据文件路径
  原样报告给用户。

## 9. 最终汇报格式

必须包含：机器档案；A–D 各类 pass/fail/skip 计数；发现的 bug 清单（每条含
复现步骤与 transcript/截图路径）；六个 [人工] 动作哪些已完成、哪些待用户配合；
剩余风险。

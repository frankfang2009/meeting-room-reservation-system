# v2.3.0 标签产物定点复测任务书（测试机代理执行版，2026-08-19）

> 背景：v2.3.0 已打标签（`4aa3df3c`，tag run 32207285782）并发布 GitHub Release。
> T2 期间发现的四个交付缺陷（B3/B4/B6/B7）的修复已在修复版上实弹验证（E80–E82），
> 但**最终标签产物整包**尚未做过定点复测。本任务用 tag run 产物补这一环。
> 硬约束与权限纪律全部沿用 `v2/docs/T2-WINDOWS-TASK.md` 第 1 节（D 盘纪律、
> 非提权 DACL 验证、不关安全设置、[人工] 动作叫人），本文件不重复。

## 步骤

1. **更新仓库**：`git fetch origin && git checkout main && git pull`（应为 `4aa3df3c`）。
2. **下载标签产物并校验**：
   ```
   gh run download 32207285782 -n meeting-room-v2-candidate -D D:\mrv2-t2\artifacts\v2.3.0-tag
   ```
   SHA-256 必须等于：
   - 安装包 `c6c36f1b72641e7f479bfa477ea367f691b6e8f3fad7805e10954b368334dd80`
   - 累计升级包 `e163f21a537b164b53246fa8423b586170a83d1adfb41f3e90bf2175ebfed43c`
   不符即停并报告。
3. **清理现存安装**（上一轮保留的真客户安装将被清除，数据均为测试数据）：
   ```
   Unregister-ScheduledTask -TaskName '会议室预约系统 V2','会议室预约系统 V2 每日备份' -Confirm:$false
   Get-NetFirewallRule -DisplayName '会议室预约系统V2-手动','会议室预约系统V2-后台' | Remove-NetFirewallRule
   reg.exe delete HKLM\Software\MeetingRoomReservationV2 /f     # 勿用 Remove-Item（T2-B1 教训）
   Remove-Item -LiteralPath 'C:\Program Files\会议室预约系统V2' -Recurse -Force
   ```
4. **T1 全新安装腿（标签安装包）**：管理员 pwsh 7 + Start-Transcript：
   `.github/scripts/v2-windows-acceptance.ps1 -CandidateZip <标签安装包.zip> -WorkRoot D:\mrv2-t2\work\v230-spot`
   预期全绿 `MRV2_T1=PASS`。
5. **⑥ 恢复闭环（B4/B6/B7 在最终交付物上的回归）**：在同一安装上——
   UI 创建一笔预约 → `② 立即备份.bat`（[人工] UAC）→ 停止服务并破坏 db →
   `⑥ 从备份恢复.bat`（[人工] UAC）→ 验证：恢复成功文案、`/healthz` ok、
   预约数据回来、install_id 不变。
6. **[可选，人在场] B3 真窗口提示可见性**：再清理一次后普通用户双击
   `安装V2.3.0.bat`，确认「确认继续全新安装？请输入 YES：」在屏幕可见，
   输入 YES 后约 1–2 分钟安装成功。不做也可（E81 已验证过同修复）。
7. **收尾清理**：复测完成后按第 3 步四件套清理干净，机器恢复无安装状态
   （后续 V2.4.0 候选测试要用）。
8. **证据回流**：在 `v2/docs/T2-WINDOWS-EVIDENCE-2026-08-18.md` 追加新章节
   「v2.3.0 标签产物定点复测（2026-08-19）」，E85 起编号，格式沿用现有表格；
   分支 `codex/t2-v230-spot-evidence` 提交推送并开 PR，标题
   「docs: v2.3.0 标签产物定点复测证据」。transcript/截图照旧落 D 盘并在表中引用。
9. **汇报**：各步 pass/fail、transcript 路径、异常与残留。

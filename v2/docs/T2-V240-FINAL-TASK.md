# V2.4.0 最终候选验收任务书（测试机代理执行版，2026-08-19）

> 背景：main `0e42b270` 已包含 V2.4.0 全部内容（#27 交接 + #33 电池修复 + #34 叠加
> 状态修复）。E91–E95 在 `27e963f`/`5e15b9f` 候选上验证过，但**最终提交**（含 #34
> 的堆叠通知弹窗与指派目标改动）尚未整包真机验收。这是打 v2.4.0 标签前的最后一环。
> 硬约束与权限纪律沿用 `v2/docs/T2-WINDOWS-TASK.md` 第 1 节。

## 步骤

1. **更新仓库**：`git fetch origin && git checkout main && git pull`（应为 `0e42b270`）。
2. **下载最终候选并校验**：
   ```
   gh run download 32222981711 -n meeting-room-v2-candidate -D D:\mrv2-t2\artifacts\v2.4.0-final
   ```
   SHA-256 必须等于：
   - 安装包 `d584f9c9874e456024ee8815dc24e81fa90179a33f16a25ab13eec7bd609a59c`
   - 累计升级包 `8f01835707eb8684a31fb9b646bc9276972ee34fd4c93a8521506d272443a257`
3. **清理现存安装**（四件套，注册表用 `reg.exe delete /f` + `reg.exe query` 验证）。
4. **T1 全新安装腿**（管理员 pwsh 7 + transcript）：
   `.github/scripts/v2-windows-acceptance.ps1 -CandidateZip <安装包> -WorkRoot D:\mrv2-t2\work\v240-final`
   预期 12 步全绿——注意新版包含 T2-B9 电池策略断言（system-registration 步）。
5. **#34 叠加修复定点冒烟**（真实浏览器，两员工+管理员账号）：
   - 同一员工堆积两条变更通知 → 弹窗应为单一滚动区、分组计数、固定底部动作，
     「稍后处理」交接不会误确认预约变更；
   - 管理员指派目录：对某预约发起指派时，目录应排除该预约当前预约者，
     且允许指派给管理员自己（自指派提示语正确）。
   - 控制台 0 error。
6. **收尾**：测试完成后机器清理干净（四件套）。
7. **证据回流**：在 `v2/docs/T2-WINDOWS-EVIDENCE-2026-08-18.md` 追加
   「V2.4.0 最终候选验收（2026-08-19）」小节，E96 起编号；分支
   `codex/v240-final-evidence` 提交推送开 PR，标题
   「docs: V2.4.0 最终候选真机验收证据」。
8. **汇报**：各步 pass/fail + transcript 路径。全过即可放行打 v2.4.0 标签。

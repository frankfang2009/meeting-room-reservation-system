# V2.4.1 Windows 修复后复验证据（2026-08-28 R2，测试机代理执行）

> 依据《V2.4.1 Windows 修复后复验任务书（2026-08-28）》（`v2/docs/V241-WINDOWS-ACCEPTANCE-TASK.md`，
> 文档提交 `9ffd2de4`）执行。本文件只含脱敏 Markdown；截图与 transcript 保存在受控本地目录。
> 本轮（R2）与首轮（2026-08-28 上午，锚点 `2391ad66`，升级腿 FAIL/V241-B1）严格区分；
> 首轮证据见 `codex/v241-windows-acceptance-evidence-20260828` 分支
> `v2/docs/V241-WINDOWS-ACCEPTANCE-EVIDENCE-2026-08-28.md`，其结果不冒充本轮。

## 0. 结论速览

| 项 | 判定 |
|---|---|
| 第 1 节 锚点核对 | **PASS** |
| 第 4 节 静态门禁 | **PASS**（计数与任务书预期完全一致：backend 151、installer 115 + 仅 1 项 POSIX skip、cross-layer 32、frontend 173） |
| 第 5 节 候选重建 | **PASS**（双副本可复现 `MRV2_REPRODUCIBLE_BUILD=PASS`，首轮 SHA 已作废） |
| 第 5 节 T1 全新安装腿 | **PASS** |
| 第 5 节 T1 累计升级腿 | **PASS（已越过首轮 `run-cumulative-update` 失败点，备份序列摘要符合要求）** |
| 第 6 节 / 第 7 节 | 逐项见 §7/§8（自动腿覆盖项 PASS；真实双击 UAC/重启/第二设备/系统 DPI/断电演练为用户侧 SKIP，与首轮一致） |
| §7.8 工作交接页四状态 | **PASS**（新锚点后端 + 字节不变前端复验） |
| 放行状态 | `formal_external_release_allowed=false`（即使全部通过也仅报告"Windows 验收证据完成"） |

## 1. 第 1 节：SHA 与任务书锚点核对 —— PASS

```
git rev-parse HEAD                 → 9ffd2de4583d9464b7954e3feccdc2a4763fe908（复验任务书文档提交）
git rev-parse HEAD^                → 095675c0ead7aa3e3293b2cc5e9e10162101569b（= 复验任务书产品代码锚点）
git diff --name-only HEAD^..HEAD   → v2/docs/V241-WINDOWS-ACCEPTANCE-TASK.md（仅任务书）
```
`2391ad66..095675c` 产品差异：`v2app/backup.py`（V241-B1 新代码侧）、`update_core.py`（V241-B1 更新器侧）、`build_macos_package.py`（V241-B2）、两个测试文件（B1 回归 ×2 处 + O1 平台跳过）——**前端文件零变化**（与首轮 UI 证据同字节）。

## 2. 机器档案（脱敏，与首轮同机同配置）

Windows 11 家庭中文版 23H2（build 22631，x64）· 未加域 · 本地管理员账户（UAC 过滤令牌，EnableLUA=1）· Windows Defender + 火绒全程在位未关闭 · AppLocker 家庭版 N/A · pwsh 7.6.5 + Windows PowerShell 5.1.22621.4391 · Python 3.13.14（per-user，D 盘）+ ruff 0.12.12 + Node 22.17.1（zip 版，官方 SHASUMS 已核）· 工作文件全部位于 D 盘（TEMP 重定向）。提权经用户本人 UAC 批准 2 次（12:14 runner-r2、12:17 runner-r2b）。

## 3. 第 4 节：静态门禁 —— PASS（预期计数逐项命中）

| 门禁 | 任务书预期 | 实际 | 证据（evidence-20260828\） |
|---|---|---|---|
| `ruff check v2/backend v2/installer v2/tests` | 退出 0 | **rc=0**（All checks passed!） | `r2-gate-ruff.txt` |
| `unittest discover -s v2/backend/tests` | 151 项退出 0 | **151 tests, OK（skipped=5，均为既有 POSIX/macOS 跳过）**；点名 `test_backup_sequence_skips_foreign_sidecar_after_rollback_retry … ok` | `r2-gate-unittest-backend.txt` |
| `unittest discover -s v2/installer/tests` | 115 项退出 0，Windows 只允许 POSIX 执行位 1 项 skip | **115 tests, OK（skipped=1，且仅为 `test_staging_from_zip_restores_top_folder_and_exec_bits`）**；点名 `test_online_backup_reconciles_watermark_above_foreign_sidecars … ok`（"跨版本 sidecar 残留时，重试更新不再序列碰撞"）、`test_online_backup_reconcile_is_noop_without_backup_files … ok`；首轮失败的 `test_builds_reproducible_zip_with_edition_and_permissions`（V241-B2）本轮随套件 PASS | `r2-gate-unittest-installer.txt` |
| `unittest discover -s v2/tests` | 32 项退出 0 | **32 tests, OK** | `r2-gate-unittest-tests.txt` |
| `npm ci` + `npm run check`（Node 22.17.1） | 173 项 + 生产构建退出 0 | **rc=0 / rc=0（173 pass / 0 fail，vite build 成功）** | `r2-gate-npm-ci.txt`、`r2-gate-npm-check.txt` |
| `git diff --check` | 无输出 | **无输出** | `r2-gate-gitdiffcheck.txt` |

首轮 6 个点名安装器测试（探针 Windows PowerShell、fail-closed 清理/残留、健康停止三态）本轮随 115 项全绿继续 PASS。

## 4. 第 5 节：候选重建 —— PASS（首轮 SHA 作废）

`v2-reproducible-build.sh` 自新锚点双副本构建：**`MRV2_REPRODUCIBLE_BUILD=PASS`**（`r2-repro-build.log`）。文件名仍为 V2.4.0（`v2/VERSION` 切版契约），候选身份以下表为准；首轮 `950f8979…`/`6d6d6505…` 未复用。

| 产物 | 字节数 | SHA-256 |
|---|---|---|
| 会议室预约系统-V2.4.0-安装包.zip | 12,109,412 | `8c30891dbbd4e398d3d15d07c669b6dbeff59a87e05806a88a4a581ae9893b1b` |
| 会议室预约系统-V2.4.0-累计升级包.zip | 23,844,436 | `ca30d7d4e8144edf6d499dff287601eec61e525a2f3f45eda6bde54599053766` |
| （升级腿基线）会议室预约系统-V2.1.0-安装包.zip | 12,062,260 | `55a4db9861d682250204ee4b3044216a098de3fea84649b40c8e1fb2423075f5`（与冻结记录一致） |

## 5. 第 5 节 T1 全新安装腿 —— PASS

`v2-windows-acceptance.ps1 -CandidateZip <8c30891d…> -WorkRoot D:\mrv2-v241\work\fresh-r2`（新的不存在的 WorkRoot；前置清理复核零残留后运行；12:14–12:17，transcript `31-fresh-r2-transcript.log`，rc=0）：

- `MRV2_T1=PASS`
- `data/backups/logs` 三项 `STANDARD_USER_ACL:<name>:directory=PASS;file=PASS`
- 探针进程为脚本硬编码的 `%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe`（单测继续锁定）；本机 LocalAccounts×PS7 缺陷沿用 5.1 委托垫片（仅测试基建，见首轮 V241-O2/V241-O8）
- 成功后无 `MRV2Acl*` 账号、无 `standard-user-read-probe.txt`、无 `standard-user-acl-probe` 目录（腿内断言 + 终清理复核 MRV2Acl=0）
- 8080 被占拒绝启动且不杀占用者、损坏 fail-closed、SYSTEM 双任务 + HKLM + LocalSubnet 防火墙注册等步骤全过

## 6. 第 5 节 T1 累计升级腿 —— PASS（越过首轮失败点）

`v2-windows-upgrade-acceptance.ps1 -BaselineZip <冻结V2.1.0> -UpdateZip <ca30d7d4…> -WorkRoot D:\mrv2-v241\work\upgrade-r2b`（新的不存在的 WorkRoot；12:17–12:27，transcript `36-upgrade-r2b-transcript.log`，**rc=0**）。

逐步：preflight → install-baseline（V2.1.0 BAT RC_0）→ first-setup-and-business-data → wait-backup-catch-up（幂等 no-op）→ prepare-cumulative-update → **health-failure-rollback**（故障注入→健康失败→fail-closed 回滚）→ **standard-user-private-roots-after-rollback**：`data/backups/logs` 三项 `list=PASS;read=PASS` → **run-cumulative-update：成功，已越过首轮失败点** → verify-version-and-identity → verify-service-and-data（业务数据/会话/笔录室升级存活）→ dacl-boundaries-after-upgrade → **`MRV2_T1U=PASS`**。

**脱敏备份序列摘要（任务书 §5 要求，`33-post-upgrade-snapshot-r2.log`）**：

| 备份文件 | 字节数 | 说明 |
|---|---|---|
| reservation-v2-backup-00000001.db | 135,168 | 基线水位内既有备份（未触碰） |
| reservation-v2-backup-00000002.db | 135,168 | 基线水位内既有备份（未触碰） |
| reservation-v2-backup-00000003.db | **155,648** | **跨版本 sidecar（健康失败尝试的新版运行时所写）：原样保留，未被覆盖** |
| reservation-v2-backup-00000004.db | 135,168 | **重试更新的更新前在线备份：落位空闲序列 4，未覆盖任何既有文件** |

升级成功后 `backup-status.json`：`{"detail":"idempotent_noop","schema":1,"sequence":null,"status":"current"}`；现场库升级后为 155,648B（schema4）。与首轮同点对比：首轮该步骤因序列冲突 `RuntimeError` fail-closed（rc=1），本轮修复后重试直接成功——**V241-B1 修复经实机验证生效**。

## 7. 第 6 节：普通用户 / UAC / 安装根 —— 逐项

| 条目 | 判定 | 依据 |
|---|---|---|
| §6.1 真实双击 BAT 的 UAC 取消/接受 | **SKIP（需用户本人）** | 本轮提权 2 次 UAC 均由用户本人批准；无真实双击场景（T2 E67/E73 历史记录可参考） |
| §6.2 安装根精确 + 绑定可信根 | **PASS（正向）/ SKIP（篡改负面用例）** | 双腿安装根与注册表断言通过；负面用例未执行（单测锁定），与首轮一致 |
| §6.3 标准用户 RX app/runtime、私有目录不可读 | **PASS** | 全新腿 ACL_SUMMARY + 升级腿回滚后与升级后 DACL 步（`dacl-boundaries-after-upgrade` PASS：runtime 仅 Users RX，私有根无 Users ACE） |
| §6.4 维护入口按需 UAC、不可绕过 | **PASS（行为证据）+ SKIP（真实双击）** | 同首轮 |
| §6.5 8080 被占拒绝且不杀占用 | **PASS** | 全新腿 port-conflict 步 |
| §6.6 SmartScreen/Defender/EDR/AppLocker/GPO | **部分** | 全程未关闭（productState 记录；EnableLUA=1；AppLocker N/A）；SmartScreen 真实弹窗需用户双击 |

## 8. 第 7 节：运行 / 网络 / 恢复 —— 逐项

| 条目 | 判定 | 依据 |
|---|---|---|
| §7.1 首启仅回环→设置后 LAN | **PASS** | 双腿 loopback-health→first-setup→LAN 重启（install_id 稳定） |
| §7.2 防火墙仅 TCP8080/Domain+Private/LocalSubnet | **PASS** | 双腿 system-registration 断言；终清理复核 fw=0 |
| §7.3 第二台设备 | **SKIP（无第二设备）** | 公开大屏白名单脱敏由腿内 public-display 步 PASS |
| §7.4 真实重启后恢复 | **SKIP（需用户重启）** | 主任务 Boot 触发/SYSTEM/Highest 由腿内断言核验 |
| §7.5 每日 02:00/补备份/30 份轮转/无 WAL 残留 | **PASS（触发与补跑）/ SKIP（30 份实跑）** | catch-up 幂等 no-op；手动备份无 WAL/SHM/journal/.part-* 伴随 |
| §7.6 预约→备份→损坏→恢复闭环 | **PASS（T1 层）+ SKIP（真实 UAC 交互恢复）** | fail-closed-corruption + 升级腿回滚/重试数据保持 |
| §7.7 断电演练 | **SKIP（未获批准）** | 不得模拟成 PASS |
| §7.8 分辨率/缩放/交接页四状态 | **PASS（R2 复验，见 §9）** | — |

## 9. §7.8 工作交接页四状态 R2 复验 —— PASS

方法学：新锚点后端源码直跑（Python 3.13.14，端口 18080，独立数据目录，install_id `e41833fd…`）+ 同锚点 `npm run build` 生产前端（**前端文件相对首轮零变化**，首轮全视口矩阵证据继续有效，本轮复做四状态断言并留新截图）。合成数据：3 账号（甲/乙/丙）+ 5 条明日预约（R2-H-001…005）+ 定向交接。

| 状态 | 断言结果 | 视口 |
|---|---|---|
| 全空 | 无分组壳/零计数/概览带；陶土色圆形图标 + 「暂无工作交接」+ 说明逐字一致；徽标空；无横向滚动 | 1440×900（截图 `r2-empty-1440x900.png`） |
| 仅待我确认 | 「我发起的」不渲染；动作顺序 查看预约/不接受/接受交接；徽标=3 | 1440×900（`r2-incoming-only-1440x900.png`） |
| 仅我发起 | 「待我确认」不渲染；徽标**空**；动作顺序 查看预约/处理中/撤回申请；「处理中」`<span>` 非交互，`rgb(164,82,61)`/`rgb(250,237,231)` 陶土色 | 683×480(150%)（`r2-outgoing-only-683x480-150pct.png`），无裁切 |
| 两者并存 | 「待我确认」在上；四行动作列左缘 891px 对齐；徽标=1；无横向滚动 | 1280×720（`r2-both-1280x720.png`） |

语义复验（未开始预约）：查看预约抽屉开/关正常返回；撤回（甲 R1）行即时消失计数更新；接受（乙 R3）toast「已接受，预约已转入您名下」且所有权 API 终验转移；拒绝（乙 R4）toast「已拒绝，预约仍归原预约者」归属不变；终态归属 001→甲、002→乙（待确认）、003→乙、004→甲、005→乙 全部符合语义。

## 10. 执行过程记录（执行者侧，如实）

1. runner-r2（12:14 UAC）：30 清理 rc=0 → 31 全新腿 rc=0 → 32 升级腿在 preflight 失败（"previous install still present"）——**执行者队列顺序失误**：首轮流程两腿之间有清理步，本轮漏排；升级腿未触达任何产品步骤。33 快照、34 终清理随后执行并把机器还原干净（34 final verify 全零）。失败 transcript 存 `32-upgrade-r2-transcript.preflight-dirty.log`。
2. runner-r2b（12:17 UAC）：全新 WorkRoot `upgrade-r2b` 重跑升级腿 → **PASS**；33 快照（备份序列摘要）；34 终清理零残留。重试仅此一次，未反复覆盖现场。
3. 首轮遗留的卡死 runner 进程按 4 小时上限自行退出，与本轮无竞争。

## 11. 系统对象与探针残留清单（终态 `34-final-cleanup-r2.log`）

计划任务=0 · 防火墙=0 · 注册表=False · 安装根=False · 8080=0 · `MRV2Acl*=0`；无 `.update-*`/临时程序树/canary/维护锁残留。UI 复验实例（18080）已停止，其数据目录为 D 盘独立合成数据目录。

## 12. 需用户本人操作 / 未执行清单

1. §6.1 真实双击安装 BAT 的 UAC 取消与接受（含 SmartScreen 真实弹窗）。
2. §7.3 第二台局域网设备登录与公开大屏目检。
3. §7.4 真实重启后主任务/健康/每日备份恢复复核。
4. §7.8 真实系统 DPI 缩放抽查与 DevTools console 抽查（本轮以 CSS 视口模拟 + DOM/服务端近似，方法与首轮一致）。
5. §7.7 断电演练（若批准；升级腿重试路径现已实机 PASS，断电重试同包具备执行条件）。

## 13. 结果判定

- 第 1 节 PASS；第 4 节 PASS（计数逐项命中，无任何 FAIL/ERROR）；第 5 节候选重建 PASS；全新腿 PASS；**升级腿 PASS（越过首轮失败点，备份序列摘要达标）**；第 6–7 节自动覆盖项 PASS、用户侧项如实 SKIP；§7.8 PASS。
- 与首轮差异的唯一产品变量为 V241-B1/B2/O1 修复合入；首轮放行阻断项（V241-B1）已在**本分支、本锚点、本轮候选包**上实机解除。
- `formal_external_release_allowed=false`。本轮未修改产品代码、未创建 PR/合并、未打标签、未发布/签名/分发；未关闭 UAC/SmartScreen/Defender/EDR/防火墙；仅使用合成测试数据。

# V2.2.1 发布门禁

状态说明：`[x]` 只表示下方“本轮可复跑证据”已经通过；`[ ]` 表示仍需 CI、
Windows 实机或人工验收。自动化通过不等于客户环境通过。只有全部正式外发必需项
完成后，才可把候选清单中的 `formal_external_release_allowed` 改为 `true`。
自 V2.2.1 起 `formal_external_release_allowed` 按交付物分类适用：Windows 安装包与
累计升级包继续适用上述全部门禁；macOS 自托管版适用本文件新增的 macOS 门禁
（见"V2.2.1 macOS 自托管版证据"一节），其正式分发渠道是 GitHub Release。

仓库公开仅表示 Apache-2.0 源代码可访问，不代表任何 GitHub Actions 产物或未签名
候选包可正式外发。公开仓库不得托管已撤销的 V1 可执行附件；Windows 候选不得自动
发布，macOS 自托管版只能以带 SHA-256 发布说明的 GitHub Release 形式发布。

公开仓库自动化分层固定如下：`.github/workflows/ci.yml` 对每个 PR 和 main 运行仓库策略、
V2 Linux、V2 Windows 与 V2 macOS 门禁，但不制作或上传安装包；`v2-windows-acceptance.yml` 以相同
触发面运行 T1 Windows 真实安装验收，只在 runner 内部构建一次性测试候选（与正式候选
同名同源、规范命名），不上传、不保留任何可分发制品；`release-candidate.yml` 仅允许从
main 手动运行或由与 `v2/VERSION` 一致的 `v2.*` 标签触发，同一提交构建全新安装与
累计升级两套候选、macOS 便携包/DMG 候选及各自侧车，只保留 7 天且不自动创建
GitHub Release；V1 `windows-upgrade.yml` 只在 V1 路径变化或手动触发时运行。

## V2.2.1 macOS 自托管版证据（2026-08-17）

| 编号 | 命令或证据 | 实际结果 |
| --- | --- | --- |
| E47 | 本机（Apple Silicon，macOS）`v2-macos-acceptance.sh` 从候选 DMG 出发的 8 步真实验收 | 挂载 DMG → 从 `/Volumes` 直接启动被拒绝并提示拖出 → 拖出复制 → 启动 → `/healthz` 首启仅回环且未设置 → 首次设置事务 201 → LAN 监听切换且回环可见 install_id → 管理员登录与 bootstrap 版本读取 → 180 秒内首份 catch-up 备份落盘且无 WAL/SHM/journal 伴随 → `停止.command` 干净退出且 service.pid 清理、端口关闭；`data/install.json` 与 `install_id` 自动生成且 `setup_complete` 与数据库一致 |
| E48 | 本机两次独立 `build_macos_package` + `cmp` | 便携包 zip 两次构建逐字节一致（1138 个条目）；zip 反向验证条目、权限（.command 与 runtime/bin 0755）与内容全对齐；`latest-macos.json`、`.sha256.txt`、manifest、SBOM、第三方声明、runtime provenance 侧车齐全；包内无 data/backups/logs、无 `node_modules`、无合成标记 |
| E49 | 本机 `build_runtime_macos` + 冻结解释器冒烟 | python-build-standalone `cpython-3.13.14+20260718` tarball SHA-256 `dca7c3ba…770` 锁定校验通过；裁剪 pip/pydoc/idle/pkgconfig/share/include 后 runtime 94 MB，bin 仅剩 python/python3/python3.13（物化副本、无符号链接）；冻结解释器 `import flask(3.1.3), waitress(3.0.2)` 成功 |
| E50 | 版本检查契约测试（backend `tests.test_update_check`） | 17 项通过：严格 schema、版本比较、24 小时限频、60 秒手动限频、畸形 JSON/超体积/超时/离线安静降级、员工与未登录 403、禁用部署 403、sidecar 损坏自愈、macOS 身份引导幂等与半状态拒绝 |
| E51 | CI `candidate-macos`（待打 `v2.2.1` 标签后以标签 run 为准补记） | macos runner 上双构建字节一致 → DMG 生成与内容反向校验 → 与 E47 同一脚本的 8 步真实验收 → 候选 artifact 上传（7 天保留） |

macOS 自托管版"稳定"门禁 = E47–E51 全绿 + 团队 Apple Silicon 真机抽验 +
GitHub Release 发布说明附全部 SHA-256。本机证据（E47–E50）已于 2026-08-17
在 Apple Silicon Mac 上复跑通过。

## V2.2.0 当前本地证据（2026-08-17）

| 编号 | 命令或证据 | 实际结果 |
| --- | --- | --- |
| E38 | `v2/scripts/check.sh` | Ruff、compileall、`git diff --check` 通过；backend 94/94、installer 78/78、跨层 29/29、frontend 153/153 通过；真实 Waitress 交接通过；Vite 6.4.3 生产构建成功（CSS 139.88 kB，JS 466.87 kB） |
| E39 | 隔离数据目录、18080 服务、管理员与员工合成账号的真实 Chromium 验收 | 管理员 overall 为有效 3 场、已结束 3 场、4.5 小时、取消 1 场/25%，可切换李静个人 2 场/2.5 小时；员工只有本人面板且无人员选择器，伪造 overall/person 均为 `403 FORBIDDEN`；个人中心已简化为纯设置页，无任何统计入口 |
| E40 | 真实 Chromium 下载两种范围 CSV，并以解析器反读 | 管理员选择李静与员工本人两次下载均为 3 条、15 列，仅含 `QA-EMP-001/002/CANCEL`；UTF-8 BOM、中文表头、文件名和下载完成提示正确；日常正向流程控制台 0 error/0 warning，权限负向测试仅产生两条预期 403 资源错误 |
| E41 | 1440×900 与 1024×720 真实 Chromium 尺寸测量及截图复核 | 两档均为 `document/body/main scrollWidth = clientWidth`；筛选区、三项主指标、取消护栏、趋势、分布、热力图与导出区无文档级横向溢出；1024 档按既定桌面规则有序换行 |
| E42 | 累计升级事务故障注入与包构建测试 | 支持矩阵仅为 V2.1.0；在线备份后先记录并停服再快照完整 data；程序替换失败回滚；身份三文件部分提交可由同包恢复；目标版本提交后的新数据不回滚；确定性更新包、反向加载、篡改拒绝、零参数 BAT 和无 mutable data 均通过 |
| E45 | T1 层 Windows 真实升级验收（PR CI run 32022081677，2026-08-17） | windows-latest 上用冻结 V2.1.0 基线真实 BAT 安装 → V2.2.0 累计升级包真实 BAT 升级一次通过（7m04s）：版本三处一致、install_id 不变、预约/登录会话/房间全部保留、update-receipt complete、无 `.update-*` 残留目录；升级后 app/runtime 树 Users 只读执行、data/backups/logs 无 Users ACE（替换后受保护 DACL 已重固化） |
| E46 | `release-candidate.yml` workflow_dispatch 自 main（run 32022795403，2026-08-17，合并提交 f4104fb8） | 双交付物两次独立构建逐字节一致（`MRV2_REPRODUCIBLE_BUILD=PASS`）：安装包候选 SHA-256 `4fb41974a7c76ac483e68ce69e30356fbb9b401ac20411e621276ce70452f389`，累计升级包候选 SHA-256 `d28873fa4b1e1b6ab3d79d7401e4098cdef831fabad6919660fe2b5f024b055c`；Windows 零参数安装门禁与累计升级包门禁均 PASS |

E43/E44（T1 Windows 真实验收发现的两个备份缺陷修复）已随 v2.1.0 标签基线合入，
记录于下方历史 V2.1.0 证据表。PR #21 已合并（f4104fb8），E38–E42 本地证据已由
合并前 CI 复核；E45/E46 为合并后 main 上的 CI 证据。若后续打 `v2.2.0` 标签，
同源可复现构建应得到与 E46 相同的字节与 SHA（以标签触发的正式 run 为准再行核对）。
T2 层 Windows 实机证据（UAC/SmartScreen/真实重启/局域网第二设备/签名）仍待补，
`formal_external_release_allowed` 继续为 `false`。

## 历史 V2.1.0 增量证据（2026-08-14 至 2026-08-15）

| 编号 | 命令或证据 | 实际结果 |
| --- | --- | --- |
| E10 | `v2/scripts/check.sh` | Ruff 和编译检查通过；backend 64/64、installer 60/60、跨层 18/18、frontend 107/107 通过；Vite 6.4.3 生产构建成功（CSS 124.09 kB，JS 425.74 kB） |
| E11 | 对隔离模拟数据库连续创建 3 份备份并以 `keep_count=2` 轮转；在旧备份旁注入伪造 `-wal` / `-shm` / `.part-wal` | 只保留序列 2、3 的 `.db + .json`；伴随文件和隐藏临时文件全部清理；两份可恢复备份均为 `journal_mode=delete` |
| E12 | 使用隔离 18080 服务和模拟账号做真实浏览器回归 | 错误登录后焦点回到密码框；并发占用后草稿跨页保留，选择新时段必须确认原/新时段；成功提示离开日历即清除 |
| E13 | F01 已取消预约详情读取边界；`v2/scripts/check.sh` | 普通员工可读他人 active 详情，读他人 cancelled 稳定返回 `403 FORBIDDEN`；owner/admin 可读 cancelled；全量门禁通过 |
| E14 | F02 笔录室轮询的空闲会话语义；`v2/scripts/check.sh` | 周期性 `GET /api/v1/rooms` 不更新 `_last_active_at`，主动日历 GET 仍更新；全量门禁通过 |
| E15 | F03 备份失败与审计双重故障；`v2/scripts/check.sh` | 失败审计写入异常仅记日志，对管理员仍稳定返回 `500 BACKUP_FAILED`；全量门禁通过 |
| E16 | F04 槽位唯一约束竞争兜底；`v2/scripts/check.sh` | create/update 预检漏过但槽位写入触发唯一约束时均返回 `409 SLOT_CONFLICT` 与最小冲突投影；全量门禁通过 |
| E17 | F05 恢复码回环边界；`v2/scripts/check.sh` | recovery 状态下回环 `/healthz` 可见 `install_id/recovery_code`，局域网来源不返回两字段；全量门禁通过 |
| E18 | F06 长页面键盘滚动；源码测试、真实浏览器与 `v2/scripts/check.sh` | 所有 `.main-canvas` 可聚焦且有可见焦点/滚动条，PageDown 能推动长页滚动；冻结样式 SHA 已同步；全量门禁通过 |
| E19 | F07 会话过期草稿保护；纯函数、源码、真实浏览器与 `v2/scripts/check.sh` | 草稿字段按用户 ID 写入 `sessionStorage`，同账号重登后恢复并提示，异账号不可见，主动退出清除；全量门禁通过 |
| E20 | F08 日历方向键坐标导航；纯函数、真实浏览器与 `v2/scripts/check.sh` | 混合禁用格与预约跨时段占位下，上下键保持笔录室列、左右键保持时段行；全量门禁通过 |
| E21 | F09 时长内部停止点；源码测试、真实浏览器截图与 `v2/scripts/check.sh` | 仅显示 60/90/120/150 四点，并随当前时长和动态上限呈现 reached/available/unavailable；全量门禁通过 |
| E22 | F10 日历换日加载隔离；源码测试、延迟响应真实浏览器与 `v2/scripts/check.sh` | 换日后旧预约不会出现在新日期标题下；加载期间保留房间头、时间轴与安静骨架条；全量门禁通过 |
| E23 | F11 两类预约冲突重检；API/源码测试、真实浏览器与 `v2/scripts/check.sh` | 时段冲突呈现 busy 及仍占用/已可用结果；修订冲突补齐第三动作并按 ID 更新比较基线，同时保留草稿；全量门禁通过 |
| E24 | F12 历史跨月原地追加；API/源码测试、真实浏览器与 `v2/scripts/check.sh` | 更早月份按分隔线追加，不改变月份控件；跨月与同月游标分页复用全部筛选条件；全量门禁通过 |
| E25 | F13 Toast 语义色调；源码/样式测试、真实浏览器与 `v2/scripts/check.sh` | success/info/error 分别映射对勾/信息/警告图标；负面消息不再显示成功对勾，实时播报保留；全量门禁通过 |
| E26 | F14 非模态弹层关闭行为；源码测试、真实浏览器与 `v2/scripts/check.sh` | 预约/日历/历史/用户/审计弹层支持 Esc 与外部点击关闭；Esc 和弹层内完成动作返回触发按钮，外部点击保留新目标焦点；全量门禁通过 |
| E27 | F15 紧凑操作命中区；样式审计、1024×720 真实浏览器与 `v2/scripts/check.sh` | 抽屉返回、提醒确认、地址复制、删除笔录室均不小于 44×44px；新建用户外层原已为 46px，表头与校验摘要不是触控目标；冻结样式 SHA 已同步；全量门禁通过 |
| E28 | F16 三房间日历窄档适配；样式测试、1024×720 截图与 `v2/scripts/check.sh` | 三房间日历由 852/860 的 8px 内部溢出收敛为 852/852，文档宽度保持 1024/1024；更多房间仍可在日历容器内滚动；冻结样式 SHA 已同步；全量门禁通过 |
| E29 | F17 九项低频 UX 小修；纯函数/源码/样式测试、隔离真实浏览器与 `v2/scripts/check.sh` | toast/提醒随抽屉 inert；历史月份限 12 个月且房间/标签为 radio；日历限业务日 ±2 年；个人设置姓名必填并有 busy；事件读取可重试；取消徽章对比度 7.02:1；开始时间按槽对齐；无可用房间给出明确提示；冻结样式 SHA 已同步；全量门禁通过 |
| E30 | F18 API 契约缺口；跨层路由/字段断言与 `v2/scripts/check.sh` | 补齐本人 upcoming、房间删除影响/最多 50 条阻断投影，以及三类只读 integration scope、响应字段、过期与错误码语义；全量门禁通过 |
| E31 | F19 用户裁决；源码/契约断言与 `v2/scripts/check.sh` | 保留预约状态徽章；960/720/620px 仅定义为防御性桌面窗口适配；过期层明确限定当前标签页、预约草稿和同账号恢复；全量门禁通过 |
| E32 | `v2/scripts/check.sh`（V2.1.0 定制收口） | Ruff、compileall、`git diff --check` 通过；backend 79/79、installer 62/62、跨层 20/20、frontend 137/137 通过；Vite 6.4.3 生产构建成功（CSS 129.75 kB，JS 448.43 kB） |
| E33 | schema v1→v2 新库、就地迁移、故障注入和备份恢复自动化 | 新鲜安装直接得到 schema v2；v1 业务数据与 C2/C3/C4/C5 新列在同一事务迁移；中途失败全部回滚；受验证 v1 备份恢复后迁移，迁移失败恢复原现场 |
| E34 | 隔离数据目录、18080 服务与真实 Chromium 浏览器完整回归 | 完成首次设置和 3 笔录室；工作时间修改产生审计；隐藏房间不进入公开大屏；默认标签、60 分钟提醒和个人模板重新进入后仍保存；创建合成预约并复制提醒，剪贴板只含姓名、日期、开始时间和房间，不含案号、事项、备注；控制台 0 错误/0 警告，业务请求均为 200/201 |
| E35 | V2.1.0 版本、安装器与发布契约一致性检查 | `VERSION`、后端产品版本、前端 package/lock、安装启动器与候选 ZIP/payload 名称、SBOM 元数据、产品/API/发布文档统一为 V2.1.0；Linux 可复现构建和 Windows 候选门禁均从 `v2/VERSION` 读取当前名称；旧 V2.0.0 候选与更新基线历史证据未冒充当前发布证据 |
| E36 | 本轮界面跟进的源码/样式回归与 1440×900、1024×720 真实 Chromium 验收 | 工作时间与最近备份均暴露为整行可点击按钮并保留焦点返回；抽屉末个输入与满宽保存操作无重叠；个人中心页头、3 项概览和 4 项数据的布局边界无交叉，两个尺寸下 `scrollWidth = clientWidth`；干净会话控制台 0 错误/0 警告 |
| E37 | 长个人标签预约表单回归、真实 Chromium 边界测量与 `v2/scripts/check.sh` | “本人复核 01”“本人加急 01”等长名称保留完整按钮无障碍名称和悬停文案，视觉文字在各自按钮内省略；四个标签仍保持单行，标签按钮与相邻按钮、备注区域无交叉；全量门禁通过 |
| E43 | T1 Windows 真实安装验收发现备份管道 fsync 只读句柄缺陷；源码修复 + POSIX fcntl 回归测试 + `v2/scripts/check.sh` | 真实 Windows 服务的备份补跑与人工备份此前必然以 `OSError [Errno 9]` 失败（POSIX 单测允许只读 fsync 故未暴露）；`create_backup` 改为读写句柄落盘，新增回归测试锁定备份管道所有 fsync 均为可写句柄；Windows CI 验收见 `v2-windows-acceptance.yml` |
| E44 | T1 Windows 真实安装验收发现备份控制台报告破坏成功路径；源码修复 + pythonw/cp1252 仿真回归测试 + `v2/scripts/check.sh` | fsync 修复后备份文件已成功落盘，但补跑 worker 在 cp1252 重定向句柄上打印中文 `备份完成` 以 `UnicodeEncodeError` 失败并把状态写成 failed；pythonw 计划任务下 stdout 为 None 时 print 同样会失败。备份入口改为重配置 UTF-8 流与 None 安全报告，服务启动补跑子进程注入 `PYTHONUTF8=1`，回归测试仿真两种真实 Windows 上下文 |

本轮修复已改变生产源码，因此下方提交
`e953eff59541f2760e7525b73a2ae2ca54197003` 的 CI 与候选 SHA 只作历史证据；
当前增量提交仍需新 CI、可复现六件套、Windows 实机和签名验收。
`formal_external_release_allowed` 继续为 `false`。

## 历史可复跑证据（2026-08-10 至 2026-08-12）

| 编号 | 命令或证据 | 实际结果 |
| --- | --- | --- |
| E1 | `cd v2/backend && PYTHONWARNINGS=error::ResourceWarning .venv/bin/python -m unittest discover -s tests -v` | runner 64 通过；独立用例 64；继承重复 0；含无有效备份时系统总体告警、备份追平后恢复正常、当前用户已完成活动汇总及本人边界、笔录室删除阻断预约投影、历史状态服务端筛选与游标绑定；真实 Waitress handoff 通过且无 ResourceWarning |
| E2 | `v2/backend/.venv/bin/python -m unittest discover -s v2/installer/tests -v` | runner 60 通过；独立用例 60；继承重复 0 |
| E3 | `v2/backend/.venv/bin/python -m unittest discover -s v2/tests -v` | runner 18 通过；独立用例 18；继承重复 0；含个人活动服务端聚合/本人边界与损坏或迁移后虚拟环境重建契约 |
| E4 | `cd v2/frontend && npm run check` | ESLint 0 告警；104/104 通过；含抽屉首个可见字段自动聚焦/背景隔离、个人中心偏好设置单入口、个人中心真实活动概览/四项统计、热力图及按日下钻删除、账号隔离默认首页、服务端个人标签保存、滑块端点坐标、下一条同室预约动态上限、服务端时间下的过期时段禁用、日历刻度与当前时间线、紧凑日期导航、单/双/多房间布局、房间统计刷新、笔录室删除预检/阻断预约直达调整与返回、临近预约导航时钟徽标、取消记录中文状态/克制标签/正常与已取消筛选、真实 LAN 地址复制及 HTTP fallback、安全审计中文化/隐藏/未读提醒回归；全局 CSS 拆分契约和更新后的源码 SHA 通过；Vite 6.4.3 生产构建成功（CSS 121.80 kB，JS 422.69 kB） |
| E5 | `v2/backend/.venv/bin/python -m ruff check --no-cache v2/backend v2/installer v2/tests`、`v2/backend/.venv/bin/python -m compileall -q v2/backend v2/installer v2/tests`、`git diff --check` | 全部通过 |
| E6 | `.github/scripts/v2-reproducible-build.sh` 对历史提交对应源码执行两次独立 source/frontend/runtime/payload/package 构建并导出六件套 | 两个副本分别完成 `npm ci`，两次构建逐层一致并输出 `MRV2_REPRODUCIBLE_BUILD=PASS`；候选 ZIP SHA-256 为 `48d4b2d52feea9e790cbb069ce3bbafe450ff09f359888b2f1e7de8bf084177b`；六件套位于 ignored `v2/out/release-20260812/six-pack-3` |
| E7 | `cd v2/backend && .venv/bin/python -m unittest tests.test_hardening.SetupListenerProcessTests.test_real_waitress_setup_handoff_has_no_bad_file_descriptor -v` | 真实 Waitress 子进程收到 setup 201，连续两次确认 `bind_mode=lan`；无 `Bad file descriptor`/`Errno 9` |
| E8 | 历史提交中的 `.github/workflows/v2-baseline.yml` YAML 解析 | 当时的候选 workflow YAML 解析通过；当前工作流已按上方公开仓库自动化分层替代 |
| E9 | GitHub Actions 对提交 `e953eff59541f2760e7525b73a2ae2ca54197003` 的 [push run 31611162491](https://github.com/frankfang2009/meeting-room-reservation-system/actions/runs/31611162491) 与 [PR run 31611166614](https://github.com/frankfang2009/meeting-room-reservation-system/actions/runs/31611166614) | 两条运行均通过 Linux contracts、独立双构建、六件套上传及 Windows hosted candidate gate；下载 push artifact 后与本地六件套六个文件逐字节一致，ZIP SHA-256 同为 `48d4b2d52feea9e790cbb069ce3bbafe450ff09f359888b2f1e7de8bf084177b`；仅有 Actions Node 20 弃用提示，不影响本轮结论 |

此前本地候选 ZIP SHA-256
`e3c371e360beb4fa9242b353cec81236d17b36d5e57fd8d37b5faea9b976c6a8`
已因后续滑块、过期时段和本次 P1/P2 修复明确作废，不得分发。历史源码的本地六件套
位于 ignored `v2/out/release-20260812/six-pack-3`，ZIP SHA-256 为
`48d4b2d52feea9e790cbb069ce3bbafe450ff09f359888b2f1e7de8bf084177b`。
该候选已因 2026-08-14 生产源码变更明确撤销，不得分发或作为当前验收证据。

## 源码、契约与安全自动化

- [x] 角色统一为 `admin | employee`，共享日历可见性与本人/管理员写权限由前后端共同锁定。（E1、E3）
- [x] fresh-install、无 V1 读取/迁移/删除边界已写入产品契约并有相邻 V1 指纹测试。（E1、E2、E3）
- [x] 预约、占用槽和事件的 create/update/cancel 故障点均验证事务回滚。（E1）
- [x] 公开大屏由服务端白名单投影，前端遇到额外私有字段立即拒绝。（E1、E3、E4）
- [x] setup 响应完成后才由 Waitress 主循环关闭旧 listener；前端等待两个稳定 LAN 健康响应后自动跳转，失败可重试。（E1、E4、E7）
- [x] 重新登录在 session+bootstrap 完整校验前完全卸载旧工作台 DOM；失败仍保持隔离，用户或角色变化再通过 keyed remount 清除抽屉、token、审计、用户列表和草稿。（E4）
- [x] 管理员编辑他人预约时使用原 owner 的个人标签；缺少 owner 标签时不回退到管理员语义。（E1、E3、E4）
- [x] 日期、月份、带时区审计范围及绑定筛选的游标覆盖最小值、最大值、非法格式和反向范围；`9999-12` 稳定返回 422 JSON。（E1）
- [x] `purpose` 在前端、后端、API 契约和产品契约中统一为必填，服务端不注入默认事项。（E1、E3、E4）
- [x] API 413/404/405/500/503、requestId、登录限速、会话空闲/绝对过期、备份失败和恢复 fail-closed 自动化通过。（E1、E4）
- [x] 普通用户错误提示覆盖服务未启动、非 JSON、权限不足、session 失效、数据库恢复、端口冲突及备份/恢复失败，详细异常只写日志。（E1、E3、E4）
- [x] 正式 Runtime 钉死 CPython 3.13.14 官方 `embed-amd64` URL/SHA、requirements lock 摘要和完整 runtime 树；伪 PE、替换身份和合成 fixture 不能进入正式 `Bundle.load`。（E2、E3、E6、E9）
- [x] 制品级 CycloneDX SBOM 与许可证侧车生成逻辑覆盖 CPython/Python runtime 和 package-lock 中的前端生产依赖；开发构建工具不冒充运行时依赖，内外 manifest 绑定正式 lock 摘要。（E2、E3、E6、E9）
- [x] 候选构建脚本在两个独立源码目录分别构建 frontend、wheelhouse、runtime、payload、全新安装包与累计升级包，并比较每层结果；V2.2.0 当前脚本仍需在提交后取得新 CI 证据。（E38、E42）
- [x] Windows BAT 与候选门禁对 launcher 缺失、工具目录缺失、runtime Python 缺失/启动失败、产品拒绝和产品成功使用独立码或精确 marker。（E2、E3）
- [x] 未使用的 `getReservation/getUsers/getPreferences` 已删除；`getRooms` 作为预约变更后的房间统计刷新接口保留并有调用；变更记录、审计和提醒接口保留。（E4）
- [x] 个人中心简化为纯偏好设置页（移除“我的活动”统计页），个人统计唯一入口是数据中心本人视角；全应用只有一套 reports 统计口径。（E38、E39）
- [x] 数据中心对两种角色开放；员工 self-only，管理员 overall/self/person；范围先由服务端解析，员工扩权请求稳定 403。看板不含排名、绩效分数、案件量或利用率。（E38、E39、E41）
- [x] CSV 复用当前服务端范围和筛选，固定 15 列、UTF-8 BOM、CRLF、20,000 行上限、公式防护与无业务内容审计；真实下载与反读通过。（E38、E40）
- [x] 系统数据库可用但尚无有效备份时，总体状态与备份服务一致显示告警；完成备份并追平当前序列后才恢复“系统运行正常”。（E1）
- [x] 抽屉打开时首个可见字段立即获得焦点，主工作台同步进入 `inert`/`aria-hidden` 隔离；关闭后焦点返回原触发控件。个人中心偏好设置只保留标签页单入口。（E4；本轮本地真实浏览器已复核 1024×720 与 1440 宽度，Windows 实机仍按下方门禁执行）
- [x] 日历头部把日期跳转合并到日期标题，并将前一天/今天/后一天收为一个分段控件；正式 LAN 模式只在真实地址存在时提供复制操作，普通 HTTP 浏览器有安全 fallback。（E4）
- [x] 笔录室删除先由只读服务端预检分流；无未结束预约才确认删除，有阻断预约则返回最多 50 条管理员可操作投影及总数，前端列出并直达调整/详情且可返回原清单；DELETE 仍在事务中复检。（E1、E4）
- [x] V2.2.0 离线累计升级器、独立 runtime、零参数 BAT、确定性构建器、来源矩阵、停服快照、程序/数据回滚、部分身份提交恢复及提交后数据保护已有自动化；评审修复后升级在替换 `app`/`runtime` 后重新固化受保护 DACL，回滚/恢复路径同样固化，断电遗留旧程序临时目录自动清理；正式外发仍由 Windows 10/11、普通用户与签名门禁阻断。（E38、E42）
- [x] 项目本地 Python/Node 版本、隔离依赖、Ruff/ESLint 及一键 bootstrap/check 命令已经固定；V2 不再复用视觉原型的 `node_modules`。（E4、E5）
- [x] 全局 CSS 继续按功能域拆分；入口顺序和更新后的源码 SHA 由测试锁定，滑块、日历、房间和安全审计规则只落在对应功能域。（E4、E6、E9）

## 当前增量提交待新 CI，Windows 实机与签名仍未完成

- [ ] 对当前增量提交在 GitHub Actions 重新运行 Linux 双构建和 Windows hosted candidate gate，并归档日志及 artifact。
- [ ] 普通用户在 Windows 10 双击真实候选零参数 BAT，记录 UAC 接受与取消。
- [ ] 普通用户在 Windows 11 双击真实候选零参数 BAT，记录 UAC 接受与取消。
- [ ] 从最终冻结 V2.1.0 实机安装执行 V2.2.0 零参数累计升级，核对账号、预约、设置、备份、日志、install_id、原任务/防火墙/运行状态全部保持。
- [ ] 在 V2.1.0→V2.2.0 实机升级的程序替换、健康检查和身份提交阶段分别故障注入，验证提交前回滚与提交后不回滚新数据。
- [ ] 验证固定 `%ProgramFiles%\会议室预约系统V2` 安装、中文 UI、标准用户账户和 UAC 路径。
- [ ] 首次设置前第二台局域网电脑无法连接；设置完成后自动上线且第二台电脑可连接。
- [ ] private/domain 防火墙规则只开放 TCP 8080 到 `LocalSubnet`。
- [ ] 真实重启后 V2 专属主任务与每日备份任务正常启动、补跑且不重复并发。
- [ ] 实机端口冲突不杀未知进程、不换端口，并显示可操作提示。
- [ ] 标准用户不能修改程序，也不能读取数据库、secret、PID、日志或备份；维护入口按需 UAC。
- [ ] 删除、损坏或代际篡改已设置数据库后不初始化、不重开 setup，并完成真实备份恢复演练。
- [ ] 每日备份、超过一天启动补备份、30 份轮转、恢复故障回滚和跨重启恢复通过。
- [ ] SmartScreen、EDR、AppLocker、单位组策略及未签名包分发结果已记录；不得关闭安全软件规避。
- [ ] 1024×720、1280×720、1440×900、1920×1080 的 Windows 浏览器视觉回归通过。
- [ ] 键盘焦点、抽屉焦点陷阱、管理员→员工会话过期重登、电视大屏长时间刷新完成实机验收。
- [ ] Authenticode 签名、签名后验签与签名制品 SHA 归档完成；没有证书时正式外发门禁保持关闭。

## 发布物

- [ ] 为当前增量源码重新生成全新安装与累计升级两套 ZIP、SHA、manifest、SBOM、第三方许可证和 runtime provenance，并反向加载。
- [ ] 为当前增量源码完成两个独立本地构建，并在同一提交的正式 workflow 重复两套候选的整个构建链。
- [ ] 对当前增量源码的新候选重新确认不含数据库、secret、日志、备份、测试文件、`node_modules` 或源码映射。
- [x] 用户说明明确 V2 全新安装、V1 不迁移，以及恢复/日志/网络安全边界。（E2、E3）
- [ ] 将通过 CI 和 Windows 实机验收的最终同一 SHA 两套候选及侧车移入受控发布归档。
- [ ] macOS：以 `v2.2.1` 标签 run 的候选 zip/DMG/清单/侧车为正式发布物，GitHub Release 发布说明附 SHA-256 与未签名首启说明，发布后验证 `releases/latest/download/latest-macos.json` 重定向。
- [ ] 完整归档 Windows 10/11、标准用户、备份恢复、重启、第二台电脑和安全软件证据。

## 已撤销候选

2026-08-09 首版候选，以及 2026-08-10 早期本地 SHA
`aad1c9b39224db570d129af344cf3a1d12ec1677f159985154527fc282a48e1a` 和仍保留在
ignored `v2/out` 验证目录中的
`eedee50ece4a198c98e79867b7bf961a36d6432d7aa4a3954ae6f9e967f259d6`，以及 CSS 拆分后但本次 UI 修复前的
`e3c371e360beb4fa9242b353cec81236d17b36d5e57fd8d37b5faea9b976c6a8`，以及 2026-08-12 后续历史候选
`48d4b2d52feea9e790cbb069ce3bbafe450ff09f359888b2f1e7de8bf084177b`，均已明确撤销，
不得继续分发或用作验收证据。旧审核只保留问题追踪价值；本清单只承认上方各证据表
（E1–E44）明确列出的可复跑结果，且它仍不是正式外发批准。

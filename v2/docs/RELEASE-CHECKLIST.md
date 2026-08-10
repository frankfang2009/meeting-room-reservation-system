# V2.0.0 发布门禁

状态说明：`[x]` 只表示下方“本轮可复跑证据”已经通过；`[ ]` 表示仍需 CI、
Windows 实机或人工验收。自动化通过不等于客户环境通过。只有全部正式外发必需项
完成后，才可把候选清单中的 `formal_external_release_allowed` 改为 `true`。

## 本轮可复跑证据（2026-08-10）

| 编号 | 命令或证据 | 实际结果 |
| --- | --- | --- |
| E1 | `cd v2/backend && PYTHONWARNINGS=error::ResourceWarning .venv/bin/python -m unittest discover -s tests -v` | runner 62 通过；独立用例 62；继承重复 0；真实 Waitress handoff 通过且无 ResourceWarning |
| E2 | `v2/backend/.venv/bin/python -m unittest discover -s v2/installer/tests -v` | runner 60 通过；独立用例 60；继承重复 0 |
| E3 | `v2/backend/.venv/bin/python -m unittest discover -s v2/tests -v` | runner 17 通过；独立用例 17；继承重复 0；含损坏或迁移后虚拟环境重建契约 |
| E4 | `cd v2/frontend && npm run check` | ESLint 0 告警；74/74 通过；Vite 6.4.3 生产构建成功 |
| E5 | `v2/backend/.venv/bin/python -m ruff check v2/backend v2/installer v2/tests`、`python -m compileall -q v2/backend v2/installer v2/tests`、`git diff --check` | 全部通过 |
| E6 | `PYTHON_BIN=v2/backend/.venv/bin/python .github/scripts/v2-reproducible-build.sh "$PWD" /private/tmp/meeting-room-v2-final-repro-20260810-3 /private/tmp/meeting-room-v2-runtime-materials/python-3.13.14-embed-amd64.zip /private/tmp/meeting-room-v2-runtime-materials/wheels /private/tmp/meeting-room-v2-final-repro-20260810-3-export` | 两个独立源码目录各自 `npm ci`、构建 frontend/runtime/payload/六件套；逐层一致并输出 `MRV2_REPRODUCIBLE_BUILD=PASS`；SBOM 共 16 个组件，其中 4 个前端生产组件 |
| E7 | `cd v2/backend && python -m unittest tests.test_hardening.SetupListenerProcessTests.test_real_waitress_setup_handoff_has_no_bad_file_descriptor -v` | 真实 Waitress 子进程收到 setup 201，连续两次确认 `bind_mode=lan`；无 `Bad file descriptor`/`Errno 9` |
| E8 | `ruby -e 'require "yaml"; YAML.parse_file(".github/workflows/v2-baseline.yml")'` | workflow YAML 解析通过 |

本轮本地六件套位于 `/private/tmp/meeting-room-v2-final-repro-20260810-3-export`，
不是批准的外发归档。ZIP SHA-256 是
`e3c371e360beb4fa9242b353cec81236d17b36d5e57fd8d37b5faea9b976c6a8`；清单仍为
`formal_external_release_allowed=false`。任何后续生产源代码变化都必须作废该 SHA
并重跑 E1–E8。

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
- [x] 正式 Runtime 钉死 CPython 3.13.14 官方 `embed-amd64` URL/SHA、requirements lock 摘要和完整 runtime 树；伪 PE、替换身份和合成 fixture 不能进入正式 `Bundle.load`。（E2、E3、E6）
- [x] 制品级 CycloneDX SBOM 与许可证侧车同时覆盖 CPython/Python runtime 和 package-lock 中的前端生产依赖；开发构建工具不冒充运行时依赖，内外 manifest 绑定正式 lock 摘要。（E2、E3、E6）
- [x] CI 定义为两个独立源码目录分别构建 frontend、wheelhouse、runtime、payload 与六件套，并比较每层结果。（E3、E8）
- [x] Windows BAT 与候选门禁对 launcher 缺失、工具目录缺失、runtime Python 缺失/启动失败、产品拒绝和产品成功使用独立码或精确 marker。（E2、E3）
- [x] 未使用的 `getReservation/getRooms/getUsers/getPreferences` 已删除；变更记录、审计和提醒接口保留。（E4）
- [x] `update_core.py` 明确标记为不进入 V2.0.0 payload 的非生产未来基线，未宣称在线升级可用。（E2、E3）
- [x] 项目本地 Python/Node 版本、隔离依赖、Ruff/ESLint 及一键 bootstrap/check 命令已经固定；V2 不再复用视觉原型的 `node_modules`。（E4、E5）

## 尚未完成的 CI 与 Windows 实机

- [ ] 在 GitHub Actions 实际运行更新后的 Linux 双构建和 Windows hosted candidate gate，并归档日志及 artifact。
- [ ] 普通用户在 Windows 10 双击真实候选零参数 BAT，记录 UAC 接受与取消。
- [ ] 普通用户在 Windows 11 双击真实候选零参数 BAT，记录 UAC 接受与取消。
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

- [x] 当前本地候选 ZIP、SHA、manifest、SBOM、第三方许可证和 runtime provenance 六件套已成套生成并反向加载。（E2、E6）
- [x] 两个本地构建结果逐字节一致，且正式 workflow 会对独立源码目录重复整个构建链。（E6、E8）
- [x] 候选包不含数据库、secret、日志、备份、测试文件、`node_modules` 或源码映射。（E2、E3）
- [x] 用户说明明确 V2 全新安装、V1 不迁移，以及恢复/日志/网络安全边界。（E2、E3）
- [ ] 将通过 CI 和 Windows 实机验收的最终同一 SHA 六件套移入受控发布归档。
- [ ] 完整归档 Windows 10/11、标准用户、备份恢复、重启、第二台电脑和安全软件证据。

## 已撤销候选

2026-08-09 首版候选，以及 2026-08-10 早期本地 SHA
`aad1c9b39224db570d129af344cf3a1d12ec1677f159985154527fc282a48e1a` 和仍保留在
ignored `v2/out` 验证目录中的
`eedee50ece4a198c98e79867b7bf961a36d6432d7aa4a3954ae6f9e967f259d6`，均已明确撤销，
不得继续分发或用作验收证据。旧审核只保留问题追踪价值；本清单只承认上方
2026-08-10 的可复跑结果，
且它仍不是正式外发批准。

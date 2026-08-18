# V2 安装与累计更新基线

本目录实现 V2.2.3 全新安装、V2.1.0 起源离线累计升级与 macOS 自托管便携包，
不包含 V1 迁移、导入、目录搜索或自动删除。

## 先组装客户 payload

前端完成 `npm run build` 后，把正式后端与 `dist/client` 组装成不含现场数据的
客户目录骨架：

```bash
python3 -m v2.installer.assemble_payload \
  --backend-root /absolute/path/to/v2/backend \
  --frontend-dist /absolute/path/to/v2/frontend/dist/client \
  --frontend-lock /absolute/path/to/v2/frontend/package-lock.json \
  --output /absolute/path/to/payload-v2.2.1
```

组装器要求正式 `service.py`、其 `server.py` 运行依赖、`backup.py`、
`restore.py`、`requirements.txt`、`requirements-win-amd64.lock`、`v2app` 和前端
`index.html` 和 package-lock v3 全部存在，输出
必须不存在。生产布局固定为 `_程序文件/app/{service.py,server.py,backup.py,
restore.py,v2app,static}`，并从 package-lock 生成只含生产依赖的确定性前端组件证据。
它会加入客户顶层 ①启动、②备份、③设置开机启动、
④停止、⑤取消开机启动、⑥从备份恢复和使用说明。所有维护入口都先取得 UAC
管理员授权并交叉核对固定安装根、install_id、HKLM 登记和专属资源。④/⑤只
委托 `app/service.py --stop` 按 PID 身份安全停止；不会按端口或 python 进程名
结束未知进程。

## 组装冻结 Windows runtime

正式 runtime 不手工复制 site-packages。`build_runtime.py` 固定核验 CPython
3.13.14 官方 `python-3.13.14-embed-amd64.zip`，其 SHA-256 必须为
`90b4e5b9898b72d744650524bff92377c367f44bd5fbd09e3148656c080ad907`；再按
后端的 `requirements-win-amd64.lock` 逐个核验 11 个 Windows wheel，安全解压并
自动生成实际 CycloneDX SBOM、从 wheel/CPython 汇总的许可证说明和 provenance：

```bash
python3 -m v2.installer.build_runtime \
  --python-embed-zip /absolute/path/to/python-3.13.14-embed-amd64.zip \
  --wheelhouse /absolute/path/to/verified-wheels \
  --lock-file /absolute/path/to/v2/backend/requirements-win-amd64.lock \
  --output /absolute/path/to/runtime-v2.2.1
```

输出目录必须不存在。正式 `Bundle.load` 还要求 lock 摘要和包含 provenance 在内的
完整 runtime 树摘要与 `installer_core.py` 中经审核的批准值完全一致；测试 helper
只能通过私有测试入口构造 fixture，不能进入正式候选链。构建器拒绝上游 ZIP/wheel
哈希不符、lock 外组件、组件缺失、
wheel 文件冲突、路径穿越、链接、`.data` 特殊安装布局和许可证材料缺失。生成的
`python313._pth` 只包含 `python313.zip`、`.`、`Lib\site-packages`、`..\app`，
不会加载 `site`、用户 site 或裸父目录。

## 生产安装包

`build_package.py` 接收两个已经准备好的输入：

- `payload-root`：将安装到客户目录的程序文件，必须包含
  `_程序文件/app/service.py`；不得含 `data`、`backups`、`logs`、runtime、版本、
  产品代际或事务文件。
- `runtime-root`：冻结的 CPython 3.13 AMD64 embeddable runtime。除
  `python.exe`/`pythonw.exe` 外，必须有固定 `python313._pth`、带哈希依赖锁、
  CycloneDX SBOM、第三方许可证说明和绑定其哈希的 runtime provenance。
  正式目录应由上一步 `build_runtime.py` 生成；格式参考和人工审查提示见
  `supply_chain_templates/`。

示例：

```bash
python3 -m v2.installer.build_package \
  --payload-root /absolute/path/to/payload \
  --runtime-root /absolute/path/to/runtime \
  --output /absolute/path/to/会议室预约系统-V2.2.3-安装包.zip
```

构建器生成 ZIP、外部 SHA-256、发布清单、制品级 SBOM、第三方许可证说明和 runtime
来源侧车，已有任一输出一律拒绝覆盖。制品级 SBOM 与许可证侧车合并已验证的
CPython/Python 依赖及前端 package-lock 中的生产依赖；runtime provenance 仍与 ZIP 内
同名材料逐字节一致。所有侧车哈希、正式 package-lock 摘要都写入内外清单。payload、runtime、`install.py` 和
`installer_core.py` 均有文件级和树级 SHA-256 清单并反向加载验证。ZIP 顶层
只有零参数 `安装V2.2.3.bat`、安装说明和 `_V2安装工具`。SHA-256 只能证明与
已知摘要一致，不能代替代码签名、上游制品签名复核或可信发布渠道。
构建时还会要求 payload 中随正式后端交付的 `requirements-win-amd64.lock` 与
runtime 内 lock 逐字节一致，并验证 payload 内前端组件证据及 package-lock SHA，
防止程序、冻结解释器和前端生产依赖来自不同构建输入。

## 后端服务接缝

安装后的 `_程序文件/app/service.py` 必须遵守以下固定契约：

- 读取 `data/install.json`，并验证 `product_generation=2`、install_id 和端口；
- SQLite `app_meta.setup_complete` 是是否完成首次设置的唯一真源；文件镜像的
  `false` 不能把已完成数据库降级，服务会先原子向上修复镜像再按数据库真值绑定；
  文件镜像为 `true` 而数据库缺失、损坏或未设置时则进入回环恢复态；
- `setup_complete=false` 时只绑定 `127.0.0.1:8080`；
- 首次设置必须在一个数据库事务中写入首个管理员、至少一个笔录室、工作时间、
  `product_generation=2`、`schema_version=2` 和 `setup_complete=true`；升级预检仍接受
  可在服务启动前就地迁移的 schema v1 基线数据库；
- 数据库事务提交后原子镜像 `install.json.setup_complete=true`，再由 supervisor
  重启服务，才可绑定 `0.0.0.0:8080`；
- CLI 固定支持无参数启动、`--check` 和 `--stop`；`MEETING_ROOM_OPEN_BROWSER=1`
  时打开本机页面。`--stop` 必须同时核验 PID 文件身份令牌、可执行文件路径、
  service.py 路径和 install_id；不得按端口或进程名杀进程；
- 首次启动的 `/healthz` 必须返回：

```json
{
  "ok": true,
  "product_generation": 2,
  "install_id": "现场 install_id",
  "setup_complete": false,
  "bind_mode": "loopback",
  "port": 8080
}
```

## 提交与回滚边界

- 正式安装目录固定为 Windows Known Folder API 返回的
  `%ProgramFiles%\会议室预约系统V2`；只接受不存在或完全为空的目标。测试代码
  可以直接注入临时目录，但生产 CLI/BAT 不识别目录、确认或健康检查绕过变量。
- staging 创建后先设为仅 SYSTEM+Administrators 可访问，再写事务、日志、
  install_id 或 secret；payload 和 runtime 全部校验后才原子放到目标。
- 最终程序树普通 Users 仅可读取/执行；`data`、`backups`、`logs` 及其全部内容
  仅 SYSTEM+Administrators。安装器在提交前和启动后枚举全树复核 DACL。
- 服务任务和每日 02:00 备份任务都以 SYSTEM 运行并绑定 install_id；备份任务
  使用 `StartWhenAvailable` 补跑和 `IgnoreNew` 防重入。专属任务、防火墙规则先
  以禁用状态创建，`版本.txt` 最后写入。
- 写入版本前失败，只删除带本事务 ID 的新 V2 目录并恢复原空目录。
- 写入版本后失败，保留全部 V2 文件和可能产生的新数据，只允许修复。
- 安装器从不枚举、读取、迁移或删除任何 V1 业务目录。

## 离线累计升级

`build_update_package.py` 使用与新装包相同的已组装 payload 和冻结 runtime，
生成零参数 `升级到V2.2.0.bat`、`_V2更新工具`、SHA-256、manifest、
SBOM、许可说明和 runtime provenance。

```bash
python3 -m v2.installer.build_update_package \
  --payload-root /absolute/path/to/payload-v2.2.0 \
  --runtime-root /absolute/path/to/runtime-v2.2.0 \
  --output /absolute/path/to/会议室预约系统-V2.2.0-累计升级包.zip
```

首个支持矩阵只包含最终冻结的 V2.1.0。`update_core.py` 只从明确路径
或 V2 专属 HKLM 记录取得安装根，以 SQLite 交叉验证 generation、schema
和 setup 真值。它先执行在线备份，再快照 data 全树、旧 app 与 runtime；
只停止已验证属于当前 install_id 的任务和服务。在健康检查通过并
恢复原运行状态后，才最后写入版本、安装清单和回执。提交前失败自动
回滚；中断后用同一更新包重跑会校验事务身份并恢复或完成收尾。

这是本机离线 UAC 升级，不是网页、联网或自动更新。实机、局域网和
Authenticode 门禁完成前，内外 manifest 均保持
`formal_external_release_allowed=false`。

## 验证

```bash
python3 -m compileall -q v2/installer
python3 -m unittest discover -s v2/installer/tests -v
```

自动测试不能代替普通用户 Windows 10/11 的 UAC、SmartScreen/EDR、标准用户
DACL 负向测试、计划任务 02:00/断电补跑、备份恢复故障注入、防火墙、重启和
第二台局域网电脑验收。候选包完成这些验收前不得把发布清单中的正式外发开关
改为 true。

## macOS 自托管便携包

```bash
# 1) 冻结 arm64 runtime（python-build-standalone tarball 按 SHA-256 锁定）
python -m v2.installer.build_runtime_macos \
  --python-tarball /path/cpython-3.13.14-aarch64.tar.gz \
  --wheelhouse /path/macos-wheels \
  --output /absolute/path/to/runtime-macos

# 2) 组装可复现便携包 zip + latest-macos.json + 供应链侧车
python -m v2.installer.build_macos_package \
  --backend-root v2/backend \
  --frontend-dist v2/frontend/dist/client \
  --runtime-root /absolute/path/to/runtime-macos \
  --output-dir /absolute/path/to/output

# 3) 从已校验 zip 生成 DMG 并反向核对内容
python -m v2.installer.build_macos_dmg \
  --from-zip /absolute/path/to/output/会议室预约系统-V2.2.3-macOS-arm64.zip \
  --output /absolute/path/to/output/会议室预约系统-V2.2.3-macOS-arm64.dmg
```

zip 是字节级可复现事实源（CI 候选车道做两次独立构建 `cmp`）；DMG 只做内容级
校验。真实验收脚本：`.github/scripts/v2-macos-acceptance.sh`。

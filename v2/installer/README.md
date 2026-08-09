# V2 安装与累计更新基线

本目录只实现 V2.0.0 全新安装和未来 V2 累计更新的安全基础，不包含 V1
迁移、导入、目录搜索或自动删除。

## 先组装客户 payload

前端完成 `npm run build` 后，把正式后端与 `dist/client` 组装成不含现场数据的
客户目录骨架：

```bash
python3 -m v2.installer.assemble_payload \
  --backend-root /absolute/path/to/v2/backend \
  --frontend-dist /absolute/path/to/v2/frontend/dist/client \
  --output /absolute/path/to/payload-v2.0.0
```

组装器要求正式 `service.py`、其 `server.py` 运行依赖、`backup.py`、
`requirements.txt`、`v2app` 和前端
`index.html` 全部存在，输出必须不存在。它会加入客户顶层 ①启动、②备份、
③设置开机启动、④停止、⑤取消开机启动和使用说明。④/⑤只委托 `service.py
--stop` 按 PID 身份安全停止；不会按端口或 python 进程名结束未知进程。

## 生产安装包

`build_package.py` 接收两个已经准备好的输入：

- `payload-root`：将安装到客户目录的程序文件，必须包含
  `_程序文件/service.py`；不得含 `data`、`backups`、`logs`、runtime、版本、
  产品代际或事务文件。
- `runtime-root`：冻结的 64 位 Windows Python runtime，至少包含
  `python.exe` 和 `pythonw.exe`。

示例：

```bash
python3 -m v2.installer.build_package \
  --payload-root /absolute/path/to/payload \
  --runtime-root /absolute/path/to/runtime \
  --output /absolute/path/to/会议室预约系统-V2.0.0-安装包.zip
```

构建器生成 ZIP、外部 SHA-256 文本和外部发布清单，已有输出一律拒绝覆盖。
payload、runtime、`install.py` 和 `installer_core.py` 均有文件级和树级 SHA-256
清单并反向加载验证。ZIP 顶层只有零参数 `安装V2.0.0.bat`、安装说明和
`_V2安装工具`。SHA-256 只能证明与已知摘要一致，不能代替代码签名或可信渠道。

## 后端服务接缝

安装后的 `_程序文件/service.py` 必须遵守以下固定契约：

- 读取 `data/install.json`，并验证 `product_generation=2`、install_id 和端口；
- SQLite `app_meta.setup_complete` 是是否完成首次设置的唯一真源；文件镜像不一致
  时一律先按未设置处理，只绑定回环，并由服务从数据库安全修复镜像；
- `setup_complete=false` 时只绑定 `127.0.0.1:8080`；
- 首次设置必须在一个数据库事务中写入首个管理员、至少一个笔录室、工作时间、
  `product_generation=2`、`schema_version=1` 和 `setup_complete=true`；
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

- 只接受不存在或完全为空、由用户明确给出的本地固定盘目标。
- staging、payload 和 runtime 全部校验后才把目录原子放到目标。
- 专属任务和防火墙规则先以禁用状态创建，`版本.txt` 最后写入。
- 写入版本前失败，只删除带本事务 ID 的新 V2 目录并恢复原空目录。
- 写入版本后失败，保留全部 V2 文件和可能产生的新数据，只允许修复。
- 安装器从不枚举、读取、迁移或删除任何 V1 业务目录。

## 后续 V2 更新

`update_core.py` 是未来 V2.0.1+ 累计更新器的共同前置层：只从明确路径、环境变量
或 V2 专属 HKLM 记录取得安装根；严格拒绝 V1/未知数据库；以 SQLite 交叉验证
generation、schema、setup 状态和基本业务状态，不单信 install.json 镜像；禁止
更新负载携带现场可变文件；在写程序前复制并哈希整个 data 树。V2.0.0 本身不
交付尚未经过 V2.0.0 → V2.0.1 实机验证的正式更新入口。

## 验证

```bash
python3 -m compileall -q v2/installer
python3 -m unittest discover -s v2/installer/tests -v
```

自动测试不能代替普通用户 Windows 10/11 的 UAC、SmartScreen/EDR、计划任务、
防火墙、重启和第二台局域网电脑验收。

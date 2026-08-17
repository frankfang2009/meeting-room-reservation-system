# V2 development environment

V2 development uses an isolated Python 3.13.14 environment and a frontend-local Node.js
22.17.1 installation. Never point V2 `node_modules` at the visual prototype or reuse the V1
virtual environment.

## First-time bootstrap

From the repository root:

```bash
v2/scripts/bootstrap-dev.sh
```

The script installs a project-local uv-managed Python under `.tools/`, creates
`v2/backend/.venv`, installs the exact backend requirements, and performs `npm ci` in
`v2/frontend`. It refuses to overwrite a symbolic-link `node_modules` directory.

## Repeatable checks

```bash
v2/scripts/check.sh
```

Set `V2_PYTHON=/absolute/path/to/python` only for an intentional compatibility run. The default
must be `v2/backend/.venv/bin/python` and must report Python 3.13.14, Flask 3.1.3, and Waitress
3.0.2. The gate runs pinned Ruff and ESLint checks before the unit and production-build suites.
Python developer-only tools live in `v2/backend/requirements-dev.txt`; frontend build and lint
tools remain in `devDependencies` and do not enter the shipped production-dependency SBOM.

Generated builds, QA screenshots, candidate packages, databases, logs, backups, secrets, and
runtime trees belong under ignored locations such as `v2/out/`; they are not source files.
Visual audit summaries may be committed, while full screenshot runs are CI/local artifacts.

Automated checks do not replace Windows 10/11 ordinary-user installation, UAC, DACL, scheduled
task, firewall, reboot, backup/restore, LAN-client, signing, SmartScreen, or EDR acceptance.

## macOS 便携包（自托管版）

在 Apple Silicon Mac 上完成上述 bootstrap 后：

```bash
# 前端产物（如尚未构建）
cd v2/frontend && npm run build && cd ../..

# 冻结 runtime → 便携包 zip → DMG（详见 v2/installer/README.md）
python -m v2.installer.build_runtime_macos --python-tarball … --wheelhouse … --output /tmp/mrv2/runtime
python -m v2.installer.build_macos_package --backend-root v2/backend \
  --frontend-dist v2/frontend/dist/client --runtime-root /tmp/mrv2/runtime --output-dir /tmp/mrv2/pkg
python -m v2.installer.build_macos_dmg --from-zip "/tmp/mrv2/pkg/会议室预约系统-V$(cat v2/VERSION)-macOS-arm64.zip" \
  --output "/tmp/mrv2/会议室预约系统-V$(cat v2/VERSION)-macOS-arm64.dmg"

# 真实验收（DMG 挂载 → 拖出 → 启动 → 首次设置 → 备份 → 停止）
bash .github/scripts/v2-macos-acceptance.sh \
  "/tmp/mrv2/pkg/会议室预约系统-V$(cat v2/VERSION)-macOS-arm64.zip" \
  "/tmp/mrv2/会议室预约系统-V$(cat v2/VERSION)-macOS-arm64.dmg" /tmp/mrv2/accept
```

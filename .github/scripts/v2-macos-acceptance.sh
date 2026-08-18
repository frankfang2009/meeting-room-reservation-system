#!/usr/bin/env bash
# V2 macOS 自托管便携包真实验收：从 DMG 挂载开始，走完首次运行全流程。
# 用法：v2-macos-acceptance.sh <便携包ZIP> <DMG文件> <工作目录>
set -euo pipefail

ZIP_PATH="${1:?用法: v2-macos-acceptance.sh <便携包ZIP> <DMG> <工作目录>}"
DMG_PATH="${2:?缺少 DMG 路径}"
WORK_ROOT="${3:?缺少工作目录}"
PY="${PY:-python3}"
ADMIN_PASSWORD="mac-acceptance-pass-1"
PORT=8080

mkdir -p "$WORK_ROOT"
cd "$WORK_ROOT"

# DMG 双击安装体验：挂载 → 拖出（复制）→ 推出。
MOUNT_POINT="$WORK_ROOT/dmg-mount"
mkdir -p "$MOUNT_POINT"
hdiutil attach "$DMG_PATH" -nobrowse -readonly -mountpoint "$MOUNT_POINT" >/dev/null
restore_mount() { hdiutil detach "$MOUNT_POINT" -force >/dev/null 2>&1 || true; }
trap restore_mount EXIT

APP_FOLDER="$WORK_ROOT/会议室预约系统V2-macOS"
if [ -e "$APP_FOLDER" ]; then echo "FAIL: 拖出目标已存在"; exit 1; fi
cp -R "$MOUNT_POINT/会议室预约系统V2-macOS" "$APP_FOLDER"
if [ -e "$APP_FOLDER/data" ]; then echo "FAIL: 交付包含现场 data 目录"; exit 1; fi
restore_mount

# 卷守卫：直接从 DMG 挂载卷启动必须被拒绝（默认挂载在 /Volumes 下，
# 与真实用户双击 DMG 的体验一致）。
echo "[1/9] 卷守卫（/Volumes 挂载卷拒绝启动）"
GUARD_OUTPUT="$(hdiutil attach "$DMG_PATH" -nobrowse -readonly | tail -1 | cut -f3)"
GUARD_MOUNT="${GUARD_OUTPUT%/}"
[ -d "$GUARD_MOUNT" ] || { echo "FAIL: DMG 未挂载到 /Volumes：$GUARD_OUTPUT"; exit 1; }
GUARD_LOG="$(cd "$GUARD_MOUNT" && bash "$GUARD_MOUNT/会议室预约系统V2-macOS/启动.command" 2>&1 || true)"
hdiutil detach "$GUARD_MOUNT" -force >/dev/null 2>&1 || true
echo "$GUARD_LOG" | grep -q "拖" || { echo "FAIL: 挂载卷启动未被拒绝：$GUARD_LOG"; exit 1; }

# 启动（前台服务的守护进程化运行方式）。
echo "[2/9] 拖出后启动"
xattr -dr com.apple.quarantine "$APP_FOLDER" >/dev/null 2>&1 || true
bash "$APP_FOLDER/启动.command" > "$WORK_ROOT/service.out" 2>&1 &
SERVICE_PID=$!
cleanup_service() {
  kill "$SERVICE_PID" >/dev/null 2>&1 || true
  wait "$SERVICE_PID" 2>/dev/null || true
}
trap cleanup_service EXIT

wait_health() {
  local deadline=$(( $(date +%s) + $1 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl --silent --max-time 2 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}
wait_health 90 || { echo "FAIL: 服务未在 90 秒内就绪"; cat "$WORK_ROOT/service.out"; exit 1; }

echo "[3/9] 首启健康检查（仅回环、未设置）"
HEALTH="$(curl --silent http://127.0.0.1:$PORT/healthz)"
echo "$HEALTH" | grep -q '"ok": *true' || { echo "FAIL: healthz 未就绪：$HEALTH"; exit 1; }
echo "$HEALTH" | grep -q '"setup_complete": *false' || { echo "FAIL: 首启应处于未设置状态：$HEALTH"; exit 1; }
echo "$HEALTH" | grep -q '"bind_mode": *"loopback"' || { echo "FAIL: 首启应只绑回环：$HEALTH"; exit 1; }

echo "[4/9] 首次设置事务（管理员+笔录室+工作时间）"
CSRF="$(curl --silent -c "$WORK_ROOT/cookies.txt" -H "Host: localhost:8080" http://127.0.0.1:$PORT/api/v1/session | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["csrfToken"])')"
SETUP_STATUS="$(curl --silent -o "$WORK_ROOT/setup.json" -w '%{http_code}' \
  -b "$WORK_ROOT/cookies.txt" -c "$WORK_ROOT/cookies.txt" \
  -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF" \
  -H "Host: localhost:8080" \
  -X POST http://127.0.0.1:$PORT/api/v1/setup/complete \
  -d '{"admin":{"username":"admin","password":"'"$ADMIN_PASSWORD"'","name":"验收管理员","department":"验收部门"},"rooms":[{"name":"笔录室 1"},{"name":"笔录室 2"}],"workStart":"08:30","workEnd":"17:30"}')"
[ "$SETUP_STATUS" = "201" ] || { echo "FAIL: 首次设置失败（${SETUP_STATUS}）"; cat "$WORK_ROOT/setup.json"; exit 1; }

# 设置完成后服务会重建监听为 LAN 模式；等待切换完成。
echo "[5/9] 等待 LAN 监听切换并登录"
LAN_READY=""
for _ in $(seq 1 60); do
  HEALTH="$(curl --silent http://127.0.0.1:$PORT/healthz || true)"
  if echo "$HEALTH" | grep -q '"bind_mode": *"lan"'; then LAN_READY="yes"; break; fi
  sleep 1
done
[ -n "$LAN_READY" ] || { echo "FAIL: 服务未切换到 LAN 监听：$HEALTH"; exit 1; }
echo "$HEALTH" | grep -q '"install_id":' || { echo "FAIL: 回环应可见 install_id：$HEALTH"; exit 1; }
LOGIN_STATUS="$(curl --silent -o "$WORK_ROOT/login.json" -w '%{http_code}' \
  -b "$WORK_ROOT/cookies.txt" -c "$WORK_ROOT/cookies.txt" \
  -H "Content-Type: application/json" -H "X-CSRF-Token: $CSRF" \
  -H "Host: localhost:8080" \
  -X POST http://127.0.0.1:$PORT/api/v1/session \
  -d '{"username":"admin","password":"'"$ADMIN_PASSWORD"'"}')"
[ "$LOGIN_STATUS" = "200" ] || { echo "FAIL: 管理员登录失败（${LOGIN_STATUS}）"; cat "$WORK_ROOT/login.json"; exit 1; }
BOOTSTRAP="$(curl --silent -b "$WORK_ROOT/cookies.txt" -H "Host: localhost:8080" http://127.0.0.1:$PORT/api/v1/bootstrap)"
echo "$BOOTSTRAP" | grep -q '"productVersion"' || { echo "FAIL: bootstrap 缺少版本：$BOOTSTRAP"; exit 1; }

echo "[6/9] 等待首份自动备份落盘（catch-up）"
BACKUP_READY=""
for _ in $(seq 1 180); do
  if ls "$APP_FOLDER/backups/"*.db >/dev/null 2>&1; then BACKUP_READY="yes"; break; fi
  sleep 1
done
[ -n "$BACKUP_READY" ] || { echo "FAIL: 180 秒内没有自动备份"; ls -la "$APP_FOLDER/backups" || true; exit 1; }
if ls "$APP_FOLDER/backups/"*-wal "$APP_FOLDER/backups/"*-shm "$APP_FOLDER/backups/"*-journal >/dev/null 2>&1; then
  echo "FAIL: 备份目录存在 WAL/SHM/journal 伴随文件"; exit 1
fi
[ -f "$APP_FOLDER/data/install.json" ] || { echo "FAIL: 首启未生成安装身份 install.json"; exit 1; }
[ -f "$APP_FOLDER/logs/service.log" ] || { echo "FAIL: 服务日志未生成"; exit 1; }

echo "[7/9] 安装身份与 ZIP 清单对齐校验"
"$PY" - "$APP_FOLDER" "$ZIP_PATH" <<'PYEOF'
import json, sys, zipfile
from pathlib import Path
app_folder = Path(sys.argv[1])
zip_path = Path(sys.argv[2])
identity = json.loads((app_folder / "data/install.json").read_text(encoding="utf-8"))
assert identity["product_generation"] == 2, identity
assert identity["setup_complete"] is True, identity
manifest_name = [n for n in zipfile.ZipFile(zip_path).namelist() if n.endswith("app/service.py")]
assert manifest_name, "zip 缺少 app/service.py"
print("install.json 与 ZIP 结构校验通过")
PYEOF

echo "[8/9] macOS 版版本检查已启用（仅读侧车，不发起清单请求）"
EXPECTED_VERSION="$(basename "$ZIP_PATH" | sed -n 's/.*-V\([0-9][0-9.]*\)-macOS-arm64.zip/\1/p')"
[ -n "$EXPECTED_VERSION" ] || { echo "FAIL: 无法从 ZIP 文件名解析版本：$ZIP_PATH"; exit 1; }
ADMIN_SYSTEM="$(curl --silent -b "$WORK_ROOT/cookies.txt" -H "Host: localhost:8080" http://127.0.0.1:$PORT/api/v1/admin/system)"
UPDATE_VIEW="$(echo "$ADMIN_SYSTEM" | "$PY" -c 'import json,sys; c=json.load(sys.stdin).get("updateCheck"); assert c and c.get("enabled") is True, c; assert c.get("currentVersion")==sys.argv[1], c; assert c.get("status") in {"current","unknown","available"}, c; print("updateCheck.enabled=true,currentVersion="+sys.argv[1])' "$EXPECTED_VERSION")"
echo "$UPDATE_VIEW"

echo "[9/9] 停止入口干净退出"
cleanup_service
trap - EXIT
bash "$APP_FOLDER/停止.command" > "$WORK_ROOT/stop.out" 2>&1 || { echo "FAIL: 停止入口失败"; cat "$WORK_ROOT/stop.out"; exit 1; }
if curl --silent --max-time 2 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
  echo "FAIL: 停止后端口仍在响应"; exit 1
fi
[ -e "$APP_FOLDER/data/service.pid" ] && { echo "FAIL: 停止后 service.pid 残留"; exit 1; } || true

echo "MAC_ACCEPTANCE_PASS"

#!/bin/bash
# 会议室预约系统 V2 · macOS 自托管版启动入口
set -euo pipefail

APP_ROOT="$(cd "$(dirname "$0")" && pwd)"

case "$APP_ROOT" in
  /Volumes/*)
    echo "当前正在 DMG 挂载卷（只读）内运行。"
    echo "请先把整个「会议室预约系统V2-macOS」文件夹拖到「应用程序」或任意可写位置，再双击启动。"
    exit 1
    ;;
esac

PYTHON="$APP_ROOT/runtime/bin/python3.13"
if [ ! -x "$PYTHON" ]; then
  echo "缺少运行环境：$PYTHON"
  echo "请重新下载完整的应用文件夹，不要拆分或单独复制部分文件。"
  exit 1
fi

export MEETING_ROOM_OPEN_BROWSER=1
export PYTHONUTF8=1
cd "$APP_ROOT"
echo "会议室预约系统 V2 正在启动…… 关闭本窗口或使用「停止.command」即可停止服务。"
exec "$PYTHON" "$APP_ROOT/app/service.py"

#!/bin/bash
# 会议室预约系统 V2 · macOS 自托管版停止入口
set -euo pipefail

APP_ROOT="$(cd "$(dirname "$0")" && pwd)"

PYTHON="$APP_ROOT/runtime/bin/python3.13"
if [ ! -x "$PYTHON" ]; then
  echo "缺少运行环境：$PYTHON"
  exit 1
fi

exec "$PYTHON" "$APP_ROOT/app/service.py" --stop

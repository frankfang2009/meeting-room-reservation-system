#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/../.." && pwd)
tools_root="$repo_root/.tools"
backend_root="$repo_root/v2/backend"
frontend_root="$repo_root/v2/frontend"
uv_cache="$tools_root/uv-cache"
python_install="$tools_root/python"

command -v uv >/dev/null 2>&1 || {
  echo "uv is required to bootstrap the V2 Python environment" >&2
  exit 2
}
command -v node >/dev/null 2>&1 || {
  echo "Node.js 22.17.1 is required" >&2
  exit 2
}
command -v npm >/dev/null 2>&1 || {
  echo "npm is required" >&2
  exit 2
}

test "$(node --version)" = "v22.17.1" || {
  echo "Node.js must be exactly 22.17.1; found $(node --version)" >&2
  exit 2
}
if [ -L "$frontend_root/node_modules" ]; then
  echo "V2 node_modules is a symbolic link. Remove only that link, then rerun bootstrap." >&2
  exit 2
fi

mkdir -p "$tools_root" "$uv_cache" "$python_install"
uv python install --cache-dir "$uv_cache" --install-dir "$python_install" --no-bin 3.13.14
python_bin=$(find "$python_install" -type f -path '*/bin/python3.13' -print | LC_ALL=C sort | head -n 1)
test -n "$python_bin" || {
  echo "Python 3.13.14 installation was not found under $python_install" >&2
  exit 1
}
if [ -x "$backend_root/.venv/bin/python" ]; then
  "$backend_root/.venv/bin/python" -c 'import sys; assert sys.version_info[:3] == (3, 13, 14)' || {
    echo "Existing V2 virtual environment is not Python 3.13.14; move it aside before bootstrap." >&2
    exit 2
  }
else
  # A moved worktree leaves uv's interpreter symlink and console-script
  # shebangs pointing at the former absolute path. The directory is still a
  # virtual environment, so uv requires an explicit clear before rebuilding.
  uv venv --clear --cache-dir "$uv_cache" --python "$python_bin" "$backend_root/.venv"
fi
uv pip install --cache-dir "$uv_cache" --python "$backend_root/.venv/bin/python" \
  --requirement "$backend_root/requirements.txt" \
  --requirement "$backend_root/requirements-dev.txt"

(cd "$frontend_root" && npm ci)
echo "V2 development environment is ready"

#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/../.." && pwd)
backend_root="$repo_root/v2/backend"
frontend_root="$repo_root/v2/frontend"
python_bin=${V2_PYTHON:-$backend_root/.venv/bin/python}

test -x "$python_bin" || {
  echo "V2 Python environment is missing. Run v2/scripts/bootstrap-dev.sh." >&2
  exit 2
}
"$python_bin" -c 'import importlib.metadata as m, sys; assert sys.version_info[:3] == (3, 13, 14); assert m.version("Flask") == "3.1.3"; assert m.version("waitress") == "3.0.2"; assert m.version("ruff") == "0.12.12"'
test "$(node --version)" = "v22.17.1"
test ! -L "$frontend_root/node_modules"

(cd "$repo_root" && "$python_bin" -m ruff check v2/backend v2/installer v2/tests)
(cd "$backend_root" && "$python_bin" -m unittest discover -s tests -v)
(cd "$repo_root" && "$python_bin" -m unittest discover -s v2/installer/tests -v)
(cd "$repo_root" && "$python_bin" -m unittest discover -s v2/tests -v)
"$python_bin" -m compileall -q "$repo_root/v2/backend" "$repo_root/v2/installer" "$repo_root/v2/tests"
(cd "$frontend_root" && npm run check)
(cd "$repo_root" && git diff --check)

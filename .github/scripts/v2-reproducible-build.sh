#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 5 ]; then
  echo "usage: $0 SOURCE_ROOT WORK_ROOT PYTHON_EMBED_ZIP WHEELHOUSE EXPORT_ROOT" >&2
  exit 2
fi

source_root=$(cd "$1" && pwd)
work_root=$2
python_embed_zip=$3
wheelhouse=$4
export_root=$5
python_bin=${PYTHON_BIN:-python}
artifact="会议室预约系统-V2.0.0-安装包.zip"

git -C "$source_root" rev-parse --is-inside-work-tree >/dev/null
test -f "$python_embed_zip"
test -d "$wheelhouse"
test ! -e "$work_root"
test ! -e "$export_root"
mkdir -p "$work_root" "$export_root"

source_list="$work_root/source-files.txt"
git -c core.quotePath=false -C "$source_root" \
  ls-files --cached --others --exclude-standard -- v2 .github > "$source_list"

for label in first second; do
  replica="$work_root/$label/source"
  materials="$work_root/$label/materials"
  runtime="$work_root/$label/runtime"
  payload="$work_root/$label/payload"
  output="$work_root/$label/output"
  mkdir -p "$replica" "$materials/wheels" "$output"
  tar -C "$source_root" -cf - -T "$source_list" | tar -xf - -C "$replica"
  cp "$python_embed_zip" "$materials/python-3.13.14-embed-amd64.zip"
  cp "$wheelhouse"/*.whl "$materials/wheels/"

  if [ "${V2_REPRO_USE_EXISTING_NODE_MODULES:-0}" = "1" ]; then
    test -d "$source_root/v2/frontend/node_modules"
    ln -s "$source_root/v2/frontend/node_modules" "$replica/v2/frontend/node_modules"
  else
    (cd "$replica/v2/frontend" && npm ci)
  fi
  (cd "$replica/v2/frontend" && npm run build)

  (
    cd "$materials/wheels"
    find . -type f -name '*.whl' -print | LC_ALL=C sort | while IFS= read -r wheel; do
      digest=$(shasum -a 256 "$wheel" | cut -d ' ' -f 1)
      printf '%s  %s\n' "$digest" "${wheel#./}"
    done
  ) > "$materials/wheelhouse.sha256"

  (
    cd "$replica"
    "$python_bin" -m v2.installer.build_runtime \
      --python-embed-zip "$materials/python-3.13.14-embed-amd64.zip" \
      --wheelhouse "$materials/wheels" \
      --lock-file v2/backend/requirements-win-amd64.lock \
      --output "$runtime"
    "$python_bin" -m v2.installer.assemble_payload \
      --backend-root v2/backend \
      --frontend-dist v2/frontend/dist/client \
      --output "$payload"
    "$python_bin" -m v2.installer.build_package \
      --payload-root "$payload" \
      --runtime-root "$runtime" \
      --output "$output/$artifact"
  )
done

diff -qr "$work_root/first/source/v2/frontend/dist/client" \
  "$work_root/second/source/v2/frontend/dist/client"
cmp "$work_root/first/materials/wheelhouse.sha256" \
  "$work_root/second/materials/wheelhouse.sha256"
diff -qr "$work_root/first/runtime" "$work_root/second/runtime"
cmp "$work_root/first/runtime/supply-chain/runtime-provenance.json" \
  "$work_root/second/runtime/supply-chain/runtime-provenance.json"
diff -qr "$work_root/first/payload" "$work_root/second/payload"

for suffix in \
  "" \
  ".sha256.txt" \
  ".manifest.json" \
  ".sbom.cdx.json" \
  ".THIRD-PARTY-NOTICES.txt" \
  ".runtime-provenance.json"; do
  cmp "$work_root/first/output/$artifact$suffix" \
    "$work_root/second/output/$artifact$suffix"
  cp "$work_root/first/output/$artifact$suffix" "$export_root/"
done

if LC_ALL=C grep -R -a -F "$work_root" \
  "$work_root/first/runtime" "$work_root/first/payload" "$work_root/first/output"; then
  echo "candidate contains a temporary build path" >&2
  exit 1
fi

echo "MRV2_REPRODUCIBLE_BUILD=PASS"
shasum -a 256 "$export_root/$artifact"

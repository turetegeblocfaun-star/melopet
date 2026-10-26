#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$(command -v python3 || command -v python || true)"
if [[ -n "$PYTHON_BIN" ]]; then
  exec "$PYTHON_BIN" "$SCRIPT_DIR/stop-all.py"
fi

ARCH="$(uname -m)"
if [[ "$ARCH" == "arm64" || "$ARCH" == "aarch64" ]]; then
  UV_BIN="$SCRIPT_DIR/../.bootstrap/uv/macos-arm64/uv"
else
  UV_BIN="$SCRIPT_DIR/../.bootstrap/uv/macos-x64/uv"
fi
if [[ ! -x "$UV_BIN" ]]; then
  echo "❌ 未找到 Python 3，演示包中的 uv 引导程序也不完整。" >&2
  exit 1
fi
exec "$UV_BIN" run --python 3.11 --no-project "$SCRIPT_DIR/stop-all.py"

#!/usr/bin/env bash
# 在 macOS/Linux 构建桌宠运行环境；跨平台逻辑统一由 bootstrap.py 维护。
set -euo pipefail

DESKTOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="$(command -v python3 || command -v python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "❌ 未找到 Python 3.11/3.12。" >&2
  exit 1
fi
"$PYTHON_BIN" "$DESKTOP_DIR/bootstrap.py"

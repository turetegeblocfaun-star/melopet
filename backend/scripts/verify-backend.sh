#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$BACKEND_DIR"

echo "[1/7] 校验依赖锁"
uv lock --check

echo "[2/7] 同步开发依赖"
uv sync --extra dev --quiet

echo "[3/7] 静态检查"
uv run ruff check .

echo "[4/7] 自动化测试与覆盖率"
uv run pytest --cov=melopet_backend --cov-report=term

echo "[5/7] Python 字节码构建"
uv run python -m compileall -q src

echo "[6/7] 源码安全扫描"
uvx bandit -q -r src

echo "[7/7] 依赖漏洞扫描"
uvx pip-audit

echo "✅ MeloPet M1 后端质量门已全部通过"

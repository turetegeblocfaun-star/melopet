#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PACKAGE_TIMESTAMP="$(date '+%Y%m%d-%H%M%S')"
PACKAGE_NAME="MeloPet-Demo-Full-$PACKAGE_TIMESTAMP"
DESKTOP_DIR="$HOME/Desktop"
ARCHIVE_PATH="$DESKTOP_DIR/$PACKAGE_NAME.zip"
CHECKSUM_PATH="$ARCHIVE_PATH.sha256"
STAGING_DIR="$(mktemp -d -t melopet-package)"
STAGED_PROJECT="$STAGING_DIR/$PACKAGE_NAME"

cleanup() {
  rm -rf "$STAGING_DIR"
}
trap cleanup EXIT

mkdir -p "$STAGED_PROJECT"
rsync -a "$PROJECT_DIR/" "$STAGED_PROJECT/" \
  --exclude '.git/' \
  --exclude '.validation/' \
  --exclude '**/.venv/' \
  --exclude '**/node_modules/' \
  --exclude '**/__pycache__/' \
  --exclude '**/.pytest_cache/' \
  --exclude '**/.ruff_cache/' \
  --exclude 'frontend/.vinext/' \
  --exclude 'frontend/.next/' \
  --exclude 'frontend/dist/' \
  --exclude 'frontend/.wrangler/' \
  --exclude 'backend/runtime/' \
  --exclude 'backend/.env' \
  --exclude 'frontend/.env' \
  --exclude 'frontend/.env.local' \
  --exclude 'desktop-pet/pet.log' \
  --exclude 'desktop-pet/pet.lock' \
  --exclude 'desktop-pet/pet-state.json' \
  --exclude 'desktop-pet/pet-command.json' \
  --exclude '*.pyc' \
  --exclude '*.zip' \
  --exclude '*.zip.sha256' \
  --exclude '.DS_Store'

GIT_REVISION="$(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo unavailable)"
{
  echo "MeloPet complete demo package"
  echo "builtAt=$PACKAGE_TIMESTAMP"
  echo "sourceRevision=$GIT_REVISION"
  echo "platforms=macOS 12+, Windows 10/11"
  echo "entryGuide=演示启动说明.md"
  echo "secretsIncluded=false"
  echo "runtimeDataIncluded=false"
} > "$STAGED_PROJECT/BUILD-INFO.txt"

chmod +x \
  "$STAGED_PROJECT/启动MeloPet演示.command" \
  "$STAGED_PROJECT/停止MeloPet演示.command" \
  "$STAGED_PROJECT/scripts/"*.sh \
  "$STAGED_PROJECT/scripts/"*.py \
  "$STAGED_PROJECT/backend/scripts/"*.sh \
  "$STAGED_PROJECT/desktop-pet/"*.sh \
  "$STAGED_PROJECT/desktop-pet/"*.py \
  "$STAGED_PROJECT/desktop-pet/"*.command

PYTHON_BIN="$(command -v python3 || command -v python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "❌ 未找到 Python 3，无法生成跨平台演示包。" >&2
  exit 1
fi
"$PYTHON_BIN" "$PROJECT_DIR/scripts/prepare-uv-bootstrap.py" \
  "$STAGED_PROJECT" \
  --cache "$PROJECT_DIR/.validation/package-uv-cache"

"$PYTHON_BIN" "$PROJECT_DIR/scripts/package-archive.py" "$STAGED_PROJECT" "$ARCHIVE_PATH" >/dev/null

(cd "$DESKTOP_DIR" && shasum -a 256 "$PACKAGE_NAME.zip" > "$PACKAGE_NAME.zip.sha256")
echo "$ARCHIVE_PATH"
echo "$CHECKSUM_PATH"

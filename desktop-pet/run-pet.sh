#!/usr/bin/env bash
# 启动 MeloPet 桌面宠物
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$DIR")"
PY="$ROOT/.validation/desktop-pet-venv/bin/python"
"$DIR/bootstrap.sh"
LOCK_FILE="$DIR/pet.lock"
pid_is_running() {
  local pid="$1"
  local status
  kill -0 "$pid" 2>/dev/null || return 1
  status="$(ps -o stat= -p "$pid" 2>/dev/null | tr -d '[:space:]')"
  [[ -n "$status" && "$status" != Z* ]]
}
if [[ -f "$LOCK_FILE" ]]; then
  PID="$(tr -dc '0-9' < "$LOCK_FILE")"
  if [[ -n "$PID" ]] && pid_is_running "$PID"; then
    echo "桌宠已在运行（PID ${PID}）。"
    exit 0
  fi
fi
nohup "$PY" "$DIR/pet.py" >> "$DIR/pet.log" 2>&1 </dev/null &
echo "✅ 桌宠已启动"

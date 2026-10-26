#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="$BACKEND_DIR/runtime/validation"

stop_process() {
  local label="$1"
  local pid_file="$2"
  local expected_fragment="$3"
  if [[ ! -f "$pid_file" ]]; then
    echo "$label 未运行（无 PID 文件）"
    return
  fi

  local pid command_line
  pid="$(<"$pid_file")"
  if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
    echo "⚠️ $label PID 文件无效，未执行 kill：$pid_file" >&2
    return
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    unlink "$pid_file"
    echo "$label 已停止"
    return
  fi

  command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  if [[ "$command_line" != *"$expected_fragment"* ]]; then
    echo "⚠️ PID $pid 不像 MeloPet $label，为安全起见未停止：$command_line" >&2
    return
  fi

  kill "$pid"
  for _attempt in {1..30}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      break
    fi
    sleep 0.1
  done
  if kill -0 "$pid" 2>/dev/null; then
    echo "⚠️ $label 没有在 3 秒内退出，保留 PID 文件：$pid_file" >&2
    return
  fi
  unlink "$pid_file"
  echo "已停止 $label"
}

stop_process "API" "$STATE_DIR/api.pid" "melopet_backend.api"
stop_process "Worker" "$STATE_DIR/worker.pid" "melopet_backend.worker"

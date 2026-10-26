#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="$BACKEND_DIR/runtime/validation"
API_PID_FILE="$STATE_DIR/api.pid"
WORKER_PID_FILE="$STATE_DIR/worker.pid"
API_LOG="$STATE_DIR/api.log"
WORKER_LOG="$STATE_DIR/worker.log"
PORT="${MELOPET_VALIDATION_PORT:-8010}"
BASE_URL="http://127.0.0.1:$PORT"

mkdir -p "$STATE_DIR"
cd "$BACKEND_DIR"

running_pid() {
  local pid_file="$1"
  if [[ ! -f "$pid_file" ]]; then
    return 1
  fi
  local pid
  pid="$(<"$pid_file")"
  [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null
}

remove_stale_pid() {
  local pid_file="$1"
  if [[ -f "$pid_file" ]] && ! running_pid "$pid_file"; then
    unlink "$pid_file"
  fi
}

remove_stale_pid "$API_PID_FILE"
remove_stale_pid "$WORKER_PID_FILE"

echo "[1/5] 准备 Python 依赖"
uv sync --extra dev --quiet

echo "[2/5] 初始化 SQLite 持久化数据库"
"$BACKEND_DIR/.venv/bin/melopet-init-db"

if ! running_pid "$WORKER_PID_FILE"; then
  echo "[3/5] 启动独立 Worker"
  WORKER_POLL_SECONDS=0.2 nohup "$BACKEND_DIR/.venv/bin/python" -m melopet_backend.worker \
    >>"$WORKER_LOG" 2>&1 </dev/null &
  worker_pid=$!
  printf '%s\n' "$worker_pid" >"$WORKER_PID_FILE"
else
  echo "[3/5] Worker 已在运行"
fi

if ! running_pid "$API_PID_FILE"; then
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "❌ 端口 $PORT 已被其他进程占用。可使用 MELOPET_VALIDATION_PORT 更换端口。" >&2
    "$SCRIPT_DIR/stop-validation.sh" >/dev/null 2>&1 || true
    exit 1
  fi
  echo "[4/5] 启动本地 API"
  nohup "$BACKEND_DIR/.venv/bin/python" -m uvicorn melopet_backend.api:app \
    --host 127.0.0.1 --port "$PORT" >>"$API_LOG" 2>&1 </dev/null &
  api_pid=$!
  printf '%s\n' "$api_pid" >"$API_PID_FILE"
else
  echo "[4/5] API 已在运行"
fi

healthy=false
for _attempt in {1..50}; do
  if curl --silent --fail "$BASE_URL/healthz" >/dev/null; then
    healthy=true
    break
  fi
  sleep 0.2
done

if [[ "$healthy" != "true" ]]; then
  echo "❌ API 未能健康启动，请检查 $API_LOG" >&2
  "$SCRIPT_DIR/stop-validation.sh" >/dev/null 2>&1 || true
  exit 1
fi

echo "[5/5] 运行真实 HTTP 全链路验证"
MELOPET_VALIDATION_URL="$BASE_URL" "$BACKEND_DIR/.venv/bin/melopet-demo"

echo
echo "验证环境已就绪："
echo "  中文验收台: $BASE_URL/validation"
echo "  Swagger: $BASE_URL/docs"
echo "  健康检查: $BASE_URL/healthz"
echo "  就绪状态: $BASE_URL/readyz"
echo "  API 日志: $API_LOG"
echo "  Worker 日志: $WORKER_LOG"
echo "  停止环境: $SCRIPT_DIR/stop-validation.sh"

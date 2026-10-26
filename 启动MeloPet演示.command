#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
"$SCRIPT_DIR/scripts/start-all.sh"
STATUS=$?
if [[ $STATUS -ne 0 ]]; then
  echo
  read -r -p "启动失败，按回车键关闭窗口……"
fi
exit $STATUS

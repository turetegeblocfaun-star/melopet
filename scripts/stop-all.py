#!/usr/bin/env python3
"""停止由 start-all.py 启动的 MeloPet 演示进程。"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
PROCESS_FILE = PROJECT_DIR / ".validation" / "demo-processes.json"
PET_LOCK = PROJECT_DIR / "desktop-pet" / "pet.lock"


def _stop_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return


def main() -> int:
    stopped: list[str] = []
    if PROCESS_FILE.is_file():
        try:
            state = json.loads(PROCESS_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            state = {}
        processes = state.get("processes", {})
        for name in ("frontend", "api", "worker"):
            pid = processes.get(name)
            if isinstance(pid, int) and pid > 1:
                _stop_pid(pid)
                stopped.append(name)
        PROCESS_FILE.unlink(missing_ok=True)

    if PET_LOCK.is_file():
        try:
            pet_pid = int(PET_LOCK.read_text(encoding="utf-8").strip())
            if os.name == "nt":
                _stop_pid(pet_pid)
            else:
                os.kill(pet_pid, signal.SIGTERM)
            stopped.append("desktop-pet")
        except (OSError, ValueError):
            pass
    time.sleep(0.4)
    print("✅ 已停止：" + ("、".join(stopped) if stopped else "没有发现运行中的演示进程"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

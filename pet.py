#!/usr/bin/env python3
"""MeloPet 桌宠入口与本地状态控制器。

直接运行会使用项目的 PySide6 运行环境启动动态桌宠；桌宠已运行时，
也可通过命令行切换四个声明式状态，不开放网络端口：

    python pet.py
    python pet.py --state listen
    python pet.py --state dance --no-say
    python pet.py --auto-cycle off
    python pet.py --visibility hide
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DESKTOP_DIR = ROOT / "desktop-pet"
DESKTOP_ENTRY = DESKTOP_DIR / "pet.py"
LOCK_FILE = DESKTOP_DIR / "pet.lock"
COMMAND_FILE = DESKTOP_DIR / "pet-command.json"
LOCAL_VENV = ROOT / ".validation" / "desktop-pet-venv"
LEGACY_VENV = DESKTOP_DIR / ".venv"
ACTIONS = ("idle", "listen", "dance", "sleep")


def _windows_pid_is_running(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _pid_is_running(pid: int) -> bool:
    if os.name == "nt":
        return _windows_pid_is_running(pid)
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    result = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        check=False,
        text=True,
    )
    status = result.stdout.strip()
    return result.returncode == 0 and bool(status) and not status.startswith("Z")


def _running_pid() -> int | None:
    try:
        pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
        return pid if _pid_is_running(pid) else None
    except (FileNotFoundError, ValueError, OSError):
        return None


def _write_command(*, action: str | None, say: bool, auto_cycle: str | None, visibility: str | None) -> None:
    pid = _running_pid()
    if pid is None:
        raise SystemExit("桌宠尚未运行，请先执行 python pet.py 或在网页点击“放到桌面”。")
    payload: dict[str, object] = {
        "commandId": uuid.uuid4().hex,
        "say": say,
        "source": "root-cli",
    }
    if action:
        payload["action"] = action
    if auto_cycle:
        payload["autoCycle"] = auto_cycle == "on"
    if visibility:
        payload["visible"] = visibility == "show"
    temporary = COMMAND_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, COMMAND_FILE)
    details = action or auto_cycle or visibility or "refresh"
    print(f"✅ 已向桌宠进程 {pid} 发送指令：{details}")


def _runtime_python() -> Path:
    if sys.platform == "win32":
        relative_candidates = (
            Path("Scripts/pythonw.exe"),
            Path("Scripts/python.exe"),
        )
    else:
        relative_candidates = (Path("bin/python"),)
    for venv in (LOCAL_VENV, LEGACY_VENV):
        for relative in relative_candidates:
            candidate = venv / relative
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    launcher = "desktop-pet\\启动桌宠.cmd" if sys.platform == "win32" else "desktop-pet/bootstrap.sh"
    raise SystemExit(f"未找到可用的桌宠运行环境。\n请先执行：{launcher}")


def start_pet() -> None:
    if not DESKTOP_ENTRY.is_file():
        raise SystemExit(f"桌宠入口不存在：{DESKTOP_ENTRY}")
    python = _runtime_python()
    os.execv(str(python), [str(python), str(DESKTOP_ENTRY)])


def main() -> None:
    parser = argparse.ArgumentParser(description="MeloPet 动态桌宠")
    parser.add_argument("--state", choices=ACTIONS, help="切换待机/听歌/跳舞/睡觉状态")
    parser.add_argument("--no-say", action="store_true", help="切换状态时不显示台词")
    parser.add_argument("--auto-cycle", choices=("on", "off"), help="开启或关闭自动轮播")
    parser.add_argument("--visibility", choices=("show", "hide"), help="显示或隐藏桌宠")
    args = parser.parse_args()
    if args.state or args.auto_cycle or args.visibility:
        _write_command(
            action=args.state,
            say=not args.no_say,
            auto_cycle=args.auto_cycle,
            visibility=args.visibility,
        )
        return
    start_pet()


if __name__ == "__main__":
    main()

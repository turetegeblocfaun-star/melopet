#!/usr/bin/env python3
"""为 macOS/Windows 构建当前机器可用的 MeloPet 桌宠环境。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

DESKTOP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = DESKTOP_DIR.parent
VENV_DIR = PROJECT_DIR / ".validation" / "desktop-pet-venv"


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def environment_ready(python: Path) -> bool:
    if not python.is_file():
        return False
    result = subprocess.run(
        [str(python), "-c", "import PySide6"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def main() -> int:
    python = venv_python()
    if environment_ready(python):
        print(f"✅ 桌宠运行环境已就绪：{python}")
        return 0

    VENV_DIR.parent.mkdir(parents=True, exist_ok=True)
    uv = shutil.which("uv")
    if uv:
        subprocess.run([uv, "venv", "--python", "3.11", str(VENV_DIR)], check=True)
        subprocess.run(
            [uv, "pip", "install", "--python", str(python), "PySide6>=6.7,<7"],
            check=True,
        )
    else:
        if sys.version_info[:2] not in {(3, 11), (3, 12)}:
            print("❌ 请安装 Python 3.11/3.12 或 uv 后重试。", file=sys.stderr)
            return 1
        subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)], check=True)
        subprocess.run(
            [str(python), "-m", "pip", "install", "PySide6>=6.7,<7"],
            check=True,
        )
    print(f"✅ 桌宠运行环境已安装：{python}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

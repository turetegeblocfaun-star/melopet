"""把已选择的音灵候选激活为桌面宠物。

本模块负责两件事：
1. 从本地数据库读取某个桌宠候选任务的已选候选与情绪画像，生成
   desktop-pet/pet-profile.json（物种/配色/配饰/动作/名字/台词/SVG）。
2. 写入配置后，启动（或重启）desktop-pet/pet.py 桌面窗口进程。

仅在本地开发验收环境使用；生产环境应由独立桌宠客户端拉取配置。
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from .config import BACKEND_DIR

ROOT = BACKEND_DIR.parent
DESKTOP_DIR = ROOT / "desktop-pet"
PROFILE_FILE = DESKTOP_DIR / "pet-profile.json"
LOCK_FILE = DESKTOP_DIR / "pet.lock"
PET_LOG = DESKTOP_DIR / "pet.log"
PET_PY = DESKTOP_DIR / "pet.py"
COMMAND_FILE = DESKTOP_DIR / "pet-command.json"
LOCAL_VENV = DESKTOP_DIR.resolve().parent / ".validation" / "desktop-pet-venv"
LEGACY_VENV = DESKTOP_DIR / ".venv"
CATALOGS_DIR = BACKEND_DIR / "catalogs"
ASSETS_DIR = BACKEND_DIR / "assets"
PET_ACTIONS = {"idle", "listen", "dance", "sleep"}
_PET_PROCESS: subprocess.Popen[bytes] | None = None

# 与 desktop-pet/build-pet-profile.py 保持一致。
MOOD_ENERGY = {
    "heroic_drive": 84,
    "joy_bright": 74,
    "romantic_tender": 48,
    "healing_warm": 40,
    "calm_focus": 24,
    "mysterious_dark": 42,
    "melancholy_sad": 20,
    "nostalgic_story": 32,
    "UNKNOWN": 50,
}

MOTION_NAMES = {
    "motion_bounce_dev": "活泼",
    "motion_float_dev": "灵动",
    "motion_breathe_dev": "安静",
}

SPECIES_NAMES = {
    "species_cat_dev": "猫咪",
    "species_fox_dev": "狐狸",
    "species_bird_dev": "小鸟",
    "species_bunny_dev": "兔子",
    "species_bear_dev": "小熊",
    "species_spirit_dev": "星灵",
}

ACCESSORY_NAMES = {
    "accessory_star_dev": "星星发饰",
    "accessory_note_dev": "音符发饰",
    "accessory_ribbon_dev": "蝴蝶结",
    "accessory_halo_dev": "光环",
    "accessory_headphone_dev": "大耳机",
}


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _asset_file(catalog: dict[str, Any], asset_id: str) -> Path:
    for asset in catalog["assets"]:
        if asset.get("assetId") == asset_id:
            uri = asset["uri"]
            if uri.startswith("inline://"):
                raise ValueError(f"资产 {asset_id} 是内联资产，无法解析。")
            return ASSETS_DIR / uri
    raise ValueError(f"资产目录中找不到 {asset_id}")


def _resolve_svg(path: Path, replacements: dict[str, str] | None = None) -> str:
    raw = path.read_text(encoding="utf-8")
    for token, value in (replacements or {}).items():
        raw = raw.replace(token, value)
    return raw


def build_profile_from_db(db_path: Path, task_id: str) -> dict[str, Any]:
    """从数据库读取 task_id 对应的已选候选，返回 pet-profile 字典。"""
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        selection = connection.execute(
            "SELECT * FROM pet_selections WHERE task_id = ?", (task_id,)
        ).fetchone()
        if not selection:
            raise LookupError("尚无已选择的音灵候选")

        candidate = connection.execute(
            "SELECT * FROM pet_candidates WHERE id = ?",
            (selection["candidate_record_id"],),
        ).fetchone()
        if not candidate:
            raise LookupError("找不到已选择的候选")

        spec = json.loads(candidate["spec_json"])
        copy = json.loads(candidate["copy_json"])

        profile = connection.execute(
            "SELECT * FROM mood_profiles WHERE revision = ?",
            (candidate["profile_revision"],),
        ).fetchone()
        if not profile:
            raise LookupError("找不到候选对应的情绪画像")
        percentages = json.loads(profile["percentages_json"])
    finally:
        connection.close()

    mapping = _load_json(CATALOGS_DIR / "mapping-rules.v0.1.json")
    taxonomy = _load_json(CATALOGS_DIR / "mood-taxonomy.v0.1.json")
    catalog = _load_json(CATALOGS_DIR / "asset-catalog.dev-0.1.json")

    body_color, accent_color = mapping["paletteColors"][spec["bodyPaletteId"]]
    species_svg = _resolve_svg(
        _asset_file(catalog, spec["speciesId"]),
        {"__BODY__": body_color, "__ACCENT__": accent_color},
    )
    accessory_id = spec["accessoryIds"][0] if spec["accessoryIds"] else None
    accessory_svg = _resolve_svg(_asset_file(catalog, accessory_id)) if accessory_id else ""
    headphone_svg = _resolve_svg(_asset_file(catalog, "accessory_headphone_dev"))

    mood_by_id = {item["moodId"]: item for item in taxonomy["taxonomy"]}
    ordered = sorted(
        [item for item in percentages if item["moodId"] != "UNKNOWN"],
        key=lambda item: -int(item["percent"]),
    )
    primary_id = ordered[0]["moodId"] if ordered else "UNKNOWN"
    primary_percent = int(ordered[0]["percent"]) if ordered else 0
    primary_name = mood_by_id.get(primary_id, {}).get("displayName", primary_id)

    total = sum(int(item["percent"]) for item in percentages)
    energy = round(
        sum(MOOD_ENERGY.get(item["moodId"], 50) * int(item["percent"]) for item in percentages) / total
    ) if total else 50

    return {
        "name": copy.get("petName", "小宠"),
        "intro": copy.get("intro", ""),
        "clickLines": copy.get("clickLines", []),
        "speciesId": spec["speciesId"],
        "speciesName": SPECIES_NAMES.get(spec["speciesId"], spec["speciesId"]),
        "motionId": spec.get("idleMotionId") or "motion_breathe_dev",
        "motionName": MOTION_NAMES.get(spec.get("idleMotionId"), "安静"),
        "accessoryId": accessory_id,
        "accessoryName": ACCESSORY_NAMES.get(accessory_id, "") if accessory_id else "",
        "bodyColor": body_color,
        "accentColor": accent_color,
        "primaryMoodId": primary_id,
        "primaryMoodName": primary_name,
        "primaryMoodPercent": primary_percent,
        "energy": energy,
        "speciesSvg": species_svg,
        "accessorySvg": accessory_svg,
        "headphoneSvg": headphone_svg,
        "source": {
            "petTaskId": task_id,
            "candidateRecordId": candidate["id"],
            "profileRevision": candidate["profile_revision"],
        },
    }


def _stop_existing_pet() -> None:
    """停止当前运行的桌宠实例；POSIX 上再用精确路径 pkill 兜底。"""
    global _PET_PROCESS
    if _PET_PROCESS is not None:
        if _PET_PROCESS.poll() is None:
            _PET_PROCESS.terminate()
            try:
                _PET_PROCESS.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _PET_PROCESS.kill()
                _PET_PROCESS.wait(timeout=2)
        _PET_PROCESS = None
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
            os.kill(pid, signal.SIGTERM)
        except (ValueError, ProcessLookupError, OSError):
            pass
    if os.name != "nt":
        subprocess.run(["pkill", "-f", str(PET_PY)], capture_output=True)
    time.sleep(0.8)
    if LOCK_FILE.exists():
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass


def _runtime_python() -> Path:
    relative_candidates = (
        (Path("Scripts/pythonw.exe"), Path("Scripts/python.exe"))
        if os.name == "nt"
        else (Path("bin/python"),)
    )
    for venv in (LOCAL_VENV, LEGACY_VENV):
        for relative in relative_candidates:
            candidate = venv / relative
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
    launcher = "desktop-pet\\启动桌宠.cmd" if os.name == "nt" else "desktop-pet/bootstrap.sh"
    raise FileNotFoundError(f"桌宠运行环境不可用，请先执行 {launcher}")


def _write_command(payload: dict[str, Any]) -> None:
    COMMAND_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = COMMAND_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, COMMAND_FILE)


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
    except OSError:
        return False
    result = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        check=False,
        text=True,
    )
    status = result.stdout.strip()
    return result.returncode == 0 and bool(status) and not status.startswith("Z")


def _running_pid() -> int:
    try:
        pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
        if _pid_is_running(pid):
            return pid
        raise LookupError("桌宠进程未运行")
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError, OSError) as exc:
        raise LookupError("桌宠进程未运行") from exc


def push_action(action: str, *, say: bool = True) -> dict[str, Any]:
    """通过本地原子命令文件推送动作，不开放本地网络端口。"""
    if action not in PET_ACTIONS:
        raise ValueError("不支持的桌宠动作")
    pid = _running_pid()
    _write_command(
        {
            "commandId": uuid.uuid4().hex,
            "action": action,
            "say": say,
            "visible": True,
            "source": "melopet-backend",
        }
    )
    return {"accepted": True, "action": action, "pid": pid}


def write_profile_and_launch(profile_dict: dict[str, Any]) -> dict[str, Any]:
    """写入 pet-profile.json 并启动桌面宠物进程，返回激活结果。"""
    global _PET_PROCESS
    if not DESKTOP_DIR.is_dir() or not PET_PY.is_file():
        raise FileNotFoundError("桌宠程序文件不完整")
    python = _runtime_python()
    PROFILE_FILE.write_text(
        json.dumps(profile_dict, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(PROFILE_FILE, 0o644)

    _stop_existing_pet()
    _write_command(
        {
            "commandId": uuid.uuid4().hex,
            "action": "idle",
            "say": False,
            "visible": True,
            "source": "melopet-activation",
        }
    )

    process_options: dict[str, Any]
    if os.name == "nt":
        process_options = {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        }
    else:
        process_options = {"start_new_session": True}

    with open(PET_LOG, "ab") as log_handle:
        process = subprocess.Popen(
            [str(python), str(PET_PY)],
            cwd=str(DESKTOP_DIR),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            **process_options,
        )
    _PET_PROCESS = process
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise OSError(f"桌宠进程启动失败（退出码 {return_code}），请查看 desktop-pet/pet.log")
        try:
            locked_pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, OSError, ValueError):
            locked_pid = None
        if locked_pid == process.pid and _pid_is_running(process.pid):
            break
        time.sleep(0.05)
    else:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
        _PET_PROCESS = None
        raise OSError("桌宠启动超时，控制通道尚未就绪，请查看 desktop-pet/pet.log")
    return {
        "activated": True,
        "name": profile_dict["name"],
        "speciesName": profile_dict["speciesName"],
        "pid": process.pid,
    }

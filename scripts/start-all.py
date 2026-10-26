#!/usr/bin/env python3
"""MeloPet macOS/Windows 一键演示启动器。

首次运行把全部依赖放在项目内的 .validation，不写入系统 Python；若本机
Node.js 版本不足，则从 nodejs.org 下载固定版本并校验官方 SHA-256。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import webbrowser
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
BACKEND_DIR = PROJECT_DIR / "backend"
FRONTEND_DIR = PROJECT_DIR / "frontend"
DESKTOP_DIR = PROJECT_DIR / "desktop-pet"
STATE_DIR = PROJECT_DIR / ".validation"
TOOLS_DIR = STATE_DIR / "tools"
PROCESS_FILE = STATE_DIR / "demo-processes.json"
NODE_VERSION = "22.14.0"
MINIMUM_NODE = (22, 13)


def _executable(venv: Path, name: str) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / f"{name}.exe"
    return venv / "bin" / name


def _prepend_path(environment: dict[str, str], directory: Path) -> None:
    environment["PATH"] = str(directory) + os.pathsep + environment.get("PATH", "")


def _run(command: list[str], *, cwd: Path, environment: dict[str, str]) -> None:
    subprocess.run(command, cwd=str(cwd), env=environment, check=True)


def _ensure_uv(environment: dict[str, str]) -> Path:
    configured = environment.get("UV_EXE")
    system_uv = Path(configured) if configured else None
    if system_uv is None:
        found = shutil.which("uv", path=environment.get("PATH"))
        system_uv = Path(found) if found else None
    if system_uv and system_uv.is_file():
        _prepend_path(environment, system_uv.parent)
        return system_uv

    tools_venv = TOOLS_DIR / "python-tools"
    local_uv = _executable(tools_venv, "uv")
    if not local_uv.is_file():
        print("[准备] 安装项目内 uv 工具……", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(tools_venv)], check=True)
        tools_python = _executable(tools_venv, "python")
        subprocess.run(
            [str(tools_python), "-m", "pip", "install", "--disable-pip-version-check", "uv>=0.8,<1"],
            check=True,
        )
    _prepend_path(environment, local_uv.parent)
    return local_uv


def _node_version(node: Path) -> tuple[int, int] | None:
    try:
        result = subprocess.run(
            [str(node), "--version"],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        return None
    match = re.fullmatch(r"v(\d+)\.(\d+)\.\d+\s*", result.stdout)
    return (int(match.group(1)), int(match.group(2))) if match else None


def _node_platform() -> tuple[str, str]:
    system = {"darwin": "darwin", "win32": "win", "linux": "linux"}.get(sys.platform)
    machine = platform.machine().lower()
    architecture = "arm64" if machine in {"arm64", "aarch64"} else "x64"
    if system is None:
        raise RuntimeError(f"暂不支持自动准备 Node.js：{sys.platform}")
    return system, architecture


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=90) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def _safe_destination(root: Path, member_name: str) -> Path:
    destination = (root / member_name).resolve()
    try:
        destination.relative_to(root.resolve())
    except ValueError as exc:
        raise RuntimeError("Node.js 压缩包包含非法路径") from exc
    return destination


def _extract_node(archive: Path, destination: Path) -> None:
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as source:
            for item in source.infolist():
                _safe_destination(destination, item.filename)
            source.extractall(destination)
        return
    with tarfile.open(archive, "r:gz") as source:
        for item in source.getmembers():
            _safe_destination(destination, item.name)
        source.extractall(destination)


def _ensure_node(environment: dict[str, str]) -> tuple[Path, Path]:
    system_node = shutil.which("node", path=environment.get("PATH"))
    system_npm = shutil.which("npm", path=environment.get("PATH"))
    if system_node and system_npm:
        version = _node_version(Path(system_node))
        if version and version >= MINIMUM_NODE:
            return Path(system_node), Path(system_npm)

    system, architecture = _node_platform()
    extension = "zip" if system == "win" else "tar.gz"
    distribution = f"node-v{NODE_VERSION}-{system}-{architecture}"
    filename = f"{distribution}.{extension}"
    target = TOOLS_DIR / distribution
    node = target / ("node.exe" if system == "win" else "bin/node")
    npm = target / ("npm.cmd" if system == "win" else "bin/npm")
    if _node_version(node) and npm.is_file():
        _prepend_path(environment, node.parent)
        return node, npm

    print(f"[准备] 下载并校验项目内 Node.js {NODE_VERSION}……", flush=True)
    base_url = f"https://nodejs.org/dist/v{NODE_VERSION}"
    downloads = STATE_DIR / "downloads"
    checksum_file = downloads / f"SHASUMS256-{NODE_VERSION}.txt"
    archive = downloads / filename
    _download(f"{base_url}/SHASUMS256.txt", checksum_file)
    checksums = {
        line.split()[1]: line.split()[0]
        for line in checksum_file.read_text(encoding="utf-8").splitlines()
        if len(line.split()) == 2
    }
    expected = checksums.get(filename)
    if not expected:
        raise RuntimeError(f"官方校验表中不存在 {filename}")
    _download(f"{base_url}/{filename}", archive)
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != expected:
        raise RuntimeError("Node.js 下载文件 SHA-256 校验失败")

    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="node-extract-", dir=str(TOOLS_DIR)))
    try:
        _extract_node(archive, temporary)
        extracted = temporary / distribution
        if target.exists():
            shutil.rmtree(target)
        os.replace(extracted, target)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    _prepend_path(environment, node.parent)
    return node, npm


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) != 0


def _url_ready(url: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 500
    except (OSError, ValueError):
        return False


def _wait_for(url: str, process: subprocess.Popen[bytes], seconds: int) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"服务提前退出（退出码 {process.returncode}）：{url}")
        if _url_ready(url):
            return
        time.sleep(0.25)
    raise RuntimeError(f"服务启动超时：{url}")


def _spawn(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    log_file: Path,
) -> subprocess.Popen[bytes]:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    options: dict[str, object]
    if os.name == "nt":
        options = {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
        }
    else:
        options = {"start_new_session": True}
    with log_file.open("ab") as output:
        return subprocess.Popen(
            command,
            cwd=str(cwd),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            **options,
        )


def _npm_command(npm: Path, arguments: list[str]) -> list[str]:
    if os.name == "nt":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(npm), *arguments]
    return [str(npm), *arguments]


def _existing_demo_ready(api_url: str, frontend_url: str) -> bool:
    if not PROCESS_FILE.is_file():
        return False
    return _url_ready(f"{api_url}/healthz") and _url_ready(frontend_url)


def main() -> int:
    api_port = int(os.environ.get("MELOPET_API_PORT", "8010"))
    frontend_port = int(os.environ.get("MELOPET_FRONTEND_PORT", "4173"))
    api_url = f"http://127.0.0.1:{api_port}"
    frontend_url = f"http://127.0.0.1:{frontend_port}/"
    if _existing_demo_ready(api_url, frontend_url):
        print(f"✅ MeloPet 已在运行：{frontend_url}")
        if os.environ.get("MELOPET_NO_BROWSER") != "1":
            webbrowser.open(frontend_url)
        return 0
    for port in (api_port, frontend_port):
        if not _port_available(port):
            print(f"❌ 端口 {port} 已被其他程序占用。", file=sys.stderr)
            return 1

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "APP_ENV": "development",
            "MODEL_PROVIDER": "disabled",
            "MUSIC_SIGNAL_PROVIDER": "disabled",
            "WORKER_POLL_SECONDS": "0.2",
            "MELOPET_API_ORIGIN": api_url,
            "MELOPET_API_PORT": str(api_port),
            "MELOPET_FRONTEND_PORT": str(frontend_port),
        }
    )

    uv = _ensure_uv(environment)
    _node, npm = _ensure_node(environment)
    print("[1/5] 准备后端 Python 依赖……", flush=True)
    _run([str(uv), "sync", "--extra", "dev", "--quiet"], cwd=BACKEND_DIR, environment=environment)
    backend_python = _executable(BACKEND_DIR / ".venv", "python")
    print("[2/5] 初始化本地演示数据库……", flush=True)
    _run([str(backend_python), "-m", "melopet_backend.db"], cwd=BACKEND_DIR, environment=environment)
    print("[3/5] 准备 macOS/Windows 桌宠环境……", flush=True)
    _run([str(backend_python), str(DESKTOP_DIR / "bootstrap.py")], cwd=PROJECT_DIR, environment=environment)
    print("[4/5] 准备前端依赖……", flush=True)
    _run(_npm_command(npm, ["ci", "--silent"]), cwd=FRONTEND_DIR, environment=environment)

    print("[5/5] 启动 API、Worker 和演示页面……", flush=True)
    processes: dict[str, subprocess.Popen[bytes]] = {}
    try:
        processes["worker"] = _spawn(
            [str(backend_python), "-m", "melopet_backend.worker"],
            cwd=BACKEND_DIR,
            environment=environment,
            log_file=STATE_DIR / "worker.log",
        )
        processes["api"] = _spawn(
            [
                str(backend_python),
                "-m",
                "uvicorn",
                "melopet_backend.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(api_port),
            ],
            cwd=BACKEND_DIR,
            environment=environment,
            log_file=STATE_DIR / "api.log",
        )
        _wait_for(f"{api_url}/healthz", processes["api"], 30)
        processes["frontend"] = _spawn(
            _npm_command(npm, ["run", "dev"]),
            cwd=FRONTEND_DIR,
            environment=environment,
            log_file=STATE_DIR / "frontend.log",
        )
        _wait_for(frontend_url, processes["frontend"], 60)
    except Exception:
        for process in reversed(list(processes.values())):
            if process.poll() is None:
                process.terminate()
        raise

    PROCESS_FILE.write_text(
        json.dumps(
            {
                "apiPort": api_port,
                "frontendPort": frontend_port,
                "processes": {name: process.pid for name, process in processes.items()},
                "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print("\n✅ MeloPet 完整演示环境已启动")
    print(f"   演示地址：{frontend_url}")
    print(f"   停止服务：{SCRIPT_DIR / 'stop-all.py'}")
    if os.environ.get("MELOPET_NO_BROWSER") != "1":
        webbrowser.open(frontend_url)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"\n❌ 启动失败：{exc}", file=sys.stderr)
        print(f"请查看日志目录：{STATE_DIR}", file=sys.stderr)
        raise SystemExit(1) from exc

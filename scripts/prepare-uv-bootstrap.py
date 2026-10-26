#!/usr/bin/env python3
"""下载、校验并写入演示包所需的四平台 uv 引导程序。"""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import shutil
import tarfile
import urllib.request
import zipfile
from pathlib import Path

UV_VERSION = "0.12.5"
RELEASE_BASE = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}"
TARGETS = {
    "macos-arm64": ("uv-aarch64-apple-darwin.tar.gz", "uv"),
    "macos-x64": ("uv-x86_64-apple-darwin.tar.gz", "uv"),
    "windows-arm64": ("uv-aarch64-pc-windows-msvc.zip", "uv.exe"),
    "windows-x64": ("uv-x86_64-pc-windows-msvc.zip", "uv.exe"),
}


def _read_url(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def _expected_checksum(filename: str) -> str:
    raw = _read_url(f"{RELEASE_BASE}/{filename}.sha256").decode("utf-8").strip()
    checksum = raw.split()[0]
    if len(checksum) != 64:
        raise RuntimeError(f"无效的 uv 校验文件：{filename}.sha256")
    return checksum


def _extract_binary(filename: str, binary_name: str, archive: bytes) -> bytes:
    if filename.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(archive)) as source:
            matches = [name for name in source.namelist() if name.rsplit("/", 1)[-1] == binary_name]
            if len(matches) != 1:
                raise RuntimeError(f"{filename} 中找不到唯一的 {binary_name}")
            return source.read(matches[0])
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
        matches = [item for item in source.getmembers() if item.name.rsplit("/", 1)[-1] == binary_name]
        if len(matches) != 1:
            raise RuntimeError(f"{filename} 中找不到唯一的 {binary_name}")
        handle = source.extractfile(matches[0])
        if handle is None:
            raise RuntimeError(f"无法读取 {filename} 中的 {binary_name}")
        return handle.read()


def _valid_cached_binary(path: Path) -> bool:
    checksum_file = path.with_suffix(path.suffix + ".sha256")
    if not path.is_file() or not checksum_file.is_file():
        return False
    expected = checksum_file.read_text(encoding="utf-8").strip()
    return len(expected) == 64 and hashlib.sha256(path.read_bytes()).hexdigest() == expected


def prepare(package_root: Path, cache_root: Path | None = None) -> None:
    bootstrap_root = package_root / ".bootstrap" / "uv"
    for platform_name, (filename, binary_name) in TARGETS.items():
        destination = bootstrap_root / platform_name / binary_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        cached = cache_root / platform_name / binary_name if cache_root else None
        if cached and _valid_cached_binary(cached):
            print(f"复用已校验的 {platform_name} uv {UV_VERSION}……", flush=True)
            shutil.copy2(cached, destination)
        else:
            print(f"准备 {platform_name} uv {UV_VERSION}……", flush=True)
            archive = _read_url(f"{RELEASE_BASE}/{filename}")
            actual = hashlib.sha256(archive).hexdigest()
            expected = _expected_checksum(filename)
            if actual != expected:
                raise RuntimeError(f"uv 下载文件 SHA-256 校验失败：{filename}")
            destination.write_bytes(_extract_binary(filename, binary_name, archive))
            if cached:
                cached.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, cached)
                cached.with_suffix(cached.suffix + ".sha256").write_text(
                    hashlib.sha256(destination.read_bytes()).hexdigest() + "\n",
                    encoding="utf-8",
                )
        os.chmod(destination, 0o755)
    (bootstrap_root / "VERSION.txt").write_text(UV_VERSION + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package_root", type=Path)
    parser.add_argument("--cache", type=Path)
    args = parser.parse_args()
    cache = args.cache.resolve() if args.cache else None
    prepare(args.package_root.resolve(), cache)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

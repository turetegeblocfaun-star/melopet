#!/usr/bin/env python3
"""生成保留 UTF-8 文件名与 Unix 执行权限的 MeloPet ZIP。"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

REQUIRED_RELATIVE_FILES = (
    "演示启动说明.md",
    "启动MeloPet演示.command",
    "启动MeloPet演示.cmd",
    "scripts/start-all.py",
    "backend/src/melopet_backend/api.py",
    "frontend/components/melopet-app.tsx",
    "desktop-pet/pet.py",
    "desktop-pet/bootstrap.py",
    ".bootstrap/uv/macos-arm64/uv",
    ".bootstrap/uv/macos-x64/uv",
    ".bootstrap/uv/windows-arm64/uv.exe",
    ".bootstrap/uv/windows-x64/uv.exe",
)


def build_archive(project: Path, archive: Path) -> None:
    root_name = project.name
    with zipfile.ZipFile(
        archive,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=False,
    ) as destination:
        for path in sorted(project.rglob("*")):
            relative = path.relative_to(project).as_posix()
            archive_name = f"{root_name}/{relative}"
            if path.is_dir():
                info = zipfile.ZipInfo.from_file(path, archive_name + "/")
                destination.writestr(info, b"")
            elif path.is_file():
                destination.write(path, archive_name)


def verify_archive(project_name: str, archive: Path) -> None:
    with zipfile.ZipFile(archive) as source:
        broken = source.testzip()
        if broken:
            raise RuntimeError(f"ZIP CRC 校验失败：{broken}")
        names = set(source.namelist())
        for relative in REQUIRED_RELATIVE_FILES:
            required = f"{project_name}/{relative}"
            if required not in names:
                raise RuntimeError(f"演示包缺少关键文件：{required}")
        command_info = source.getinfo(f"{project_name}/启动MeloPet演示.command")
        unix_mode = command_info.external_attr >> 16
        if not unix_mode & 0o111:
            raise RuntimeError("macOS 一键启动文件缺少执行权限")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("project", type=Path)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    archive = args.archive.resolve()
    if not project.is_dir():
        raise SystemExit(f"打包目录不存在：{project}")
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        archive.unlink()
    build_archive(project, archive)
    verify_archive(project.name, archive)
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

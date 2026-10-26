from __future__ import annotations

import json
import os

import pytest

from melopet_backend import desktop_launch


def test_push_action_writes_validated_atomic_command(tmp_path, monkeypatch):
    lock_file = tmp_path / "pet.lock"
    command_file = tmp_path / "pet-command.json"
    lock_file.write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr(desktop_launch, "LOCK_FILE", lock_file)
    monkeypatch.setattr(desktop_launch, "COMMAND_FILE", command_file)

    result = desktop_launch.push_action("listen", say=False)

    command = json.loads(command_file.read_text(encoding="utf-8"))
    assert result == {"accepted": True, "action": "listen", "pid": os.getpid()}
    assert command["action"] == "listen"
    assert command["say"] is False
    assert command["visible"] is True
    assert command["source"] == "melopet-backend"


def test_push_action_rejects_unknown_action():
    with pytest.raises(ValueError, match="不支持"):
        desktop_launch.push_action("fly")


def test_pid_probe_rejects_posix_zombie(monkeypatch):
    monkeypatch.setattr(desktop_launch.os, "kill", lambda _pid, _signal: None)
    monkeypatch.setattr(
        desktop_launch.subprocess,
        "run",
        lambda *_args, **_kwargs: desktop_launch.subprocess.CompletedProcess([], 0, stdout="Z"),
    )

    assert desktop_launch._pid_is_running(12345) is False


def test_pid_probe_uses_read_only_windows_api(monkeypatch):
    monkeypatch.setattr(desktop_launch.os, "name", "nt")
    monkeypatch.setattr(desktop_launch, "_windows_pid_is_running", lambda pid: pid == 12345)

    def fail_if_kill_is_used(*_args):
        raise AssertionError("Windows 进程探测不能调用 os.kill(pid, 0)")

    monkeypatch.setattr(desktop_launch.os, "kill", fail_if_kill_is_used)

    assert desktop_launch._pid_is_running(12345) is True

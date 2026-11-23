import asyncio
import shutil
import subprocess
import sys

import pytest

from agent_toolkit._atk import weekly_command
from agent_toolkit._atk.serve import cli as serve_cli

_linux = pytest.mark.skipif(sys.platform == "win32", reason="systemdのユーザータイマーはLinuxで確かめる")


@pytest.fixture(name="atk_command")
def _atk_command(tmp_path):
    path = tmp_path / "bin" / "atk"
    path.parent.mkdir()
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


@_linux
def test_install_registers_sunday_14_user_timer(monkeypatch, tmp_path, atk_command):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PATH", f"{atk_command.parent}:/usr/bin")
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(weekly_command.subprocess, "run", run)
    assert weekly_command.install(None) == 0
    units = tmp_path / "config" / "systemd" / "user"
    timer = (units / "atk-weekly.timer").read_text(encoding="utf-8")
    service = (units / "atk-weekly.service").read_text(encoding="utf-8")
    assert "OnCalendar=Sun *-*-* 14:00:00" in timer
    assert "Persistent=true" in timer
    assert f'ExecStart="{atk_command}" weekly run' in service
    assert f'Environment="PATH={atk_command.parent}:/usr/bin"' in service
    assert calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", "atk-weekly.timer"],
    ]
    calls.clear()
    assert weekly_command.uninstall() == 0
    assert not (units / "atk-weekly.timer").exists()
    assert calls[0] == ["systemctl", "--user", "disable", "--now", "atk-weekly.timer"]


@_linux
@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyzeが無い環境")
def test_generated_units_are_valid_for_systemd(tmp_path, atk_command):
    for name, content in weekly_command.systemd_units(atk_command, "/usr/bin:/bin").items():
        (tmp_path / name).write_text(content, encoding="utf-8")
    calendar = subprocess.run(
        ["systemd-analyze", "calendar", weekly_command.CALENDAR], capture_output=True, text=True, check=True, timeout=30
    )
    assert "Sun" in calendar.stdout and "14:00:00" in calendar.stdout
    verified = subprocess.run(
        ["systemd-analyze", "--user", "verify", str(tmp_path / "atk-weekly.timer")],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert verified.returncode == 0, verified.stderr


def test_install_skips_plugin_cache_and_temporary_atk(monkeypatch, tmp_path):
    cache = tmp_path / "home" / ".claude" / "plugins" / "cache" / "agent-toolkit" / "1.0" / "scripts"
    cache.mkdir(parents=True)
    (cache / "atk").write_text("#!/bin/sh\n", encoding="utf-8")
    (cache / "atk").chmod(0o755)
    monkeypatch.setenv("PATH", str(cache))
    with pytest.raises(ValueError, match="PATH"):
        weekly_command._stable_atk(None)  # noqa: SLF001  # pylint: disable=protected-access


def test_windows_registration_uses_sunday_14_and_catch_up(atk_command):
    script = weekly_command.windows_register_script(atk_command)
    assert "-Weekly -DaysOfWeek Sunday -At '14:00'" in script
    assert "-StartWhenAvailable" in script
    assert f"-Execute '{atk_command}' -Argument 'weekly run'" in script


@pytest.mark.asyncio
async def test_serve_loop_launches_weekly_run_until_stop(monkeypatch):
    stop = asyncio.Event()
    calls = []

    class Launcher:
        def launch(self):
            calls.append(True)
            stop.set()
            return True

    monkeypatch.setattr(weekly_command, "ServeLauncher", Launcher)
    await asyncio.wait_for(serve_cli._weekly_loop(stop), timeout=5)  # noqa: SLF001  # pylint: disable=protected-access
    assert calls == [True]

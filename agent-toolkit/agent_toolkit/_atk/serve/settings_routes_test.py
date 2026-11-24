import base64
import json
import re
import subprocess

import pytest

from agent_toolkit._atk.serve import settings_routes


@pytest.mark.parametrize("legacy", [False, True])
def test_posix_probe_keeps_snapshot_show_and_update_on_ssh(monkeypatch, legacy):
    calls = []

    def run(command, **_kwargs):
        assert command[0] == "ssh"
        operation = command[-1]
        calls.append(operation)
        if operation == "uname -s":
            return subprocess.CompletedProcess(command, 0, b"Linux\n", b"")
        if operation == "atk config snapshot":
            return subprocess.CompletedProcess(command, 2 if legacy else 0, b'{"settings":{"serve_host":{}}}', b"")
        if operation == "atk config show":
            return subprocess.CompletedProcess(command, 0, b"serve_host old\n", b"")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(settings_routes, "run_with_timeout", run)
    settings_routes._remote_update("host", {"action": "set", "key": "serve_host", "value": "localhost"})  # noqa: SLF001  # pylint: disable=protected-access
    assert calls == ["uname -s", "atk config snapshot"] + (
        ["atk config show", "atk config set serve_host localhost"] if legacy else ["atk config update"]
    )


@pytest.mark.parametrize("update_status", [0, 124])
def test_windows_runs_snapshot_and_update_only_on_the_unelevated_route(monkeypatch, update_status):
    payload = {"action": "set", "key": "serve_host", "value": "日本語 & | % ^"}
    calls = []

    def run(command, **kwargs):
        calls.append(command[0])
        data = kwargs["input"]
        if command[0] == "ssh":
            assert command[-1] == "uname -s"
            return subprocess.CompletedProcess(command, 1, b"", b"uname unavailable")
        assert command[0] == "ssh-unelevated"
        assert command[-3:] == ["--", "windows-host", "-"]
        assert b"exit $global:LASTEXITCODE" in data
        if b"'snapshot'" in data:
            return subprocess.CompletedProcess(command, 0, b'{"settings":{"serve_host":{}}}', b"")
        assert b"'update'" in data
        marker = re.search(rb"FromBase64String\('([^']+)'\)", data)
        assert marker is not None
        assert json.loads(base64.b64decode(marker[1])) == payload
        return subprocess.CompletedProcess(command, update_status, b"", b"")

    monkeypatch.setattr(settings_routes, "run_with_timeout", run)
    if update_status:
        with pytest.raises(ValueError, match="保存"):
            settings_routes._remote_update("windows-host", payload)  # noqa: SLF001  # pylint: disable=protected-access
    else:
        settings_routes._remote_update("windows-host", payload)  # noqa: SLF001  # pylint: disable=protected-access
    assert calls == ["ssh", "ssh-unelevated", "ssh-unelevated"]


def test_unrecognized_host_runs_legacy_operations_only_on_the_unelevated_route(monkeypatch):
    operations = []

    def run(command, **kwargs):
        if command[0] == "ssh":
            assert command[-1] == "uname -s"
            return subprocess.CompletedProcess(command, 0, b"MSYS_NT-10.0\n", b"")
        assert command[0] == "ssh-unelevated"
        operation = kwargs["input"].splitlines()[2]
        operations.append(operation)
        if b"'snapshot'" in operation:
            return subprocess.CompletedProcess(command, 2, b"", b"")
        if b"'show'" in operation:
            return subprocess.CompletedProcess(command, 0, b"serve_host old\n", b"")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(settings_routes, "run_with_timeout", run)
    settings_routes._remote_update("host", {"action": "set", "key": "serve_host", "value": "localhost"})  # noqa: SLF001  # pylint: disable=protected-access
    assert operations == [
        b"$atkArgs = @('config', 'snapshot')",
        b"$atkArgs = @('config', 'show')",
        b"$atkArgs = @('config', 'set', 'serve_host', 'localhost')",
    ]


@pytest.mark.parametrize("save", [False, True])
def test_ssh_probe_connection_failure_does_not_try_unelevated(monkeypatch, save):
    def run(command, **_kwargs):
        assert command[0] == "ssh"
        assert command[-1] == "uname -s"
        return subprocess.CompletedProcess(command, 255, b"", b"Connection refused")

    monkeypatch.setattr(settings_routes, "run_with_timeout", run)
    with pytest.raises(settings_routes.WebInputError) as result:
        if save:
            settings_routes._remote_update("host", {"action": "set", "key": "serve_host", "value": "localhost"})  # noqa: SLF001  # pylint: disable=protected-access
        else:
            settings_routes._remote_snapshot("host")  # noqa: SLF001  # pylint: disable=protected-access
    assert result.value.next_action == "接続先とSSHの接続を確認する"

import base64
import json
import re
import subprocess

import pytest

from agent_toolkit._atk.serve import settings_routes


@pytest.mark.parametrize("update_status", [0, 124])
def test_windows_ssh_launch_failure_keeps_the_write_on_the_unelevated_route(monkeypatch, update_status):
    payload = {"action": "set", "key": "serve_host", "value": "日本語 & | % ^"}
    calls = []

    def run(command, **kwargs):
        calls.append(command[0])
        data = kwargs["input"]
        if command[0] == "ssh":
            assert command[-1] == "atk config snapshot"
            return subprocess.CompletedProcess(command, 2, b"", b"uv: trusted mount rejected (os error 448)")
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

    monkeypatch.setattr(settings_routes.subprocess, "run", run)
    if update_status:
        with pytest.raises(ValueError, match="保存"):
            settings_routes._remote_update("windows-host", payload)  # noqa: SLF001  # pylint: disable=protected-access
    else:
        settings_routes._remote_update("windows-host", payload)  # noqa: SLF001  # pylint: disable=protected-access
    assert calls == ["ssh", "ssh-unelevated", "ssh-unelevated"]

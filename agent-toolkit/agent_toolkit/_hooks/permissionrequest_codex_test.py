"""permissionrequest_codexの厳密一致と回収対象の保護を検証する。"""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import subprocess
import sys

import pytest

from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._common import plugin_root_leases
from agent_toolkit._hooks import permissionrequest_codex as subject

_ENTRYPOINT = pathlib.Path(__file__).resolve().parents[1] / "hook.py"
_ALLOW = {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": {"behavior": "allow"}}}


def _payload(command: str, *, tool_name: str = "Bash", event: str = "PermissionRequest") -> str:
    return json.dumps({"hook_event_name": event, "tool_name": tool_name, "tool_input": {"command": command}})


def _rm(target: pathlib.Path | str, *extra: str) -> str:
    tokens = ["rm", "-rf", "--", str(target), *extra]
    return subprocess.list2cmdline(tokens) if os.name == "nt" else shlex.join(tokens)


def _decision(capsys: pytest.CaptureFixture[str], command: str, **kwargs: str) -> object:
    assert subject.main(_payload(command, **kwargs)) == 0
    output = capsys.readouterr().out
    return json.loads(output) if output else None


def test_allows_rm_of_work_area_and_its_child(capsys: pytest.CaptureFixture[str]) -> None:
    """一時root直下の作業領域と、その配下の子をどちらも許可する。"""
    area = _managed_temp.create_managed_temp("hook-test")
    child = area / "nested"
    child.mkdir()
    assert _decision(capsys, _rm(area)) == _ALLOW
    assert _decision(capsys, _rm(child)) == _ALLOW
    assert area.is_dir(), "許可の判定だけを行い、対象を削除しない"


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf {area}",
        "rm -r -f -- {area}",
        "rm -rf -- {area} {area}",
        "rm -fr -- {area}",
        "rm -rf -- relative/path",
        "atk managed-temp cleanup --path {area}",
    ],
)
def test_rejects_noncanonical_command(command: str, capsys: pytest.CaptureFixture[str]) -> None:
    """`rm -rf -- <1パス>`以外の形は判定を返さない。"""
    area = _managed_temp.create_managed_temp("hook-test")
    assert _decision(capsys, command.format(area=shlex.quote(str(area)))) is None


def test_rejects_root_outside_and_link(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """root自身・root外・リンクとリンクを経由する経路を許可しない。"""
    area = _managed_temp.create_managed_temp("hook-test")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = _managed_temp.temp_root_path() / "hook-link"
    link.symlink_to(outside, target_is_directory=True)
    for target in (_managed_temp.temp_root_path(), outside, link, link / "child", area.parent.parent):
        assert _decision(capsys, _rm(target)) is None, target


def test_rejects_plugin_root_in_use(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """使用中の保持配布物とその配下は許可しない。"""
    area = _managed_temp.create_managed_temp("agents-server-plugin-root-shared")
    monkeypatch.setattr(plugin_root_leases, "plugin_root_in_use", lambda path: path == area)
    assert _decision(capsys, _rm(area)) is None
    assert _decision(capsys, _rm(area / "agent-toolkit")) is None


@pytest.mark.parametrize("payload", ["", "not json", "[]", json.dumps({"hook_event_name": "PermissionRequest"})])
def test_malformed_payload_returns_no_decision(payload: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert subject.main(payload) == 0
    assert not capsys.readouterr().out


def test_rejects_wrong_tool_and_event(capsys: pytest.CaptureFixture[str]) -> None:
    area = _managed_temp.create_managed_temp("hook-test")
    assert _decision(capsys, _rm(area), tool_name="Write") is None
    assert _decision(capsys, _rm(area), event="PreToolUse") is None


def test_entrypoint_subprocess_allows_valid_rm_without_deleting(tmp_path: pathlib.Path) -> None:
    """共通のhook起動スクリプト`hook.py`へ実stdinを渡してallow JSONと非削除を確認する。"""
    env = os.environ.copy()
    if os.name == "nt":
        env["LOCALAPPDATA"] = str(tmp_path / "local-app-data")
    else:
        env["XDG_CACHE_HOME"] = str(tmp_path / "cache")
        env["XDG_STATE_HOME"] = str(tmp_path / "state")
    root = (
        tmp_path / "local-app-data" / "agent-toolkit" / "managed-temp"
        if os.name == "nt"
        else tmp_path / "cache" / "agent-toolkit" / "managed-temp"
    )
    target = root / "hook-subprocess-abcd1234"
    target.mkdir(parents=True, mode=0o700)
    result = subprocess.run(
        [sys.executable, str(_ENTRYPOINT), "permissionrequest_codex"],
        input=_payload(_rm(target)),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env=env,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == _ALLOW
    assert not result.stderr
    assert target.is_dir()

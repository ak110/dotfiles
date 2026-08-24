"""pytools._internal.warm_agents_server のテスト。"""

import json
import pathlib

import pytest

from pytools._internal import claude_common as _claude_common
from pytools._internal import warm_agents_server as _warmup

from ._test_helpers import _FakeResult

_PLUGIN_ID = "agent-toolkit@ak110-dotfiles"


def _write_script(path: pathlib.Path) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def _setup(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> list[list[str]]:
    root = tmp_path / "dotfiles"
    _write_script(root / "scripts" / "agents_server_mcp.py")
    monkeypatch.setattr(_warmup.claude_common, "find_dotfiles_root", lambda: root)
    monkeypatch.setattr(_warmup.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))

    claude_script = tmp_path / "claude" / "scripts" / "agents_server_mcp.py"
    _write_script(claude_script)
    installed = tmp_path / "installed_plugins.json"
    installed.write_text(
        json.dumps({"plugins": {_PLUGIN_ID: [{"installPath": str(tmp_path / "claude")}]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(_warmup, "_INSTALLED_PLUGINS_PATH", installed)
    codex_script = (
        tmp_path
        / "codex"
        / "plugins"
        / "cache"
        / "ak110-dotfiles"
        / "agent-toolkit"
        / "1.0.0"
        / "scripts"
        / "agents_server_mcp.py"
    )
    _write_script(codex_script)

    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_kwargs: object) -> _FakeResult:
        calls.append(cmd)
        if cmd[:3] == ["codex", "plugin", "list"]:
            return _FakeResult(
                stdout=json.dumps({"installed": [{"pluginId": _PLUGIN_ID, "version": "1.0.0", "enabled": True}]})
            )
        return _FakeResult()

    monkeypatch.setattr(_claude_common, "run_subprocess", fake_run)
    return calls


def test_warms_all_targets_with_help(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """3系統の参照先へ同じ``--help``ウォームアップを行う。"""
    calls = _setup(monkeypatch, tmp_path)

    assert _warmup.run() is False
    warmups = [cmd for cmd in calls if cmd[:4] == ["uv", "run", "--no-project", "--script"]]
    assert len(warmups) == 3
    assert all(cmd[-1] == "--help" for cmd in warmups)


def test_missing_uv_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """uv不在時は外部コマンドを実行しない。"""
    calls = _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(_warmup.shutil, "which", lambda name: None if name == "uv" else f"/usr/bin/{name}")

    assert _warmup.run() is False
    assert not calls


def test_warmup_failure_does_not_raise(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """個別のuv失敗を後処理全体へ伝播させない。"""
    calls = _setup(monkeypatch, tmp_path)

    def fake_run(cmd: list[str], **_kwargs: object) -> _FakeResult:
        calls.append(cmd)
        if cmd[:3] == ["codex", "plugin", "list"]:
            return _FakeResult(stdout=json.dumps({"installed": []}))
        return _FakeResult(returncode=1)

    monkeypatch.setattr(_claude_common, "run_subprocess", fake_run)
    assert _warmup.run() is False

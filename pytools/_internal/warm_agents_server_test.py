"""pytools._internal.warm_agents_server のテスト。"""

import json
import logging
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


def test_warms_all_targets_with_dependency_check(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """2系統の参照先へ同じ依存検査を行う。"""
    calls = _setup(monkeypatch, tmp_path)

    assert _warmup.run() is False
    warmups = [cmd for cmd in calls if cmd[:4] == ["uv", "run", "--no-project", "--script"]]
    assert len(warmups) == 2
    assert all(cmd[-1] == "--check-dependencies" for cmd in warmups)


def test_repository_scripts_are_not_targets(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """リポジトリ内の同名スクリプトは参照先へ加えない。"""
    calls = _setup(monkeypatch, tmp_path)
    repository = tmp_path / "dotfiles"
    _write_script(repository / "scripts" / "agents_server_mcp.py")
    _write_script(repository / "agent-toolkit" / "scripts" / "agents_server_mcp.py")
    monkeypatch.setattr(_warmup.claude_common, "find_dotfiles_root", lambda: repository)

    assert _warmup.run() is False
    warmups = [cmd for cmd in calls if cmd[:4] == ["uv", "run", "--no-project", "--script"]]
    assert len(warmups) == 2
    assert all(not any(str(repository) in argument for argument in cmd) for cmd in warmups)


def test_missing_target_is_logged_and_not_warmed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """不在の参照先を除外し、残る参照先だけをウォームアップする。"""
    calls = _setup(monkeypatch, tmp_path)
    missing = tmp_path / "claude" / "scripts" / "agents_server_mcp.py"
    missing.unlink()
    caplog.set_level(logging.INFO, logger=_warmup.__name__)

    assert _warmup.run() is False
    warmups = [cmd for cmd in calls if cmd[:4] == ["uv", "run", "--no-project", "--script"]]
    assert len(warmups) == 1
    assert all(str(missing) not in argument for cmd in warmups for argument in cmd)
    assert f"対象が存在しないため除外: {missing}" in caplog.text


def test_no_existing_target_skips(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """参照先がすべて不在ならウォームアップを行わない。"""
    calls = _setup(monkeypatch, tmp_path)
    (tmp_path / "claude" / "scripts" / "agents_server_mcp.py").unlink()
    (
        tmp_path
        / "codex"
        / "plugins"
        / "cache"
        / "ak110-dotfiles"
        / "agent-toolkit"
        / "1.0.0"
        / "scripts"
        / "agents_server_mcp.py"
    ).unlink()

    assert _warmup.run() is False
    assert not [cmd for cmd in calls if cmd[:4] == ["uv", "run", "--no-project", "--script"]]


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

"""pytools._internal.warm_pyfltr_mcp のテスト。

実際の`uvx`を起動せず、Claude Code・CodexのMCP定義から導出した起動形と失敗の伝播を検証する。
"""

import json
import logging
import pathlib

import pytest

from pytools._internal import claude_common as _claude_common
from pytools._internal import warm_pyfltr_mcp as _warmup

from ._test_helpers import _FakeResult, command_matches

_PLUGIN_ID = "agent-toolkit@ak110-dotfiles"
_CODEX_ROOT = pathlib.Path("codex") / "plugins" / "cache" / "ak110-dotfiles" / "agent-toolkit" / "1.0.0"


def _definition(args: list[str], *, command: str = "uvx", stdio: bool = False) -> dict[str, object]:
    server: dict[str, object] = {"command": command, "args": args}
    if stdio:
        server = {"type": "stdio", **server}
    return {"mcpServers": {"pyfltr": server, "agents_server": {"command": "uv", "args": ["run"]}}}


def _write(path: pathlib.Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


class _Env:
    """一時領域へClaude Code・Codexのplugin配置を作成し、外部コマンドを記録する。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        self.calls: list[list[str]] = []
        self.uvx_result: _FakeResult | None = _FakeResult(stdout="pyfltr 3.19.5\n")
        self.claude_definition = tmp_path / "claude" / ".mcp.json"
        self.codex_definition = tmp_path / _CODEX_ROOT / ".mcp.codex.json"
        requirement = ["--from", "pyfltr>=3.17.8", "pyfltr", "mcp"]
        _write(self.claude_definition, _definition(requirement))
        _write(self.codex_definition, _definition(requirement, stdio=True))
        installed = tmp_path / "installed_plugins.json"
        _write(installed, {"plugins": {_PLUGIN_ID: [{"installPath": str(tmp_path / "claude")}]}})
        monkeypatch.setattr(_warmup, "_INSTALLED_PLUGINS_PATH", installed)
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
        monkeypatch.setattr(_claude_common, "resolve_executable", lambda name, **_kwargs: pathlib.Path(name))
        monkeypatch.setattr(_claude_common, "run_subprocess", self._run)

    def _run(self, cmd: list[str], **_kwargs: object) -> _FakeResult | None:
        self.calls.append(cmd)
        if command_matches(cmd, ["codex", "plugin", "list"]):
            return _FakeResult(
                stdout=json.dumps({"installed": [{"pluginId": _PLUGIN_ID, "version": "1.0.0", "enabled": True}]})
            )
        return self.uvx_result

    def uvx_calls(self) -> list[list[str]]:
        return [cmd for cmd in self.calls if pathlib.Path(cmd[0]).stem == "uvx"]


@pytest.fixture(name="env")
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> _Env:
    return _Env(monkeypatch, tmp_path)


def test_same_launch_from_both_hosts_runs_once(env: _Env, caplog: pytest.LogCaptureFixture) -> None:
    """Claude Code・Codexの両定義から同じ起動形を導出し、`mcp`を`--version`へ置き換えて1回だけ起動する。"""
    caplog.set_level(logging.INFO)

    assert _warmup.run() is False

    assert [cmd[1:] for cmd in env.uvx_calls()] == [["--from", "pyfltr>=3.17.8", "pyfltr", "--version"]]
    assert "環境構築を確認 (exit 0、" in caplog.text
    assert ".mcp.json" in caplog.text
    assert ".mcp.codex.json" in caplog.text


def test_changed_requirement_is_used_for_each_definition(env: _Env) -> None:
    """定義の要求指定を変えた版では、変更後の要求指定で起動する。定義ごとに異なれば両方を起動する。"""
    _write(env.codex_definition, _definition(["--from", "pyfltr>=9.9.9", "pyfltr", "mcp"], stdio=True))

    _warmup.run()

    assert sorted(cmd[2] for cmd in env.uvx_calls()) == ["pyfltr>=3.17.8", "pyfltr>=9.9.9"]
    assert all(cmd[-1] == "--version" for cmd in env.uvx_calls())


def test_missing_uvx_fails_without_running(env: _Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """uvx不在は外部コマンドを実行せず工程の失敗とする。"""
    monkeypatch.setattr(
        _claude_common,
        "resolve_executable",
        lambda name, **_kwargs: None if name == "uvx" else pathlib.Path(name),
    )

    with pytest.raises(RuntimeError, match="uvx CLI が見つからず"):
        _warmup.run()
    assert not env.calls


def test_missing_definitions_fail(env: _Env) -> None:
    """両ホストのMCP定義が不在なら工程の失敗とする。"""
    env.claude_definition.unlink()
    env.codex_definition.unlink()

    with pytest.raises(RuntimeError, match="MCP定義が見つからず"):
        _warmup.run()
    assert not env.uvx_calls()


@pytest.mark.parametrize(
    ("definition", "message"),
    [
        ({"mcpServers": {"agents_server": {"command": "uv", "args": []}}}, "MCP定義にpyfltrが無い"),
        (_definition(["--from", "pyfltr", "pyfltr", "mcp"], command="uv"), "起動形が`uvx ... mcp`ではない"),
        (_definition(["--from", "pyfltr", "pyfltr", "run"]), "起動形が`uvx ... mcp`ではない"),
    ],
)
def test_invalid_definition_fails(env: _Env, definition: dict[str, object], message: str) -> None:
    """pyfltrの欠落と起動形の不一致は起動せずに工程の失敗とする。"""
    _write(env.claude_definition, definition)

    with pytest.raises(RuntimeError, match=message):
        _warmup.run()
    assert not env.uvx_calls()


@pytest.mark.parametrize(
    ("result", "message"),
    [(_FakeResult(returncode=2, stderr="resolution failed"), "exit 2"), (None, "exit codeなし")],
)
def test_launch_failure_fails(env: _Env, result: _FakeResult | None, message: str) -> None:
    """非0終了と、上限到達・起動不能で結果が無い場合を工程の失敗とする。"""
    env.uvx_result = result

    with pytest.raises(RuntimeError, match=message):
        _warmup.run()

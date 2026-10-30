"""pytools._internal.warmup_agents_server のテスト。

`resolve_executable`・`common.run_subprocess`・plugin一覧の入力を差し替え、
ウォームアップ対象の列挙と、構築の失敗をスキップと数えることを検証する。実際の`uv`は起動しない。
"""

import asyncio
import json
import pathlib
import subprocess
import sys

import pytest

from pytools._internal import common as _common
from pytools._internal import post_apply_outcome
from pytools._internal import warmup_agents_server as _warmup
from pytools._internal.warmup_agents_server import _check_startup as _CHECK_STARTUP
from pytools._internal.warmup_agents_server import _check_targets as _CHECK_TARGETS
from pytools._internal.warmup_agents_server import _StartupOutcome as _STARTUP_OUTCOME

from ._test_helpers import _FakeResult, command_matches

_PLUGIN_ID = "agent-toolkit@ak110-dotfiles"


def _write_script(path: pathlib.Path) -> pathlib.Path:
    """起動スクリプトのダミーを作成してパスを返す。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    if path.name == "agents_server_mcp.py":
        root = path.parents[1]
        (root / "pyproject.toml").write_text('[project]\nname = "agent-toolkit"\n', encoding="utf-8")
        (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        launcher = path.parents[1] / "scripts" / "launch.py"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text("", encoding="utf-8")
    return path


def _codex_list_json(*entries: dict[str, object]) -> str:
    return json.dumps({"installed": list(entries)}, ensure_ascii=False)


def _setup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    *,
    codex_entries: tuple[dict[str, object], ...] = ({"pluginId": _PLUGIN_ID, "version": "1.0.0", "enabled": True},),
    codex_returncode: int = 0,
    installed_plugins: object | str | None = None,
    warmup_returncode: int | None = 0,
) -> list[list[str]]:
    """対象パスと外部コマンドを差し替え、記録用のコマンド一覧を返す。

    `uv`・`codex`はPATH上に存在する扱いとし、`codex plugin list --json`は
    `codex_entries`から組み立てた出力を返す。`uv python find`はテスト専用のPython実行
    ファイルを返す。launcherの結果は`warmup_returncode`で指定し、
    `None`は実行自体の失敗（タイムアウト等）を表す。
    """
    monkeypatch.setattr(_common, "resolve_executable", lambda name, **_kwargs: pathlib.Path(name))
    monkeypatch.setattr(_common, "find_dotfiles_root", lambda: None)

    async def ready(_command):
        return _STARTUP_OUTCOME(True, "起動確認済み")

    monkeypatch.setattr(_warmup, "_check_startup", ready)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))

    plugin_cache = tmp_path / "claude" / "plugins" / "cache" / "ak110-dotfiles" / "agent-toolkit" / "1.0.0"
    _write_script(plugin_cache / "agent_toolkit" / "agents_server_mcp.py")
    _write_script(plugin_cache / "scripts" / "launch.py")
    installed_path = tmp_path / "installed_plugins.json"
    if installed_plugins is None:
        installed_plugins = {"version": 2, "plugins": {_PLUGIN_ID: [{"installPath": str(plugin_cache)}]}}
    if isinstance(installed_plugins, str):
        installed_path.write_text(installed_plugins, encoding="utf-8")
    else:
        installed_path.write_text(json.dumps(installed_plugins, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(_warmup, "_INSTALLED_PLUGINS_PATH", installed_path)

    python = tmp_path / "python"
    python.write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run_subprocess(cmd: list[str], **_kwargs: object) -> _FakeResult | None:
        calls.append(cmd)
        if command_matches(cmd, ["codex", "plugin", "list"]):
            return _FakeResult(returncode=codex_returncode, stdout=_codex_list_json(*codex_entries))
        if command_matches(cmd, ["uv", "python", "find"]):
            return _FakeResult(returncode=0, stdout=f"{python}\n")
        if warmup_returncode is None:
            return None
        return _FakeResult(returncode=warmup_returncode)

    monkeypatch.setattr(_common, "run_subprocess", fake_run_subprocess)

    async def check_command(command):
        result = fake_run_subprocess(list(command))
        completed = (
            None
            if result is None
            else subprocess.CompletedProcess(list(command), result.returncode, result.stdout, result.stderr)
        )
        return _warmup.plugin_warmup.CommandOutcome(list(command), completed, 0)

    monkeypatch.setattr(_warmup.plugin_warmup, "check_command", check_command)
    return calls


def _codex_script(codex_home: pathlib.Path, version: str) -> pathlib.Path:
    """Codexプラグインキャッシュ内の`agents_server_mcp.py`のパスを返す。"""
    return (
        codex_home
        / "plugins"
        / "cache"
        / "ak110-dotfiles"
        / "agent-toolkit"
        / version
        / "agent_toolkit"
        / "agents_server_mcp.py"
    )


def _warmed(calls: list[list[str]]) -> list[str]:
    """記録済みコマンドから依存検査付きで起動したスクリプトのパスを取り出す。"""
    return [cmd[-2] for cmd in calls if cmd[-1:] == ["--check-dependencies"]]


def _prepared(calls: list[list[str]]) -> list[str]:
    """記録済みコマンドから依存準備を試行したスクリプトのパスを取り出す。"""
    return [cmd[-1] for cmd in calls if "--prepare" in cmd]


class TestPrerequisites:
    """uv不在時の挙動。"""

    def test_missing_uv_skips(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """uvが無い環境では外部コマンドを実行しない。"""
        calls = _setup(monkeypatch, tmp_path)
        monkeypatch.setattr(
            _common,
            "resolve_executable",
            lambda name, **_kwargs: None if name == "uv" else pathlib.Path(name),
        )

        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()
        assert not calls

    def test_minimal_mode_skips_before_target_resolution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """minimalモードは有効版の照会も依存準備も行わない。"""
        monkeypatch.setattr(_warmup.dotfiles_mode, "is_minimal", lambda: True)
        monkeypatch.setattr(_warmup, "_targets", lambda: pytest.fail("対象を解決しない"))
        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()


class TestTargets:
    """ウォームアップ対象の列挙。"""

    def test_warms_all_existing_targets_once(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """実在する2系統のpluginパスへ解決済みPythonで依存検査付きの起動確認を実行する。"""
        calls = _setup(monkeypatch, tmp_path)
        codex_script = _write_script(_codex_script(tmp_path / "codex", "1.0.0"))

        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()
        warmed = _warmed(calls)
        assert sorted(warmed) == sorted(
            [
                str(
                    tmp_path
                    / "claude"
                    / "plugins"
                    / "cache"
                    / "ak110-dotfiles"
                    / "agent-toolkit"
                    / "1.0.0"
                    / "agent_toolkit"
                    / "agents_server_mcp.py"
                ),
                str(codex_script),
            ]
        )
        assert len(warmed) == len(set(warmed))

    def test_resolves_python_once_before_warmup(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """対象ごとのprepareと起動確認は、1回だけ解決したPythonを共用する。"""
        calls = _setup(monkeypatch, tmp_path)
        _write_script(_codex_script(tmp_path / "codex", "1.0.0"))

        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()

        python_find = [call for call in calls if command_matches(call, ["uv", "python", "find"])]
        launcher_calls = [call for call in calls if "--project" in call]
        assert len(python_find) == 1
        assert launcher_calls
        assert {call[0] for call in launcher_calls} == {str(tmp_path / "python")}

    def test_missing_paths_are_excluded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """存在しない対象パスは除外し、残る対象は実行する。"""
        calls = _setup(
            monkeypatch,
            tmp_path,
            installed_plugins={"version": 2, "plugins": {_PLUGIN_ID: [{"installPath": str(tmp_path / "missing")}]}},
        )

        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()
        assert not _warmed(calls)

    def test_broken_installed_plugins_json_is_excluded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """`installed_plugins.json`が不正でも例外を送出せず残る対象を実行する。"""
        calls = _setup(monkeypatch, tmp_path, installed_plugins="{ broken")

        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()
        assert not _warmed(calls)

    def test_absent_installed_plugins_file_is_excluded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """`installed_plugins.json`が無い場合もClaude Code分だけを除外する。"""
        calls = _setup(monkeypatch, tmp_path)
        monkeypatch.setattr(_warmup, "_INSTALLED_PLUGINS_PATH", tmp_path / "absent.json")

        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()
        assert not _warmed(calls)


class TestCodexResolution:
    """Codexプラグインキャッシュの版解決。"""

    def test_uses_enabled_version_from_cli(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """配布元と異なる旧版が有効な場合、その版のパスを対象にする。"""
        calls = _setup(
            monkeypatch,
            tmp_path,
            codex_entries=({"pluginId": _PLUGIN_ID, "version": "0.9.0", "enabled": True},),
        )
        old_script = _write_script(_codex_script(tmp_path / "codex", "0.9.0"))
        current_script = _write_script(_codex_script(tmp_path / "codex", "1.0.0"))

        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()
        warmed = _warmed(calls)
        assert str(old_script) in warmed
        assert str(current_script) not in warmed

    def test_disabled_version_is_excluded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """`enabled`が偽の版はCodexから参照されないため対象にしない。"""
        calls = _setup(
            monkeypatch,
            tmp_path,
            codex_entries=({"pluginId": _PLUGIN_ID, "version": "1.0.0", "enabled": False},),
        )
        codex_script = _write_script(_codex_script(tmp_path / "codex", "1.0.0"))

        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()
        assert str(codex_script) not in _warmed(calls)

    def test_codex_home_env_is_honored(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """`CODEX_HOME`配下のキャッシュを対象にする。"""
        calls = _setup(monkeypatch, tmp_path)
        custom_home = tmp_path / "custom-codex"
        monkeypatch.setenv("CODEX_HOME", str(custom_home))
        script = _write_script(_codex_script(custom_home, "1.0.0"))

        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()
        assert str(script) in _warmed(calls)

    def test_missing_codex_cli_excludes_codex_target(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """codex CLI不在ではCodex分だけを除外して継続する。"""
        calls = _setup(monkeypatch, tmp_path)
        monkeypatch.setattr(
            _common,
            "resolve_executable",
            lambda name, **_kwargs: None if name == "codex" else pathlib.Path(name),
        )
        codex_script = _write_script(_codex_script(tmp_path / "codex", "1.0.0"))

        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()
        assert str(codex_script) not in _warmed(calls)
        assert len(_warmed(calls)) == 1

    def test_failed_codex_list_excludes_codex_target(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """一覧取得の失敗ではCodex分だけを除外して継続する。"""
        calls = _setup(monkeypatch, tmp_path, codex_returncode=1)
        codex_script = _write_script(_codex_script(tmp_path / "codex", "1.0.0"))

        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()
        assert str(codex_script) not in _warmed(calls)

    def test_missing_entry_excludes_codex_target(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """該当プラグインのエントリが無い場合もCodex分だけを除外する。"""
        calls = _setup(
            monkeypatch,
            tmp_path,
            codex_entries=({"pluginId": "other@other", "version": "1.0.0", "enabled": True},),
        )
        codex_script = _write_script(_codex_script(tmp_path / "codex", "1.0.0"))

        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()
        assert str(codex_script) not in _warmed(calls)


class TestFailureHandling:
    """構築の失敗はスキップと数え、残る対象の構築を続ける。"""

    def test_continues_after_nonzero_exit(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """非0終了でも残る対象を実行し、スキップを返す。"""
        calls = _setup(monkeypatch, tmp_path, warmup_returncode=1)
        _write_script(_codex_script(tmp_path / "codex", "1.0.0"))

        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()
        assert len(_prepared(calls)) == 1

    def test_continues_after_execution_error(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """実行自体の失敗（タイムアウト等でNone）でも残る対象を実行する。"""
        calls = _setup(monkeypatch, tmp_path, warmup_returncode=None)
        _write_script(_codex_script(tmp_path / "codex", "1.0.0"))

        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()
        assert len(_prepared(calls)) == 1

    def test_prepared_server_startup_failure_is_reported(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """依存準備の成功と起動失敗を区別し、残る対象を確認した後で失敗を返す。"""
        _setup(monkeypatch, tmp_path)
        observed = []

        async def fail_startup(command: list[str]):
            observed.append(command)
            return _STARTUP_OUTCOME(False, "起動確認に失敗")

        monkeypatch.setattr(_warmup, "_check_startup", fail_startup)
        outcome = _warmup.run()
        assert outcome.failure and "起動確認に失敗" in outcome.failure
        assert outcome.next_action and "update-dotfiles logs" in outcome.next_action
        assert len(observed) == 1
        assert "--check-dependencies" not in observed[0]
        assert "--prepare" not in observed[0]


@pytest.mark.parametrize("behavior", ["ready", "empty", "timeout", "exit"])
def test_public_stdio_startup_checks_protocol_and_closes_owned_process(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, behavior: str
) -> None:
    """公開MCPのinitializeとtools/listを使い、0件・停止・異常終了を起動成功へ混ぜない。"""
    server = tmp_path / "server.py"
    server.write_text(
        "import json, sys, time\n"
        f"behavior = {behavior!r}\n"
        "for line in sys.stdin:\n"
        "    message = json.loads(line)\n"
        "    if 'id' not in message: continue\n"
        "    if behavior == 'exit':\n"
        "        print('startup failed', file=sys.stderr); sys.exit(7)\n"
        "    if behavior == 'timeout': time.sleep(60)\n"
        "    if message['method'] == 'initialize':\n"
        "        result = {'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}},\n"
        "                  'serverInfo': {'name': 'startup-probe', 'version': '1'}}\n"
        "    else:\n"
        "        result = {'tools': [] if behavior == 'empty' else [{'name': 'ping', 'inputSchema': {'type': 'object'}}]}\n"
        "    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_warmup, "_STARTUP_TIMEOUT", 0.5)
    outcome = asyncio.run(_CHECK_STARTUP([sys.executable, str(server)]))
    assert outcome.ok is (behavior == "ready")
    if behavior != "ready":
        assert "MCP起動確認に失敗" in outcome.message
        assert str(server) in outcome.message
    if behavior == "exit":
        assert "startup failed" in outcome.message


@pytest.mark.parametrize("failed", [None, "dependency", "startup", "exception"])
def test_parallel_checks_keep_order_and_collect_other_roots(monkeypatch, failed):
    """独立rootを重ね、1root失敗でも残るrootの確認を終了まで回収する。"""
    commands = [["python", name] for name in ("first", "second", "third")]
    observed = []

    async def exercise():
        all_started = asyncio.Event()

        async def dependency(command):
            name = command[-2]
            observed.append((name, "dependency"))
            if len(observed) == 3:
                all_started.set()
            await asyncio.wait_for(all_started.wait(), 5)
            if name == "second" and failed == "exception":
                raise RuntimeError("確認の例外")
            code = 7 if name == "second" and failed == "dependency" else 0
            return _warmup.plugin_warmup.CommandOutcome(command, subprocess.CompletedProcess(command, code, "", ""), 0)

        async def startup(command):
            observed.append((command[-1], "startup"))
            return _STARTUP_OUTCOME(not (command[-1] == "second" and failed == "startup"), command[-1])

        monkeypatch.setattr(_warmup.plugin_warmup, "check_command", dependency)
        monkeypatch.setattr(_warmup, "_check_startup", startup)
        return await _CHECK_TARGETS(commands)

    outcomes = asyncio.run(exercise())
    assert len(outcomes) == 3
    first, second, third = outcomes
    assert not isinstance(first, BaseException)
    assert not isinstance(third, BaseException)
    assert first[1] is not None and first[1].ok
    assert third[1] is not None and third[1].ok
    assert ("first", "startup") in observed and ("third", "startup") in observed
    if failed == "exception":
        assert isinstance(second, RuntimeError)
    else:
        assert not isinstance(second, BaseException)
        if failed == "dependency":
            assert second[1] is None
        else:
            assert second[1] is not None
            assert second[1].ok is (failed is None)


class TestCoverage:
    """統合したウォームアップが温める環境の範囲。"""

    def test_targets_prepare_hook_plugin_root(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """hookが使う各plugin rootは公開launcherのprepare対象に含まれる。"""
        calls = _setup(monkeypatch, tmp_path)
        claude_root = tmp_path / "claude" / "plugins" / "cache" / "ak110-dotfiles" / "agent-toolkit" / "1.0.0"
        codex_root = tmp_path / "codex" / "plugins" / "cache" / "ak110-dotfiles" / "agent-toolkit" / "1.0.0"
        _write_script(codex_root / "agent_toolkit" / "agents_server_mcp.py")

        _warmup.run()

        assert sorted(_warmed(calls)) == sorted(
            [
                str(claude_root / "agent_toolkit" / "agents_server_mcp.py"),
                str(codex_root / "agent_toolkit" / "agents_server_mcp.py"),
            ]
        )
        assert all(cmd[-1] == "--check-dependencies" for cmd in calls if "--check-dependencies" in cmd)

    def test_worktree_script_is_target(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """作業ツリーの基盤CLIとserveが使う起動スクリプトもfull warmupで準備する。"""
        calls = _setup(monkeypatch, tmp_path)
        repository = tmp_path / "dotfiles"
        _write_script(repository / "agent-toolkit" / "agent_toolkit" / "agents_server_mcp.py")
        monkeypatch.setattr(_common, "find_dotfiles_root", lambda: repository)

        _warmup.run()

        assert str(repository / "agent-toolkit" / "agent_toolkit" / "agents_server_mcp.py") in _warmed(calls)

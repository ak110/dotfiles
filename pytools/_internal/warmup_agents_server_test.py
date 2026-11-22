"""pytools._internal.warmup_agents_server のテスト。

`resolve_executable`・`common.run_subprocess`・plugin一覧の入力を差し替え、
ウォームアップ対象の列挙と、構築の失敗をスキップと数えることを検証する。実際の`uv`は起動しない。
"""

import asyncio
import contextlib
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Any, cast

import anyio
import mcp
import mcp.client.stdio
import psutil
import pytest
from agent_toolkit._agents_server import service_forward, service_transport
from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._common import plugin_environment, plugin_environment_cleanup, process_tree, session_review, windows_job
from agent_toolkit._common.runtime_environment import CallerContext
from agent_toolkit._testing import isolation, late_descendant
from agent_toolkit._testing.codex_plugin_support import _REAL_MCP_PREPARE_TIMEOUT
from agent_toolkit._testing.plugin_environment_support import prepare_plugin_environment

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
        launcher = root / "scripts" / "launch.py"
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text("# /// script\n# ///\n", encoding="utf-8")
        (root / "scripts" / "launch.py.lock").write_text("version = 1\n", encoding="utf-8")
    return path


def _main_checkout_config(path: pathlib.Path) -> dict[str, str]:
    return {"repositories": json.dumps({"github.com/ak110/dotfiles": {"path": str(path), "edit": "allow"}})}


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
    monkeypatch.setattr(_common, "find_dotfiles_root", lambda: tmp_path)
    monkeypatch.setattr(_warmup.config_settings, "load_config_strict", lambda: _main_checkout_config(tmp_path))
    monkeypatch.setattr(_warmup.claude_common, "CLAUDE_HOME", tmp_path / "claude")
    # 合成の導入環境を参照する実プロセスはない。回収処理そのものは実行する。
    monkeypatch.setattr(plugin_environment_cleanup, "_process_references", lambda: ([], False))

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


class TestElevated:
    """Windowsで昇格したプロセスから実行した場合。"""

    def test_elevated_run_defers_service_publication_to_next_unelevated_run(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """依存の準備は行い、管理サービスの起動と公開は行わず、次の非昇格の実行で起動確認する。

        2026-11-19、SSHで昇格して実行した`update-dotfiles`が管理サービスを起動して受付先として公開し、
        非昇格の旧世代と会話の転送側が新しい受付先を使えなくなった。
        """
        calls = _setup(monkeypatch, tmp_path)
        started: list[list[str]] = []

        async def startup(command):
            started.append(list(command))
            return _STARTUP_OUTCOME(True, "起動確認済み")

        monkeypatch.setattr(_warmup, "_check_startup", startup)
        monkeypatch.setattr(_warmup.windows_security, "is_elevated_above_user_processes", lambda: True)

        outcome = _warmup.run()

        assert outcome.failure is None and outcome.deferred
        assert [notice.command for notice in outcome.notices] == ["update-dotfiles"]
        assert _warmed(calls) and not started

        monkeypatch.setattr(_warmup.windows_security, "is_elevated_above_user_processes", lambda: False)
        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()
        assert len(started) == 1


class TestTargets:
    """ウォームアップ対象の列挙。"""

    def test_update_marker_is_not_a_service_startup_input(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """再実行の整理用の印だけが変わっても、同じサービスの起動確認を繰り返さない。"""
        _setup(monkeypatch, tmp_path)
        monkeypatch.setattr(_warmup.post_apply_cache, "record_path", lambda name: tmp_path / f"{name}.json")
        monkeypatch.setattr(_warmup.plugin_warmup, "cleanup_prepared", lambda _prepared: None)
        monkeypatch.setattr(_warmup.windows_security, "is_elevated_above_user_processes", lambda: False)
        started: list[list[str]] = []

        async def startup(command: list[str]):
            started.append(command)
            return _STARTUP_OUTCOME(True, "起動確認済み")

        monkeypatch.setattr(_warmup, "_check_startup", startup)
        monkeypatch.setenv("DOTFILES_PRUNE_READY_FILE", str(tmp_path / "update-one"))
        monkeypatch.setenv("UPDATE_DOTFILES_BOOTSTRAP_REPORT", str(tmp_path / "bootstrap-one.json"))
        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()
        assert len(started) == 1

        monkeypatch.setenv("DOTFILES_PRUNE_READY_FILE", str(tmp_path / "update-two"))
        monkeypatch.setenv("UPDATE_DOTFILES_BOOTSTRAP_REPORT", str(tmp_path / "bootstrap-two.json"))
        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()
        assert len(started) == 1

        monkeypatch.setenv("AGENT_TOOLKIT_RUNTIME_DATA", str(tmp_path / "new-runtime"))
        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()
        assert len(started) == 2

    def test_cached_full_update_reclaims_completed_bundle(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """MCPの起動確認を省略する更新も、成功確定済みの抽出物を回収する。"""
        _setup(monkeypatch, tmp_path)
        monkeypatch.setattr(_warmup.post_apply_cache, "matches", lambda *_args, **_kwargs: True)
        bundle = tmp_path / "bundle"
        bundle.mkdir()
        scope = bundle / "scope.json"
        scope.write_text('{"version":1,"records":{}}', encoding="utf-8")
        raw = bundle / "timeline.jsonl"
        raw.write_bytes(b"reproducible")
        runs = session_review.directory() / "runs"
        runs.mkdir(parents=True, exist_ok=True)
        (runs / "old-completed.json").write_text(
            json.dumps(
                {
                    "run_id": "old-completed",
                    "status": "completed",
                    "targets": [],
                    "prepared": [
                        {
                            "scope_path": str(scope),
                            "disposable_bundle_files": {
                                "timeline.jsonl": hashlib.sha256(b"reproducible").hexdigest(),
                            },
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        assert _warmup.run() == post_apply_outcome.PostApplyOutcome()
        assert not raw.exists() and scope.is_file()

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
        # 同じ依存定義を持つrootは環境準備を共有するが、起動確認は両rootへ届く。
        assert len(_prepared(calls)) == 1
        assert _prepared(calls)[0] in warmed

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

    def test_registered_legacy_version_keeps_shared_environments_until_registration_moves(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """旧形式の版が登録されている間は共有環境を回収せず、登録が新しい版へ移った後の更新で回収する。

        依存の固定内容が`uv.lock`だった旧形式の版の共有環境は、現行の起動部分では特定できない。
        2026-11-19、Claude Codeの登録が旧版のまま更新すると、その版のhookとMCPが使う共有環境を回収し、次の起動が失敗した。
        """
        _setup(monkeypatch, tmp_path)
        _write_script(_codex_script(tmp_path / "codex", "1.0.0"))
        claude_cache = tmp_path / "claude" / "plugins" / "cache" / "ak110-dotfiles" / "agent-toolkit"
        legacy = claude_cache / "0.9.0"
        _write_script(legacy / "agent_toolkit" / "agents_server_mcp.py")
        (legacy / "scripts" / "launch.py.lock").unlink()
        (legacy / "pyproject.toml").write_text('[project]\nname = "agent-toolkit"\n', encoding="utf-8")
        (legacy / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        legacy_environment = plugin_environment.runtime_data(os.environ) / "environments" / "legacy"
        (legacy_environment / "bin").mkdir(parents=True)

        def register(root: pathlib.Path) -> None:
            (tmp_path / "installed_plugins.json").write_text(
                json.dumps({"version": 2, "plugins": {_PLUGIN_ID: [{"installPath": str(root)}]}}), encoding="utf-8"
            )

        register(legacy)
        assert _warmup.run().failure is None
        assert legacy_environment.is_dir()

        register(claude_cache / "1.0.0")
        assert _warmup.run().failure is None
        assert not legacy_environment.exists()


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
        assert outcome.next_action == post_apply_outcome.CHECK_UPDATE_LOG
        assert len(observed) == 1
        assert "--check-dependencies" not in observed[0]
        assert "--prepare" not in observed[0]


@pytest.mark.parametrize("behavior", ["ready", "empty", "timeout", "exit"])
def test_public_stdio_startup_checks_protocol_and_closes_owned_process(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, behavior: str, caplog: pytest.LogCaptureFixture
) -> None:
    """公開MCPのinitializeとtools/listを使い、0件・停止・異常終了を起動成功へ混ぜない。"""
    server = _probe_server(tmp_path, behavior)
    monkeypatch.setattr(_warmup, "_STARTUP_TIMEOUT", 0.5)
    tasks: list[asyncio.Task[None]] = []
    fail_after = anyio.fail_after

    async def arm(scope: anyio.CancelScope) -> None:
        ready = tmp_path / ("startup.ready" if behavior == "timeout" else "process.ready")
        while not ready.exists():
            await asyncio.sleep(0.01)
        scope.deadline = anyio.current_time() + (0.05 if behavior == "timeout" else 0.5)

    @contextlib.contextmanager
    def after_ready(delay, **kwargs):
        # プロセスの起動時間を除き、応答待ちだけを短い期限で観測する。
        controlled = delay == 0.5
        with fail_after(30 if controlled else delay, **kwargs) as scope:
            if controlled:
                tasks.append(asyncio.create_task(arm(scope)))
            yield scope

    monkeypatch.setattr(anyio, "fail_after", after_ready)

    async def check():
        try:
            return await _CHECK_STARTUP([sys.executable, str(server)])
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    outcome = asyncio.run(check())
    assert outcome.ok is (behavior == "ready")
    if behavior != "ready":
        assert "MCP起動確認に失敗" in outcome.message
        assert str(server) in outcome.message
    if behavior == "exit":
        assert "startup failed" in outcome.message and "Traceback" not in outcome.message
        assert "startup failed" in caplog.text and "Traceback" in caplog.text


def _stop_services(directory: pathlib.Path) -> None:
    """公開前の候補を含め、隔離した管理領域に残る管理サービスを専用の起動元とともに止める。"""
    for path in directory.glob("*.json"):
        if path.name == "generations.json":
            continue
        identity = json.loads(path.read_text(encoding="utf-8"))["process"]
        if not service_transport.process_alive(identity):
            continue
        with contextlib.suppress(psutil.NoSuchProcess):
            service = psutil.Process(int(identity.split(":", 1)[0]))
            owners = [process for process in service.parents() if _serves(process, directory)]
            result = process_tree.stop_tree(owners[-1] if owners else service)
            assert not result.residual and not result.unresolved, result.descriptions()
        assert not service_transport.process_alive(identity)


def _probe_server(tmp_path: pathlib.Path, behavior: str, *, stderr: str = "") -> pathlib.Path:
    """initializeとtools/listへ`behavior`どおりに応答するMCPサーバーのスクリプトを作る。"""
    server = tmp_path / "server.py"
    server.write_text(
        "import json, pathlib, sys, time\n"
        f"pathlib.Path({str(tmp_path / 'process.ready')!r}).touch()\n"
        f"sys.stderr.write({stderr!r})\n"
        f"behavior = {behavior!r}\n"
        "for line in sys.stdin:\n"
        "    message = json.loads(line)\n"
        "    if 'id' not in message: continue\n"
        "    if behavior == 'exit':\n"
        "        print('startup failed', file=sys.stderr); sys.exit(7)\n"
        "    if behavior == 'timeout':\n"
        f"        exec({late_descendant.held_source(tmp_path / 'startup.release')!r})\n"
        "    if message['method'] == 'initialize':\n"
        "        result = {'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}},\n"
        "                  'serverInfo': {'name': 'startup-probe', 'version': '1'}}\n"
        "    else:\n"
        "        result = {'tools': [] if behavior == 'empty' else [{'name': 'ping', 'inputSchema': {'type': 'object'}}]}\n"
        "    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)\n",
        encoding="utf-8",
    )
    return server


@pytest.mark.skipif(sys.platform == "win32", reason="POSIXのファイルサイズ制限を使う")
def test_service_start_keeps_stderr_when_child_cannot_write_files(tmp_path, caplog):
    """子が通常ファイルに追記できないときも、起動前に出た原因と終了値を回収する。"""
    service = tmp_path / "agent_toolkit" / "_agents_server" / "service.py"
    service.parent.mkdir(parents=True)
    service.touch()
    script = (
        "import os, resource, sys\n"
        "resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))\n"
        "try: os.write(2, b'missing dependency XYZ\\nstartup failed\\n')\n"
        "finally: sys.exit(17)\n"
    )
    outcome = asyncio.run(_CHECK_STARTUP([sys.executable, "-c", script, "--project", str(tmp_path)]))
    assert not outcome.ok and "exit 17" in outcome.message and "startup failed" in outcome.message
    assert "missing dependency XYZ" in caplog.text


@pytest.mark.windows
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
@pytest.mark.parametrize("failed", ["service-start", "publish", None])
@pytest.mark.parametrize("inherited_job", [False, True])
def test_startup_reports_only_persistent_job_warning_and_preserves_failed_child_output(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failed: str | None,
    inherited_job: bool,
    newline: str,
) -> None:
    """常駐起動と確認用接続の両方から診断が出ても、確認用接続だけのJob警告は画面に出さない。"""
    root = tmp_path / "root"
    service = root / "agent_toolkit" / "_agents_server" / "service.py"
    service.parent.mkdir(parents=True)
    service.write_text("", encoding="utf-8")
    probe = _probe_server(tmp_path, "ready", stderr=service_forward.INHERITED_JOB_WARNING + "\n短命な確認の診断\n")
    command = [sys.executable, str(probe), "--project", str(root)]
    deferred: list[str | None] = []
    check_command = _warmup.plugin_warmup.check_command

    async def start_service(started: list[str], *, environment: dict[str, str], timeout: float):
        assert timeout > 0
        stderr = "常駐起動の標準エラー\n"
        if inherited_job:
            stderr += service_forward.INHERITED_JOB_WARNING + "\n"
        deferred.append(environment.get(service_forward.DEFER_PUBLISH_ENV))
        code = 1 if failed == "service-start" else 0
        script = f"import sys; sys.stderr.reconfigure(newline={newline!r}); sys.stderr.write({stderr!r}); sys.exit({code})"
        return await check_command([started[0], "-c", script], environment=environment, timeout=timeout)

    def publish(_caller: object, target: pathlib.Path, *, timeout: float) -> dict:
        assert target == root and timeout > 0
        if failed == "publish":
            raise RuntimeError("候補の世代が生存していない")
        return {}

    monkeypatch.setattr(_warmup.plugin_warmup, "check_command", start_service)
    monkeypatch.setattr(service_forward, "publish_service", publish)
    outcome = asyncio.run(_CHECK_STARTUP(command))
    assert outcome.ok is (failed is None)
    assert outcome.inherited_job is (inherited_job and failed != "service-start")
    if failed is not None:
        assert f"段階={failed}" in outcome.message
        assert "常駐起動の標準エラー" in caplog.text and "Traceback" in caplog.text
        assert ("短命な確認の診断" in caplog.text) is (failed == "publish")
    if failed == "service-start":
        assert "子の出力: 常駐起動の標準エラー" in outcome.message
    # 確認用の接続より先に、公開を保留して起動する。
    assert deferred == ["1"]


_PLUGIN_ROOT = pathlib.Path(plugin_environment.__file__).resolve().parents[2]


@pytest.fixture(name="service_environment", scope="module")
def _service_environment(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """実の管理サービスを起動する試験が共用する、準備済みの依存環境を指す環境を返す。状態領域は各試験が指定する。"""
    environment = {
        key: value
        for key, value in isolation.build_host_environ().items()
        if key not in {"AGENT_TOOLKIT_OWNER_SESSION", "CLAUDE_CODE_SESSION_ID", "VIRTUAL_ENV", "CLAUDECODE", "UV_FROZEN"}
    }
    # Windowsは依存環境のDLLを260文字を超えるパスから読み込めないため、試験名を含まない短い領域へ準備する。
    environment["AGENT_TOOLKIT_RUNTIME_DATA"] = str(tmp_path_factory.mktemp("rt"))
    prepare_plugin_environment(_PLUGIN_ROOT, environment, timeout=_REAL_MCP_PREPARE_TIMEOUT)
    return environment


def _serves(process: psutil.Process, directory: pathlib.Path) -> bool:
    """`process`が`directory`の管理サービスか、その専用の起動元かを返す。"""
    with contextlib.suppress(psutil.Error):
        return str(directory) in process.cmdline()
    return False


def _launch_command(root: pathlib.Path) -> list[str]:
    return [
        sys.executable,
        str(_PLUGIN_ROOT / "scripts" / "launch.py"),
        "--project",
        str(root),
        str(root / "agent_toolkit" / "agents_server_mcp.py"),
    ]


@pytest.mark.windows
@pytest.mark.timeout(240)
@pytest.mark.cpu_limit(
    3.0,
    reason="実のlauncher・転送側・管理サービスを起動し、MCPのinitializeとtools/listを通して世代を公開する。",
)
def test_published_service_outlives_startup_check(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, service_environment: dict[str, str]
) -> None:
    """起動確認が公開した管理サービスの世代は、確認用の接続と子プロセスの終了後も生存し、次の版の確認で受付を引き継ぐ。

    WindowsのMCP SDKは確認用の子プロセスを離脱不可のKILL_ON_JOB_CLOSEのJobへ入れ、接続を閉じるとJobの全プロセスを止める。
    2026-11-18、確認用の転送側が起動した新世代が確認の終了とともに終了し、受付先が終了した世代を指し続けた。
    """
    environment = dict(service_environment, XDG_STATE_HOME=str(tmp_path), LOCALAPPDATA=str(tmp_path))
    # 起動確認は自身の環境を、起動する子プロセスと公開先の管理領域の解決に使う。
    monkeypatch.setattr(os, "environ", environment)
    monkeypatch.setattr(_warmup, "_STARTUP_TIMEOUT", 120.0)
    # 開発worktreeを公開せず、通常の配布先として検証する。
    previous_root = tmp_path / "previous" / "agent-toolkit"
    updated = tmp_path / "updated" / "agent-toolkit"
    ignore = shutil.ignore_patterns(".venv", "__pycache__", "node_modules", "*_test.py")
    shutil.copytree(_PLUGIN_ROOT, previous_root, ignore=ignore)
    shutil.copytree(previous_root, updated, ignore=ignore)
    with (updated / "agent_toolkit" / "agents_server_mcp.py").open("a", encoding="utf-8") as script:
        script.write("# updated\n")
    registry = GenerationRegistry(service_transport.namespace(CallerContext(environment, tmp_path)))

    def published(target: pathlib.Path) -> dict:
        outcome = asyncio.run(_CHECK_STARTUP(_launch_command(target)))
        assert outcome.ok, outcome.message
        current = registry.current()
        assert current is not None and current["generation"] == service_forward.code_identity(target, registry.directory)[0]
        assert service_transport.process_alive(current["process"])
        return current

    try:
        previous = published(previous_root)
        published(updated)
        # 会話を持たない旧世代は、次の世代の公開後に退出する。
        deadline = time.monotonic() + 30
        while service_transport.process_alive(previous["process"]):
            assert time.monotonic() < deadline, "旧世代が退出しない"
            time.sleep(0.1)
    finally:
        _stop_services(registry.directory)


@pytest.mark.windows
@pytest.mark.skipif(sys.platform != "win32", reason="Windows Jobの終了が所属プロセスを止める条件を実APIで確かめる")
@pytest.mark.timeout(240)
@pytest.mark.cpu_limit(1.5, reason="実Windows Job・管理サービスと資源回収を通す。sthenoの所有CPU実測は1.079秒。")
def test_service_outlives_killing_job_of_its_launcher(tmp_path: pathlib.Path, service_environment: dict[str, str]) -> None:
    """離脱を許すKILL_ON_JOB_CLOSEのJobの中から起動した管理サービスは、そのJobの終了後も生存する。

    SSHのセッションは所属プロセスをこの種類のJobへ入れ、venvのpython.exeは子を自動で離脱させるJobをその内側に作る。
    2026-11-19、SSHのセッションから起動した管理サービスが外側のJobに残り、セッションの終了とともに終了した。
    """
    environment = dict(service_environment, XDG_STATE_HOME=str(tmp_path), LOCALAPPDATA=str(tmp_path))
    registry = GenerationRegistry(service_transport.namespace(CallerContext(environment, tmp_path)))
    try:
        with windows_job.WindowsJob() as job:

            def member(command: list[str]) -> subprocess.Popen[bytes]:
                process = subprocess.Popen(  # pylint: disable=consider-using-with
                    command, stdin=subprocess.DEVNULL, env=environment, creationflags=windows_job.CREATE_SUSPENDED
                )
                # _handleはWindows版CPythonの属性であり、Linux上の型検査からは解決できない。
                job.assign_suspended(int(cast(Any, process)._handle), process.pid)  # pylint: disable=protected-access,no-member
                return process

            # 入力の無い転送側は、管理サービスをreadyにして公開した後で終了する。
            launcher = member(_launch_command(_PLUGIN_ROOT))
            unbroken = member([sys.executable, "-c", "import time; time.sleep(600)"])
            assert launcher.wait(timeout=120) == 0
            current = registry.current()
            assert current is not None and service_transport.process_alive(current["process"])
            # 管理サービスは専用の起動元が寿命を所有するため、起動元がJobに残ると、Jobの終了に遅れて管理サービスも止まる。
            service = psutil.Process(int(current["process"].split(":", 1)[0]))
            owners = [process for process in service.parents() if _serves(process, registry.directory)]
            assert owners
        # Jobの終了が所属プロセスへ届いたことを、離脱していない所属プロセスの終了で確かめる。
        unbroken.wait(timeout=30)
        exited, _running = psutil.wait_procs([service, *owners], timeout=3)
        assert not exited
        assert service_transport.process_alive(current["process"])
    finally:
        _stop_services(registry.directory)


def test_unreclaimed_startup_process_is_reported_apart_from_startup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """起動確認が回収できなかった子プロセスを、起動の失敗へ混ぜず回収不能として返し、残る対象の確認を続ける。"""
    commands = [["python", "first"], ["python", "second"]]

    async def dependency(command):
        return _warmup.plugin_warmup.CommandOutcome(command, subprocess.CompletedProcess(command, 0, "", ""), 0)

    async def startup(command):
        if command[-1] == "first":
            raise process_tree.ReclaimError(["1: 残存"], [], context="起動確認")
        return _STARTUP_OUTCOME(True, command[-1])

    monkeypatch.setattr(_warmup.plugin_warmup, "check_command", dependency)
    monkeypatch.setattr(_warmup, "_check_startup", startup)
    first, second = asyncio.run(_CHECK_TARGETS(commands))
    assert isinstance(first, process_tree.ReclaimError)
    assert not isinstance(second, BaseException) and second[1] is not None and second[1].ok


@pytest.mark.parametrize("failed", [None, "dependency", "startup", "exception"])
def test_parallel_checks_keep_order_and_collect_other_roots(monkeypatch, failed):
    """依存確認は独立rootを重ね、MCP起動確認は重ねず、1root失敗でも残るrootの確認を終了まで回収する。

    Windowsでは先に終わった起動確認が、共有の管理サービスを他の確認の接続中に止めるため、起動確認を重ねない。
    """
    commands = [["python", name] for name in ("first", "second", "third")]
    observed = []
    active_startups = [0]
    max_active_startups = [0]

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
            active_startups[0] += 1
            max_active_startups[0] = max(max_active_startups[0], active_startups[0])
            # 並列に起動していれば、ここで他の起動確認が開始する。
            await asyncio.sleep(0.01)
            active_startups[0] -= 1
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
    assert max_active_startups[0] == 1
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

    def test_worktree_script_is_target(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """作業ツリーの基盤CLIとserveが使う起動スクリプトもfull warmupで準備する。"""
        calls = _setup(monkeypatch, tmp_path)
        repository = tmp_path / "dotfiles"
        _write_script(repository / "agent-toolkit" / "agent_toolkit" / "agents_server_mcp.py")
        monkeypatch.setattr(_common, "find_dotfiles_root", lambda: repository)
        monkeypatch.setattr(_warmup.config_settings, "load_config_strict", lambda: _main_checkout_config(repository))

        _warmup.run()

        assert str(repository / "agent-toolkit" / "agent_toolkit" / "agents_server_mcp.py") in _warmed(calls)


@pytest.mark.windows
@pytest.mark.parametrize("cwd_is_primary", [False, True])
@pytest.mark.parametrize(
    "origin",
    [
        "linked",
        "registered-linked",
        "registered-linked-from-primary",
        "primary",
        "unregistered-primary",
        "unregistered-linked",
        "empty-path",
        "unknown",
        "unapproved-install",
    ],
)
def test_warmup_starts_shared_service_only_from_registered_locations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, cwd_is_primary: bool, origin: str, caplog: pytest.LogCaptureFixture
) -> None:
    primary = tmp_path / "main"
    linked = tmp_path / "linked"
    primary.mkdir()
    linked.mkdir()
    repository = linked if origin in {"linked", "registered-linked", "unregistered-linked", "empty-path"} else primary
    installed = tmp_path / "claude" / "plugins" / "cache" / "ak110-dotfiles" / "agent-toolkit" / "1.0.0"
    candidate = linked / "agent-toolkit" if origin == "unapproved-install" else installed
    calls = _setup(
        monkeypatch,
        tmp_path,
        installed_plugins={"version": 2, "plugins": {_PLUGIN_ID: [{"installPath": str(candidate)}]}},
    )
    registered = linked if origin.startswith("registered-linked") else primary
    unregistered = origin.startswith("unregistered-") or origin == "empty-path"

    def config():
        if origin == "empty-path":
            return {"repositories": json.dumps({"github.com/ak110/dotfiles": {"path": "", "edit": "allow"}})}
        return {} if unregistered else _main_checkout_config(registered)

    monkeypatch.setattr(_warmup.config_settings, "load_config_strict", config)

    def locate(path: pathlib.Path):
        assert unregistered and path == repository
        return _warmup.config_repositories.RepositoryLocation("github.com/ak110/dotfiles", path, primary / ".git")

    monkeypatch.setattr(_warmup.config_repositories, "locate", locate)
    project = repository / "agent-toolkit"
    _write_script(project / _warmup._SCRIPT_RELATIVE)  # pylint: disable=protected-access
    _write_script(candidate / "agent_toolkit" / "agents_server_mcp.py")
    for root in (project, candidate):
        _write_script(root / "agent_toolkit" / "_agents_server" / "service.py")
    monkeypatch.chdir(primary if cwd_is_primary else linked)
    monkeypatch.setattr(_common, "find_dotfiles_root", lambda: None if origin == "unknown" else repository)
    monkeypatch.setattr(_warmup, "_check_startup", _CHECK_STARTUP)
    saves: list[object] = []
    cleanups: list[object] = []
    monkeypatch.setattr(_warmup.post_apply_cache, "matches", lambda *args: args in saves)
    monkeypatch.setattr(_warmup.post_apply_cache, "save", lambda *args: saves.append(args))
    monkeypatch.setattr(_warmup.plugin_warmup, "cleanup_prepared", cleanups.append)
    dependency_check = _warmup.plugin_warmup.check_command
    startups: list[pathlib.Path] = []
    connections: list[pathlib.Path] = []
    published: list[pathlib.Path] = []

    async def check(command, **_kwargs):
        if command[-1] != "--check-dependencies":
            startups.append(pathlib.Path(command[-1]))
        return await dependency_check(command)

    @contextlib.asynccontextmanager
    async def stdio(parameters, **_kwargs):
        connections.append(pathlib.Path(parameters.args[-1]))
        yield (None, None)

    class Client:
        def __init__(self, *_streams):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_failure):
            pass

        async def initialize(self):
            pass

        async def list_tools(self):
            return SimpleNamespace(tools=[object()])

    def publish(_caller, root: pathlib.Path, **_kwargs):
        published.append(root)

    monkeypatch.setattr(_warmup.plugin_warmup, "check_command", check)
    monkeypatch.setattr(mcp.client.stdio, "stdio_client", stdio)
    monkeypatch.setattr(mcp, "ClientSession", Client)
    monkeypatch.setattr(service_forward, "publish_service", publish)

    assert _warmup.run().failure is None
    blocked = origin in {"linked", "unregistered-linked", "empty-path", "unknown", "registered-linked-from-primary"}
    if blocked:
        assert _prepared(calls)
        roots = []
    elif origin == "unapproved-install":
        roots = [project]
        assert str(candidate) in caplog.text
        assert "通常のプラグイン配布先と一致しません" in caplog.text
    elif origin == "unregistered-primary":
        roots = [installed]
    else:
        roots = [installed, project]
    assert startups == [root / "agent_toolkit" / "agents_server_mcp.py" for root in roots]
    assert connections == startups
    assert published == roots
    assert bool(saves) is not blocked
    assert bool(cleanups) is not blocked
    if blocked:
        assert "共有管理サービスの起動と公開を省略" in caplog.text
        assert f"登録先: {'なし・未確認' if unregistered else registered}" in caplog.text
        assert f"更新元: {'未確認' if origin == 'unknown' else repository}" in caplog.text
        assert "atk config repos set の --path" in caplog.text
        assert "登録を主checkoutに合わせ、通常の更新を行ってください" in caplog.text
        assert "linked worktreeを登録したまま通常の更新で復旧することには対応していません" in caplog.text
    if origin == "unknown":
        assert "起動元のcheckoutを確認できません" in caplog.text
    if origin == "unapproved-install":
        _write_script(installed / "agent_toolkit" / "_agents_server" / "service.py")
        (tmp_path / "installed_plugins.json").write_text(
            json.dumps({"version": 2, "plugins": {_PLUGIN_ID: [{"installPath": str(installed)}]}}),
            encoding="utf-8",
        )
        assert _warmup.run().failure is None
        assert published == [project, installed, project]

"""`atk wi process-loop`の自己更新の確認と再起動のテスト。

待機ループがタイムアウト復帰した際の上流差分の反映・常駐コードのハッシュ比較・再起動を公開CLI経由で検証する。
"""

import collections.abc
import contextlib
import os
import pathlib
import shutil
import subprocess
import sys
from typing import Any

import pytest

from agent_toolkit import atk  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk import config as _config  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._atk.wi import process_loop_update as _pl_update
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit._testing.process_loop_support import (
    fake_run_with_remote_url,
    isolate_process_loop_commands,
    raise_system_exit_0,
)
from agent_toolkit.atk_test import _setup_notes  # noqa: E402  # pylint: disable=wrong-import-position

# 上流差分確認関数は`_run_until_stop`が差し替えるため、公開CLI経由では検証できない。
# private参照はモジュール冒頭で別名束縛し、抑制コメントを1箇所へ集約する。
_has_upstream_diff = _pl_update._has_upstream_diff  # pylint: disable=protected-access
_restart_process_loop = _pl_update.restart_process_loop  # pylint: disable=protected-access
_RESTART_SPEC_ENV = _pl_env.RESTART_SPEC_ENV  # pylint: disable=protected-access
_RESTART_EXIT_CODE = _pl_update._RESTART_EXIT_CODE  # pylint: disable=protected-access
_INTERNAL_MISE_REFRESHED_ARG = _pl_update._INTERNAL_MISE_REFRESHED_ARG  # pylint: disable=protected-access
_INTERNAL_DOTFILES_UPDATED_ARG = _pl_update._INTERNAL_DOTFILES_UPDATED_ARG  # pylint: disable=protected-access


def _run_posix_launcher(
    tmp_path: pathlib.Path,
    top_level: str,
    subcommand: str,
    *,
    send_term: bool = False,
    return_code: int | None = None,
    omit_spec: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """決定論的なuv代用品でPOSIXランチャーを実行する。"""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log = tmp_path / "uv-calls"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/usr/bin/env python3
import os
import pathlib
import signal
import sys

if sys.argv[-1].endswith("process_loop_log.py"):
    print(os.environ["FAKE_SUPERVISOR_LOG"])
    raise SystemExit(0)

log = pathlib.Path(os.environ["FAKE_UV_LOG"])
calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
calls.append(" ".join(sys.argv[1:]))
log.write_text("\\n".join(calls) + "\\n", encoding="utf-8")
spec = os.environ.get("AGENT_TOOLKIT_RESTART_SPEC")
if spec and os.environ.get("FAKE_UV_RETURN_CODE"):
    raise SystemExit(int(os.environ["FAKE_UV_RETURN_CODE"]))
if spec and os.environ.get("FAKE_UV_SEND_TERM") == "1":
    os.kill(os.getppid(), signal.SIGTERM)
    raise SystemExit(0)
if spec and len(calls) == 1:
    if os.environ.get("FAKE_UV_OMIT_SPEC") != "1":
        pathlib.Path(spec).write_text("\\n".join(sys.argv[-3:]) + "\\n", encoding="utf-8")
    raise SystemExit(75)
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join((str(fake_bin), env["PATH"]))
    env["FAKE_UV_LOG"] = str(call_log)
    env["FAKE_SUPERVISOR_LOG"] = str(tmp_path / "process-wi.log")
    if send_term:
        env["FAKE_UV_SEND_TERM"] = "1"
    if return_code is not None:
        env["FAKE_UV_RETURN_CODE"] = str(return_code)
    if omit_spec:
        env["FAKE_UV_OMIT_SPEC"] = "1"
    launcher = pathlib.Path(atk.__file__).resolve().parents[1] / "bin" / "atk"
    result = subprocess.run(
        [str(launcher), top_level, subcommand],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return result, call_log.read_text(encoding="utf-8").splitlines()


@pytest.mark.parametrize(
    ("top_level", "subcommand", "expected_call_count"),
    [
        ("wi", "process-loop", 2),
        ("mq", "process-loop", 2),
        ("wi", "list", 1),
        ("plans", "process-loop", 1),
    ],
)
def test_posix_launcher_restarts_only_process_loop_aliases(
    tmp_path: pathlib.Path,
    top_level: str,
    subcommand: str,
    expected_call_count: int,
) -> None:
    """POSIXランチャーはwiと旧mqのprocess-loopだけを再起動対象にする。"""
    result, uv_calls = _run_posix_launcher(tmp_path, top_level, subcommand)

    assert result.returncode == 0, result.stderr
    assert len(uv_calls) == expected_call_count
    if expected_call_count == 2:
        log = (tmp_path / "process-wi.log").read_text(encoding="utf-8")
        assert "event=launcher_restart" in log
        assert "event=launcher_exit" in log


def test_windows_launcher_routes_same_process_loop_aliases() -> None:
    """WindowsランチャーもPOSIX版と同じ2つのトップレベル名を受理する。"""
    launcher = pathlib.Path(atk.__file__).resolve().parents[1] / "bin" / "atk.cmd"
    data = launcher.read_bytes()
    text = data.decode("cp932")

    assert b"\n" not in data.replace(b"\r\n", b"")
    assert 'if not "%~1"=="wi" if not "%~1"=="mq" goto :run_once' in text
    assert 'if not "%~2"=="process-loop" goto :run_once' in text


def test_posix_launcher_logs_received_termination(tmp_path: pathlib.Path) -> None:
    result, _calls = _run_posix_launcher(tmp_path, "wi", "process-loop", send_term=True)

    assert result.returncode == 143
    log = (tmp_path / "process-wi.log").read_text(encoding="utf-8")
    assert "event=launcher_exit" in log
    assert "status=143 signal=TERM" in log


@pytest.mark.parametrize(
    ("return_code", "omit_spec", "expected_event"),
    [(2, False, "launcher_child_end"), (None, True, "launcher_restart_spec_missing")],
)
def test_posix_launcher_logs_non_restart_exit(
    tmp_path: pathlib.Path, return_code: int | None, omit_spec: bool, expected_event: str
) -> None:
    result, _calls = _run_posix_launcher(tmp_path, "wi", "process-loop", return_code=return_code, omit_spec=omit_spec)

    assert result.returncode == (75 if omit_spec else 2)
    log = (tmp_path / "process-wi.log").read_text(encoding="utf-8")
    assert f"event={expected_event}" in log


_RESUME_ARGV_CASES = [
    ["--resume"],
    ["--resume", "00000000-0000-0000-0000-000000000000"],
    ["--resume=00000000-0000-0000-0000-000000000000"],
]
"""再開指定の書き方（値なし、空白区切りの値、等号区切りの値）。"""


@pytest.fixture(autouse=True)
def _resolve_process_loop_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンド・Claude設定・managed-tempの登録簿をユーザー環境から分離する。"""
    monkeypatch.setattr(_config.platformdirs, "user_config_dir", lambda _name, **_kwargs: str(tmp_path / "config"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "managed-temp-state")
    monkeypatch.setattr(shutil, "which", lambda command: f"/resolved/{command}")
    monkeypatch.delenv(_RESTART_SPEC_ENV, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))


def _command_was_called(calls: list[list[str]], command: str) -> bool:
    """呼び出し配列の先頭要素の基底名が一致するか確認する。"""
    return any(pathlib.Path(call[0]).stem.lower() == command for call in calls)


class TestWaitLoopAutoRestart:
    """待機ループ復帰時の自動更新反映・再起動を公開CLI経由で検証する。"""

    def _run_until_stop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        *,
        wait_return: bool,
        has_upstream_diff: bool,
        changed_file_name: str | None = None,
        extra_argv: list[str] | None = None,
        dotfiles_root_missing: bool = False,
        create_canonical_entry: bool = False,
        pending_count: int = 0,
        ambiguous_partition_change: bool = False,
        nested_change: str | None = None,
    ) -> tuple[list[list[str]], list[tuple[str, list[str]]]]:
        """件数と`_wait_for_changes`を固定でモックしてprocess-loopを1回実行する。

        戻り値は(subprocess呼び出し記録, execv呼び出し記録)。
        `changed_file_name`指定時は初回の待機復帰直前に対象ファイルの内容を変更する。
        `dotfiles_root_missing=True`時は`_resolve_dotfiles_root`が`None`を返す
        （`~/dotfiles`未検出）状況を模擬する。
        `create_canonical_entry=True`時はダミーチェックアウト配下へ`atk.py`を配置し、
        再起動先の切り替え先が実在する状況を模擬する。
        """
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_notes(tmp_path)
        fake_dotfiles_root = tmp_path / "dotfiles_root"
        fake_scripts_dir = fake_dotfiles_root / "agent-toolkit" / "scripts"
        fake_scripts_dir.mkdir(parents=True)
        initial_content = "b.py\nx = 1\n" if ambiguous_partition_change else "x = 1\n"
        (fake_scripts_dir / "a.py").write_text(initial_content, encoding="utf-8")
        (fake_scripts_dir / "a_test.py").write_text("def test_a(): pass\n", encoding="utf-8")
        nested_file = fake_scripts_dir / "_atk" / "wi" / "nested.py"
        if nested_change in {"change", "delete"}:
            nested_file.parent.mkdir(parents=True)
            nested_file.write_text("value = 1\n", encoding="utf-8")
        if create_canonical_entry:
            canonical_entry = fake_dotfiles_root / "agent-toolkit" / "agent_toolkit" / "atk.py"
            canonical_entry.parent.mkdir(parents=True)
            canonical_entry.write_text("# canonical entry point\n", encoding="utf-8")
        # `_resolve_dotfiles_root`は`~/dotfiles`を直接参照するため、
        # `atk`実行コード自体の物理配置（`__file__`）とは独立にテスト用ダミーへ差し替える。
        resolved_root = None if dotfiles_root_missing else fake_dotfiles_root
        monkeypatch.setattr(_pl_update, "resolve_dotfiles_root", lambda: resolved_root)
        subprocess_calls: list[list[str]] = []
        base_fake_run = fake_run_with_remote_url(myrepo, [], 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            subprocess_calls.append(list(cmd))
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: pending_count)
        monkeypatch.setattr(
            _pl_session,
            "select_available_orchestrator",
            lambda candidates, _env, _cwd: candidates[0],
        )

        wait_calls = {"n": 0}

        def fake_wait(*_a: object, **_kw: object) -> bool:
            wait_calls["n"] += 1
            if wait_calls["n"] > 1:
                raise KeyboardInterrupt
            if changed_file_name is not None:
                (fake_scripts_dir / changed_file_name).write_text("changed = True\n", encoding="utf-8")
            if ambiguous_partition_change:
                (fake_scripts_dir / "a.py").write_text("", encoding="utf-8")
                (fake_scripts_dir / "b.py").write_text("\nx = 1\n", encoding="utf-8")
            if nested_change == "add":
                nested_file.parent.mkdir(parents=True)
                nested_file.write_text("value = 1\n", encoding="utf-8")
            elif nested_change == "change":
                nested_file.write_text("value = 2\n", encoding="utf-8")
            elif nested_change == "delete":
                nested_file.unlink()
            elif nested_change == "test":
                nested_file.parent.mkdir(parents=True)
                nested_file.with_name("nested_test.py").write_text("value = 1\n", encoding="utf-8")
            elif nested_change == "cache":
                cache_file = fake_scripts_dir / "_atk" / "__pycache__" / "cached.py"
                cache_file.parent.mkdir(parents=True)
                cache_file.write_text("value = 1\n", encoding="utf-8")
            return wait_return

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait)
        monkeypatch.setattr(_pl_update, "_has_upstream_diff", lambda *_a, **_kw: has_upstream_diff)

        execv_calls: list[tuple[str, list[str]]] = []

        def fake_execv(path: str, argv: list[str]) -> None:
            execv_calls.append((path, list(argv)))
            raise SystemExit(0)

        monkeypatch.setattr(os, "execv", fake_execv)

        argv = ["wi", "process-loop", "--target-repo", str(myrepo), *(extra_argv or [])]
        monkeypatch.setattr(sys, "argv", [str(pathlib.Path(atk.__file__)), *argv])
        with pytest.raises((SystemExit, KeyboardInterrupt)):
            atk.main(argv, home=tmp_path)
        return subprocess_calls, execv_calls

    def test_hash_diff_on_timeout_triggers_restart(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """タイムアウト復帰・上流差分なし・ハッシュ差分ありの場合に再起動されること。"""
        _, execv_calls = self._run_until_stop(
            monkeypatch,
            tmp_path,
            wait_return=False,
            has_upstream_diff=False,
            changed_file_name="a.py",
        )
        assert execv_calls, "ハッシュ差分検知時はos.execvが呼ばれる必要がある"

    def test_different_file_partition_on_timeout_triggers_restart(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """旧方式で同じ入力列になるファイル分割の変更でも再起動されること。"""
        _, execv_calls = self._run_until_stop(
            monkeypatch,
            tmp_path,
            wait_return=False,
            has_upstream_diff=False,
            ambiguous_partition_change=True,
        )
        assert execv_calls

    def test_restart_targets_dotfiles_checkout_entry_point(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """再起動先が`~/dotfiles`チェックアウト配下の`atk.py`へ切り替わること。

        プラグインキャッシュ配下から起動された場合、切り替えないと旧コードを再実行し続ける。
        """
        _, execv_calls = self._run_until_stop(
            monkeypatch,
            tmp_path,
            wait_return=False,
            has_upstream_diff=False,
            changed_file_name="a.py",
            create_canonical_entry=True,
        )

        assert execv_calls
        canonical_entry = tmp_path / "dotfiles_root" / "agent-toolkit" / "agent_toolkit" / "atk.py"
        _, restart_argv = execv_calls[0]
        assert str(canonical_entry) in restart_argv

    def test_test_file_change_on_timeout_skips_restart(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """タイムアウト復帰時に`*_test.py`のみが変化しても再起動されないこと。"""
        _, execv_calls = self._run_until_stop(
            monkeypatch,
            tmp_path,
            wait_return=False,
            has_upstream_diff=False,
            changed_file_name="a_test.py",
        )
        assert not execv_calls

    @pytest.mark.parametrize(
        ("nested_change", "restarts"), [("add", True), ("change", True), ("delete", True), ("test", False), ("cache", False)]
    )
    def test_nested_code_change_detection(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        nested_change: str,
        restarts: bool,
    ) -> None:
        """再帰走査は実装の追加・変更・削除だけを再起動対象にする。"""
        _, execv_calls = self._run_until_stop(
            monkeypatch,
            tmp_path,
            wait_return=False,
            has_upstream_diff=False,
            nested_change=nested_change,
        )
        assert bool(execv_calls) is restarts

    def test_upstream_diff_present_calls_update_dotfiles(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """上流差分ありの場合のみ`update-dotfiles`が実行されること。"""
        subprocess_calls, _ = self._run_until_stop(
            monkeypatch,
            tmp_path,
            wait_return=False,
            has_upstream_diff=True,
        )
        assert _command_was_called(subprocess_calls, "update-dotfiles")

    def test_successful_update_restart_marks_dotfiles_updated(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """待機中の更新成功による再起動へ更新済み指定を渡す。"""
        _, execv_calls = self._run_until_stop(
            monkeypatch,
            tmp_path,
            wait_return=False,
            has_upstream_diff=True,
            changed_file_name="a.py",
        )

        assert execv_calls
        assert _INTERNAL_DOTFILES_UPDATED_ARG in execv_calls[0][1]

    def test_no_upstream_diff_skips_update_dotfiles(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """上流差分なしの場合は`update-dotfiles`が実行されないこと。"""
        subprocess_calls, _ = self._run_until_stop(
            monkeypatch,
            tmp_path,
            wait_return=False,
            has_upstream_diff=False,
        )
        assert not _command_was_called(subprocess_calls, "update-dotfiles")

    def test_no_update_flag_skips_check_after_timeout(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """`--no-update`指定時はタイムアウト復帰後の上流差分確認・再起動チェックが行われないこと。"""
        subprocess_calls, execv_calls = self._run_until_stop(
            monkeypatch,
            tmp_path,
            wait_return=False,
            has_upstream_diff=True,
            changed_file_name="a.py",
            extra_argv=["--no-update"],
        )
        assert not _command_was_called(subprocess_calls, "update-dotfiles")
        assert not execv_calls

    def test_change_detected_defers_update_to_ready_session_boundary(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """変更検知だけでは待機中更新を行わず、次のready処理開始境界へ委ねること。"""
        subprocess_calls, execv_calls = self._run_until_stop(
            monkeypatch,
            tmp_path,
            wait_return=True,
            has_upstream_diff=True,
            changed_file_name="a.py",
        )
        assert not _command_was_called(subprocess_calls, "update-dotfiles")
        assert not execv_calls

    def test_missing_dotfiles_root_skips_check_entirely(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """`~/dotfiles`が見つからない環境ではタイムアウト復帰時の更新チェック自体を行わないこと。

        `atk`がプラグインキャッシュ配下から実行され、かつ`~/dotfiles`チェックアウトが
        見つからない場合の防御的フォールバックを検証する。
        """
        subprocess_calls, execv_calls = self._run_until_stop(
            monkeypatch,
            tmp_path,
            wait_return=False,
            has_upstream_diff=True,
            changed_file_name="a.py",
            dotfiles_root_missing=True,
        )
        assert not _command_was_called(subprocess_calls, "update-dotfiles")
        assert not execv_calls

    @pytest.mark.parametrize("resume_argv", _RESUME_ARGV_CASES)
    def test_wait_loop_restart_preserves_resume_option(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        resume_argv: list[str],
    ) -> None:
        """Claude起動前の待機中再起動ではresume指定を保持する。"""
        _, execv_calls = self._run_until_stop(
            monkeypatch,
            tmp_path,
            wait_return=False,
            has_upstream_diff=False,
            changed_file_name="a.py",
            extra_argv=resume_argv,
        )

        assert execv_calls
        _, restart_argv = execv_calls[0]
        for arg in resume_argv:
            assert arg in restart_argv
        assert "--target-repo" in restart_argv

    @pytest.mark.parametrize("resume_argv", _RESUME_ARGV_CASES)
    def test_session_restart_drops_resume_option_and_value(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        resume_argv: list[str],
    ) -> None:
        """Claudeセッション正常終了後の再起動ではresume指定と値を除去する。"""
        _, execv_calls = self._run_until_stop(
            monkeypatch,
            tmp_path,
            wait_return=False,
            has_upstream_diff=False,
            extra_argv=resume_argv,
            pending_count=1,
        )

        assert execv_calls
        _, restart_argv = execv_calls[0]
        assert not any(arg.startswith("--resume") for arg in restart_argv)
        assert "00000000-0000-0000-0000-000000000000" not in restart_argv
        assert _INTERNAL_DOTFILES_UPDATED_ARG not in restart_argv
        assert "--target-repo" in restart_argv

    def test_codex_session_restart_uses_configuration_without_removed_options(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """Codex正常終了後の再起動で設定を使い、廃止オプションを引き継がずresumeだけを除去する。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["config", "set", "orchestrate_model", "codex:gpt-5.6-sol/high"],
                home=tmp_path,
            )
        assert exc_info.value.code == 0
        _, execv_calls = self._run_until_stop(
            monkeypatch,
            tmp_path,
            wait_return=False,
            has_upstream_diff=False,
            extra_argv=["--resume", "session-id"],
            pending_count=1,
        )

        assert execv_calls
        _, restart_argv = execv_calls[0]
        assert "--orchestrator" not in restart_argv
        assert "--model" not in restart_argv
        assert "--resume" not in restart_argv
        assert "session-id" not in restart_argv
        assert "--target-repo" in restart_argv

    def test_pending_session_uses_isolated_hook_debug_log(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`pending_count=1`でClaudeを起動する場合も一時設定ディレクトリへ診断ログを保存する。"""
        subprocess_calls, _ = self._run_until_stop(
            monkeypatch,
            tmp_path,
            wait_return=False,
            has_upstream_diff=False,
            pending_count=1,
        )

        # `claude auth status`は待機間隔をキャッシュTTLで決めるための事前照会であり、委譲セッションの起動ではない。
        claude_command = next(
            call for call in subprocess_calls if call[:1] == ["claude"] and call[:3] != ["claude", "auth", "status"]
        )
        assert claude_command[:3] == ["claude", "--debug=hooks", "--debug-file"]
        debug_log = pathlib.Path(claude_command[3])
        assert debug_log.is_file()
        assert debug_log.parent == tmp_path / ".claude" / "debug"


def test_restart_writes_spec_and_exits_when_launcher_env_is_set(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """受け渡しファイルの指定時は起動対象を書き、専用の終了コードで終了する。"""
    spec = tmp_path / "restart-spec"
    script = tmp_path / "atk.py"
    monkeypatch.setenv(_RESTART_SPEC_ENV, str(spec))

    def unexpected(*_args: object) -> None:
        raise AssertionError("ランチャー経由では実体を置き換えない")

    monkeypatch.setattr(os, "execv", unexpected)
    with pytest.raises(SystemExit) as exc_info:
        _restart_process_loop([str(script), "wi", "process-loop", "--target-repo", "example/repo"])

    assert exc_info.value.code == _RESTART_EXIT_CODE
    assert spec.read_text(encoding="utf-8").splitlines() == [
        str(script.resolve()),
        "wi",
        "process-loop",
        "--target-repo",
        "example/repo",
    ]


def test_restart_falls_back_to_exec_without_launcher_env(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """受け渡しファイルの指定が無い直接起動では実体を置き換える。"""
    calls: list[tuple[str, list[str]]] = []
    project_root = tmp_path / "plugin"
    script = project_root / "agent_toolkit" / "atk.py"

    def record(path: str, argv: list[str]) -> None:
        calls.append((path, argv))
        raise SystemExit(0)

    monkeypatch.setattr(os, "execv", record)
    with pytest.raises(SystemExit):
        _restart_process_loop([str(script), "wi", "process-loop"])

    assert calls == [
        (
            "/resolved/uv",
            [
                "/resolved/uv",
                "run",
                "--project",
                str(project_root),
                "--locked",
                "--no-default-groups",
                str(script.resolve()),
                "wi",
                "process-loop",
            ],
        )
    ]


def test_restart_spec_carries_refreshed_marker_once_and_next_restart_drops_it(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ランチャーから起動した場合は更新成功後だけ内部指定を一回渡し、再々起動へ残さない。"""
    first_spec = tmp_path / "first-restart-spec"
    script = tmp_path / "atk.py"
    monkeypatch.setenv(_RESTART_SPEC_ENV, str(first_spec))

    with pytest.raises(SystemExit):
        _restart_process_loop(
            [str(script), "wi", "process-loop", _INTERNAL_MISE_REFRESHED_ARG],
            mise_refreshed=True,
        )

    first_lines = first_spec.read_text(encoding="utf-8").splitlines()
    assert first_lines.count(_INTERNAL_MISE_REFRESHED_ARG) == 1

    second_spec = tmp_path / "second-restart-spec"
    monkeypatch.setenv(_RESTART_SPEC_ENV, str(second_spec))
    with pytest.raises(SystemExit):
        _restart_process_loop(first_lines, mise_refreshed=False)

    assert _INTERNAL_MISE_REFRESHED_ARG not in second_spec.read_text(encoding="utf-8").splitlines()


def test_direct_restart_carries_refreshed_marker_once(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """直接再起動のexecv引数も既存指定を除去して更新成功の一個だけを渡す。"""
    calls: list[list[str]] = []

    def record(_path: str, argv: list[str]) -> None:
        calls.append(argv)
        raise SystemExit(0)

    monkeypatch.setattr(os, "execv", record)
    with pytest.raises(SystemExit):
        _restart_process_loop(
            [str(tmp_path / "atk.py"), "wi", "process-loop", _INTERNAL_MISE_REFRESHED_ARG],
            mise_refreshed=True,
        )

    assert calls[0].count(_INTERNAL_MISE_REFRESHED_ARG) == 1


def test_restart_spec_carries_updated_marker_once_and_next_restart_drops_it(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """更新成功の内部指定を一回だけ渡し、次の再起動へ持ち越さない。"""
    first_spec = tmp_path / "first-restart-spec"
    script = tmp_path / "atk.py"
    monkeypatch.setenv(_RESTART_SPEC_ENV, str(first_spec))

    with pytest.raises(SystemExit):
        _restart_process_loop(
            [str(script), "wi", "process-loop", _INTERNAL_DOTFILES_UPDATED_ARG],
            dotfiles_updated=True,
        )

    first_lines = first_spec.read_text(encoding="utf-8").splitlines()
    assert first_lines.count(_INTERNAL_DOTFILES_UPDATED_ARG) == 1

    second_spec = tmp_path / "second-restart-spec"
    monkeypatch.setenv(_RESTART_SPEC_ENV, str(second_spec))
    with pytest.raises(SystemExit):
        _restart_process_loop(first_lines, dotfiles_updated=False)

    assert _INTERNAL_DOTFILES_UPDATED_ARG not in second_spec.read_text(encoding="utf-8").splitlines()


def test_restart_spec_targets_dotfiles_checkout_entry_point(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ファイルで値を受け渡した場合も更新後のチェックアウト配下の`atk.py`へ切り替える。"""
    spec = tmp_path / "restart-spec"
    canonical = tmp_path / "dotfiles" / "agent-toolkit" / "agent_toolkit" / "atk.py"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("# entry\n", encoding="utf-8")
    monkeypatch.setenv(_RESTART_SPEC_ENV, str(spec))

    with pytest.raises(SystemExit):
        _restart_process_loop([str(tmp_path / "old" / "atk.py"), "wi", "process-loop"], tmp_path / "dotfiles")

    assert spec.read_text(encoding="utf-8").splitlines()[0] == str(canonical)


def test_restart_spec_drops_resume_option_and_value(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ファイルで値を受け渡した場合も再開オプションの除去を維持する。"""
    spec = tmp_path / "restart-spec"
    monkeypatch.setenv(_RESTART_SPEC_ENV, str(spec))

    with pytest.raises(SystemExit):
        _restart_process_loop(
            [str(tmp_path / "atk.py"), "wi", "process-loop", "--resume", "session-id", "--no-alerts"],
            resume_consumed=True,
        )

    lines = spec.read_text(encoding="utf-8").splitlines()
    assert "--resume" not in lines
    assert "session-id" not in lines
    assert "--no-alerts" in lines


def test_has_upstream_diff_reports_stderr_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """fetch失敗時にgitの標準エラー出力を警告本文へ含め、差分なし扱いで復帰する。"""

    def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(128, cmd, output="", stderr="fatal: Unable to create index.lock: File exists")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _has_upstream_diff(tmp_path) is False
    captured = capsys.readouterr()
    assert "index.lock" in captured.err


def test_has_upstream_diff_acquires_repo_lock_for_target(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """上流差分確認が対象作業コピーのパスでプロセス間ロックを取得する。"""
    acquired: list[pathlib.Path] = []

    @contextlib.contextmanager
    def fake_repo_lock(repo_path: pathlib.Path, **_kwargs: object) -> collections.abc.Iterator[None]:
        acquired.append(repo_path)
        yield

    monkeypatch.setattr(_wi_sync, "repo_lock", fake_repo_lock)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, stdout="0\n", stderr=""),
    )
    assert _has_upstream_diff(tmp_path) is False
    assert acquired == [tmp_path]


@pytest.fixture(name="process_loop_commands_isolated")
def _process_loop_commands_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンド・Claude設定・初期値のTTLをユーザー環境から分離する。常駐ループ全体を動かすテストが使う。"""
    isolate_process_loop_commands(monkeypatch, tmp_path)


@pytest.mark.usefixtures("process_loop_commands_isolated")
class TestProcessLoopUpdateAndRestart:
    """1反復後のupdate-dotfiles実行と自身再起動の挙動を検証する。"""

    def test_update_and_execv_called_by_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`--no-update`未指定で開始前更新とclaude後の`os.execv`再起動が行われること。"""
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_notes(tmp_path)
        subprocess_calls: list[list[str]] = []
        base_fake_run = fake_run_with_remote_url(myrepo, [], 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            subprocess_calls.append(list(cmd))
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(
            _wi_readiness,
            "count_pending_entries",
            lambda *_a, **_kw: 1,
        )
        execv_calls: list[tuple[str, list[str]]] = []

        def fake_execv(path: str, argv: list[str]) -> None:
            execv_calls.append((path, list(argv)))
            raise SystemExit(0)

        monkeypatch.setattr(os, "execv", fake_execv)
        with pytest.raises(SystemExit):
            atk.main(
                ["wi", "process-loop", "--target-repo", str(myrepo)],
                home=tmp_path,
            )
        assert execv_calls
        assert execv_calls[0][0] == "/resolved/uv"
        assert pathlib.Path(execv_calls[0][1][0]).name == "uv"
        expected_script = pathlib.Path(sys.argv[0]).resolve()
        assert execv_calls[0][1][1:7] == [
            "run",
            "--project",
            str(expected_script.parent.parent),
            "--locked",
            "--no-default-groups",
            str(expected_script),
        ]
        assert _command_was_called(subprocess_calls, "update-dotfiles")
        captured = capsys.readouterr()
        assert "process-loopを再起動します。" in captured.out
        # テスト実行環境（非TTY）ではコンソールタイトル制御文字を一切出力しないこと。
        assert "\033]2;" not in captured.out
        assert "\033]2;" not in captured.err

    def test_update_dotfiles_receives_stripped_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """セッション終了後の`update-dotfiles`起動へ、仮想環境を除去した環境を渡すこと。

        `update-dotfiles`は`chezmoi apply`を経て対象リポジトリのuvベースのパッケージ操作へ至るため、
        起動元ツールのエフェメラル仮想環境を引き継がせない。
        """
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_notes(tmp_path)
        venv_root = "/home/user/.cache/uv/environments-v2/atk-0123456789abcdef"
        monkeypatch.setenv("VIRTUAL_ENV", venv_root)
        monkeypatch.setenv("PATH", os.pathsep.join((f"{venv_root}/bin", "/usr/bin")))
        update_envs: list[Any] = []
        base_fake_run = fake_run_with_remote_url(myrepo, [], 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if pathlib.Path(cmd[0]).stem.lower() == "update-dotfiles":
                update_envs.append(kwargs.get("env"))
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: 1)
        monkeypatch.setattr(os, "execv", raise_system_exit_0)

        with pytest.raises(SystemExit):
            atk.main(["wi", "process-loop", "--target-repo", str(myrepo)], home=tmp_path)

        assert len(update_envs) == 1
        for child_env in update_envs:
            assert child_env is not None
            assert "VIRTUAL_ENV" not in child_env
            assert child_env["PATH"] == "/usr/bin"

    def test_wait_loop_update_dotfiles_receives_stripped_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """待機ループ復帰時の`update-dotfiles`起動へも、仮想環境を除去した環境を渡すこと。"""
        venv_root = "/home/user/.cache/uv/environments-v2/atk-0123456789abcdef"
        monkeypatch.setenv("VIRTUAL_ENV", venv_root)
        monkeypatch.setenv("PATH", os.pathsep.join((f"{venv_root}/bin", "/usr/bin")))
        update_envs: list[Any] = []

        def fake_run(cmd: list[str], *_a: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if pathlib.Path(cmd[0]).stem.lower() == "update-dotfiles":
                update_envs.append(kwargs.get("env"))
            stdout = "1\n" if "rev-list" in cmd else ""
            return subprocess.CompletedProcess(cmd, 0, stdout, "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_pl_update, "code_hash", lambda _d: "same-hash")  # 再起動へ進ませない
        _pl_update.check_and_restart_on_update(tmp_path, "same-hash", ["argv0"])  # pylint: disable=protected-access  # noqa: SLF001

        assert len(update_envs) == 1
        assert update_envs[0] is not None
        assert "VIRTUAL_ENV" not in update_envs[0]
        assert update_envs[0]["PATH"] == "/usr/bin"

    def test_no_update_skips_restart(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`--no-update`指定時にupdate-dotfilesと`os.execv`のいずれも呼ばれないこと。"""
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_notes(tmp_path)
        counts = iter([1, 1, 0])
        subprocess_calls: list[list[str]] = []
        base_fake_run = fake_run_with_remote_url(myrepo, [], 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            subprocess_calls.append(list(cmd))
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(
            _wi_readiness,
            "count_pending_entries",
            lambda *_a, **_kw: next(counts),
        )

        def fake_wait(*_a: object, **_kw: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait)
        execv_calls: list[tuple[str, list[str]]] = []
        monkeypatch.setattr(
            os,
            "execv",
            lambda p, a: execv_calls.append((p, list(a))),
        )
        with pytest.raises(SystemExit):
            atk.main(
                ["wi", "process-loop", "--target-repo", str(myrepo), "--no-update"],
                home=tmp_path,
            )
        assert not execv_calls
        assert not _command_was_called(subprocess_calls, "update-dotfiles")

    def test_missing_update_command_reports_error_and_continues_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """update-dotfilesを解決できない場合も次反復へ進み、待機を継続すること。"""
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_notes(tmp_path)
        counts = iter([1, 0])
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, [], 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))
        monkeypatch.setattr(
            shutil,
            "which",
            lambda command: None if command == "update-dotfiles" else f"/resolved/{command}",
        )

        def fake_wait(*_a: object, **_kw: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop", "--target-repo", str(myrepo)], home=tmp_path)

        assert exc_info.value.code == 0
        assert "update-dotfilesコマンドを利用できない" in capsys.readouterr().err

    def test_missing_uv_command_reports_error_and_continues_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """再起動用uvを解決できない場合も次反復へ進み、待機を継続すること。"""
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_notes(tmp_path)
        counts = iter([1, 1, 0])
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, [], 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))
        monkeypatch.setattr(
            shutil,
            "which",
            lambda command: None if command == "uv" else f"/resolved/{command}",
        )

        def fake_wait(*_a: object, **_kw: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait)
        monkeypatch.setattr(os, "execv", lambda *_a, **_kw: pytest.fail("uv未解決時はexecvを呼ばないこと"))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop", "--target-repo", str(myrepo)], home=tmp_path)

        assert exc_info.value.code == 0
        assert "uvコマンドを利用できない" in capsys.readouterr().err

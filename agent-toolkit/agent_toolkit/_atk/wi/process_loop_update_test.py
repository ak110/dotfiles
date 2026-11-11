# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""待機中のコード変更と上流差分による自動再起動を検証する。"""

import os
import pathlib
import subprocess
import sys
from typing import Any

import pytest

from agent_toolkit import atk  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk.wi import process_loop_ownership as ownership
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._atk.wi import process_loop_update as _pl_update
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._testing.process_loop_support import fake_run_with_remote_url, set_orchestrate_model
from agent_toolkit._testing.process_loop_update_support import (
    _INTERNAL_DOTFILES_UPDATED_ARG,
    _RESUME_ARGV_CASES,
    _command_was_called,
)
from agent_toolkit._testing.process_loop_update_support import (
    _process_loop_commands_isolated as _process_loop_commands_isolated,
)
from agent_toolkit._testing.process_loop_update_support import _registered_update_boundary as _registered_update_boundary
from agent_toolkit._testing.process_loop_update_support import _resolve_process_loop_commands as _resolve_process_loop_commands
from agent_toolkit.atk_test import _setup_notes  # noqa: E402  # pylint: disable=wrong-import-position


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
        fake_plugin_root = fake_dotfiles_root / "agent-toolkit"
        fake_package_dir = fake_plugin_root / "agent_toolkit"
        fake_scripts_dir = fake_plugin_root / "scripts"
        fake_package_dir.mkdir(parents=True)
        fake_scripts_dir.mkdir(parents=True)
        initial_content = "b.py\nx = 1\n" if ambiguous_partition_change else "x = 1\n"
        (fake_package_dir / "runtime.py").write_text(initial_content, encoding="utf-8")
        (fake_package_dir / "runtime_test.py").write_text("def test_a(): pass\n", encoding="utf-8")
        (fake_scripts_dir / "launch.py").write_text("print('launch')\n", encoding="utf-8")
        nested_file = fake_package_dir / "_atk" / "wi" / "nested.py"
        if nested_change in {"change", "delete"}:
            nested_file.parent.mkdir(parents=True)
            nested_file.write_text("value = 1\n", encoding="utf-8")
        if create_canonical_entry:
            canonical_entry = fake_dotfiles_root / "agent-toolkit" / "agent_toolkit" / "atk.py"
            canonical_entry.parent.mkdir(parents=True, exist_ok=True)
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
            ownership, "resume_repositories", lambda _session, targets: set(targets) if pending_count else set()
        )
        monkeypatch.setattr(
            _pl_session,
            "select_available_orchestrator",
            lambda candidates, _env, _cwd, **_kwargs: (*candidates[0], None, None),
        )

        wait_calls = {"n": 0}

        def fake_wait(*_a: object, **_kw: object) -> bool:
            wait_calls["n"] += 1
            if wait_calls["n"] > 1:
                raise KeyboardInterrupt
            if changed_file_name is not None:
                (fake_plugin_root / changed_file_name).write_text("changed = True\n", encoding="utf-8")
            if ambiguous_partition_change:
                (fake_package_dir / "runtime.py").write_text("", encoding="utf-8")
                (fake_package_dir / "other.py").write_text("\nx = 1\n", encoding="utf-8")
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
                cache_file = fake_package_dir / "_atk" / "__pycache__" / "cached.py"
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

        argv = ["wi", "process-loop", "--no-alerts", *(extra_argv or [])]
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
            changed_file_name="agent_toolkit/runtime.py",
        )
        assert execv_calls, "ハッシュ差分検知時はos.execvが呼ばれる必要がある"

    def test_launcher_change_on_timeout_restarts(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """待機復帰時に実行用launch.pyの変更を検出して再起動する。"""
        _, execv_calls = self._run_until_stop(
            monkeypatch, tmp_path, wait_return=False, has_upstream_diff=False, changed_file_name="scripts/launch.py"
        )
        assert execv_calls

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
            changed_file_name="agent_toolkit/runtime.py",
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
            changed_file_name="agent_toolkit/runtime_test.py",
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
            changed_file_name="agent_toolkit/runtime.py",
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
            changed_file_name="agent_toolkit/runtime.py",
            extra_argv=["--no-update"],
        )
        assert not _command_was_called(subprocess_calls, "update-dotfiles")
        assert not execv_calls

    def test_change_detected_defers_update_to_ready_session_boundary(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """変更を検知しただけでは待機中に更新せず、次にready項目の処理を始める時点へ更新を委ねること。"""
        subprocess_calls, execv_calls = self._run_until_stop(
            monkeypatch,
            tmp_path,
            wait_return=True,
            has_upstream_diff=True,
            changed_file_name="agent_toolkit/runtime.py",
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
            changed_file_name="agent_toolkit/runtime.py",
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
            changed_file_name="agent_toolkit/runtime.py",
            extra_argv=resume_argv,
        )

        assert execv_calls
        _, restart_argv = execv_calls[0]
        for arg in resume_argv:
            assert arg in restart_argv
        assert "--target-repo" not in restart_argv

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
        assert "--target-repo" not in restart_argv

    def test_codex_session_restart_uses_configuration_without_removed_options(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """Codex正常終了後の再起動で設定を使い、廃止オプションを引き継がずresumeだけを除去する。"""
        set_orchestrate_model(tmp_path, "codex:gpt-5.6-sol/high")
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
        assert "--target-repo" not in restart_argv

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

        # `claude auth status`は待機間隔をキャッシュTTLで決めるための事前照会のため、委譲セッションの起動から除く。
        claude_command = next(
            call for call in subprocess_calls if call[:1] == ["claude"] and call[:3] != ["claude", "auth", "status"]
        )
        assert claude_command[:3] == ["claude", "--debug=hooks", "--debug-file"]
        debug_log = pathlib.Path(claude_command[3])
        assert debug_log.is_file()
        assert debug_log.parent == tmp_path / ".claude" / "debug"

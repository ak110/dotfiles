"""atk (agent-toolkit `atk mq`) のprocess-loop待機ループ自動再起動のテスト。

待機ループがタイムアウト復帰した際の上流差分反映・常駐コードのハッシュ比較・再起動を
公開CLI経由で検証する。process-loopサブコマンドの他のテストは`_atk_mq_process_loop_test.py`に、
既存サブコマンドの残テストは`atk_test.py`にある。共通ヘルパーは両ファイルから再利用する。
"""

import collections.abc
import contextlib
import os
import pathlib
import subprocess
import sys
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_config as _config  # noqa: E402  # pylint: disable=wrong-import-position
import _atk_mq_process_loop as _process_loop  # noqa: E402  # pylint: disable=wrong-import-position
import atk  # noqa: E402  # pylint: disable=wrong-import-position
from _atk_mq_process_loop_test import _fake_run_with_remote_url  # noqa: E402  # pylint: disable=wrong-import-position
from atk_test import _setup_notes  # noqa: E402  # pylint: disable=wrong-import-position

# 上流差分確認は`_run_until_stop`が当該関数自体を差し替えるため公開CLI経由では検証できない。
# private参照はモジュール冒頭で別名束縛し、抑制コメントを1箇所へ集約する。
_has_upstream_diff = _process_loop._has_upstream_diff  # pylint: disable=protected-access
_restart_process_loop = _process_loop._restart_process_loop  # pylint: disable=protected-access
_RESTART_SPEC_ENV = _process_loop._RESTART_SPEC_ENV  # pylint: disable=protected-access
_RESTART_EXIT_CODE = _process_loop._RESTART_EXIT_CODE  # pylint: disable=protected-access
_INTERNAL_MISE_REFRESHED_ARG = _process_loop._INTERNAL_MISE_REFRESHED_ARG  # pylint: disable=protected-access


@pytest.fixture(autouse=True)
def _resolve_process_loop_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンドとClaude設定をユーザー環境から分離する。"""
    monkeypatch.setattr(_config.platformdirs, "user_config_dir", lambda _name, **_kwargs: str(tmp_path / "config"))
    monkeypatch.setattr(_process_loop.shutil, "which", lambda command: f"/resolved/{command}")
    monkeypatch.delenv(_RESTART_SPEC_ENV, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))


def _command_was_called(calls: list[list[str]], command: str) -> bool:
    """呼び出し配列の先頭要素を基底名で照合する。"""
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
        if create_canonical_entry:
            (fake_scripts_dir / "atk.py").write_text("# canonical entry point\n", encoding="utf-8")
        # `_resolve_dotfiles_root`は`~/dotfiles`を直接参照するため、
        # `atk`実行コード自体の物理配置（`__file__`）とは独立にテスト用ダミーへ差し替える。
        resolved_root = None if dotfiles_root_missing else fake_dotfiles_root
        monkeypatch.setattr(_process_loop, "_resolve_dotfiles_root", lambda: resolved_root)
        subprocess_calls: list[list[str]] = []
        base_fake_run = _fake_run_with_remote_url(myrepo, [], 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            subprocess_calls.append(list(cmd))
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: pending_count)

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
            return wait_return

        monkeypatch.setattr(_process_loop, "_wait_for_changes", fake_wait)
        monkeypatch.setattr(_process_loop, "_has_upstream_diff", lambda *_a, **_kw: has_upstream_diff)

        execv_calls: list[tuple[str, list[str]]] = []

        def fake_execv(path: str, argv: list[str]) -> None:
            execv_calls.append((path, list(argv)))
            raise SystemExit(0)

        monkeypatch.setattr(os, "execv", fake_execv)

        argv = ["mq", "process-loop", "--target-repo", str(myrepo), *(extra_argv or [])]
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
        canonical_entry = tmp_path / "dotfiles_root" / "agent-toolkit" / "scripts" / "atk.py"
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

    def test_upstream_diff_present_calls_update_dotfiles(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """上流差分ありの場合のみ`update-dotfiles`が実行されること。"""
        subprocess_calls, _ = self._run_until_stop(
            monkeypatch,
            tmp_path,
            wait_return=False,
            has_upstream_diff=True,
        )
        assert _command_was_called(subprocess_calls, "update-dotfiles")

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

    @pytest.mark.parametrize(
        "resume_argv",
        [
            ["--resume"],
            ["--resume", "00000000-0000-0000-0000-000000000000"],
            ["--resume=00000000-0000-0000-0000-000000000000"],
        ],
    )
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

    @pytest.mark.parametrize(
        "resume_argv",
        [
            ["--resume"],
            ["--resume", "00000000-0000-0000-0000-000000000000"],
            ["--resume=00000000-0000-0000-0000-000000000000"],
        ],
    )
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
        """`pending_count=1`経路のClaude起動も一時設定ディレクトリへ診断ログを保存する。"""
        subprocess_calls, _ = self._run_until_stop(
            monkeypatch,
            tmp_path,
            wait_return=False,
            has_upstream_diff=False,
            pending_count=1,
        )

        claude_command = next(call for call in subprocess_calls if call[:1] == ["claude"])
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
        _restart_process_loop([str(script), "mq", "process-loop", "--target-repo", "example/repo"])

    assert exc_info.value.code == _RESTART_EXIT_CODE
    assert spec.read_text(encoding="utf-8").splitlines() == [
        str(script.resolve()),
        "mq",
        "process-loop",
        "--target-repo",
        "example/repo",
    ]


def test_restart_falls_back_to_exec_without_launcher_env(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """受け渡しファイルの指定が無い直接起動では実体を置き換える。"""
    calls: list[tuple[str, list[str]]] = []

    def record(path: str, argv: list[str]) -> None:
        calls.append((path, argv))
        raise SystemExit(0)

    monkeypatch.setattr(os, "execv", record)
    with pytest.raises(SystemExit):
        _restart_process_loop([str(tmp_path / "atk.py"), "mq", "process-loop"])

    assert calls == [
        (
            "/resolved/uv",
            ["/resolved/uv", "run", "--no-project", "--script", str((tmp_path / "atk.py").resolve()), "mq", "process-loop"],
        )
    ]


def test_restart_spec_carries_refreshed_marker_once_and_next_restart_drops_it(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ランチャー経路は更新成功後だけ内部指定を一回渡し、再々起動へ残さない。"""
    first_spec = tmp_path / "first-restart-spec"
    script = tmp_path / "atk.py"
    monkeypatch.setenv(_RESTART_SPEC_ENV, str(first_spec))

    with pytest.raises(SystemExit):
        _restart_process_loop(
            [str(script), "mq", "process-loop", _INTERNAL_MISE_REFRESHED_ARG],
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
            [str(tmp_path / "atk.py"), "mq", "process-loop", _INTERNAL_MISE_REFRESHED_ARG],
            mise_refreshed=True,
        )

    assert calls[0].count(_INTERNAL_MISE_REFRESHED_ARG) == 1


def test_restart_spec_targets_dotfiles_checkout_entry_point(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """受け渡しファイル経路でも更新後のチェックアウト配下の`atk.py`へ切り替える。"""
    spec = tmp_path / "restart-spec"
    canonical = tmp_path / "dotfiles" / "agent-toolkit" / "scripts" / "atk.py"
    canonical.parent.mkdir(parents=True)
    canonical.write_text("# entry\n", encoding="utf-8")
    monkeypatch.setenv(_RESTART_SPEC_ENV, str(spec))

    with pytest.raises(SystemExit):
        _restart_process_loop([str(tmp_path / "old" / "atk.py"), "mq", "process-loop"], tmp_path / "dotfiles")

    assert spec.read_text(encoding="utf-8").splitlines()[0] == str(canonical)


def test_restart_spec_drops_resume_option_and_value(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """受け渡しファイル経路でも再開オプションの除去を維持する。"""
    spec = tmp_path / "restart-spec"
    monkeypatch.setenv(_RESTART_SPEC_ENV, str(spec))

    with pytest.raises(SystemExit):
        _restart_process_loop(
            [str(tmp_path / "atk.py"), "mq", "process-loop", "--resume", "session-id", "--no-alerts"],
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

    monkeypatch.setattr(_process_loop.subprocess, "run", fake_run)
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

    monkeypatch.setattr(_process_loop, "_repo_lock", fake_repo_lock)
    monkeypatch.setattr(
        _process_loop.subprocess,
        "run",
        lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, stdout="0\n", stderr=""),
    )
    assert _has_upstream_diff(tmp_path) is False
    assert acquired == [tmp_path]

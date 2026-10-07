"""`atk wi process-loop`の待機中のmiseのツールの更新のテスト。"""

import pathlib
import subprocess
from typing import NoReturn

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import process_loop as _process_loop
from agent_toolkit._atk.wi import process_loop_mise as _pl_mise
from agent_toolkit._atk.wi import process_loop_update as _pl_update
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import process_loop_worktree as _pl_worktree
from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._testing.process_loop_support import DOTFILES_REPO_ID, isolate_process_loop_commands
from agent_toolkit.atk_test import _setup_notes


@pytest.fixture(autouse=True)
def _isolate_process_loop_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンド・Claude設定・初期値のTTLをユーザー環境から分離する。"""
    isolate_process_loop_commands(monkeypatch, tmp_path)


class TestMiseLatestRefresh:
    """dotfiles向けprocess-loopが所有するmise latest再評価を検証する。"""

    @staticmethod
    def _run_idle_loop(
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        *,
        extra_argv: list[str],
        wait_results: list[bool | BaseException],
        monotonic_values: list[float] | None = None,
        pending_counts: tuple[int, ...] = (),
        update_succeeded: bool | None = None,
    ) -> list[pathlib.Path]:
        """待機を`wait_results`の順に終え、miseの更新を受けたrootの列を返す。

        `pending_counts`を渡すと処理対象の件数をその順に返し、`update_succeeded`を渡すと再起動しない
        `update-dotfiles`の成否をその値にする。省略すると処理対象は常に0件とする。
        """
        _setup_notes(tmp_path)
        target = tmp_path / "dotfiles-target"
        target.mkdir()
        dotfiles_root = tmp_path / "dotfiles-root"
        dotfiles_root.mkdir()
        monkeypatch.setattr(_process_loop, "resolve_local_worktree", lambda _value: target)
        monkeypatch.setattr(_process_loop, "resolve_repo_id", lambda *_args, **_kwargs: DOTFILES_REPO_ID)
        monkeypatch.setattr(_pl_update, "resolve_dotfiles_root", lambda: dotfiles_root)
        counts = iter(pending_counts)
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_args, **_kwargs: next(counts, 0))
        if update_succeeded is not None:
            monkeypatch.setattr(_pl_update, "update_before_session", lambda *_args, **_kwargs: (False, update_succeeded))
        refresh_calls: list[pathlib.Path] = []

        def fake_refresh(root: pathlib.Path) -> bool:
            refresh_calls.append(root)
            return True

        monkeypatch.setattr(_pl_mise, "refresh_mise_tools", fake_refresh)
        waits = iter(wait_results)

        def fake_wait(*_args: object, **_kwargs: object) -> bool:
            result = next(waits)
            if isinstance(result, BaseException):
                raise result
            return result

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait)
        if monotonic_values is not None:
            times = iter(monotonic_values)
            monkeypatch.setattr(_process_loop.time, "monotonic", lambda: next(times))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "process-loop", f"--target-repo={target}", "--no-alerts", *extra_argv],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        return refresh_calls

    @pytest.mark.parametrize(
        ("extra_argv", "expected_calls"),
        [
            ([], 1),
            (["--internal-mise-refreshed"], 0),
            (["--no-update"], 0),
        ],
    )
    def test_startup_refresh_contract(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        extra_argv: list[str],
        expected_calls: int,
    ) -> None:
        """手動起動、正常再起動済み、更新無効の各起動契機を区別する。"""
        calls = self._run_idle_loop(
            monkeypatch,
            tmp_path,
            extra_argv=extra_argv,
            wait_results=[KeyboardInterrupt()],
            monotonic_values=[0.0] if expected_calls or "--internal-mise-refreshed" in extra_argv else None,
        )

        assert len(calls) == expected_calls

    def test_wait_loop_refreshes_at_24_hours_but_not_before(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """期限前の待機復帰では省き、24時間到達時に一度だけ再評価する。"""
        dotfiles_root = tmp_path / "dotfiles-root"
        calls = self._run_idle_loop(
            monkeypatch,
            tmp_path,
            extra_argv=[],
            wait_results=[True, True, KeyboardInterrupt()],
            monotonic_values=[0.0, 86399.0, 86400.0, 86401.0],
        )

        assert calls == [dotfiles_root, dotfiles_root]

    def test_successful_update_without_restart_resets_deadline(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """update-dotfiles成功後は起動時刻ではなく成功後から次の24時間を数える。"""
        refresh_calls = self._run_idle_loop(
            monkeypatch,
            tmp_path,
            extra_argv=[],
            wait_results=[True, True, KeyboardInterrupt()],
            monotonic_values=[0.0, 100.0, 86450.0],
            pending_counts=(1, 0, 0),
            update_succeeded=True,
        )
        dotfiles_root = tmp_path / "dotfiles-root"

        assert refresh_calls == [dotfiles_root]

    def test_failed_update_without_restart_keeps_deadline(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """update-dotfiles失敗時は起動時から数えた24時間の期限を維持する。"""
        refresh_calls = self._run_idle_loop(
            monkeypatch,
            tmp_path,
            extra_argv=[],
            wait_results=[True, True, KeyboardInterrupt()],
            monotonic_values=[0.0, 86400.0, 86400.0],
            pending_counts=(1, 0, 0),
            update_succeeded=False,
        )
        dotfiles_root = tmp_path / "dotfiles-root"

        assert refresh_calls == [dotfiles_root, dotfiles_root]

    @pytest.mark.parametrize("update_returncode", [0, 9])
    def test_session_restart_marks_only_successful_update(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        update_returncode: int,
    ) -> None:
        """セッション終了後の再起動では、子セッション後更新の結果を扱わない。"""
        _setup_notes(tmp_path)
        target = tmp_path / "dotfiles-target"
        target.mkdir()
        dotfiles_root = tmp_path / "dotfiles-root"
        dotfiles_root.mkdir()
        monkeypatch.setattr(_process_loop, "resolve_local_worktree", lambda _value: target)
        monkeypatch.setattr(_process_loop, "resolve_repo_id", lambda *_args, **_kwargs: DOTFILES_REPO_ID)
        monkeypatch.setattr(_pl_update, "resolve_dotfiles_root", lambda: dotfiles_root)
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_args, **_kwargs: 1)
        monkeypatch.setattr(_pl_update, "update_before_session", lambda *_args, **_kwargs: (True, True))
        monkeypatch.setattr(_pl_mise, "refresh_mise_tools", lambda _root: True)
        monkeypatch.setattr(_pl_worktree, "_sync_worktree_with_upstream", lambda *_args: target)
        monkeypatch.setattr(_process_loop.time, "monotonic", lambda: 0.0)

        def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            returncode = 0 if command[0] == "claude" else update_returncode
            return subprocess.CompletedProcess(command, returncode, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        restart_kwargs: list[dict[str, object]] = []

        def fake_restart(*_args: object, **kwargs: object) -> NoReturn:
            restart_kwargs.append(kwargs)
            raise SystemExit(0)

        monkeypatch.setattr(_pl_update, "restart_process_loop", fake_restart)

        with pytest.raises(SystemExit):
            atk.main(["wi", "process-loop", f"--target-repo={target}", "--no-alerts"], home=tmp_path)

        assert restart_kwargs[-1]["mise_refreshed"] is False

    def test_mise_command_uses_dotfiles_root_and_quiet_mode(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """miseをdotfiles rootで静音実行し、長時間導入用の上限を指定する。"""
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert _pl_mise.refresh_mise_tools(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001
        command, kwargs = calls[0]
        assert command == ["/resolved/mise", "install", "--quiet"]
        assert kwargs["cwd"] == tmp_path
        assert kwargs["timeout"] == 600
        assert kwargs["capture_output"] is True
        env = kwargs["env"]
        assert isinstance(env, dict)
        # 作業ツリーの`mise.lock`を書き戻さないlockedモードで、対象をプロジェクトのlockfileに限る。
        assert env["MISE_LOCKED"] == "1"
        assert env["MISE_LOCKED_SCOPES"] == "project"

    @pytest.mark.parametrize(("failure", "expected_detail"), [("nonzero", "exit code 7"), ("timeout", "途中出力")])
    def test_mise_failure_warns_and_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        failure: str,
        expected_detail: str,
    ) -> None:
        """0以外の終了とtimeoutを警告へ変換し、process-loopへ失敗を送出しない。"""

        def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if failure == "timeout":
                raise subprocess.TimeoutExpired(command, 600, stderr="途中出力")
            return subprocess.CompletedProcess(command, 7, "", "registry error")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert not _pl_mise.refresh_mise_tools(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001
        warning = capsys.readouterr().err
        assert "process-loopを継続します" in warning
        assert expected_detail in warning

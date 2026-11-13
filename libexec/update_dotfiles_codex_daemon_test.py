# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""update_dotfilesのcodex_daemonの試験。"""

# pylint: disable=protected-access

import json
import pathlib
import subprocess
import sys
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_dotfiles  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_runtime  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import (  # noqa: E402  # pylint: disable=wrong-import-position
    _DAEMON,
    _DAEMON_HELPER,
    _FAKE_CODEX,
    _STDIO_DELEGATION,
    _UPDATE_LOOP,
    _codex,
    _fake_popen,
    _fake_run,
)
from update_dotfiles_test_helpers import _isolate_sync_report as _isolate_sync_report  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _logs_environment as _logs_environment  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _no_codex_processes as _no_codex_processes  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _own_git_process_scan as _own_git_process_scan  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _real_stash_runtime as _real_stash_runtime  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _separate_git_recovery as _separate_git_recovery  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _skip_agent_toolkit_prepare as _skip_agent_toolkit_prepare  # noqa: E402  # pylint: disable=wrong-import-position


class TestCodexDaemonPause:
    """`chezmoi apply`前後の管理daemonの一時停止と再起動を、停止条件の境界ごとに検証する。"""

    @pytest.fixture(name="codex_env")
    def _codex_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> list[list[str]]:
        """Linuxで管理daemonと更新ループだけが稼働する状態を既定とする。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(update_dotfiles.sys, "platform", "linux")
        monkeypatch.setattr(update_dotfiles.shutil, "which", lambda name, path=None: _FAKE_CODEX if name == "codex" else None)
        monkeypatch.setattr(update_dotfiles.codex_processes, "codex_processes", lambda: (_DAEMON, _DAEMON_HELPER, _UPDATE_LOOP))
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        return calls

    @staticmethod
    def _commands(calls: list[list[str]]) -> list[str]:
        return [f"daemon {call[-1]}" if call[0] == _FAKE_CODEX else call[1] for call in calls]

    @pytest.mark.parametrize("operation", ["_pause_codex_daemon", "_resume_codex_daemon"])
    def test_unknown_daemon_error_does_not_skip_apply(
        self, monkeypatch: pytest.MonkeyPatch, codex_env: list[list[str]], operation: str
    ) -> None:
        """内側の任意段の未知の例外でも、必須の配備を実行し失敗として集計する。"""

        def fail(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("injected daemon failure")

        monkeypatch.setattr(update_dotfiles, operation, fail)
        monkeypatch.setattr(subprocess, "run", _fake_run({}, codex_env, stdout_by_command={"diff": "change"}))
        assert update_dotfiles.main() == 1
        assert "apply" in self._commands(codex_env)

    def test_unchanged_apply_keeps_running_daemon(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_env: list[list[str]],
    ) -> None:
        """適用差分が無ければ、applyを継続し管理daemonの停止・起動を増やさない。"""
        monkeypatch.setattr(subprocess, "run", _fake_run({}, codex_env))
        assert update_dotfiles.main() == 0
        assert self._commands(codex_env) == ["git", "init", "status", "diff", "apply"]

    def test_daemon_only_is_stopped_before_apply_and_restarted(
        self, monkeypatch: pytest.MonkeyPatch, codex_env: list[list[str]], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """利用セッションが無ければ、apply前に停止しapply後に起動する。"""
        monkeypatch.setattr(subprocess, "run", _fake_run({}, codex_env, stdout_by_command={"diff": "apply change"}))

        assert update_dotfiles.main() == 0
        assert self._commands(codex_env) == ["git", "init", "status", "diff", "daemon stop", "apply", "daemon start"]
        assert [_FAKE_CODEX, "app-server", "daemon", "stop"] in codex_env
        output = capsys.readouterr().out
        assert "Codex管理daemonを一時停止します" in output
        assert "Codex管理daemonを再起動しました。" in output
        log_text = update_dotfiles_runtime.LOG_PATH.read_text(encoding="utf-8")
        assert "Codex管理daemonを一時停止" in log_text
        assert "Codex管理daemonを再起動" in log_text

    def test_update_loop_only_runs_without_daemon_commands(
        self, monkeypatch: pytest.MonkeyPatch, codex_env: list[list[str]], capsys: pytest.CaptureFixture[str]
    ) -> None:
        """管理daemonが停止済みで更新ループだけが残る場合は、daemonを起動せずに更新する。"""
        monkeypatch.setattr(update_dotfiles.codex_processes, "codex_processes", lambda: (_UPDATE_LOOP,))
        monkeypatch.setattr(subprocess, "run", _fake_run({}, codex_env, stdout_by_command={"diff": "apply change"}))

        assert update_dotfiles.main() == 0
        assert self._commands(codex_env) == ["git", "init", "status", "diff", "apply"]
        assert "Codex管理daemon" not in capsys.readouterr().out

    @pytest.mark.parametrize(
        "processes",
        [
            pytest.param((_DAEMON, _STDIO_DELEGATION), id="delegation"),
            pytest.param((_DAEMON, _codex(14, "codex", "session")), id="interactive-session"),
        ],
    )
    def test_daemon_is_kept_when_use_cannot_be_excluded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_env: list[list[str]],
        capsys: pytest.CaptureFixture[str],
        processes: tuple[Any, ...],
    ) -> None:
        """利用セッションがあれば、停止と事前案内を省いてapplyを続行する。"""
        monkeypatch.setattr(update_dotfiles.codex_processes, "codex_processes", lambda: processes)
        monkeypatch.setattr(subprocess, "run", _fake_run({}, codex_env, stdout_by_command={"diff": "apply change"}))

        assert update_dotfiles.main() == 0
        assert self._commands(codex_env) == ["git", "init", "status", "diff", "apply"]
        captured = capsys.readouterr()
        assert "Codex管理daemon" not in captured.out
        assert "延期" not in captured.out
        assert not captured.err
        log_text = update_dotfiles_runtime.LOG_PATH.read_text(encoding="utf-8")
        assert "Codex管理daemonを停止せず" not in log_text
        assert "延期された場合" not in log_text

    def test_session_started_during_apply_prevents_daemon_start(
        self, monkeypatch: pytest.MonkeyPatch, codex_env: list[list[str]]
    ) -> None:
        """停止後にセッションが開始した場合は、apply後のdaemon操作を見送る。"""
        run = _fake_run({}, codex_env, stdout_by_command={"diff": "apply change"})

        def apply_starts_session(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
            if argv[:2] == ["chezmoi", "apply"]:
                monkeypatch.setattr(update_dotfiles.codex_processes, "codex_processes", lambda: (_STDIO_DELEGATION,))
            return run(argv, **kwargs)

        monkeypatch.setattr(subprocess, "run", apply_starts_session)
        assert update_dotfiles.main() == 0
        assert self._commands(codex_env) == ["git", "init", "status", "diff", "daemon stop", "apply"]

    def test_non_linux_does_not_inspect_codex(self, monkeypatch: pytest.MonkeyPatch, codex_env: list[list[str]]) -> None:
        """Linux以外では診断ログ復元が無く、plugin保護もeuryale限定のため、daemonを扱わない。"""
        monkeypatch.setattr(update_dotfiles.sys, "platform", "win32")
        monkeypatch.setattr(
            update_dotfiles.codex_processes,
            "codex_processes",
            lambda: (_ for _ in ()).throw(AssertionError("Linux以外でCodexを走査してはならない")),
        )
        monkeypatch.setattr(subprocess, "run", _fake_run({}, codex_env, stdout_by_command={"diff": "apply change"}))

        assert update_dotfiles.main() == 0
        assert self._commands(codex_env) == ["git", "init", "status", "diff", "apply"]

    def test_apply_failure_still_restarts_daemon(
        self, monkeypatch: pytest.MonkeyPatch, codex_env: list[list[str]], sync_report_path: pathlib.Path
    ) -> None:
        """applyが失敗しても停止したdaemonを起動し、applyの終了コードと失敗段を保つ。"""
        monkeypatch.setattr(subprocess, "run", _fake_run({"apply": 5}, codex_env, stdout_by_command={"diff": "apply change"}))

        assert update_dotfiles.main() == 5
        assert self._commands(codex_env)[-2:] == ["apply", "daemon start"]
        assert "chezmoi apply" in json.loads(sync_report_path.read_text(encoding="utf-8"))["failed_stage"]

    def test_apply_exception_still_restarts_daemon(self, monkeypatch: pytest.MonkeyPatch, codex_env: list[list[str]]) -> None:
        """apply段が例外で中断しても、停止したdaemonの起動を試みる。"""
        fake_run = _fake_run({}, codex_env, stdout_by_command={"diff": "apply change"})

        def run(argv: list[str], *args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if argv[:2] == ["chezmoi", "apply"]:
                raise KeyboardInterrupt
            return fake_run(argv, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", run)

        with pytest.raises(KeyboardInterrupt):
            update_dotfiles.main()
        assert self._commands(codex_env)[-1] == "daemon start"

    def test_stop_failure_is_reported_and_restart_is_attempted(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_env: list[list[str]],
        sync_report_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """停止に失敗してもapplyと起動を行い、停止の失敗を終了コードと同期結果へ反映する。"""
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run(
                {"daemon stop": 3},
                codex_env,
                stdout_by_command={"diff": "apply change"},
                stderr_by_command={"daemon stop": "stop failed\n"},
            ),
        )

        assert update_dotfiles.main() == 1
        assert self._commands(codex_env)[-3:] == ["daemon stop", "apply", "daemon start"]
        assert "Codex管理daemonを停止できませんでした（exit 3: stop failed）。" in capsys.readouterr().err
        report = json.loads(sync_report_path.read_text(encoding="utf-8"))
        assert report["failed_stage"] == "Codex管理daemonの一時停止と再起動"
        assert "停止できませんでした" in report["stderr_tail"]

    def test_restart_failure_reports_manual_command(
        self,
        monkeypatch: pytest.MonkeyPatch,
        codex_env: list[list[str]],
        sync_report_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """起動に失敗した場合は、手動の復帰操作を標準エラー、ログ、同期結果から確認できる。"""
        monkeypatch.setattr(
            subprocess, "run", _fake_run({"daemon start": 4}, codex_env, stdout_by_command={"diff": "apply change"})
        )

        assert update_dotfiles.main() == 1
        err = capsys.readouterr().err
        assert "Codex管理daemonを再起動できませんでした（exit 4）" in err
        assert "codex app-server daemon start" in err
        assert "update-dotfiles logs" in err
        report = json.loads(sync_report_path.read_text(encoding="utf-8"))
        assert report["failed_stage"] == "Codex管理daemonの一時停止と再起動"
        assert "codex app-server daemon start" in report["stderr_tail"]
        assert "codex app-server daemon start" in update_dotfiles_runtime.LOG_PATH.read_text(encoding="utf-8")

# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""update_dotfilesのfailuresの試験。"""

# pylint: disable=protected-access

import json
import pathlib
import subprocess
import sys
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_dotfiles  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_git  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_runtime  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import (  # noqa: E402  # pylint: disable=wrong-import-position
    _REAL_UPDATE_GIT_WITH_RECOVERY,
    _create_git_pair,
    _fake_popen,
    _fake_run,
    _FakePopen,
    _patch_real_pull,
)
from update_dotfiles_test_helpers import _isolate_sync_report as _isolate_sync_report  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _logs_environment as _logs_environment  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _no_codex_processes as _no_codex_processes  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _own_git_process_scan as _own_git_process_scan  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _real_stash_runtime as _real_stash_runtime  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _separate_git_recovery as _separate_git_recovery  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _skip_agent_toolkit_prepare as _skip_agent_toolkit_prepare  # noqa: E402  # pylint: disable=wrong-import-position


class TestStepFailureStopsExecution:
    """後続の前提になる段の失敗でそのexit codeを返し、以降の段を呼ばないことを検証する。

    失敗しても前提を壊さない段は、後続の段を続けて終了コード1で終える。
    """

    def test_step2_failure_skips_step3_and_4(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({"init": 3}, calls))
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 3
        assert len(calls) == 2
        assert calls[1][:2] == ["chezmoi", "init"]

    def test_dotfiles_pull_failure_stops_before_init(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """dotfiles自身のpullの失敗は、不確定なソースを配備しないよう以降の段を止める。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({"git": 4}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({}, calls))
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 4
        assert [call[1] for call in calls] == ["git"]

    @pytest.mark.parametrize(("failed_command", "exit_code"), [("status", 2), ("diff", 7)])
    def test_display_stage_failure_continues_to_apply(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        failed_command: str,
        exit_code: int,
    ) -> None:
        """表示専用の`chezmoi status`・`chezmoi diff`の失敗は記録して`chezmoi apply`まで続け、終了コード1で終える。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({failed_command: exit_code}, calls))
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 1
        assert [call[1] for call in calls] == ["git", "init", "status", "diff", "apply"]
        err = capsys.readouterr().err
        assert "失敗しても後続の段を続けた段:" in err
        assert f"chezmoi {failed_command}" in err

    def test_failure_guidance_follows_continued_failure_list_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """失敗の案内は続行した段の一覧の後に1回だけ出る。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({"status": 2, "diff": 7}, calls))
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 1
        err = capsys.readouterr().err
        assert err.count("次の操作:") == 1
        assert err.count("永続ログ:") == 1
        assert err.count("失敗の詳細は update-dotfiles logs") == 1
        assert err.index("失敗しても後続の段を続けた段:") < err.index("次の操作:") < err.index("永続ログ:")

    def test_post_apply_failure_shows_next_action_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """post-applyの失敗で`chezmoi apply`が失敗した実行でも、次の操作と永続ログの所在が画面に各1回出る。

        post-applyは`update-dotfiles`経由では失敗後の案内を出さないため、終了時の案内が唯一の次の操作になる。
        """
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({"apply": 1}, calls))
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 1
        err = capsys.readouterr().err
        assert err.count("次の操作:") == 1
        assert err.count("永続ログ:") == 1

    def test_multiple_continued_failures_are_all_listed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        sync_report_path: pathlib.Path,
    ) -> None:
        """続行した複数の失敗を一覧に並べ、同期記録へ全ての段の名前を残す。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({"status": 2, "diff": 7}, calls))
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 1
        err = capsys.readouterr().err
        assert "chezmoi status" in err and "chezmoi diff" in err
        report = json.loads(sync_report_path.read_text(encoding="utf-8"))
        assert "chezmoi status" in report["failed_stage"] and "chezmoi diff" in report["failed_stage"]

    def test_apply_failure_after_continued_failure_keeps_apply_exit_code(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """前提となる`chezmoi apply`が失敗した場合は、その終了コードを返し、続行した失敗も一覧に並べる。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({"status": 2, "apply": 5}, calls))
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 5
        assert "chezmoi status" in capsys.readouterr().err

    def test_mise_lock_discard_failure_continues_update(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`mise.lock`の差分を破棄できなくても、pullへ進み、失敗を記録して終了コード1で終える。"""
        local, _seed, _remote = _create_git_pair(tmp_path)
        monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", local)
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "update.lock")
        monkeypatch.setattr(update_dotfiles_git, "update_git_with_recovery", _REAL_UPDATE_GIT_WITH_RECOVERY)
        monkeypatch.setattr(update_dotfiles_git, "_discard_mise_lock_changes", lambda: False)
        _patch_real_pull(monkeypatch, local)
        chezmoi_calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run({}, chezmoi_calls))

        assert update_dotfiles.main() == 1
        assert [call[1] for call in chezmoi_calls if call[0] == "chezmoi"] == ["init", "status", "diff", "apply"]
        err = capsys.readouterr().err
        assert "mise.lockの差分を破棄できませんでしたが、更新を続けます" in err
        assert "mise.lockの差分の破棄" in err


class TestSyncReport:
    """同期結果の記録が、次のセッションの続行判定へ必要な内容を残すことを検証する。"""

    def test_successful_run_is_recorded_as_succeeded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        sync_report_path: pathlib.Path,
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(subprocess, "run", _fake_run({}, calls))
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 0

        report = json.loads(sync_report_path.read_text(encoding="utf-8"))
        assert report["status"] == "succeeded"
        assert report["exit_code"] == 0
        assert report["failed_stage"] is None
        assert report["stderr_tail"] is None
        assert report["run_id"]
        assert report["started_at"] and report["finished_at"]

    def test_failed_stage_and_stderr_are_recorded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        sync_report_path: pathlib.Path,
    ) -> None:
        """継承する段の失敗は、段の名前と終了コードから読み取れる。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({"apply": 5}, calls, stderr_by_command={"apply": "対象ツールの導入に失敗しました\n"}),
        )
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 5

        report = json.loads(sync_report_path.read_text(encoding="utf-8"))
        assert report["status"] == "failed"
        assert report["exit_code"] == 5
        assert "chezmoi apply" in report["failed_stage"]
        assert report["stderr_tail"] is None

    @pytest.mark.parametrize(
        ("failed_command", "log_stage", "stage_title"),
        [
            ("git", "1/5", "git pull"),
            ("init", "2/5", "chezmoi init (テンプレート再展開)"),
            ("status", "3/5", "chezmoi status (apply予定のファイル)"),
            ("diff", "4/5", "chezmoi diff (上書き前の差分)"),
            ("apply", "5/5", "chezmoi apply (post-apply実行)"),
        ],
    )
    def test_launch_failure_keeps_log_stage_and_report_title(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        sync_report_path: pathlib.Path,
        failed_command: str,
        log_stage: str,
        stage_title: str,
    ) -> None:
        calls: list[list[str]] = []
        successful_popen = _fake_popen({}, calls)

        def popen(argv: list[str], **kwargs: object) -> _FakePopen:
            if failed_command == "git":
                raise OSError("起動不能")
            return successful_popen(argv, **kwargs)

        successful_run = _fake_run({}, calls)

        def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if argv[1] == failed_command:
                raise OSError("起動不能")
            return successful_run(argv, **kwargs)

        monkeypatch.setattr(subprocess, "Popen", popen)
        monkeypatch.setattr(subprocess, "run", run)
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 1
        log_text = update_dotfiles_runtime.LOG_PATH.read_text(encoding="utf-8")  # noqa: SLF001
        assert f"stage起動失敗: {log_stage} {stage_title}" in log_text
        report = json.loads(sync_report_path.read_text(encoding="utf-8"))
        assert report["failed_stage"] == stage_title


class TestCapturedStderr:
    """各段が生成する標準エラー出力の転送先を検証する。"""

    def test_successful_git_output_is_forwarded_to_stdout(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(
            subprocess,
            "Popen",
            _fake_popen(
                {},
                calls,
                stdout_by_command={"git": "git stdout\n"},
                stderr_by_command={"git": "remote: Enumerating objects\n"},
            ),
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run(
                {},
                calls,
                stdout_by_command={"git": "git stdout\n"},
                stderr_by_command={"git": "remote: Enumerating objects\n"},
            ),
        )
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 0
        captured = capsys.readouterr()
        assert "git stdout\n" in captured.out
        assert "remote: Enumerating objects\n" in captured.out
        assert not captured.err

    def test_failed_git_stderr_remains_stderr(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(
            subprocess,
            "Popen",
            _fake_popen({"git": 2}, calls, stderr_by_command={"git": "fatal: pull failed\n"}),
        )
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({"git": 2}, calls, stderr_by_command={"git": "fatal: pull failed\n"}),
        )
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 2
        captured = capsys.readouterr()
        assert "fatal: pull failed" not in captured.out
        assert captured.err == (
            "fatal: pull failed\n次の操作: 失敗理由を確認し、update-dotfilesを再実行してください。\n"
            f"永続ログ: {update_dotfiles_runtime.LOG_PATH}\n"  # noqa: SLF001
            "失敗の詳細は update-dotfiles logs で直近1回の実行ログを表示して確認できる。\n"
        )
        assert len(calls) == 1

    def test_successful_status_stderr_is_forwarded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({}, calls, stderr_by_command={"status": "chezmoi status warning\n"}),
        )
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 0
        assert capsys.readouterr().err == "chezmoi status warning\n"

    def test_diff_stderr_is_forwarded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({}, calls, stderr_by_command={"diff": "chezmoi diff warning\n"}),
        )
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 0
        assert capsys.readouterr().err == "chezmoi diff warning\n"

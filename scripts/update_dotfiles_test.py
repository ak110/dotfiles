"""`scripts/update_dotfiles.py`のテスト。

通常4段とforce時5段の直列実行順序・fail-fast・排他ロック・標準ストリームを検証する。
"""

import pathlib
import subprocess
import sys
import time
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import update_dotfiles  # noqa: E402  # pylint: disable=wrong-import-position


def _fake_run(
    returncodes: dict[str, int],
    calls: list[list[str]],
    *,
    stdout_by_command: dict[str, str] | None = None,
    stderr_by_command: dict[str, str] | None = None,
) -> Any:
    """コマンド名（argv[0:2]相当）ごとの終了コードを返すfake `subprocess.run`。"""
    stdout_by_command = stdout_by_command or {}
    stderr_by_command = stderr_by_command or {}

    def fake_run(argv: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
        calls.append(list(argv))
        key = argv[1] if argv[0] == "chezmoi" else argv[0]
        returncode = returncodes.get(key, 0)
        stdout_text = stdout_by_command.get(key, "")
        stderr_text = stderr_by_command.get(key, "")
        stdout: Any = stdout_text if kwargs.get("text") else stdout_text.encode()
        stderr: Any = stderr_text if kwargs.get("text") else stderr_text.encode()
        return subprocess.CompletedProcess(argv, returncode=returncode, stdout=stdout, stderr=stderr)

    return fake_run


class TestFourStepsInOrder:
    """4段が順に呼ばれ、成功時にexit code 0を返すことを検証する。"""

    def test_all_steps_succeed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run({}, calls))
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 0
        assert calls[0][:2] == ["chezmoi", "git"]
        assert calls[1][:2] == ["chezmoi", "init"]
        assert calls[2][:2] == ["chezmoi", "status"]
        assert calls[3][:2] == ["chezmoi", "apply"]
        assert "--quiet" in calls[0]
        assert "--force" not in calls[3]

    def test_force_adds_diff_before_forced_apply(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({}, calls, stdout_by_command={"diff": "diff output\n"}),
        )
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main(["--force"]) == 0
        assert [call[1] for call in calls] == ["git", "init", "status", "diff", "apply"]
        assert calls[3] == ["chezmoi", "diff", "--no-pager"]
        assert calls[4] == ["chezmoi", "apply", "--force"]
        captured = capsys.readouterr()
        assert "=== [4/5] chezmoi diff" in captured.out
        assert "diff output\n" in captured.out
        assert not captured.err


class TestStepFailureStopsExecution:
    """途中段の失敗でそのexit codeを返し、以降の段を呼ばないことを検証する。"""

    def test_step2_failure_skips_step3_and_4(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run({"init": 3}, calls))
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 3
        assert len(calls) == 2
        assert calls[1][:2] == ["chezmoi", "init"]

    def test_step3_failure_stops_before_apply(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """`chezmoi status`段の失敗も他段と同様にfail-fastすること。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run({"status": 2}, calls))
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 2
        assert len(calls) == 3

    def test_diff_failure_stops_before_apply(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run({"diff": 7}, calls))
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main(["--force"]) == 7
        assert [call[1] for call in calls] == ["git", "init", "status", "diff"]


class TestCapturedStderr:
    """キャプチャ対象段の標準エラー出力が終了コードによらず転送されることを検証する。"""

    def test_successful_status_stderr_is_forwarded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({}, calls, stderr_by_command={"status": "chezmoi status warning\n"}),
        )
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 0
        assert capsys.readouterr().err == "chezmoi status warning\n"

    def test_force_diff_stderr_is_forwarded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({}, calls, stderr_by_command={"diff": "chezmoi diff warning\n"}),
        )
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main(["--force"]) == 0
        assert capsys.readouterr().err == "chezmoi diff warning\n"


def test_unknown_argument_exits_2() -> None:
    """未知引数はargparseの終了コード2で拒否する。"""
    with pytest.raises(SystemExit) as exc_info:
        update_dotfiles.main(["--unknown"])
    assert exc_info.value.code == 2


_LOCK_HOLDER_CODE = (
    "import filelock, pathlib, sys, time\n"
    "lock = filelock.FileLock(sys.argv[1])\n"
    "lock.acquire()\n"
    "pathlib.Path(sys.argv[2]).write_text('ready', encoding='utf-8')\n"
    "while not pathlib.Path(sys.argv[3]).exists():\n"
    "    time.sleep(0.05)\n"
    "lock.release()\n"
)


class TestLockExclusion:
    """排他ロックが機能すること（別プロセス保持中はタイムアウトしてexit code 1）を検証する。

    同一プロセス内で2個の`FileLock`オブジェクトを取得するだけでは、プロセス間排他という
    中核要件（複数の`atk mq process-loop`常駐・手動実行の同時実行対策）を検証できないため、
    `subprocess.Popen`で別プロセスにロックを保持させる。
    """

    def test_lock_timeout_returns_1_without_running_chezmoi(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run({}, calls))
        lock_path = tmp_path / "locks" / "update-dotfiles.lock"
        lock_path.parent.mkdir(parents=True)
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", lock_path)
        monkeypatch.setattr(update_dotfiles, "_LOCK_TIMEOUT_SEC", 0.2)

        ready_path = tmp_path / "holder_ready"
        release_path = tmp_path / "holder_release"
        with subprocess.Popen(  # noqa: S603
            [sys.executable, "-c", _LOCK_HOLDER_CODE, str(lock_path), str(ready_path), str(release_path)]
        ) as holder:
            try:
                for _ in range(100):
                    if ready_path.exists():
                        break
                    time.sleep(0.05)
                else:
                    pytest.fail("別プロセスがロックを取得できなかった")

                assert update_dotfiles.main() == 1
            finally:
                release_path.write_text("release", encoding="utf-8")
                holder.wait(timeout=5)
        assert not calls


class TestFilterApplyPending:
    """`chezmoi status`出力の2列目フィルタを公開インターフェース経由で検証する。"""

    def test_second_column_non_space_lines_are_kept(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        status_output = "\n".join(["  .chezmoiroot", "A  .gitignore", "RM bin/foo"])
        calls: list[list[str]] = []
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({}, calls, stdout_by_command={"status": status_output}),
        )
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 0
        captured = capsys.readouterr()
        assert "RM bin/foo" in captured.out
        assert "  .chezmoiroot" not in captured.out
        assert "A  .gitignore" not in captured.out

    def test_short_lines_are_excluded(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(
            subprocess,
            "run",
            _fake_run({}, calls, stdout_by_command={"status": "A"}),
        )
        monkeypatch.setattr(update_dotfiles, "_LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")

        assert update_dotfiles.main() == 0
        assert "\nA\n" not in f"\n{capsys.readouterr().out}\n"

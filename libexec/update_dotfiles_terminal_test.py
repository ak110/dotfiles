# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""update_dotfilesのterminalの試験。"""

# pylint: disable=protected-access

import pathlib
import subprocess
import sys
import time

import psutil
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_dotfiles  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_runtime  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import (  # noqa: E402  # pylint: disable=wrong-import-position
    _LOCK_HOLDER_CODE,
    _fake_run,
    _run_git_pull_in_pty,
)
from update_dotfiles_test_helpers import _isolate_sync_report as _isolate_sync_report  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _logs_environment as _logs_environment  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _no_codex_processes as _no_codex_processes  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _own_git_process_scan as _own_git_process_scan  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _real_stash_runtime as _real_stash_runtime  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _separate_git_recovery as _separate_git_recovery  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _skip_agent_toolkit_prepare as _skip_agent_toolkit_prepare  # noqa: E402  # pylint: disable=wrong-import-position


@pytest.mark.skipif(sys.platform == "win32", reason="ptyと/dev/ttyを使用するLinux専用のテスト")
@pytest.mark.filterwarnings("error::DeprecationWarning")
@pytest.mark.parametrize("timeout", [30, None])
def test_git_pull_preserves_terminal_interaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    timeout: int | None,
) -> None:
    """上限の有無にかかわらず、子が制御端末から入力を受け取る。

    上限ありのテストは端末入力の受け渡しを検査するため、
    並行実行の負荷でも到達しない秒数を渡す。上限の到達側は
    `test_git_pull_timeout_terminates_stream_holding_descendant`が検査する。
    """
    returncode, output, _pid_path, _elapsed = _run_git_pull_in_pty(
        tmp_path,
        monkeypatch,
        mode="interactive",
        timeout=timeout,
        input_text="secret-value",
    )
    assert returncode == 0
    assert "passphrase:" in output


@pytest.mark.skipif(sys.platform == "win32", reason="ptyを使用するLinux専用のテスト")
@pytest.mark.filterwarnings("error::DeprecationWarning")
def test_git_pull_timeout_terminates_stream_holding_descendant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """標準ストリームを継承する孫も終了し、回収上限の内側で復帰する。"""
    started = time.monotonic()
    returncode, output, pid_path, elapsed = _run_git_pull_in_pty(
        tmp_path,
        monkeypatch,
        mode="hang",
        timeout=0.05,
    )
    assert returncode == 1
    assert elapsed < 12
    del output
    pid_deadline = time.monotonic() + 5
    while not pid_path.exists():
        if time.monotonic() >= pid_deadline:
            pytest.fail(f"孫プロセスのPIDファイルが5秒以内に作成されなかった（開始から{time.monotonic() - started:.3f}秒）")
        time.sleep(0.05)
    descendant_pid = int(pid_path.read_text(encoding="utf-8"))
    assert not psutil.pid_exists(descendant_pid) or psutil.Process(descendant_pid).status() in (
        psutil.STATUS_ZOMBIE,
        psutil.STATUS_DEAD,
    )


class TestLockExclusion:
    """排他ロックが機能すること（別プロセス保持中はタイムアウトしてexit code 1）を検証する。

    同一プロセス内で2個の`FileLock`オブジェクトを取得するだけでは、プロセス間排他という
    中核要件（複数の`atk wi process-loop`常駐・手動実行の同時実行対策）を検証できないため、
    `subprocess.Popen`で別プロセスにロックを保持させる。
    """

    def test_lock_timeout_returns_1_without_running_chezmoi(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        calls: list[list[str]] = []
        monkeypatch.setattr(subprocess, "run", _fake_run({}, calls))
        lock_path = tmp_path / "locks" / "update-dotfiles.lock"
        lock_path.parent.mkdir(parents=True)
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", lock_path)
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_TIMEOUT_SEC", 0.2)

        ready_path = tmp_path / "holder_ready"
        release_path = tmp_path / "holder_release"
        with subprocess.Popen(  # noqa: S603
            [sys.executable, "-c", _LOCK_HOLDER_CODE, str(lock_path), str(ready_path), str(release_path)]
        ) as holder:
            try:
                ready_deadline = time.monotonic() + 30
                while not ready_path.exists():
                    holder_returncode = holder.poll()
                    if holder_returncode is not None:
                        pytest.fail(f"ロック保持プロセスが終了した（終了コード: {holder_returncode}）")
                    if time.monotonic() >= ready_deadline:
                        pytest.fail("別プロセスがロックを取得できなかった")
                    time.sleep(0.05)

                assert update_dotfiles.main() == 1
            finally:
                release_path.write_text("release", encoding="utf-8")
                holder.wait(timeout=5)
        assert not calls

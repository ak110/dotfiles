# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""update_dotfilesのcodex_daemonの試験。"""

# pylint: disable=protected-access

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_dotfiles  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_runtime  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import (  # noqa: E402  # pylint: disable=wrong-import-position
    _DAEMON,
    _DAEMON_HELPER,
    _UPDATE_LOOP,
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

from pytools._internal import codex_processes  # noqa: E402  # pylint: disable=wrong-import-position


@pytest.mark.parametrize("apply_code", [0, 1])
def test_chezmoi_apply_does_not_control_running_daemon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, apply_code: int
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: (_DAEMON, _DAEMON_HELPER, _UPDATE_LOOP))
    monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "locks" / "update-dotfiles.lock")
    monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls))
    monkeypatch.setattr(subprocess, "run", _fake_run({"apply": apply_code}, calls, stdout_by_command={"diff": "apply change"}))

    assert update_dotfiles.main() == apply_code
    assert ["chezmoi", "apply", "--force"] in calls
    assert all(call[1:3] != ["app-server", "daemon"] for call in calls)

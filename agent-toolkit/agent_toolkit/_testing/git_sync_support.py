import pathlib
import subprocess

import pytest

from agent_toolkit._atk import git_sync, git_sync_lock

REAL_GIT_CPU = pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitのindex・分岐・ローカルremoteへの保存/同"
        + "期を確認し、Git応答の模擬群は通常区分へ残す。同群Linu"
        + "x CI所有CPU最大0.442秒。"
    ),
)


@pytest.fixture(autouse=True)
def _agent_output_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(git_sync, "is_human_terminal", lambda: False)
    monkeypatch.setattr(git_sync_lock, "is_human_terminal", lambda: False)


@pytest.fixture(name="remote_sync")
def _remote_sync_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gitを代替するテストでは、同期の前提となるupstreamの照会も同じ外部応答へ含める。"""
    original = git_sync._run_git_result  # pylint: disable=protected-access

    def query(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
        if args == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return subprocess.CompletedProcess(args, 0, "origin/main\n", "")
        return original(args, cwd)

    monkeypatch.setattr(git_sync, "_run_git_result", query)

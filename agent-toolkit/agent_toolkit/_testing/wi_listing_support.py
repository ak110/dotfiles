import datetime
import os
import pathlib

import pytest

from agent_toolkit._testing import git_repository

_AGENT_ENVIRONMENT_VARIABLES = ("AI_AGENT", "CODEX_CI", "CLAUDECODE", "CURSOR_AGENT")


@pytest.fixture(autouse=True)
def _clear_agent_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def _git_repository_with_old_later_commit(root: pathlib.Path) -> str:
    old = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=2)).isoformat()
    env = {**os.environ, "GIT_AUTHOR_DATE": old, "GIT_COMMITTER_DATE": old}
    git_repository.init_repository(root, origin="https://github.com/example/stale.git")
    base = git_repository.commit_all(root, "base", env=env)
    git_repository.commit_all(root, "later", env=env)
    return base


_NOTICE = {"status": "notice", "old_commit_count": 1, "later_commit_count": 1}

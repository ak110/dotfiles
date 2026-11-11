"""WIのsync試験で共有する準備処理。"""

import pathlib
import subprocess

import pytest

from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._common import file_lock as _file_lock
from agent_toolkit._testing import git_repository


@pytest.fixture(name="remote_sync")
def _remote_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gitを代替するテストへupstreamの公開照会の応答を加える。"""
    original = _atk_git_sync._run_git_result  # pylint: disable=protected-access

    def query(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
        if args == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
            return subprocess.CompletedProcess(args, 0, "origin/main\n", "")
        return original(args, cwd)

    monkeypatch.setattr(_atk_git_sync, "_run_git_result", query)


_LEGACY_AWI = "---\ntarget_repo: github.com/example/repo\n---\n\n本文\n"


_LEGACY_UWI = "---\ntarget_repo: github.com/example/repo\nquestion_type: free-form\n---\n\n## 質問\n\nQ\n\n## 回答\n\n"


def _init_legacy_repo(root: pathlib.Path, entries: dict[str, str]) -> None:
    """旧2階層レイアウトのローカル限定リポジトリを`root`へ作成する。

    remoteとupstreamを設定せず、移行処理をローカルだけで完了させる。
    `entries`はrepo root相対パスと本文の対応とする。
    """
    git_repository.init_repository(root, files={".gitignore": f"{_file_lock.PLAN_LOCK_IGNORE_PATTERN}\n", **entries})
    git_repository.commit_all(root, "init")

"""pytest conftest: このディレクトリ配下のテストへ共通のfixtureを提供する。

開発機の状態からの隔離は`agent-toolkit/conftest.py`が適用する。自動fixtureとその復帰fixtureは`_testing.pytest_plugin`が登録する。
"""

import pathlib
from collections.abc import Callable

import pytest

from agent_toolkit._testing import git_repository as _git_repository


@pytest.fixture(name="make_dirty_repo")
def _make_dirty_repo() -> Callable[[pathlib.Path], pathlib.Path]:
    """変更ありのgitリポジトリを作成するfactory fixture。

    trackedファイルを変更した未コミット状態のリポジトリを返す。
    """

    def _make(tmp_path: pathlib.Path, name: str = "repo") -> pathlib.Path:
        repo = _git_repository.init_repository(tmp_path / name, files={"file.txt": "initial"}, commit_message="init")
        # trackedファイルを変更して未コミット状態にする。
        (repo / "file.txt").write_text("modified")
        return repo

    return _make


@pytest.fixture(name="make_clean_repo")
def _make_clean_repo() -> Callable[[pathlib.Path], pathlib.Path]:
    """変更なしのgitリポジトリを作成するfactory fixture。"""

    def _make(tmp_path: pathlib.Path, name: str = "clean") -> pathlib.Path:
        return _git_repository.init_repository(tmp_path / name, files={"file.txt": "clean"}, commit_message="init")

    return _make

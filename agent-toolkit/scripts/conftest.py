"""pytest conftest: テスト用のgitリポジトリ作成factory fixtureを提供する。"""

import pathlib
import subprocess
from collections.abc import Callable

import pytest


@pytest.fixture(name="make_dirty_repo")
def _make_dirty_repo() -> Callable[[pathlib.Path], pathlib.Path]:
    """変更ありのgitリポジトリを作成するfactory fixture。

    trackedファイルを変更した未コミット状態のリポジトリを返す。
    """

    def _make(tmp_path: pathlib.Path, name: str = "repo") -> pathlib.Path:
        repo = tmp_path / name
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo), capture_output=True, check=True)
        (repo / "file.txt").write_text("initial")
        subprocess.run(["git", "add", "file.txt"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "--message=init"], cwd=str(repo), capture_output=True, check=True)
        # trackedファイルを変更して未コミット状態にする。
        (repo / "file.txt").write_text("modified")
        return repo

    return _make


@pytest.fixture(name="make_clean_repo")
def _make_clean_repo() -> Callable[[pathlib.Path], pathlib.Path]:
    """変更なしのgitリポジトリを作成するfactory fixture。"""

    def _make(tmp_path: pathlib.Path, name: str = "clean") -> pathlib.Path:
        repo = tmp_path / name
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "test"], cwd=str(repo), capture_output=True, check=True)
        (repo / "file.txt").write_text("clean")
        subprocess.run(["git", "add", "file.txt"], cwd=str(repo), capture_output=True, check=True)
        subprocess.run(["git", "commit", "--message=init"], cwd=str(repo), capture_output=True, check=True)
        return repo

    return _make

"""所有者だけが使うディレクトリの作成が、POSIXでは0700を保ち、一時ディレクトリを一意に作成・削除することを検証する。"""

from __future__ import annotations

import os
import pathlib
import stat

import pytest

from agent_toolkit._common import private_directory

pytestmark = pytest.mark.windows


def test_make_temporary_creates_distinct_owner_directories(tmp_path: pathlib.Path) -> None:
    first = private_directory.make_temporary(tmp_path, "work-")
    second = private_directory.make_temporary(tmp_path, "work-")
    assert first != second
    for path in (first, second):
        assert path.is_absolute() and path.is_dir() and path.parent == tmp_path and path.name.startswith("work-")
        if os.name == "posix":
            assert stat.S_IMODE(path.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name != "posix", reason="POSIXのmodeを確かめる")
def test_mode_keeps_posix_directories_owner_only(tmp_path: pathlib.Path) -> None:
    """POSIXでは`MODE`で作成したディレクトリを所有者だけが使える。"""
    path = tmp_path / "state"
    path.mkdir(mode=private_directory.MODE)
    assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_temporary_removes_read_only_contents(tmp_path: pathlib.Path) -> None:
    with private_directory.temporary(tmp_path, "staging-") as staging:
        assert staging.parent == tmp_path and staging.name.startswith("staging-")
        locked = staging / "nested" / "locked.txt"
        locked.parent.mkdir()
        locked.write_text("x", encoding="utf-8")
        locked.chmod(stat.S_IREAD)
    assert not staging.exists()


def test_temporary_allows_body_to_remove_directory(tmp_path: pathlib.Path) -> None:
    with private_directory.temporary(tmp_path, "staging-") as staging:
        staging.rmdir()
    assert not staging.exists()

"""所有するディレクトリの権限回復と、保持時の元modeの復元を検証する。"""

from __future__ import annotations

import os
import pathlib
import stat
import typing

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.managed_temp import permissions


@pytest.mark.skipif(os.name != "posix", reason="POSIXの権限ビットと記述子を検証する")
@pytest.mark.parametrize("mode", [0, 0o100, 0o300, 0o500, 0o555])
def test_directory_restores_retained_mode_after_success_and_failure(tmp_path: pathlib.Path, mode: int) -> None:
    """走査中の成功・中断の双方で保持対象の元modeへ戻る。"""
    root = tmp_path / "locked"
    root.mkdir()
    root.chmod(mode)
    try:
        with permissions.directory(root) as opened:
            assert stat.S_IMODE(os.fstat(opened).st_mode) & 0o700 == 0o700
        assert stat.S_IMODE(root.stat().st_mode) == mode
        with pytest.raises(RuntimeError), permissions.directory(root):
            raise RuntimeError("走査の中断")
        assert stat.S_IMODE(root.stat().st_mode) == mode
    finally:
        root.chmod(0o700)


@pytest.mark.skipif(os.name != "posix", reason="POSIXの所有者とsymlinkを検証する")
def test_directory_preserves_foreign_owner_and_symlink_target(tmp_path: pathlib.Path) -> None:
    """所有者不一致とsymlinkの拒否では、対象の権限を変えない。"""
    root = tmp_path / "locked"
    root.mkdir()
    root.chmod(0)
    metadata = list(root.lstat())
    metadata[4] = os.geteuid() + 1
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    try:
        with (
            pytest.raises(managed_temp.ManagedTempError, match="所有者"),
            permissions.directory(root, expected=os.stat_result(metadata)),
        ):
            pytest.fail("所有者の異なる対象を開いた")
        with pytest.raises(managed_temp.ManagedTempError), permissions.directory(link):
            pytest.fail("symlinkを追跡した")
        assert stat.S_IMODE(root.stat().st_mode) == 0
    finally:
        root.chmod(0o700)


@pytest.mark.skipif(os.name != "posix", reason="POSIXのO_PATHを使う置換検証")
def test_permission_recovery_changes_only_the_pinned_directory(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """権限回復と再openの間で差し替わっても、リンク先のmodeを保つ。"""
    root = tmp_path / "locked"
    moved = tmp_path / "moved"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir(mode=0o500)
    root.chmod(0)
    original_chmod = os.chmod
    replaced = False

    def replace_after_chmod(path: typing.Any, mode: int, **kwargs: typing.Any) -> None:
        nonlocal replaced
        original_chmod(path, mode, **kwargs)
        if not replaced and str(path).startswith("/proc/self/fd/"):
            replaced = True
            root.rename(moved)
            root.symlink_to(outside, target_is_directory=True)

    try:
        monkeypatch.setattr(os, "chmod", replace_after_chmod)
        with pytest.raises((OSError, managed_temp.ManagedTempError)), permissions.directory(root):
            pytest.fail("置換後の対象を開いた")
        assert stat.S_IMODE(outside.stat().st_mode) == 0o500
        assert stat.S_IMODE(moved.stat().st_mode) == 0
    finally:
        original_chmod(moved if moved.exists() else root, 0o700)
        original_chmod(outside, 0o700)

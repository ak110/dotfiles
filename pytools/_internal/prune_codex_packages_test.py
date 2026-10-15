"""公式パッケージ排他と安全な削除範囲の結合試験。"""

import os
import pathlib
import subprocess
import types

import pytest
from agent_toolkit._testing import isolation

from pytools._internal import codex_processes, prune_codex_packages
from pytools.prune_codex_packages_test import _release


@pytest.mark.skipif(os.name == "nt", reason="POSIXのflock競合を実物で検証する")
def test_official_flock_blocks_pruning(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """別fdが保持する公式flockとの競合中は旧版を保持する。"""
    import fcntl  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    current = _release(tmp_path, "standalone", "0.162.1")
    old = _release(tmp_path, "standalone", "0.150.0")
    root = current.parent.parent
    (root / "current").symlink_to(current, target_is_directory=True)
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: ())
    with (root / "install.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert prune_codex_packages.prune(home=tmp_path).deferred
        assert old.is_dir()
    assert prune_codex_packages.prune(home=tmp_path).removed == [old]


def test_unknown_incomplete_and_linked_releases_are_preserved(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """不完全な生成物と帰属不明・リンク実体へ削除を広げない。"""
    current = _release(tmp_path, "standalone", "0.162.1")
    root = current.parent.parent
    (root / "current").symlink_to(current, target_is_directory=True)
    incomplete = current.parent / "0.150.0-x86_64-unknown-linux-musl"
    incomplete.mkdir()
    unknown = current.parent / "user-files"
    unknown.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = current.parent / "0.149.0-x86_64-unknown-linux-musl"
    linked.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: ())
    result = prune_codex_packages.prune(home=tmp_path)
    assert not result.removed
    assert all(path.exists() for path in (incomplete, unknown, outside, linked))


def test_codex_home_alias_preserves_selected_release(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """CODEX_HOMEへリンク経由で到達しても、選択先の実体と削除候補を正しく対応付ける。"""
    home = tmp_path / "actual"
    current = _release(home, "standalone", "0.162.1")
    old = _release(home, "standalone", "0.150.0")
    (current.parent.parent / "current").symlink_to(current, target_is_directory=True)
    alias = tmp_path / "alias"
    alias.symlink_to(home, target_is_directory=True)
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: ())
    result = prune_codex_packages.prune(home=alias)
    assert current.is_dir()
    assert result.removed == [old]
    assert not old.exists()


@pytest.mark.parametrize("available", [False, True])
def test_windows_lock_uses_exclusive_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    available: bool,
) -> None:
    """Win32 APIへ公式と同じ共有不可オープンを渡し、競合時には削除を延期する。"""
    current = _release(tmp_path, "standalone", "0.162.1")
    old = _release(tmp_path, "standalone", "0.150.0")
    (current.parent.parent / "current").symlink_to(current, target_is_directory=True)
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: ())
    calls = []
    closed = []

    def create(*args):
        calls.append(args)
        return 42 if available else prune_codex_packages.ctypes.c_void_p(-1).value

    def close(handle):
        closed.append(handle)
        return 1

    kernel = types.SimpleNamespace(CreateFileW=create, CloseHandle=close)
    monkeypatch.setattr(prune_codex_packages.ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    monkeypatch.setattr(prune_codex_packages.ctypes, "get_last_error", lambda: 32, raising=False)
    result = prune_codex_packages.prune(home=tmp_path, windows=True)
    assert calls[0][1:5] == (0xC0000000, 0, None, 4)
    assert bool(result.deferred) != available
    assert old.exists() != available
    assert closed == ([42] if available else [])


@pytest.mark.skipif(os.name != "nt", reason="Windowsの共有不可オープンを実機で検証する")
def test_windows_native_open_conflict(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """公式のcurrent junctionと共有不可排他を実機で検証し、解放後の次回に回収する。"""
    current = _release(tmp_path, "standalone", "0.162.1")
    old = _release(tmp_path, "standalone", "0.150.0")
    root = current.parent.parent
    subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(root / "current"), str(current)],
        env=isolation.host_environ(),
        check=True,
        capture_output=True,
        timeout=10,
    )
    assert (root / "current").is_junction()
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: ())
    with (root / "install.lock").open("a+b"):
        assert prune_codex_packages.prune(home=tmp_path).deferred
        assert old.is_dir()
    assert prune_codex_packages.prune(home=tmp_path).removed == [old]

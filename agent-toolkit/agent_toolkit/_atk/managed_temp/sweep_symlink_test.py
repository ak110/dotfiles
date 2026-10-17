"""symlinkを祖先に持つ領域とgitdirの自動掃引を検証する。"""

# pylint: disable=protected-access

from __future__ import annotations

import datetime
import os
import pathlib

import pytest

from agent_toolkit._atk import managed_temp as subject
from agent_toolkit._atk.managed_temp import inventory, registry
from agent_toolkit._testing.managed_temp_support import _set_tree_mtime, setattr_in_managed_temp_modules


@pytest.mark.skipif(os.name != "posix", reason="POSIXのsymlink祖先を通るパス")
@pytest.mark.parametrize("linked", ["root", "gitdir", "both"])
@pytest.mark.parametrize("registered", [True, False])
def test_sweep_preserves_worktree_through_symlink_ancestors(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, linked: str, registered: bool
) -> None:
    """初回と記録経由の掃引でworktreeを保持し、保持根拠の失効後に回収する。"""
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(real, target_is_directory=True)
    cache_parent = alias if linked in ("root", "both") else real
    cache_root = cache_parent / "cache"
    cache_root.mkdir(mode=0o700)
    setattr_in_managed_temp_modules(monkeypatch, "_temp_root", lambda: cache_root)
    repository_admin = real / "repository" / ".git" / "worktrees" / "lane"
    repository_admin.mkdir(parents=True)
    gitdir_parent = alias if linked in ("gitdir", "both") else real
    gitdir = gitdir_parent / repository_admin.relative_to(real)
    target = subject.create_managed_temp("live-worktree")
    registration = registry._registry_path(target)
    if not registered:
        registration.unlink()
    worktree = target / "wt"
    worktree.mkdir()
    git_file = worktree / ".git"
    git_file.write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    payload = worktree / "saved.txt"
    payload.write_text("作業内容を保持", encoding="utf-8")
    now = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=8)
    old_ns = int((now - datetime.timedelta(days=8)).timestamp() * 1_000_000_000)
    _set_tree_mtime(target, old_ns)

    first = subject.sweep_managed_temp(now=now)
    assert not first.deleted
    assert first.unregistered_error is None
    assert payload.read_text(encoding="utf-8") == "作業内容を保持"

    scanned: list[pathlib.Path] = []
    original_walk = inventory._latest_update_and_git_paths

    def record_walk(path: pathlib.Path, **_kwargs: object) -> tuple[int, list[pathlib.Path]]:
        scanned.append(path)
        return original_walk(path)

    monkeypatch.setattr(inventory, "_latest_update_and_git_paths", record_walk)
    assert subject.sweep_managed_temp(now=now) == first
    assert not scanned
    assert payload.read_text(encoding="utf-8") == "作業内容を保持"

    repository_admin.rmdir()
    if registered:
        # 登録済み領域はgitdirの失効後も`.git`自体を保持根拠とする。
        assert subject.sweep_managed_temp(now=now) == first
        assert not scanned
        git_file.unlink()
        _set_tree_mtime(target, old_ns)
    result = subject.sweep_managed_temp(now=now)
    assert result.deleted == [target]
    assert result.unregistered_error is None
    assert not result.stale_unregistered
    assert not target.exists()
    assert not registration.exists()

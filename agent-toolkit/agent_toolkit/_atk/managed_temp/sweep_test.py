"""期限掃引の回収と保護、旧状態の個別回収、内部回収の包含判定を検証する。"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import os
import pathlib

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.managed_temp import paths, permissions, sweep
from agent_toolkit._common import file_lock, plugin_root_leases

_NOW = datetime.datetime(2026, 10, 25, 12, 0, tzinfo=datetime.UTC)


def _set_times(path: pathlib.Path, stamp: float) -> None:
    """リンクは辿らずに更新時刻を設定する。Windowsはリンク自身の時刻を変えられないため、リンクは据え置く。"""
    if not path.is_symlink():
        os.utime(path, (stamp, stamp))
    elif os.utime in os.supports_follow_symlinks:
        os.utime(path, (stamp, stamp), follow_symlinks=False)


def _age(path: pathlib.Path, days: float) -> None:
    """対象と配下の更新時刻を`days`日前へ揃える。"""
    stamp = (_NOW - datetime.timedelta(days=days)).timestamp()
    for item in sorted(path.rglob("*"), reverse=True) if path.is_dir() and not path.is_symlink() else []:
        _set_times(item, stamp)
    _set_times(path, stamp)


def _area(name: str, *, days: float, files: dict[str, str] | None = None) -> pathlib.Path:
    path = paths.temp_root() / name
    path.mkdir(mode=0o700)
    for relative, content in (files or {"payload.txt": "x"}).items():
        (path / relative).parent.mkdir(parents=True, exist_ok=True)
        (path / relative).write_text(content, encoding="utf-8")
    _age(path, days)
    return path


def _legacy_lock(target: pathlib.Path) -> pathlib.Path:
    return paths.state_root() / f"{hashlib.sha256(os.fsencode(target)).hexdigest()}.json.cleanup.lock"


def test_sweep_removes_expired_files_and_directories_and_keeps_protected(tmp_path: pathlib.Path) -> None:
    """期限切れの通常ファイルとディレクトリは消え、期限内・`.git`・リンク・リンク先は残る。"""
    expired = _area("expired-abc", days=8)
    fresh = _area("fresh-abc", days=1)
    nested_fresh = _area("nested-abc", days=8)
    (nested_fresh / "recent.txt").write_text("recent", encoding="utf-8")
    worktree = _area("worktree-abc", days=8, files={".git": "gitdir: /nowhere\n", "src.py": ""})
    clone = _area("clone-abc", days=8, files={"repo/.git/HEAD": "ref: refs/heads/main\n"})
    old_file = paths.temp_root() / "stray.txt"
    old_file.write_text("old", encoding="utf-8")
    _age(old_file, 9)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    link = paths.temp_root() / "link-abc"
    link.symlink_to(outside, target_is_directory=True)
    _set_times(link, 0)

    result = managed_temp.sweep_managed_temp(now=_NOW)

    assert sorted(result.deleted) == sorted([expired, old_file])
    assert not result.failed
    assert not expired.exists() and not old_file.exists()
    for kept in (fresh, nested_fresh, worktree, clone, link):
        assert os.path.lexists(kept), kept
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_sweep_keeps_plugin_root_in_use(monkeypatch: pytest.MonkeyPatch) -> None:
    """使用中の保持配布物は期限切れでも残し、使用が終われば次の調査で回収する。"""
    area = _area("agents-server-plugin-root-shared-abc", days=8)
    in_use = True
    monkeypatch.setattr(plugin_root_leases, "plugin_root_in_use", lambda _path: in_use)
    assert area not in managed_temp.sweep_managed_temp(now=_NOW).deleted
    assert area.exists()
    in_use = False
    later = _NOW + datetime.timedelta(hours=2)
    assert area in managed_temp.sweep_managed_temp(now=later).deleted


@pytest.mark.skipif(
    os.scandir not in os.supports_fd or os.open not in os.supports_dir_fd, reason="記述子による相対名の走査を提供しない環境"
)
@pytest.mark.parametrize("has_git", [False, True])
def test_sweep_handles_tree_longer_than_path_max(has_git: bool) -> None:
    """連結長がPATH_MAXを超える木も調べ、期限切れなら回収し、深い位置の`.git`があれば残す。"""
    area = _area("deep-abc", days=8)
    stamp = int((_NOW - datetime.timedelta(days=8)).timestamp() * 1_000_000_000)
    with contextlib.ExitStack() as stack:
        directory = stack.enter_context(permissions.descriptor(area, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        opened = [directory]
        for index in range(24):
            name = f"{index:02d}-" + "x" * 197
            os.mkdir(name, mode=0o700, dir_fd=directory)
            directory = stack.enter_context(
                permissions.descriptor(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            )
            opened.append(directory)
        leaf = ".git" if has_git else "payload"
        payload = stack.enter_context(permissions.descriptor(leaf, os.O_WRONLY | os.O_CREAT, 0o600, dir_fd=directory))
        os.utime(payload, ns=(stamp, stamp))
        for descriptor in reversed(opened):
            os.utime(descriptor, ns=(stamp, stamp))

    result = managed_temp.sweep_managed_temp(now=_NOW)

    assert not result.failed
    assert (area in result.deleted) is not has_git
    assert os.path.lexists(area) is has_git


def test_sweep_keeps_legacy_area_while_old_cleanup_lock_is_held() -> None:
    """旧marker付きの領域は、旧版のcleanupロックを取れない間は残し、取れれば旧登録とロックも回収する。"""
    area = _area("legacy-abc", days=8, files={paths.LEGACY_MARKER_NAME: json.dumps({"path": "unused"}), "data": "x"})
    (area / paths.LEGACY_MARKER_NAME).write_text(json.dumps({"path": str(area)}), encoding="utf-8")
    _age(area, 8)
    lock_path = _legacy_lock(area)
    registry = lock_path.with_name(lock_path.name.removesuffix(".cleanup.lock"))
    registry.write_text(json.dumps({"path": str(area)}), encoding="utf-8")
    with lock_path.open("a+", encoding="utf-8") as handle:
        file_lock.acquire_lock(handle)
        try:
            result = managed_temp.sweep_managed_temp(now=_NOW)
        finally:
            file_lock.release_lock(handle)
    assert area.exists() and area not in result.deleted
    result = managed_temp.sweep_managed_temp(now=_NOW + datetime.timedelta(hours=2))
    assert area in result.deleted
    assert not registry.exists() and not lock_path.exists()


def test_legacy_state_is_collected_individually() -> None:
    """対象が無くロックを取れた旧登録・参照・ロックだけを回収し、実在する対象の旧状態と消費途中状態は残す。"""
    state = paths.state_root()
    live = _area("live-abc", days=1)

    def registry_for(target: pathlib.Path) -> pathlib.Path:
        path = state / f"{hashlib.sha256(os.fsencode(target)).hexdigest()}.json"
        path.write_text(json.dumps({"path": str(target)}), encoding="utf-8")
        return path

    live_registry = registry_for(live)
    orphan_registry = registry_for(paths.temp_root() / "gone-abc")
    consuming_registry = registry_for(paths.temp_root() / "consuming-abc")
    consuming = consuming_registry.with_name(f"{consuming_registry.name}.consuming-{'0' * 64}")
    consuming.write_text("{}", encoding="utf-8")
    orphan_lock = state / f"{'a' * 64}.json.cleanup.lock"
    orphan_lock.write_text("", encoding="utf-8")
    sessions = state / ".sessions"
    sessions.mkdir(mode=0o700)
    live_reference = sessions / ("b" * 64)
    live_reference.write_text(json.dumps({"path": str(live)}), encoding="utf-8")
    orphan_reference = sessions / ("c" * 64)
    orphan_reference.write_text(json.dumps({"path": str(paths.temp_root() / "gone-session")}), encoding="utf-8")
    (sessions / f"{'c' * 64}.lock").write_text("", encoding="utf-8")

    managed_temp.sweep_managed_temp(now=_NOW)

    assert live_registry.exists() and live_reference.exists()
    assert consuming_registry.exists() and consuming.exists()
    assert not orphan_registry.exists() and not orphan_lock.exists()
    assert not orphan_reference.exists() and not (sessions / f"{'c' * 64}.lock").exists()


def test_schedule_avoids_rescanning_until_due(monkeypatch: pytest.MonkeyPatch) -> None:
    """rootが変わらず次の調査時刻にも達しない起動では、配下の走査をしない。"""
    _area("fresh-abc", days=1)
    managed_temp.sweep_managed_temp(now=_NOW)
    walks: list[pathlib.Path] = []
    original = sweep._latest_update_and_git  # pylint: disable=protected-access

    def counting(path: pathlib.Path) -> tuple[int, bool]:
        walks.append(path)
        return original(path)

    monkeypatch.setattr(sweep, "_latest_update_and_git", counting)
    managed_temp.sweep_managed_temp(now=_NOW + datetime.timedelta(minutes=5))
    assert not walks
    root = paths.temp_root()
    recorded = root.stat().st_mtime_ns
    (root / "added-abc").mkdir()
    # ファイルシステムの時刻の粒度で記録時と同じ値にならないよう、追加後のrootの更新時刻を進める。
    os.utime(root, ns=(recorded + 1_000_000_000, recorded + 1_000_000_000))
    managed_temp.sweep_managed_temp(now=_NOW + datetime.timedelta(minutes=6))
    assert walks == [paths.temp_root() / "added-abc"]


def test_sweep_reports_removal_failure_and_retries_later(monkeypatch: pytest.MonkeyPatch) -> None:
    """削除できなかった対象は理由とともに返し、次の調査時刻（1時間後）まで再試行しない。"""
    area = _area("stuck-abc", days=8)

    def fail(_path: pathlib.Path) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(sweep, "remove_tree", fail)
    first = managed_temp.sweep_managed_temp(now=_NOW)
    assert first.failed == ((area, "denied"),)
    assert not managed_temp.sweep_managed_temp(now=_NOW + datetime.timedelta(minutes=30)).failed
    assert managed_temp.sweep_managed_temp(now=_NOW + datetime.timedelta(hours=2)).failed


def test_cleanup_rejects_root_outside_and_links(tmp_path: pathlib.Path) -> None:
    """内部回収はroot自身・root外・リンクとリンクを経由する経路を削除せず、不在は成功とする。"""
    root = paths.temp_root()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / "link-abc"
    link.symlink_to(outside, target_is_directory=True)
    for target in (root, outside, link, link / "child", pathlib.Path("relative")):
        with pytest.raises(managed_temp.ManagedTempError):
            managed_temp.cleanup_managed_temp(target)
    assert outside.is_dir() and link.is_symlink()
    managed_temp.cleanup_managed_temp(root / "missing-abc")
    area = managed_temp.create_managed_temp("work")
    child = area / "nested"
    child.mkdir()
    readonly = child / "readonly"
    readonly.mkdir()
    (readonly / "file").write_text("x", encoding="utf-8")
    readonly.chmod(0o500)
    managed_temp.cleanup_managed_temp(child)
    assert area.exists() and not child.exists()
    managed_temp.cleanup_managed_temp(area)
    assert not area.exists()

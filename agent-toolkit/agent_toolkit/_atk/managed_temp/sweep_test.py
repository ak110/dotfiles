"""期限掃引の回収と保護、旧状態の個別回収、内部回収の包含判定を検証する。"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import typing
from types import SimpleNamespace

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.managed_temp import paths, permissions, resources, sweep
from agent_toolkit._common import file_lock, plugin_root_leases, state_paths

pytestmark = pytest.mark.windows

_NOW = datetime.datetime(2026, 10, 25, 12, 0, tzinfo=datetime.UTC)


@pytest.fixture(autouse=True)
def _default_free_ratio(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sweep.MIN_FREE_RATIO_ENV, raising=False)


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
    """期限切れの通常ファイルとディレクトリは消え、期限内・リンク・リンク先は残る。"""
    expired = _area("expired-abc", days=8)
    fresh = _area("fresh-abc", days=1)
    nested_fresh = _area("nested-abc", days=8)
    (nested_fresh / "recent.txt").write_text("recent", encoding="utf-8")
    _set_times(nested_fresh / "recent.txt", _NOW.timestamp())
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

    assert sorted(result.deleted) == sorted([expired, old_file, worktree, clone])
    assert not result.failed
    assert not expired.exists() and not old_file.exists()
    for kept in (fresh, nested_fresh, link):
        assert os.path.lexists(kept), kept
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_sweep_keeps_plugin_root_in_use(monkeypatch: pytest.MonkeyPatch) -> None:
    """使用中の保持配布物は期限切れでも残し、使用が終われば次の掃引で回収する。"""
    area = _area("agents-server-plugin-root-shared-abc", days=8)
    with plugin_root_leases.plugin_root_lock(area):
        plugin_root_leases.register_plugin_root_use(area)
    monkeypatch.setattr(plugin_root_leases.psutil, "process_iter", lambda: [])
    assert area not in managed_temp.sweep_managed_temp(now=_NOW).deleted
    assert area.exists()
    record = next((state_paths.state_dir() / "plugin-root-leases").glob("*.json"))
    raw = json.loads(record.read_text(encoding="utf-8"))

    raw["created_at"] = 0
    if raw.get("start_id") is not None:
        raw["start_id"] += "-ended"
    record.write_text(json.dumps(raw), encoding="utf-8")
    later = _NOW + datetime.timedelta(days=1)
    assert area in managed_temp.sweep_managed_temp(now=later).deleted
    assert not record.exists()


@pytest.mark.parametrize("whole_removal_fails", [False, True])
def test_expired_area_reclaims_cache_even_if_whole_removal_fails(
    monkeypatch: pytest.MonkeyPatch, whole_removal_fails: bool
) -> None:
    area = _area("session-expired", days=8, files={"cache/CACHEDIR.TAG": "Signature: 8a477f597d28d172789f06886806bc55\n"})
    monkeypatch.setattr(resources, "process_references", lambda: [])

    if whole_removal_fails:

        def refuse(_path: pathlib.Path) -> None:
            raise PermissionError("busy handoff")

        monkeypatch.setattr(sweep, "remove_tree", refuse)
    result = managed_temp.sweep_managed_temp(now=_NOW)
    if whole_removal_fails:
        assert result.failed == ((area, "busy handoff"),)
        assert area.is_dir() and result.deleted == [area / "cache"]
    else:
        assert not result.failed and result.deleted == [area]
    assert not (area / "cache").exists()


@pytest.mark.skipif(
    os.scandir not in os.supports_fd or os.open not in os.supports_dir_fd, reason="記述子による相対名の走査を提供しない環境"
)
@pytest.mark.parametrize("has_git", [False, True])
def test_sweep_handles_tree_longer_than_path_max(has_git: bool) -> None:
    """連結長がPATH_MAXを超える木も調べ、期限切れなら回収し、深い位置に無効な`.git`があっても回収する。"""
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
    assert area in result.deleted
    assert not os.path.lexists(area)


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
    result = managed_temp.sweep_managed_temp(now=_NOW + datetime.timedelta(days=1))
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


def _count_walks(monkeypatch: pytest.MonkeyPatch) -> list[pathlib.Path]:
    walks: list[pathlib.Path] = []
    original = sweep._inspect_tree  # pylint: disable=protected-access

    def counting(path: pathlib.Path, now_ns: int, times: sweep.CleanupTimes) -> tuple[int, bool, int]:
        walks.append(path)
        return original(path, now_ns, times)

    monkeypatch.setattr(sweep, "_inspect_tree", counting)
    return walks


def test_daily_sweep_skips_protected_area_but_still_checks_other_areas(monkeypatch: pytest.MonkeyPatch) -> None:
    protected = _area("session-keep", days=8, files={"nested/payload.txt": "keep"})
    expired = _area("expired-abc", days=8)
    fresh = _area("fresh-abc", days=1)
    monkeypatch.setattr(sweep.registry_index, "pending_session_ids", lambda: ["keep"])
    monkeypatch.setattr(sweep.creation, "find_session_temp", lambda _identifier: protected)
    monkeypatch.setattr(sweep, "_disk_usage", lambda _path: shutil._ntuple_diskusage(1000, 800, 200))  # pylint: disable=protected-access
    walks = _count_walks(monkeypatch)

    result = managed_temp.sweep_managed_temp(now=_NOW)

    assert sorted(walks) == sorted([expired, fresh])
    assert result.deleted == [expired]
    assert not result.failed and result.pressure is None
    assert not expired.exists() and fresh.exists()
    assert (protected / "nested/payload.txt").read_text(encoding="utf-8") == "keep"


def test_sweep_runs_once_per_day(monkeypatch: pytest.MonkeyPatch) -> None:
    fresh = _area("fresh-abc", days=1)
    managed_temp.sweep_managed_temp(now=_NOW)
    expired = _area("expired-abc", days=8)
    walks = _count_walks(monkeypatch)
    listed: list[object] = []
    scandir = os.scandir

    def recording(target: typing.Any) -> typing.Any:
        listed.append(target)
        return scandir(target)

    with monkeypatch.context() as listing:
        listing.setattr(os, "scandir", recording)
        within = managed_temp.sweep_managed_temp(now=_NOW + datetime.timedelta(hours=23, minutes=59))

    assert within == sweep.SweepResult([], ())
    assert not listed and not walks and expired.exists()
    after = managed_temp.sweep_managed_temp(now=_NOW + datetime.timedelta(days=1))
    assert after.deleted == [expired]
    assert sorted(walks) == sorted([expired, fresh])


@pytest.mark.parametrize("record", ["missing", "broken", "future"])
def test_sweep_runs_when_record_is_missing_broken_or_future(record: str) -> None:
    area = _area("expired-abc", days=8)
    path = paths.state_root() / ".sweep/last-sweep"
    path.parent.mkdir(mode=0o700, exist_ok=True)
    if record == "broken":
        path.write_bytes(b"\xff not a time\n")
    elif record == "future":
        path.write_text(f"{int((_NOW + datetime.timedelta(minutes=1)).timestamp() * 1_000_000_000)}\n", encoding="ascii")

    assert area in managed_temp.sweep_managed_temp(now=_NOW).deleted
    assert int(path.read_text(encoding="ascii")) == int(_NOW.timestamp() * 1_000_000_000)


def test_sweep_record_write_failure_does_not_stop_atk(monkeypatch: pytest.MonkeyPatch) -> None:
    area = _area("expired-abc", days=8)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("read-only state")

    monkeypatch.setattr(sweep.os, "replace", fail)

    result = managed_temp.sweep_managed_temp(now=_NOW)

    assert result == sweep.SweepResult([area], ())
    record_directory = paths.state_root() / ".sweep"
    assert {entry.name for entry in record_directory.iterdir()} == {"pressure.lock"}
    later = _area("later-abc", days=8)
    assert later in managed_temp.sweep_managed_temp(now=_NOW + datetime.timedelta(minutes=1)).deleted


def test_sweep_reports_removal_failure_and_retries_on_next_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """削除できなかった対象は理由とともに返し、次の掃引（1日後）で再試行する。"""
    area = _area("stuck-abc", days=8)

    def fail(_path: pathlib.Path) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(sweep, "remove_tree", fail)
    first = managed_temp.sweep_managed_temp(now=_NOW)
    assert first.failed == ((area, "denied"),)
    assert not managed_temp.sweep_managed_temp(now=_NOW + datetime.timedelta(hours=2)).failed
    assert managed_temp.sweep_managed_temp(now=_NOW + datetime.timedelta(days=1)).failed == ((area, "denied"),)


@pytest.mark.skipif(os.name != "posix", reason="POSIXの所有UIDを検証する")
@pytest.mark.parametrize("owner", [0, 999])
def test_sweep_preserves_foreign_owned_tree_and_reports_limited_failure(monkeypatch: pytest.MonkeyPatch, owner: int) -> None:
    """異なるUIDの対象は残し、現在アカウントの権限補完失敗として報告する。"""
    if owner == os.geteuid():
        pytest.skip("現在のOSアカウントと異なるUIDの事例を検証する")
    area = _area("foreign-owner", days=8)
    area.chmod(0)
    original_lstat = pathlib.Path.lstat

    def foreign_lstat(path: pathlib.Path, **kwargs: typing.Any) -> os.stat_result:
        metadata = original_lstat(path, **kwargs)
        if path == area:
            values = list(metadata)
            values[4] = owner
            return os.stat_result(
                values, {name: getattr(metadata, name) for name in ("st_atime_ns", "st_mtime_ns", "st_ctime_ns")}
            )
        return metadata

    monkeypatch.setattr(pathlib.Path, "lstat", foreign_lstat)
    original_rmtree = shutil.rmtree

    def refuse_foreign_removal(path: pathlib.Path | str, *, onexc: typing.Any = None, **_kwargs: typing.Any) -> None:
        # CIのrootはmode=0でも削除できるため、権限回復が必要な境界を実行アカウントによらず与える。
        if pathlib.Path(path) == area:
            raise PermissionError("foreign owner")
        original_rmtree(path, onexc=onexc)

    monkeypatch.setattr(shutil, "rmtree", refuse_foreign_removal)
    try:
        result = managed_temp.sweep_managed_temp(now=_NOW)
    finally:
        area.chmod(0o700)
    assert area.exists()
    assert not result.deleted
    assert any(
        path == area
        and str(area) in reason
        and f"uid={owner}" in reason
        and "現在のOSアカウント" in reason
        and "通常の掃引では可能な対象だけを回収する" in reason
        and "削除の認可" in reason
        and "現在の利用" in reason
        and "残った理由をユーザーへ伝える" in reason
        for path, reason in result.failed
    )


def test_cleanup_rejects_root_outside_and_links(tmp_path: pathlib.Path) -> None:
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


@pytest.mark.parametrize("descriptor_walk", [False, True])
def test_sweep_removes_expired_area_with_future_file(monkeypatch: pytest.MonkeyPatch, descriptor_walk: bool) -> None:
    """未来のmtimeを持つファイルも、古いctimeなら期限切れとして回収する。"""
    monkeypatch.setattr(
        sweep, "_DESCRIPTOR_WALK", descriptor_walk and os.scandir in os.supports_fd and os.open in os.supports_dir_fd
    )
    area = _area("future-abc", days=8)
    _set_times(area / "payload.txt", 2_000_000_000)
    # ctimeはutimeで設定できないため、作成から8日後の判定で期限切れを再現する。
    now = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=8)
    result = managed_temp.sweep_managed_temp(now=now)
    assert area in result.deleted
    assert not result.failed and not area.exists()


@pytest.mark.parametrize("descriptor_walk", [False, True])
@pytest.mark.parametrize("layout", ["new-area", "old-area-new-child"])
def test_sweep_keeps_entries_changed_after_sweep_time(
    monkeypatch: pytest.MonkeyPatch, descriptor_walk: bool, layout: str
) -> None:
    monkeypatch.setattr(
        sweep, "_DESCRIPTOR_WALK", descriptor_walk and os.scandir in os.supports_fd and os.open in os.supports_dir_fd
    )
    now = datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1)
    if layout == "new-area":
        area = paths.temp_root() / "pytest-fixtures-created"
        (area / "fixtures").mkdir(parents=True, mode=0o700)
    else:
        area = _area("old-area-abc", days=8)
        (area / "fixtures").mkdir(mode=0o700)
    result = managed_temp.sweep_managed_temp(now=now)
    assert area not in result.deleted
    assert not result.failed and (area / "fixtures").is_dir()


def test_sweep_removes_expired_git_directory() -> None:
    """独立リポジトリを含む期限切れの領域を回収する。"""
    area = _area("git-directory-abc", days=8, files={"repo/.git/HEAD": "ref: refs/heads/main\n"})
    assert area in managed_temp.sweep_managed_temp(now=_NOW).deleted
    assert not area.exists()


@pytest.mark.parametrize("descriptor_walk", [False, True])
@pytest.mark.parametrize("registered", [False, True])
def test_sweep_preserves_only_registered_external_worktree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, registered: bool, descriptor_walk: bool
) -> None:
    monkeypatch.setattr(
        sweep, "_DESCRIPTOR_WALK", descriptor_walk and os.scandir in os.supports_fd and os.open in os.supports_dir_fd
    )
    gitdir = tmp_path / "external-gitdir"
    gitdir.mkdir()
    area = _area(
        "registered-abc",
        days=8,
        files={"repo/.git": f"gitdir: {gitdir}\n", "discarded/payload.txt": "reproducible"},
    )
    reference = area / "repo/.git" if registered else tmp_path / "other.git"
    reference.touch(exist_ok=True)
    (gitdir / "gitdir").write_text(str(reference), encoding="utf-8")
    _age(area, 8)
    result = managed_temp.sweep_managed_temp(now=_NOW)
    assert not result.failed
    assert (area in result.deleted) is not registered
    assert area.exists() is registered
    assert not (area / "discarded").exists()
    if registered:
        assert (area / "repo/.git").is_file()


def test_sweep_discards_schedule_of_previous_version() -> None:
    area = _area("old-schedule-abc", days=8)
    schedule = paths.state_root() / ".sweep/schedule"
    schedule.parent.mkdir(exist_ok=True)
    schedule.write_text(
        json.dumps(
            {
                "version": 4,
                "temp_root": str(paths.temp_root()),
                "root_mtime_ns": paths.temp_root().stat().st_mtime_ns,
                "legacy_checked_ns": int(_NOW.timestamp() * 1_000_000_000),
                "entries": {area.name: [area.stat().st_mtime_ns, 2_000_000_000_000_000_000]},
            }
        ),
        encoding="utf-8",
    )
    assert area in managed_temp.sweep_managed_temp(now=_NOW).deleted
    assert not schedule.exists()


@pytest.mark.skipif(not hasattr(os, "O_PATH"), reason="O_PATHによる所有木の権限回復を検証する")
@pytest.mark.parametrize("restricted", [".", "d"])
@pytest.mark.parametrize("protection", ["expired", "fresh", "worktree", "plugin"])
def test_sweep_recovers_unreadable_owned_tree_and_restores_retained_modes(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    restricted: str,
    protection: str,
) -> None:
    name = "agents-server-plugin-root-locked" if protection == "plugin" else "locked-abc"
    area = _area(name, days=9, files={"d/payload": "x"})
    if protection == "worktree":
        gitdir = tmp_path / "gitdir"
        gitdir.mkdir()
        (area / "d/.git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
        (gitdir / "gitdir").write_text(str(area / "d/.git"), encoding="utf-8")
    _age(area, 1 if protection == "fresh" else 9)
    if protection == "plugin":
        monkeypatch.setattr(plugin_root_leases, "plugin_root_in_use", lambda _path, **_kwargs: True)
    locked = area / restricted
    locked.chmod(0)
    try:
        result = managed_temp.sweep_managed_temp(now=_NOW)
        # 使用中なら探索せず、親ごと回収できた場合も、対処が必要な失敗は残らない。
        if protection in {"fresh", "worktree"} and not os.access(locked, os.R_OK):
            assert [target for target, _reason in result.failed] == [locked]
        else:
            assert not result.failed
        assert (area in result.deleted) is (protection == "expired")
        assert area.exists() is (protection != "expired")
        if protection != "expired":
            assert stat.S_IMODE(locked.stat().st_mode) == 0
    finally:
        if area.exists():
            locked.chmod(0o700)


@pytest.mark.skipif(not hasattr(os, "O_PATH"), reason="O_PATHによる所有木の権限回復を検証する")
@pytest.mark.parametrize("restricted", [".", "d"])
def test_cleanup_removes_unreadable_owned_tree(restricted: str) -> None:
    area = _area("cleanup-locked", days=9, files={"d/payload": "x"})
    locked = area / restricted
    locked.chmod(0)
    try:
        managed_temp.cleanup_managed_temp(area)
        assert not area.exists()
    finally:
        if area.exists():
            locked.chmod(0o700)


class _FakeDisk:
    def __init__(self, total: int, free_without_root: int, root: pathlib.Path) -> None:
        self.total, self.base, self.root = total, free_without_root, root

    def __call__(self, _path: object) -> typing.Any:
        used = sum(item.stat().st_size for item in self.root.rglob("*") if item.is_file())
        return shutil._ntuple_diskusage(self.total, 0, self.base - used)  # pylint: disable=protected-access


def _first_sweep_done() -> None:
    sweep._save_sweep_time(paths.state_root(), sweep._now_ns(_NOW))  # pylint: disable=protected-access


def test_space_pressure_reclaims_oldest_unused_first_and_keeps_in_use(monkeypatch: pytest.MonkeyPatch) -> None:
    _first_sweep_done()
    tag = "Signature: 8a477f597d28d172789f06886806bc55"
    old = _area("old-abc", days=3, files={"big": "x" * 400, "CACHEDIR.TAG": tag})
    newer = _area("newer-abc", days=1, files={"big": "x" * 400, "CACHEDIR.TAG": tag})
    worktree = _area("wt-abc", days=5, files={"payload.txt": "x" * 400})
    session = _area("session-keep", days=6, files={"payload.txt": "x" * 400})
    registered = pathlib.Path(paths.temp_root().parent / "gitdir-wt")
    registered.mkdir()
    (registered / "gitdir").write_text(f"{worktree / '.git'}\n", encoding="utf-8")
    (worktree / ".git").write_text(f"gitdir: {registered}\n", encoding="utf-8")
    monkeypatch.setattr(sweep.registry_index, "pending_session_ids", lambda: ["keep"])
    monkeypatch.setattr(sweep.creation, "find_session_temp", lambda _identifier: session)
    monkeypatch.setattr(resources, "process_references", lambda: [])
    monkeypatch.setattr(sweep, "_disk_usage", _FakeDisk(1000, 1700, paths.temp_root()))

    tree_bytes = sweep._tree_bytes  # pylint: disable=protected-access

    def measure(path: pathlib.Path) -> int:
        assert path != newer, "先頭の回収で下限に戻るのに、残りの木を容量計測のため再走査した"
        return tree_bytes(path)

    monkeypatch.setattr(sweep, "_tree_bytes", measure)
    result = managed_temp.sweep_managed_temp(now=_NOW)

    assert result.pressure is not None
    assert result.deleted[0] == old
    assert not old.exists()
    assert worktree.exists() and session.exists()
    assert result.pressure.top_reclaimed[0][0] == "old-abc"
    assert result.pressure.free_after > result.pressure.free_before
    assert newer.exists()


def test_pressure_reclaims_newly_expired_session_between_daily_sweeps(monkeypatch: pytest.MonkeyPatch) -> None:
    _first_sweep_done()
    session = managed_temp.resolve_session_temp("expired-between-sweeps")
    mib = 1024 * 1024
    (session / "payload").write_bytes(b"x" * (4 * mib))
    _age(session, 7)
    disk = _FakeDisk(100 * mib, 12 * mib, paths.temp_root())
    monkeypatch.setattr(sweep, "_disk_usage", disk)
    assert not managed_temp.sweep_managed_temp(now=_NOW).deleted
    result = managed_temp.sweep_managed_temp(now=_NOW + datetime.timedelta(hours=2))
    assert result.deleted == [session] and not result.failed and not session.exists()
    assert result.pressure is not None and result.pressure.free_after == 12 * mib


@pytest.mark.parametrize("age_days", [8, 7])
@pytest.mark.parametrize("first", ["daily", "pressure"])
@pytest.mark.parametrize("next_daily", [False, True], ids=["next-start", "next-day"])
def test_cache_removal_does_not_renew_session(
    monkeypatch: pytest.MonkeyPatch, age_days: int, first: str, next_daily: bool
) -> None:
    """7日ちょうどなら記録だけを残し、内部キャッシュを回収した時刻で期限を延ばさない。"""
    if first == "pressure":
        _first_sweep_done()
    session = _area(
        "old-prefix-abc",
        days=age_days,
        files={"scratch/cache/CACHEDIR.TAG": "Signature: 8a477f597d28d172789f06886806bc55\n"},
    )
    reference = paths.state_root() / ".sessions" / hashlib.sha256(b"cache-clock").hexdigest()
    reference.parent.mkdir()
    reference.write_text(json.dumps({"path": str(session)}), encoding="utf-8")
    mib = 1024 * 1024
    record = session / "handoff"
    record.write_bytes(b"x" * (5 * mib))
    cache = session / "scratch/cache"
    (cache / "data").write_bytes(b"c" * mib)
    _age(session, 8)

    _age(cache / "data", age_days)
    disk = _FakeDisk(100 * mib, 13 * mib, paths.temp_root())
    monkeypatch.setattr(sweep, "_disk_usage", disk)
    monkeypatch.setattr(resources, "process_references", lambda: [])

    initial = managed_temp.sweep_managed_temp(now=_NOW)
    assert not initial.failed and not cache.exists()
    if age_days == 7:
        assert managed_temp.find_session_temp("cache-clock") == session
        assert record.read_bytes() == b"x" * (5 * mib)
        assert disk(session).free == 8 * mib
    else:
        assert disk(session).free == 13 * mib

    later = _NOW + (datetime.timedelta(days=1) if next_daily else datetime.timedelta(minutes=70))
    assert not managed_temp.sweep_managed_temp(now=later).failed
    assert not session.exists() and disk(session).free == 13 * mib


@pytest.mark.parametrize("force", [False, True], ids=["daily", "forced"])
def test_overlapping_sweeps_do_not_renew_session(monkeypatch: pytest.MonkeyPatch, force: bool) -> None:
    session = _area(
        "session-boundary",
        days=7,
        files={
            **{f"cache-{i}/CACHEDIR.TAG": "Signature: 8a477f597d28d172789f06886806bc55" for i in range(2)},
            "handoff": "keep",
        },
    )
    monkeypatch.setattr(sweep, "_disk_usage", lambda _root: SimpleNamespace(total=100, free=6))
    monkeypatch.setattr(resources, "process_references", lambda: [])
    overlapping: list[sweep.SweepResult] = []
    remove = shutil.rmtree

    def remove_with_competing_sweep(cache: pathlib.Path) -> None:
        remove(cache)
        if not overlapping:
            with monkeypatch.context() as inner:
                inner.setattr(resources, "shutil", SimpleNamespace(rmtree=remove))
                overlapping.append(managed_temp.sweep_managed_temp(now=_NOW, force=force))

    monkeypatch.setattr(resources, "shutil", SimpleNamespace(rmtree=remove_with_competing_sweep))
    first = managed_temp.sweep_managed_temp(now=_NOW, force=force)
    assert (session / "handoff").read_text(encoding="utf-8") == "keep"
    assert overlapping == [sweep.SweepResult([], ())]
    assert not first.failed and set(first.deleted) == {session / "cache-0", session / "cache-1"}
    assert managed_temp.sweep_managed_temp(now=_NOW + datetime.timedelta(minutes=70)).deleted == [session]


def test_pressure_recognizes_legacy_session_marker_without_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    _first_sweep_done()
    session = _area("old-prefix-abc", days=3, files={"handoff": "keep"})
    marker = session / paths.LEGACY_MARKER_NAME
    marker.write_text(json.dumps({"path": str(session), "session_id": "old-session", "schema_version": 6}), encoding="utf-8")
    _age(session, 3)

    monkeypatch.setattr(sweep, "_disk_usage", _FakeDisk(1000, 50 + marker.stat().st_size + 4, paths.temp_root()))
    assert not managed_temp.sweep_managed_temp(now=_NOW).deleted
    assert (session / "handoff").read_text(encoding="utf-8") == "keep"
    assert managed_temp.sweep_managed_temp(now=_NOW + datetime.timedelta(days=5), force=True).deleted == [session]


@pytest.mark.parametrize("descriptor_walk", [False, True])
@pytest.mark.parametrize("change", ["write-during", "rename-during", "rename-after"])
def test_cache_removal_keeps_a_concurrent_handoff(monkeypatch: pytest.MonkeyPatch, change: str, descriptor_walk: bool) -> None:
    monkeypatch.setattr(
        sweep, "_DESCRIPTOR_WALK", descriptor_walk and os.scandir in os.supports_fd and os.open in os.supports_dir_fd
    )
    session = _area(
        "session-resumed",
        days=7,
        files={"handoff": "original", "cache/CACHEDIR.TAG": "Signature: 8a477f597d28d172789f06886806bc55"},
    )
    record = session / "handoff"
    now = datetime.datetime.now(datetime.UTC)
    _set_times(record, (now - datetime.timedelta(days=6, hours=23)).timestamp())
    monkeypatch.setattr(resources, "process_references", lambda: [])
    original_rmtree = shutil.rmtree

    def remove_and_resume(path: pathlib.Path) -> None:
        original_rmtree(path)
        if change == "write-during":
            record.write_text("resumed", encoding="utf-8")
        elif change == "rename-during":
            record.rename(session / "renamed")

    monkeypatch.setattr(resources, "shutil", SimpleNamespace(rmtree=remove_and_resume))
    assert managed_temp.sweep_managed_temp(now=now).deleted == [session / "cache"]
    if change == "rename-after":
        record.rename(session / "renamed")
    current = record if change == "write-during" else session / "renamed"
    timestamp = current.stat().st_mtime_ns
    assert not managed_temp.sweep_managed_temp(now=now + datetime.timedelta(days=1)).deleted
    assert current.read_text(encoding="utf-8") == ("resumed" if change == "write-during" else "original")
    assert current.stat().st_mtime_ns == timestamp


def test_cleanup_time_evidence_expires_or_can_be_lost(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.datetime.now(datetime.UTC)
    cache = _area(
        "session-cache", days=0, files={"cache/CACHEDIR.TAG": "Signature: 8a477f597d28d172789f06886806bc55", "record": "keep"}
    )
    stamp = (now - datetime.timedelta(days=6, hours=23)).timestamp()
    for entry in [*cache.rglob("*"), cache]:
        _set_times(entry, stamp)
    monkeypatch.setattr(resources, "process_references", lambda: [])
    assert managed_temp.sweep_managed_temp(now=now).deleted == [cache / "cache"]
    receipts = paths.state_root() / ".sweep/cache-parents"
    (receipt,) = receipts.iterdir()
    name = receipt.name
    receipt.unlink()
    assert not managed_temp.sweep_managed_temp(now=now + datetime.timedelta(days=1), force=True).deleted
    assert (cache / "record").read_text(encoding="utf-8") == "keep"
    (receipts / name).touch()
    assert managed_temp.sweep_managed_temp(now=now + datetime.timedelta(days=8), force=True).deleted == [cache]
    assert not list(receipts.iterdir())


def test_pressure_rechecks_expired_session_after_another_removal(monkeypatch: pytest.MonkeyPatch) -> None:
    _first_sweep_done()
    first = _area("old-scratch", days=9)
    session = _area("session-resumed", days=8)
    record = session / "payload.txt"
    original_remove = sweep.remove_tree

    def resume_during_removal(path: pathlib.Path) -> None:
        original_remove(path)
        if path == first:
            record.write_text("resumed", encoding="utf-8")

    monkeypatch.setattr(sweep, "remove_tree", resume_during_removal)
    monkeypatch.setattr(sweep, "_disk_usage", lambda _root: SimpleNamespace(total=100, free=7))
    result = managed_temp.sweep_managed_temp(now=_NOW)
    assert not result.failed and result.deleted == [first]
    assert record.read_text(encoding="utf-8") == "resumed"


@pytest.mark.parametrize("force", [False, True], ids=["between-daily-sweeps", "forced"])
@pytest.mark.parametrize("disk_mib", [80, 128], ids=["reachable", "unreachable"])
def test_pressure_reclaims_tagged_root_before_unclassified(monkeypatch: pytest.MonkeyPatch, force: bool, disk_mib: int) -> None:
    _first_sweep_done()
    mib = 1024 * 1024
    cache = _area("standalone", days=2, files={"CACHEDIR.TAG": "Signature: 8a477f597d28d172789f06886806bc55\n"})
    (cache / "data").write_bytes(b"x" * (8 * mib))
    _age(cache, 2)
    unknown = _area("unclassified", days=3, files={"CACHEDIR.TAG": "not a cache tag"})
    disk = _FakeDisk(disk_mib * mib, 10 * mib, paths.temp_root())
    monkeypatch.setattr(sweep, "_disk_usage", disk)
    monkeypatch.setattr(resources, "process_references", lambda: [])

    result = managed_temp.sweep_managed_temp(now=_NOW, force=force)

    assert result.deleted == [cache] and not result.failed and not cache.exists()
    assert result.pressure is not None and result.pressure.still_low == (disk_mib == 128)
    assert result.pressure.free_after - result.pressure.free_before >= 8 * mib
    assert unknown.is_dir()


@pytest.mark.parametrize("references_known", [True, False], ids=["busy", "unknown"])
@pytest.mark.parametrize("days", [2, 8])
@pytest.mark.parametrize("force", [False, True])
def test_pressure_keeps_tagged_root_unless_unused(
    monkeypatch: pytest.MonkeyPatch, references_known: bool, days: int, force: bool
) -> None:
    _first_sweep_done()
    cache = _area("standalone", days=days, files={"CACHEDIR.TAG": "Signature: 8a477f597d28d172789f06886806bc55\n"})
    monkeypatch.setattr(sweep, "_disk_usage", lambda _root: SimpleNamespace(total=100, free=8))
    monkeypatch.setattr(resources, "process_references", lambda: [cache / "active"] if references_known else None)
    result = managed_temp.sweep_managed_temp(now=_NOW, force=force)
    assert not result.deleted and not result.failed and cache.exists()


@pytest.mark.parametrize("force", [False, True], ids=["between-daily-sweeps", "forced"])
@pytest.mark.parametrize("disk_mib", [16, 128], ids=["recoverable", "external-shortage"])
def test_pressure_preserves_resume_records_but_reclaims_session_cache(
    monkeypatch: pytest.MonkeyPatch, force: bool, disk_mib: int
) -> None:
    _first_sweep_done()
    session = managed_temp.resolve_session_temp("weekend")
    contract = session / "work-contract.md"
    contract.write_text("再開条件", encoding="utf-8")
    handoff = session / "notes" / "引き継ぎ"
    handoff.parent.mkdir()
    handoff.write_bytes(b"only copy")
    cache = session / "scratch" / "cache"
    cache.mkdir(parents=True)
    (cache / "CACHEDIR.TAG").write_text("Signature: 8a477f597d28d172789f06886806bc55\n", encoding="ascii")
    payload_size = 1024 * 1024
    (cache / "data").write_bytes(b"x" * payload_size)
    _age(session, 3)
    disk = _FakeDisk(disk_mib * payload_size, 2 * payload_size, paths.temp_root())
    monkeypatch.setattr(sweep, "_disk_usage", disk)
    monkeypatch.setattr(resources, "process_references", lambda: [])
    free_before = disk(session).free

    result = managed_temp.sweep_managed_temp(now=_NOW, force=force)

    assert result.deleted == [cache] and not result.failed
    assert not cache.exists() and disk(session).free >= free_before + payload_size
    if not force:
        assert result.pressure is not None and result.pressure.reclaimed_bytes >= payload_size
        assert result.pressure.free_after == disk(session).free
    assert managed_temp.resolve_session_temp("weekend") == session
    assert contract.read_text(encoding="utf-8") == "再開条件" and handoff.read_bytes() == b"only copy"
    _age(session, 7)
    assert session not in managed_temp.sweep_managed_temp(now=_NOW, force=True).deleted
    assert session in managed_temp.sweep_managed_temp(now=_NOW + datetime.timedelta(seconds=1), force=True).deleted


@pytest.mark.parametrize("force", [False, True], ids=["between-daily-sweeps", "forced"])
def test_pressure_reports_unremoved_cache_in_legacy_session(monkeypatch: pytest.MonkeyPatch, force: bool) -> None:
    _first_sweep_done()
    session = _area(
        "old-prefix-abc",
        days=3,
        files={"handoff.md": "keep", "cache/CACHEDIR.TAG": "Signature: 8a477f597d28d172789f06886806bc55\n"},
    )
    reference = paths.state_root() / ".sessions" / hashlib.sha256(b"old-session").hexdigest()
    reference.parent.mkdir()
    reference.write_text(json.dumps({"path": str(session)}), encoding="utf-8")
    monkeypatch.setattr(sweep, "_disk_usage", lambda _root: SimpleNamespace(total=237, free=19))
    monkeypatch.setattr(resources, "process_references", lambda: [])

    def refuse(cache: pathlib.Path) -> None:
        raise PermissionError(f"permission denied: {cache}")

    monkeypatch.setattr(resources, "shutil", SimpleNamespace(rmtree=refuse))
    result = managed_temp.sweep_managed_temp(now=_NOW, force=force)

    assert not result.deleted
    assert len(result.failed) == 1 and result.failed[0][0] == session / "cache" and "permission denied" in result.failed[0][1]
    assert result.pressure is not None and result.pressure.still_low
    assert managed_temp.resolve_session_temp("old-session") == session
    assert (session / "handoff.md").read_text(encoding="utf-8") == "keep"


def test_normal_free_space_does_not_reclaim_or_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    _first_sweep_done()
    kept = _area("recent-abc", days=3)
    monkeypatch.setattr(sweep, "_disk_usage", lambda _path: shutil._ntuple_diskusage(1000, 0, 500))  # pylint: disable=protected-access
    monkeypatch.setattr(sweep, "_inspect_tree", lambda *_args: pytest.fail("全走査をした"))

    result = managed_temp.sweep_managed_temp(now=_NOW)

    assert result.pressure is None
    assert result.deleted == []
    assert kept.exists()


def test_space_pressure_without_reclaimable_target_reports_low_space(monkeypatch: pytest.MonkeyPatch) -> None:
    _first_sweep_done()
    in_use = _area("session-keep", days=3, files={"payload.txt": "x" * 300})
    monkeypatch.setattr(sweep.registry_index, "pending_session_ids", lambda: ["keep"])
    monkeypatch.setattr(sweep.creation, "find_session_temp", lambda _identifier: in_use)
    monkeypatch.setattr(sweep, "_disk_usage", lambda _path: shutil._ntuple_diskusage(1000, 900, 50))  # pylint: disable=protected-access

    result = managed_temp.sweep_managed_temp(now=_NOW)

    assert result.pressure is not None and result.pressure.still_low
    assert result.deleted == [] and in_use.exists()
    assert (result.pressure.temp_root_bytes or 0) > 0


def test_space_pressure_keeps_recently_updated_area(monkeypatch: pytest.MonkeyPatch) -> None:
    _first_sweep_done()
    fresh = _area("fresh-abc", days=0, files={"big": "x" * 400})
    old = _area("old-abc", days=8, files={"big": "x" * 400})
    monkeypatch.setattr(sweep, "_disk_usage", _FakeDisk(1000, 850, paths.temp_root()))

    result = managed_temp.sweep_managed_temp(now=_NOW)

    assert result.pressure is not None
    assert fresh.exists() and not old.exists()


def test_free_ratio_environment_disables_reclaim(monkeypatch: pytest.MonkeyPatch) -> None:
    _first_sweep_done()
    old = _area("old-abc", days=8, files={"big": "x" * 400})
    monkeypatch.setattr(sweep, "_disk_usage", _FakeDisk(1000, 450, paths.temp_root()))
    monkeypatch.setenv(sweep.MIN_FREE_RATIO_ENV, "0")

    assert managed_temp.sweep_managed_temp(now=_NOW).pressure is None
    assert old.exists()

    monkeypatch.setenv(sweep.MIN_FREE_RATIO_ENV, "abc")
    assert managed_temp.sweep_managed_temp(now=_NOW).pressure is not None
    assert not old.exists()


def test_space_pressure_keeps_area_whose_owner_process_is_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    _first_sweep_done()
    with subprocess.Popen([sys.executable, "-c", "pass"]) as finished:
        finished.wait()
        dead_token = f"{finished.pid} "
    direct = _area("direct-abc", days=3, files={"big": "x" * 300, sweep.OWNER_FILE: sweep.owner_token()})
    nested = _area(
        "session-abc",
        days=3,
        files={
            "cache/big": "x" * 300,
            "cache/CACHEDIR.TAG": "Signature: 8a477f597d28d172789f06886806bc55\n",
            f"atk-command-x/{sweep.OWNER_FILE}": sweep.owner_token(),
        },
    )
    monkeypatch.setattr(resources, "process_references", lambda: [])
    dead = _area("dead-abc", days=8, files={"big": "x" * 300, sweep.OWNER_FILE: dead_token})
    monkeypatch.setattr(sweep, "_disk_usage", lambda _root: SimpleNamespace(total=1000, free=50))

    result = managed_temp.sweep_managed_temp(now=_NOW)

    assert result.pressure is not None
    assert direct.exists() and (nested / "cache/big").is_file() and not dead.exists()


def test_pressure_retry_interval_keeps_free_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    _first_sweep_done()
    _area("protected-recent", days=0)
    observed: list[object] = []

    def usage(path: object, **_kwargs: typing.Any) -> typing.Any:
        observed.append(path)
        return shutil._ntuple_diskusage(1000, 950, 50)  # pylint: disable=protected-access

    monkeypatch.setattr(sweep, "_disk_usage", usage)
    first = managed_temp.sweep_managed_temp(now=_NOW)
    assert first.pressure is not None and first.pressure.still_low
    calls = len(observed)
    with monkeypatch.context() as guarded:
        guarded.setattr(sweep, "_inspect_tree", lambda *_args: pytest.fail("再帰走査を反復した"))
        guarded.setattr(sweep, "_tree_bytes", lambda *_args: pytest.fail("使用量計算を反復した"))
        for seconds in (0, 1, 299):
            assert managed_temp.sweep_managed_temp(now=_NOW + datetime.timedelta(seconds=seconds)).pressure is None
    assert len(observed) > calls
    assert managed_temp.sweep_managed_temp(now=_NOW + datetime.timedelta(seconds=300)).pressure is not None


@pytest.mark.parametrize("record", ["broken", "99999999999999999999999999", "-1"])
def test_pressure_invalid_record_retries(monkeypatch: pytest.MonkeyPatch, record: str) -> None:
    _first_sweep_done()
    (paths.state_root() / ".sweep/last-pressure").write_text(record, encoding="ascii")
    monkeypatch.setattr(sweep, "_disk_usage", lambda _path: shutil._ntuple_diskusage(1000, 950, 50))  # pylint: disable=protected-access
    assert managed_temp.sweep_managed_temp(now=_NOW).pressure is not None


def test_pressure_busy_lock_does_not_wait_or_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """他の試行がロックを保持する間は待たず、共有領域の再帰走査を重ねない。"""
    _first_sweep_done()
    monkeypatch.setattr(sweep, "_disk_usage", lambda _path: shutil._ntuple_diskusage(1000, 950, 50))  # pylint: disable=protected-access
    monkeypatch.setattr(sweep, "_inspect_tree", lambda *_args: pytest.fail("並行走査した"))
    lock_path = paths.state_root() / ".sweep/pressure.lock"
    with lock_path.open("a+", encoding="utf-8") as locked:
        file_lock.acquire_lock(locked, blocking=False)
        try:
            assert managed_temp.sweep_managed_temp(now=_NOW).pressure is None
        finally:
            file_lock.release_lock(locked)

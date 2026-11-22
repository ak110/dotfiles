"""共通ロックが作れない掃引と、権限を回復してから判定する領域の回帰試験。"""

from __future__ import annotations

import contextlib
import datetime
import errno
import hashlib
import json
import os
import pathlib
import stat
import subprocess
import sys
import typing
from types import SimpleNamespace

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.managed_temp import paths, resources, sweep
from agent_toolkit._atk.managed_temp.sweep_test import _NOW, _age, _area, _FakeDisk, _first_sweep_done, _legacy_lock
from agent_toolkit._common import file_lock

pytestmark = pytest.mark.windows


@pytest.fixture(autouse=True)
def _enable_pressure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(sweep.MIN_FREE_RATIO_ENV, raising=False)


def _deny_lock_creation(monkeypatch: pytest.MonkeyPatch, lock: pathlib.Path) -> None:
    original_open = pathlib.Path.open

    def full(path: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        mode = args[0] if args else kwargs.get("mode", "r")
        if path == lock and any(flag in mode for flag in "awx") and not path.exists():
            raise OSError(errno.ENOSPC, "disk full", str(path))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", full)


def test_daily_sweep_checks_fresh_root_ownership_only_for_regenerable_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fresh = _area("fresh-abc", days=1)
    expired = _area("expired-abc", days=8)
    ownership_checks: list[pathlib.Path] = []
    regenerable_checks: list[pathlib.Path] = []

    def in_use(area: pathlib.Path) -> bool:
        ownership_checks.append(area)
        return area == expired

    def clean(area: pathlib.Path, **_kwargs: object) -> dict[str, list[object]]:
        regenerable_checks.append(area)
        return {"removed": [], "retained": []}

    monkeypatch.setattr(sweep, "plugin_root_in_use", in_use)
    monkeypatch.setattr(sweep, "has_live_owner", lambda _area: False)
    monkeypatch.setattr(resources, "cleanup_regenerable", clean)
    monkeypatch.setattr(sweep, "_disk_usage", _FakeDisk(10000, 8000, paths.temp_root()))

    result = managed_temp.sweep_managed_temp(now=_NOW, force=True)

    assert result.deleted == []
    assert expired.is_dir()
    assert ownership_checks.count(fresh) == 1
    assert ownership_checks.count(expired) == 2
    assert regenerable_checks == [fresh]


@pytest.mark.parametrize("existing_lock", [False, True])
@pytest.mark.parametrize("entry", ["daily", "forced", "pressure"])
def test_full_disk_reclaims_only_caches_when_lock_cannot_be_created(
    monkeypatch: pytest.MonkeyPatch, existing_lock: bool, entry: str
) -> None:
    if entry == "pressure":
        _first_sweep_done()
    expired = _area("session-expired", days=9, files={"payload": "x" * 1300})
    fresh = _area("session-weekend", days=3, files={"handoff": "keep"})
    busy = _area("session-busy", days=9, files={sweep.OWNER_FILE: sweep.owner_token()})
    cache = _area(
        "restore-cache",
        days=8,
        files={"CACHEDIR.TAG": "Signature: 8a477f597d28d172789f06886806bc55", "payload": "x" * 1300},
    )
    lock = paths.state_root() / ".sweep/pressure.lock"
    if existing_lock:
        lock.parent.mkdir(exist_ok=True)
        lock.touch()
    _deny_lock_creation(monkeypatch, lock)
    used = sum(item.stat().st_size for item in paths.temp_root().rglob("*") if item.is_file())
    disk = _FakeDisk(10000, used, paths.temp_root())
    monkeypatch.setattr(sweep, "_disk_usage", disk)
    monkeypatch.setattr(resources, "process_references", lambda: [])
    assert disk(expired).free == 0
    original_remove = sweep.remove_tree

    def competing_sweep(path: pathlib.Path) -> None:
        original_remove(path)
        # 回収で空きが戻り、別の起動が通常ロックを作れる時点でも並行掃引しない。
        attempt = subprocess.run(
            [
                sys.executable,
                "-c",
                "import pathlib, sys\nfrom agent_toolkit._atk.managed_temp.sweep_lock import sweep_access\n"
                "with sweep_access(*map(pathlib.Path, sys.argv[1:])) as locked: print(locked)",
                str(expired.parent),
                str(lock.parent.parent),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        assert attempt.stdout.strip() == "False"

    monkeypatch.setattr(sweep, "remove_tree", competing_sweep)
    result = managed_temp.sweep_managed_temp(now=_NOW, force=entry == "forced")
    expected = {expired} if existing_lock else {cache}
    if existing_lock and entry != "pressure":
        expected.add(cache)
    assert set(result.deleted) == expected and not result.failed
    assert expired.exists() != (expired in expected) and cache.exists() != (cache in expected)
    assert (fresh / "handoff").read_text(encoding="utf-8") == "keep" and busy.is_dir()


@pytest.mark.parametrize("lock_full", [False, True], ids=["ordinary-lock", "full-disk-lock"])
@pytest.mark.parametrize("entry", ["daily", "forced", "pressure"])
@pytest.mark.parametrize("layout", ["nested-only", "standalone-only", "mixed"])
def test_zero_free_space_reclaims_unused_caches(
    monkeypatch: pytest.MonkeyPatch, lock_full: bool, entry: str, layout: str
) -> None:
    if entry != "daily":
        _first_sweep_done()
    if lock_full:
        _deny_lock_creation(monkeypatch, paths.state_root() / ".sweep/pressure.lock")
    tag = "Signature: 8a477f597d28d172789f06886806bc55"
    session = managed_temp.resolve_session_temp("weekend")
    record = session / "work-contract.md"
    record.write_bytes(b"only copy")
    expected = set()
    if layout in {"nested-only", "mixed"}:
        for name in ["downloads", "build"] if layout == "mixed" else ["downloads"]:
            cache = session / name
            cache.mkdir()
            (cache / "CACHEDIR.TAG").write_text(tag, encoding="ascii")
            expected.add(cache)
    if layout in {"standalone-only", "mixed"}:
        expected.add(_area("standalone", days=3, files={"CACHEDIR.TAG": tag}))
    if layout == "mixed":
        expired = _area("expired", days=9)
        if not lock_full:
            expected.add(expired)
    _age(session, 3)
    # 全件を合わせても下限に届かない。同じ容量・同じ時刻でロック作成の成否だけを変える。
    root = paths.temp_root()
    initial_size = sum(item.stat().st_size for item in root.rglob("*") if item.is_file())
    expected_free = sum(item.stat().st_size for area in expected for item in area.rglob("*") if item.is_file())
    disk = _FakeDisk(100_000, initial_size, root)
    monkeypatch.setattr(sweep, "_disk_usage", disk)
    monkeypatch.setattr(resources, "process_references", lambda: [])
    assert disk(session).free == 0
    result = managed_temp.sweep_managed_temp(now=_NOW, force=entry == "forced")
    assert set(result.deleted) == expected and not result.failed
    assert all(not path.exists() for path in expected) and disk(session).free == expected_free
    assert managed_temp.find_session_temp("weekend") == session and record.read_bytes() == b"only copy"


@pytest.mark.parametrize("entry", ["daily", "forced", "pressure"])
@pytest.mark.parametrize("observed", [False, True], ids=["unknown", "in-use"])
def test_full_disk_retains_caches_unless_unused(monkeypatch: pytest.MonkeyPatch, entry: str, observed: bool) -> None:
    if entry != "daily":
        _first_sweep_done()
    _deny_lock_creation(monkeypatch, paths.state_root() / ".sweep/pressure.lock")
    tag = "Signature: 8a477f597d28d172789f06886806bc55"
    session = _area("session-weekend", days=3, files={"handoff": "keep", "nested/CACHEDIR.TAG": tag})
    other = _area("standalone", days=3, files={"CACHEDIR.TAG": tag})
    unused = _area("separate-cache", days=3, files={"CACHEDIR.TAG": tag})
    monkeypatch.setattr(sweep, "_disk_usage", lambda _root: SimpleNamespace(total=100, free=0))
    monkeypatch.setattr(resources, "process_references", lambda: [session / "nested", other] if observed else None)
    result = managed_temp.sweep_managed_temp(now=_NOW, force=entry == "forced")
    assert result.deleted == ([unused] if observed else []) and not result.failed
    assert unused.exists() != observed
    assert result.pressure is not None and result.pressure.still_low
    assert (other / "CACHEDIR.TAG").is_file() and (session / "nested/CACHEDIR.TAG").is_file()
    assert (session / "handoff").read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(not hasattr(os, "O_PATH") or os.geteuid() == 0, reason="非rootの実際の権限回復を検証する")
@pytest.mark.parametrize("kind", ["expired", "legacy-busy", "legacy-weekend", "live", "unreadable-owner", "broken-marker"])
def test_pressure_inspects_mode_zero_area_before_removing_it(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    _first_sweep_done()
    area = _area("old-prefix", days=9, files={"handoff": "keep"})
    if kind.startswith("legacy"):
        (area / paths.LEGACY_MARKER_NAME).write_text(json.dumps({"path": str(area), "session_id": "s"}), encoding="utf-8")
    if kind == "broken-marker":
        (area / paths.LEGACY_MARKER_NAME).write_bytes(b"invalid json")
    if kind in {"live", "unreadable-owner"}:
        owner = area / sweep.OWNER_FILE
        owner.write_text(sweep.owner_token(), encoding="ascii")
        if kind == "unreadable-owner":
            owner.chmod(0)
    mib = 1024 * 1024
    if kind == "expired":
        (area / "payload").write_bytes(b"x" * (7 * mib))
    _age(area, 3 if kind == "legacy-weekend" else 9)
    # mode 000の木を空き容量の模擬値の計算のために開かない。
    monkeypatch.setattr(
        sweep, "_disk_usage", lambda _root: SimpleNamespace(total=100 * mib, free=(6 if area.exists() else 13) * mib)
    )
    area.chmod(0)
    try:
        with contextlib.ExitStack() as stack:
            if kind == "legacy-busy":
                handle = stack.enter_context(_legacy_lock(area).open("a+b"))
                file_lock.acquire_lock(handle)
            result = managed_temp.sweep_managed_temp(now=_NOW)
        assert result.deleted == ([area] if kind == "expired" else [])
        assert result.pressure is not None and result.pressure.free_before == 6 * mib
        assert result.pressure.free_after == (13 if kind == "expired" else 6) * mib
        if kind == "broken-marker":
            assert result.failed and paths.LEGACY_MARKER_NAME in result.failed[0][1]
        elif kind != "legacy-weekend":
            assert not result.failed
        if kind != "expired":
            assert stat.S_IMODE(area.stat().st_mode) == 0
            area.chmod(0o700)
            assert (area / "handoff").read_text(encoding="utf-8") == "keep"
    finally:
        if area.exists():
            area.chmod(0o700)


def test_sweep_reports_lock_open_errors_without_scanning(monkeypatch: pytest.MonkeyPatch) -> None:
    area = _area("expired", days=9)
    lock = paths.state_root() / ".sweep/pressure.lock"
    original_open = pathlib.Path.open

    def denied(path: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        if path == lock:
            raise PermissionError(errno.EACCES, "cannot open lock", str(path))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", denied)
    result = managed_temp.sweep_managed_temp(now=_NOW)
    assert not result.deleted and area.exists()
    assert len(result.failed) == 1 and result.failed[0][0] == lock and "cannot open lock" in result.failed[0][1]


@pytest.mark.parametrize("still_full", [False, True])
def test_cache_recovery_does_not_require_writable_state(monkeypatch: pytest.MonkeyPatch, still_full: bool) -> None:
    area = _area(
        "session-weekend",
        days=3,
        files={"handoff": "keep", "cache/CACHEDIR.TAG": "Signature: 8a477f597d28d172789f06886806bc55"},
    )
    cache = area / "cache"
    root = paths.state_root_path()
    assert not root.exists()
    original_mkdir = pathlib.Path.mkdir

    def full(path: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> None:
        if path == root and (still_full or cache.exists()):
            raise OSError(errno.ENOSPC, "disk full", str(path))
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(resources, "process_references", lambda: [])
    monkeypatch.setattr(pathlib.Path, "mkdir", full)
    monkeypatch.setattr(sweep, "_disk_usage", lambda _root: SimpleNamespace(total=100, free=0))
    # 物理mtimeと掃引の判定時刻を同じ時計にそろえる。
    now = datetime.datetime.now(datetime.UTC)
    last_use = now - datetime.timedelta(days=3)
    for child in [*area.rglob("*"), area]:
        os.utime(child, (last_use.timestamp(), last_use.timestamp()))
    result = managed_temp.sweep_managed_temp(now=now)
    assert result.deleted == [cache] and not result.failed and not cache.exists()
    if still_full:
        assert not root.exists()
    elif os.name == "posix":
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert (area / "handoff").read_text(encoding="utf-8") == "keep"
    # 書けた場合は回収前の利用から、書けなかった場合は回収後の親のmtimeから7日を数える。
    after_cleanup = datetime.datetime.fromtimestamp(area.stat().st_mtime, datetime.UTC)
    retained_since = after_cleanup if still_full else last_use
    assert not managed_temp.sweep_managed_temp(now=retained_since + datetime.timedelta(days=6), force=True).deleted
    expected = [] if still_full else [area]
    assert managed_temp.sweep_managed_temp(now=retained_since + datetime.timedelta(days=8), force=True).deleted == expected


@pytest.mark.parametrize("daily", [False, True])
def test_sweep_keeps_its_own_lock_and_session_references_in_shared_root(monkeypatch: pytest.MonkeyPatch, daily: bool) -> None:
    root = paths.temp_root()
    monkeypatch.setattr(paths, "_state_root_path", lambda: root)
    session = _area("old-prefix", days=3, files={"handoff": "keep"})
    reference = root / ".sessions" / hashlib.sha256(b"weekend").hexdigest()
    reference.parent.mkdir()
    reference.write_text(json.dumps({"path": str(session)}), encoding="utf-8")
    _age(reference.parent, 9)
    registry = _legacy_lock(session).with_suffix("").with_suffix("")
    registry.write_text(json.dumps({"path": str(session)}), encoding="utf-8")
    _age(registry, 9)
    _first_sweep_done()
    lock = root / ".sweep/pressure.lock"
    lock.touch()
    _age(lock.parent, 9)
    identity = lock.stat()
    expired = _area("expired", days=10)
    monkeypatch.setattr(sweep, "_disk_usage", lambda _root: SimpleNamespace(total=100, free=0))
    result = managed_temp.sweep_managed_temp(now=_NOW, force=daily)
    assert not result.failed and result.deleted == [expired]
    assert os.path.samestat(identity, lock.stat()) and registry.is_file()
    assert managed_temp.find_session_temp("weekend") == session and (session / "handoff").is_file()


def test_full_disk_recovers_when_no_sweep_state_has_ever_been_created(monkeypatch: pytest.MonkeyPatch) -> None:
    expired = _area("expired", days=9)
    cache = _area("unused-cache", days=8, files={"CACHEDIR.TAG": "Signature: 8a477f597d28d172789f06886806bc55"})
    state = paths.state_root_path()
    assert not state.exists()
    original_mkdir = pathlib.Path.mkdir

    def full(path: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> None:
        if path == state:
            raise OSError(errno.ENOSPC, "disk full", str(path))
        original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "mkdir", full)
    monkeypatch.setattr(sweep, "_disk_usage", lambda _root: SimpleNamespace(total=100, free=0))
    monkeypatch.setattr(resources, "process_references", lambda: [])
    result = managed_temp.sweep_managed_temp(now=_NOW)
    assert result.deleted == [cache] and not result.failed and not state.exists()
    assert expired.exists()

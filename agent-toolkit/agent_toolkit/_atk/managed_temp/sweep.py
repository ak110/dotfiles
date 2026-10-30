"""一時root直下の期限切れの対象の自動掃引と、旧版が残した状態の個別回収。

掃引は`atk`の共通起動から毎回呼ばれる。起動ごとの全走査を避けるため、root直下の名前ごとに
次に調べる時刻を状態rootの1表（`.sweep/schedule`）へ記録し、rootの更新時刻が変わらず
次の調査時刻にも達していない起動では一覧も取らずに戻る。
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import os
import pathlib
import secrets
import stat
import typing

from agent_toolkit._atk.managed_temp import paths, permissions
from agent_toolkit._atk.managed_temp.errors import ManagedTempError
from agent_toolkit._atk.managed_temp.removal import plugin_root_in_use, remove_tree
from agent_toolkit._common import file_lock, plugin_root_leases

_SCHEDULE_NAME = ".sweep/schedule"
_SCHEDULE_VERSION = 4
_NANOSECONDS_PER_SECOND = 1_000_000_000
_RETRY_NS = 3600 * _NANOSECONDS_PER_SECOND
"""使用中・ロック保持中・削除失敗の対象を次に調べるまでの間隔。"""
_GIT_RECHECK_NS = 86_400 * _NANOSECONDS_PER_SECOND
"""登録済みworktreeを含む期限切れの対象の再調査間隔。登録解除後は次の調査で回収する。"""
_LEGACY_INTERVAL_NS = 86_400 * _NANOSECONDS_PER_SECOND
"""旧版の状態を調べる間隔。数万件に及ぶ旧ロックを起動ごとに走査しない。"""
_DESCRIPTOR_WALK = os.scandir in os.supports_fd and os.open in os.supports_dir_fd
"""記述子を基点に相対名で走査できるか。Windowsは記述子による走査を提供しない。"""
_LEGACY_REGISTRY_SUFFIX = ".json"
_LEGACY_CLEANUP_LOCK_SUFFIX = ".json.cleanup.lock"


class SweepResult(typing.NamedTuple):
    """`sweep_managed_temp`の結果。"""

    deleted: list[pathlib.Path]
    """自動削除したパス。"""
    failed: tuple[tuple[pathlib.Path, str], ...]
    """期限切れでも削除できなかった対象と理由。次の調査時刻（1時間後）に再試行する。"""


class _Schedule(typing.NamedTuple):
    root_mtime_ns: int | None
    entries: dict[str, tuple[int, int]]
    """root直下の名前ごとの、記録時の自身の更新時刻と次に調べる時刻（ナノ秒）。"""
    legacy_checked_ns: int


def _load_schedule(state_root: pathlib.Path, temp_root: pathlib.Path) -> _Schedule:
    """予定表を読む。欠落・破損・前提の不一致では空の表を返し、呼び出し元は全件を調べる。"""
    empty = _Schedule(None, {}, 0)
    try:
        value = json.loads((state_root / _SCHEDULE_NAME).read_text(encoding="utf-8"))
    except OSError, UnicodeError, ValueError:
        return empty
    if not isinstance(value, dict) or value.get("version") != _SCHEDULE_VERSION or value.get("temp_root") != str(temp_root):
        return empty
    raw_entries = value.get("entries")
    root_mtime = value.get("root_mtime_ns")
    legacy_checked = value.get("legacy_checked_ns")
    if not isinstance(raw_entries, dict) or not isinstance(root_mtime, int) or not isinstance(legacy_checked, int):
        return empty
    entries: dict[str, tuple[int, int]] = {}
    for name, item in raw_entries.items():
        if not (isinstance(item, list) and len(item) == 2 and _is_ns(item[0]) and _is_ns(item[1])):
            return empty
        entries[name] = (item[0], item[1])
    return _Schedule(root_mtime, entries, legacy_checked)


def _save_schedule(state_root: pathlib.Path, temp_root: pathlib.Path, schedule: _Schedule) -> None:
    """予定表を置き換える。書けない場合は次回の起動が全件を調べるだけなので、失敗を報告しない。"""
    directory = state_root / pathlib.Path(_SCHEDULE_NAME).parent
    temporary = directory / f".schedule-{secrets.token_hex(8)}.tmp"
    value = {
        "version": _SCHEDULE_VERSION,
        "temp_root": str(temp_root),
        "root_mtime_ns": schedule.root_mtime_ns,
        "legacy_checked_ns": schedule.legacy_checked_ns,
        "entries": {name: list(item) for name, item in sorted(schedule.entries.items())},
    }
    try:
        directory.mkdir(mode=0o700, exist_ok=True)
        temporary.write_text(json.dumps(value, ensure_ascii=True) + "\n", encoding="utf-8")
        os.replace(temporary, state_root / _SCHEDULE_NAME)
    except OSError:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def _is_ns(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _now_ns(now: datetime.datetime) -> int:
    reference = now if now.tzinfo is not None else now.astimezone()
    return int(reference.timestamp() * _NANOSECONDS_PER_SECOND)


def _update_time(metadata: os.stat_result, now_ns: int) -> int:
    """未来の更新時刻は状態変更時刻へ置き換え、現在以前の値だけを採用する。"""
    stamp = metadata.st_mtime_ns if metadata.st_mtime_ns <= now_ns else metadata.st_ctime_ns
    return stamp if stamp <= now_ns else 0


def _registered_worktree(git_file: pathlib.Path, area: pathlib.Path) -> bool:
    """領域外のgitdirと逆参照が一致するworktreeだけを保護する。"""
    try:
        content = git_file.read_text(encoding="utf-8").strip()
        if not content.startswith("gitdir: "):
            return False
        gitdir = (git_file.parent / content.removeprefix("gitdir: ")).resolve()
        if gitdir.is_relative_to(area.resolve()) or not gitdir.is_dir():
            return False
        reference = (gitdir / "gitdir").read_text(encoding="utf-8").strip()
        return bool(reference) and os.path.samefile(gitdir / reference, git_file)
    except OSError, UnicodeError, ValueError:
        return False


def _latest_update_and_worktree(path: pathlib.Path, now_ns: int) -> tuple[int, bool]:
    """領域自身と配下の最終更新時刻（ナノ秒）と、登録済みworktreeがあるかを返す。リンクは辿らない。

    配下を含む最終更新を使うのは、中断後に再開した領域を古いと誤判定しないためである。
    記述子で辿れる環境では親の記述子からの相対名で走査し、連結長がPATH_MAXを超える木も調べる。
    """
    latest = _update_time(path.lstat(), now_ns)
    registered = False
    if _DESCRIPTOR_WALK:
        with permissions.directory(path) as opened:
            return _latest_under(opened, latest, path, path, now_ns)
    pending = [path]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as children:
            for child in children:
                metadata = child.stat(follow_symlinks=False)
                latest = max(latest, _update_time(metadata, now_ns))
                if child.name == ".git" and stat.S_ISREG(metadata.st_mode):
                    registered |= _registered_worktree(pathlib.Path(child.path), path)
                if stat.S_ISDIR(metadata.st_mode) and not paths.is_link(metadata):
                    pending.append(pathlib.Path(child.path))
    return latest, registered


def _latest_under(directory: int, latest: int, current: pathlib.Path, area: pathlib.Path, now_ns: int) -> tuple[int, bool]:
    """記述子が指すディレクトリの配下を相対名で辿り、`_latest_update_and_worktree`と同じ値を返す。"""
    registered = False
    with os.scandir(directory) as children:
        listed = list(children)
    for child in listed:
        metadata = child.stat(follow_symlinks=False)
        latest = max(latest, _update_time(metadata, now_ns))
        child_path = current / child.name
        if child.name == ".git" and stat.S_ISREG(metadata.st_mode):
            registered |= _registered_worktree(child_path, area)
        if stat.S_ISDIR(metadata.st_mode) and not paths.is_link(metadata):
            with permissions.directory(child.name, dir_fd=directory, expected=metadata, display_path=child_path) as nested:
                latest, nested_registered = _latest_under(nested, latest, child_path, area, now_ns)
            registered |= nested_registered
    return latest, registered


def _legacy_lock_path(state_root: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    """旧版が対象の回収中に保持するcleanupロックのパスを返す。"""
    return state_root / f"{hashlib.sha256(os.fsencode(path)).hexdigest()}{_LEGACY_CLEANUP_LOCK_SUFFIX}"


def _legacy_original_path(area: pathlib.Path) -> pathlib.Path:
    """旧markerが記録した元の対象パスを返す。後始末中の隔離先は元の対象のロックで旧版と排他する。"""
    try:
        marker = json.loads((area / paths.LEGACY_MARKER_NAME).read_text(encoding="utf-8"))
    except OSError, UnicodeError, ValueError:
        return area
    recorded = marker.get("path") if isinstance(marker, dict) else None
    return pathlib.Path(recorded) if isinstance(recorded, str) and pathlib.Path(recorded).is_absolute() else area


@contextlib.contextmanager
def _try_lock(path: pathlib.Path) -> typing.Generator[bool]:
    """ロックを待たずに取得できれば真を渡す。取得できない場合は偽を渡し、対象をその回は保持する。"""
    try:
        handle = path.open("a+", encoding="utf-8")
    except OSError:
        yield False
        return
    with handle:
        try:
            file_lock.acquire_lock(handle, blocking=False)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            file_lock.release_lock(handle)


def _unlink_released_lock(path: pathlib.Path) -> None:
    """解放して閉じたロックファイルを削除する。他のプロセスが開いていて削除できなければ次の調査まで残す。

    Windowsは開いているファイルを削除できないため、ロックの保持中には削除しない。
    """
    with contextlib.suppress(PermissionError):
        path.unlink(missing_ok=True)


def _remove_legacy_area(state_root: pathlib.Path, area: pathlib.Path) -> bool:
    """旧marker付きの領域を、旧版のcleanupロックを待たずに取れた場合だけ削除する。取れなければ偽を返す。"""
    original = _legacy_original_path(area)
    lock_path = _legacy_lock_path(state_root, original)
    collected = False
    with _try_lock(lock_path) as locked:
        if not locked:
            return False
        remove_tree(area)
        registry = lock_path.with_name(lock_path.name.removesuffix(".cleanup.lock"))
        if not os.path.lexists(original) and not any(state_root.glob(f"{registry.name}.consuming-*")):
            registry.unlink(missing_ok=True)
            collected = True
    if collected:
        _unlink_released_lock(lock_path)
    return True


def _sweep_legacy_state(state_root: pathlib.Path) -> None:
    """旧版の登録簿・会話参照・cleanupロックのうち、対象が無くロックを取れたものだけを個別に回収する。

    旧版プロセスが解決に使う間は残すため、対象が実在する登録・参照と、消費途中状態を持つ登録は保持する。
    """
    for name in ((state_root / ".sweep-schedule"), (state_root.parent / ".managed-temp-sweep-gate")):
        name.unlink(missing_ok=True)
    with os.scandir(state_root) as entries:
        names = sorted(entry.name for entry in entries if entry.is_file(follow_symlinks=False))
    consuming = {name.partition(".consuming-")[0] for name in names if ".consuming-" in name}
    for name in names:
        if name.endswith(_LEGACY_REGISTRY_SUFFIX) and len(name) == 64 + len(_LEGACY_REGISTRY_SUFFIX):
            if name in consuming:
                continue
            try:
                record = json.loads((state_root / name).read_text(encoding="utf-8"))
            except OSError, UnicodeError, ValueError:
                continue
            target = record.get("path") if isinstance(record, dict) else None
            if not isinstance(target, str) or os.path.lexists(target):
                continue
            with _try_lock(state_root / f"{name}.cleanup.lock") as locked:
                if locked:
                    (state_root / name).unlink(missing_ok=True)
            if locked:
                _unlink_released_lock(state_root / f"{name}.cleanup.lock")
        elif name.endswith(_LEGACY_CLEANUP_LOCK_SUFFIX):
            registry = name.removesuffix(".cleanup.lock")
            if registry in names or registry in consuming:
                continue
            with _try_lock(state_root / name) as locked:
                pass
            if locked:
                _unlink_released_lock(state_root / name)
    sessions = state_root / ".sessions"
    try:
        with os.scandir(sessions) as entries:
            references = sorted(entry.name for entry in entries if entry.is_file(follow_symlinks=False))
    except FileNotFoundError:
        return
    for name in references:
        if name.endswith(".lock") or "." in name:
            continue
        try:
            record = json.loads((sessions / name).read_text(encoding="utf-8"))
        except OSError, UnicodeError, ValueError:
            continue
        target = record.get("path") if isinstance(record, dict) else None
        if not isinstance(target, str) or os.path.lexists(target):
            continue
        with _try_lock(sessions / f"{name}.lock") as locked:
            if locked:
                (sessions / name).unlink(missing_ok=True)
        if locked:
            _unlink_released_lock(sessions / f"{name}.lock")


def _examine(
    state_root: pathlib.Path, child: pathlib.Path, metadata: os.stat_result, cutoff_ns: int, now_ns: int, max_age_ns: int
) -> int | None:
    """1件を調べ、保持する場合は次に調べる時刻を返す。削除した場合はNoneを返す。"""
    if stat.S_ISDIR(metadata.st_mode):
        latest, has_git = _latest_update_and_worktree(child, now_ns)
    else:
        latest, has_git = _update_time(metadata, now_ns), False
    if latest >= cutoff_ns:
        return latest + max_age_ns
    if has_git:
        return now_ns + _GIT_RECHECK_NS
    if plugin_root_in_use(child):
        return now_ns + _RETRY_NS
    if stat.S_ISDIR(metadata.st_mode) and os.path.lexists(child / paths.LEGACY_MARKER_NAME):
        if not _remove_legacy_area(state_root, child):
            return now_ns + _RETRY_NS
        return None
    remove_tree(child)
    return None


@plugin_root_leases.reuse_process_observation()
def sweep_managed_temp(*, now: datetime.datetime, max_age_days: int = paths.MAX_AGE_DAYS) -> SweepResult:
    """一時root直下で配下を含む最終更新から`max_age_days`を超えた対象を削除し、削除結果と失敗を返す。

    通常ファイルも同じ期限の対象とする。外部gitdirと逆参照が一致する登録済みworktree、
    使用中の保持配布物、リンクは削除しない。旧版のmarkerを持つ領域は旧版のcleanupロックを取れた場合だけ削除する。
    一時rootを指定しない場合に使う場所は`atk`だけが作成する場所であり、所有の検証なしに回収しても
    ユーザーのディレクトリを巻き込まない（2026年10月2日のユーザーの判断）。
    """
    now_ns = _now_ns(now)
    max_age_ns = max_age_days * 86_400 * _NANOSECONDS_PER_SECOND
    cutoff_ns = now_ns - max_age_ns
    try:
        temp_root = paths.temp_root()
        state_root = paths.state_root()
    except ManagedTempError as error:
        return SweepResult([], ((paths.temp_root_path(), str(error)),))
    schedule = _load_schedule(state_root, temp_root)
    root_mtime_ns = temp_root.stat().st_mtime_ns
    legacy_due = now_ns - schedule.legacy_checked_ns >= _LEGACY_INTERVAL_NS
    next_due = min((due for _top, due in schedule.entries.values()), default=None)
    if schedule.root_mtime_ns == root_mtime_ns and (next_due is None or now_ns < next_due) and not legacy_due:
        return SweepResult([], ())
    deleted: list[pathlib.Path] = []
    failed: list[tuple[pathlib.Path, str]] = []
    entries: dict[str, tuple[int, int]] = {}
    with os.scandir(temp_root) as children:
        listed = sorted(children, key=lambda child: child.name)
    for entry in listed:
        child = temp_root / entry.name
        try:
            metadata = entry.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        if paths.is_link(metadata):
            continue
        known = schedule.entries.get(entry.name)
        if known is not None and known[0] == metadata.st_mtime_ns and now_ns < known[1]:
            entries[entry.name] = known
            continue
        try:
            due = _examine(state_root, child, metadata, cutoff_ns, now_ns, max_age_ns)
        except (OSError, ManagedTempError) as error:
            if not os.path.lexists(child):
                continue
            failed.append((child, str(error)))
            entries[entry.name] = (metadata.st_mtime_ns, now_ns + _RETRY_NS)
            continue
        if due is None:
            deleted.append(child)
        else:
            entries[entry.name] = (metadata.st_mtime_ns, due)
    legacy_checked_ns = schedule.legacy_checked_ns
    if legacy_due:
        with contextlib.suppress(OSError):
            _sweep_legacy_state(state_root)
        legacy_checked_ns = now_ns
    with contextlib.suppress(OSError):
        root_mtime_ns = temp_root.stat().st_mtime_ns
    _save_schedule(state_root, temp_root, _Schedule(root_mtime_ns, entries, legacy_checked_ns))
    return SweepResult(deleted, tuple(failed))

"""一時root直下の期限切れの対象の自動掃引と、旧版が残した状態の個別回収。

掃引は`atk`の共通起動から毎回呼ばれる。起動ごとの全走査を避けるため、前回の掃引を終えた時刻だけを
状態rootの1ファイル（`.sweep/last-sweep`）へ記録し、前回から1日が過ぎていない起動では一覧も取らずに戻る。
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import json
import os
import pathlib
import secrets
import shutil
import stat
import typing

from agent_toolkit._atk.managed_temp import creation, paths, permissions
from agent_toolkit._atk.managed_temp.errors import ManagedTempError
from agent_toolkit._atk.managed_temp.removal import plugin_root_in_use, remove_tree
from agent_toolkit._common import file_lock, plugin_root_leases, session_registry

_RECORD_NAME = ".sweep/last-sweep"
_OLD_SCHEDULE_NAME = ".sweep/schedule"
"""対象ごとの予定表を持っていた版の記録。読まずに削除する。"""
_NANOSECONDS_PER_SECOND = 1_000_000_000
_SWEEP_INTERVAL_NS = 86_400 * _NANOSECONDS_PER_SECOND
"""掃引の間隔。使用中・ロック保持中・削除失敗の対象と旧版の状態も、次の掃引で調べ直す。"""
_DESCRIPTOR_WALK = os.scandir in os.supports_fd and os.open in os.supports_dir_fd
"""記述子を基点に相対名で走査できるか。Windowsは記述子による走査を提供しない。"""
_MIN_FREE_RATIO = 0.10
"""一時rootを含むFSの空きの下限（容量比）。

2026-11-12にmanaged-tempだけで容量の約16%（46GB/292GB）に達し、空きが約3%から同時実行中の測定で
短時間に尽きて`atk`の書込が失敗した。容量比にすれば、ディスクの大きさによらず同じ比率の余裕を残せる。
"""
_TOP_RECLAIMED = 5
_LEGACY_REGISTRY_SUFFIX = ".json"
_LEGACY_CLEANUP_LOCK_SUFFIX = ".json.cleanup.lock"


class SweepResult(typing.NamedTuple):
    """`sweep_managed_temp`の結果。"""

    deleted: list[pathlib.Path]
    """自動削除したパス。"""
    failed: tuple[tuple[pathlib.Path, str], ...]
    """期限切れでも削除できなかった対象と理由。次の掃引（1日後）に再試行する。"""
    pressure: SpacePressure | None = None
    """空きが下限を割った起動の回収結果。空きが下限以上の通常時はNone。"""


class SpacePressure(typing.NamedTuple):
    """空きが下限（容量の10%）を割った起動の、回収前後の空きと回収量上位。"""

    total: int
    """一時rootを含むFSの容量（バイト）。"""
    free_before: int
    free_after: int
    reclaimed_bytes: int
    top_reclaimed: tuple[tuple[str, int], ...]
    """回収量の多い順の対象名とバイト数。作成者が回収しなかった大きな領域を振り返りで見つけるための記録。"""
    temp_root_bytes: int | None
    """回収後の一時root全体の使用量。回収できる対象が残らなかった場合の警告にだけ数える。"""

    @property
    def still_low(self) -> bool:
        return self.free_after < self.total * _MIN_FREE_RATIO


def _sweep_due(state_root: pathlib.Path, now_ns: int) -> bool:
    """前回の掃引から間隔が過ぎたかを返す。記録の欠落・破損と、未来の時刻では真を返す。"""
    try:
        last_ns = int((state_root / _RECORD_NAME).read_text(encoding="ascii"))
    except OSError, ValueError:
        return True
    return not 0 <= now_ns - last_ns < _SWEEP_INTERVAL_NS


def _save_sweep_time(state_root: pathlib.Path, now_ns: int) -> None:
    """掃引を終えた時刻を置き換える。書けない場合は次回の起動が掃引するだけなので、失敗を報告しない。"""
    record = state_root / _RECORD_NAME
    temporary = record.with_name(f".last-sweep-{secrets.token_hex(8)}.tmp")
    try:
        record.parent.mkdir(mode=0o700, exist_ok=True)
        temporary.write_text(f"{now_ns}\n", encoding="ascii")
        os.replace(temporary, record)
    except OSError:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def _disk_usage(path: pathlib.Path) -> typing.Any:
    """空きの観測点。テストが実ディスクの空きから掃引を隔離できるよう、標準関数を1箇所で包む。"""
    return shutil.disk_usage(path)


def _tree_bytes(path: pathlib.Path) -> int:
    """対象の実使用量（バイト）を、リンクを辿らずに数える。数えられない項目は0として扱う。"""
    total = 0
    pending = [path]
    while pending:
        current = pending.pop()
        try:
            metadata = current.lstat()
            total += getattr(metadata, "st_blocks", 0) * 512 or metadata.st_size
            if stat.S_ISDIR(metadata.st_mode) and not paths.is_link(metadata):
                with os.scandir(current) as children:
                    pending.extend(current / child.name for child in children)
        except OSError:
            continue
    return total


def _now_ns(now: datetime.datetime) -> int:
    reference = now if now.tzinfo is not None else now.astimezone()
    return int(reference.timestamp() * _NANOSECONDS_PER_SECOND)


def _update_time(metadata: os.stat_result, now_ns: int) -> int:
    """未来の更新時刻は状態変更時刻へ置き換え、状態変更時刻も未来なら判定基準時刻を返す。

    掃引は走査の前に判定基準時刻を取得するため、走査中に並行する主体が作った・変えた項目は
    状態変更時刻も未来になる。それを古い側へ倒すと使用中の領域を消し、消した領域は回復できない。
    判定基準時刻として保持しても、次回の掃引が実際の時刻で再判定して回収できる。
    """
    stamp = metadata.st_mtime_ns if metadata.st_mtime_ns <= now_ns else metadata.st_ctime_ns
    return min(stamp, now_ns)


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
    for name in (
        (state_root / _OLD_SCHEDULE_NAME),
        (state_root / ".sweep-schedule"),
        (state_root.parent / ".managed-temp-sweep-gate"),
    ):
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
    state_root: pathlib.Path,
    child: pathlib.Path,
    metadata: os.stat_result,
    cutoff_ns: int,
    now_ns: int,
    protected_sessions: set[pathlib.Path],
) -> bool:
    """1件を調べ、削除した場合は真を返す。保持した対象は次の掃引で調べ直す。"""
    if stat.S_ISDIR(metadata.st_mode):
        latest, has_git = _latest_update_and_worktree(child, now_ns)
    else:
        latest, has_git = _update_time(metadata, now_ns), False
    if latest >= cutoff_ns or child in protected_sessions or has_git or plugin_root_in_use(child):
        return False
    return _remove(state_root, child, metadata)


def _remove(state_root: pathlib.Path, child: pathlib.Path, metadata: os.stat_result) -> bool:
    """回収可と判定済みの1件を削除し、削除した場合は真を返す。"""
    if stat.S_ISDIR(metadata.st_mode) and os.path.lexists(child / paths.LEGACY_MARKER_NAME):
        return _remove_legacy_area(state_root, child)
    remove_tree(child)
    return True


def _below_floor(temp_root: pathlib.Path) -> bool:
    """一時rootを含むFSの空きが容量の10%を下回るかを返す。観測できない場合は偽を返す。"""
    try:
        usage = _disk_usage(temp_root)
    except OSError:
        return False
    return usage.free < usage.total * _MIN_FREE_RATIO


def _reclaim_for_space(
    state_root: pathlib.Path,
    temp_root: pathlib.Path,
    now_ns: int,
    protected_sessions: set[pathlib.Path],
    deleted: list[pathlib.Path],
    failed: list[tuple[pathlib.Path, str]],
) -> SpacePressure | None:
    """空きが下限を割る場合に限り、保持期間を待たず使用中でない対象を最終更新の古い順に回収する。

    使用中・保護セッション・登録済みworktree・保持配布物・リンクは、期限の掃引と同じ判定で残す。
    消した領域は回復できず、実行中の作業の一時領域を消すと作業が壊れるためである。
    """
    try:
        usage = _disk_usage(temp_root)
    except OSError:
        return None
    if usage.free >= usage.total * _MIN_FREE_RATIO:
        return None
    candidates: list[tuple[int, pathlib.Path, os.stat_result]] = []
    with os.scandir(temp_root) as children:
        listed = sorted(children, key=lambda child: child.name)
    for entry in listed:
        child = temp_root / entry.name
        try:
            metadata = entry.stat(follow_symlinks=False)
            if paths.is_link(metadata):
                continue
            if stat.S_ISDIR(metadata.st_mode):
                latest, has_git = _latest_update_and_worktree(child, now_ns)
            else:
                latest, has_git = _update_time(metadata, now_ns), False
            if child in protected_sessions or has_git or plugin_root_in_use(child):
                continue
        except FileNotFoundError:
            continue
        except (OSError, ManagedTempError) as error:
            failed.append((child, str(error)))
            continue
        candidates.append((latest, child, metadata))
    candidates.sort(key=lambda item: (item[0], item[1].name))
    sizes: list[tuple[str, int]] = []
    free = usage.free
    for _latest, child, metadata in candidates:
        if free > usage.total * _MIN_FREE_RATIO:
            break
        size = _tree_bytes(child)
        try:
            if not _remove(state_root, child, metadata):
                continue
        except (OSError, ManagedTempError) as error:
            if os.path.lexists(child):
                failed.append((child, str(error)))
            continue
        deleted.append(child)
        sizes.append((child.name, size))
        try:
            free = _disk_usage(temp_root).free
        except OSError:
            free = usage.free + sum(size for _name, size in sizes)
    sizes.sort(key=lambda item: item[1], reverse=True)
    pressure = SpacePressure(
        usage.total, usage.free, free, sum(size for _name, size in sizes), tuple(sizes[:_TOP_RECLAIMED]), None
    )
    if pressure.still_low:
        pressure = pressure._replace(temp_root_bytes=_tree_bytes(temp_root))
    return pressure


@plugin_root_leases.reuse_process_observation()
def sweep_managed_temp(*, now: datetime.datetime, max_age_days: int = paths.MAX_AGE_DAYS) -> SweepResult:
    """一時root直下で配下を含む最終更新から`max_age_days`を超えた対象を削除し、削除結果と失敗を返す。

    通常ファイルも同じ期限の対象とする。外部gitdirと逆参照が一致する登録済みworktree、
    使用中の保持配布物、リンクは削除しない。旧版のmarkerを持つ領域は旧版のcleanupロックを取れた場合だけ削除する。
    一時rootを指定しない場合に使う場所は`atk`だけが作成する場所であり、所有の検証なしに回収しても
    ユーザーのディレクトリを巻き込まない（2026年10月2日のユーザーの判断）。
    """
    now_ns = _now_ns(now)
    cutoff_ns = now_ns - max_age_days * 86_400 * _NANOSECONDS_PER_SECOND
    try:
        temp_root = paths.temp_root()
        state_root = paths.state_root()
    except ManagedTempError as error:
        return SweepResult([], ((paths.temp_root_path(), str(error)),))
    due = _sweep_due(state_root, now_ns)
    if not due:
        # 間隔内の起動は全走査をしない。空きの確認は1回のstatvfsで済むため、下限を割る場合だけ回収へ進む。
        if not _below_floor(temp_root):
            return SweepResult([], ())
        protected = {
            area
            for identifier in session_registry.pending_session_ids()
            if (area := creation.find_session_temp(identifier)) is not None
        }
        deleted_early: list[pathlib.Path] = []
        failed_early: list[tuple[pathlib.Path, str]] = []
        pressure = _reclaim_for_space(state_root, temp_root, now_ns, protected, deleted_early, failed_early)
        return SweepResult(deleted_early, tuple(failed_early), pressure)
    # 終了したプロセスの使用記録も1日1回の掃引で回収する。間隔内の起動で走査しないことは掃引全体の契約である。
    with contextlib.suppress(OSError):
        plugin_root_leases.sweep_plugin_root_records()
    protected_sessions = {
        area
        for identifier in session_registry.pending_session_ids()
        if (area := creation.find_session_temp(identifier)) is not None
    }
    deleted: list[pathlib.Path] = []
    failed: list[tuple[pathlib.Path, str]] = []
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
        try:
            if _examine(state_root, child, metadata, cutoff_ns, now_ns, protected_sessions):
                deleted.append(child)
        except (OSError, ManagedTempError) as error:
            if os.path.lexists(child):
                failed.append((child, str(error)))
    pressure = _reclaim_for_space(state_root, temp_root, now_ns, protected_sessions, deleted, failed)
    with contextlib.suppress(OSError):
        _sweep_legacy_state(state_root)
    _save_sweep_time(state_root, now_ns)
    return SweepResult(deleted, tuple(failed), pressure)

"""一時root直下の期限切れの対象の自動掃引と、旧版が残した状態の個別回収。

掃引は照会・原稿表示を除き、`atk`の共通起動から呼ばれる。
日次掃引の終了時刻は状態rootの`.sweep/last-sweep`に記録する。
前回から1日未満でディスクの空きに余裕がある起動は、一覧も取らずに戻る。
"""

from __future__ import annotations

import contextlib
import datetime
import enum
import functools
import hashlib
import json
import os
import pathlib
import re
import secrets
import shutil
import stat
import typing

import psutil

from agent_toolkit._atk.managed_temp import creation, paths, permissions, resources
from agent_toolkit._atk.managed_temp.cleanup_times import CleanupTimes, update_time
from agent_toolkit._atk.managed_temp.errors import ManagedTempError
from agent_toolkit._atk.managed_temp.removal import plugin_root_in_use, remove_tree
from agent_toolkit._atk.managed_temp.sweep_lock import sweep_access
from agent_toolkit._common import file_lock, plugin_root_leases, private_directory, process_identity, registry_index

_RECORD_NAME = ".sweep/last-sweep"
_PRESSURE_RECORD_NAME = ".sweep/last-pressure"
_PRESSURE_INTERVAL_NS = 300 * 1_000_000_000
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
MIN_FREE_RATIO_ENV = "AGENT_TOOLKIT_MANAGED_TEMP_MIN_FREE_RATIO"
"""空きの下限（容量比）を上書きする環境変数。

pytestが起動する子プロセスの`atk`は実ディスクの空きを見て回収を始めるため、同一プロセスの差し替えでは隔離できない。
試験基盤が`0`を設定して子へ継承させ、空きによる回収を止める。
"""
OWNER_FILE = ".owner-process"
"""作成プロセスの識別を記録するファイル名。長時間書き込まない実行中の領域を、最終更新の猶予だけでは使用中と区別できないため残す。"""
_TOP_RECLAIMED = 5
_PRESSURE_GRACE_NS = 3600 * _NANOSECONDS_PER_SECOND
"""空きの不足で回収する際に残す、配下を含む最終更新からの猶予。

作成直後の領域は、実行中のコマンドが書き込む途中でもセッション登録や使用記録をまだ持たず、使用中と区別できない。
これを回収すると、空きが不足した起動と同時に動く別のコマンドの記録を消して失敗させる。
"""
_LEGACY_REGISTRY_SUFFIX = ".json"
_LEGACY_CLEANUP_LOCK_SUFFIX = ".json.cleanup.lock"


class SweepResult(typing.NamedTuple):
    """`sweep_managed_temp`の結果。"""

    deleted: list[pathlib.Path]
    """自動削除したパス。"""
    failed: tuple[tuple[pathlib.Path, str], ...]
    """再生成物や期限切れの対象の回収で対処を要する失敗とその理由。次の掃引で再試行する。"""
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
        return self.free_after < self.total * _min_free_ratio()


def _sweep_due(
    state_root: pathlib.Path, now_ns: int, *, record_name: str = _RECORD_NAME, interval_ns: int = _SWEEP_INTERVAL_NS
) -> bool:
    """前回の掃引から間隔が過ぎたかを返す。記録の欠落・破損と、未来の時刻では真を返す。"""
    try:
        last_ns = int((state_root / record_name).read_text(encoding="ascii"))
    except OSError, ValueError:
        return True
    return not 0 <= now_ns - last_ns < interval_ns


def _save_sweep_time(state_root: pathlib.Path, now_ns: int, *, record_name: str = _RECORD_NAME) -> None:
    """掃引を終えた時刻を置き換える。書けない場合は次回の起動が掃引するだけなので、失敗を報告しない。"""
    record = state_root / record_name
    temporary = record.with_name(f".last-sweep-{secrets.token_hex(8)}.tmp")
    try:
        record.parent.mkdir(mode=private_directory.MODE, exist_ok=True)
        temporary.write_text(f"{now_ns}\n", encoding="ascii")
        os.replace(temporary, record)
    except OSError:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def _disk_usage(path: pathlib.Path) -> typing.Any:
    """空きの観測点。テストが実ディスクの空きから掃引を隔離できるよう、標準関数を1箇所で包む。"""
    return shutil.disk_usage(path)


def _allocated_bytes(metadata: os.stat_result) -> int:
    blocks = getattr(metadata, "st_blocks", None)
    return metadata.st_size if blocks is None else blocks * 512


def _tree_bytes(path: pathlib.Path) -> int:
    """対象の実使用量（バイト）を、リンクを辿らずに数える。数えられない項目は0として扱う。"""
    total = 0
    pending = [path]
    while pending:
        current = pending.pop()
        try:
            metadata = current.lstat()
            total += _allocated_bytes(metadata)
            if stat.S_ISDIR(metadata.st_mode) and not paths.is_link(metadata):
                with os.scandir(current) as children:
                    pending.extend(current / child.name for child in children)
        except OSError:
            continue
    return total


def _now_ns(now: datetime.datetime) -> int:
    reference = now if now.tzinfo is not None else now.astimezone()
    return int(reference.timestamp() * _NANOSECONDS_PER_SECOND)


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


def _inspect_tree(path: pathlib.Path, now_ns: int, times: CleanupTimes) -> tuple[int, bool, int]:
    """領域自身と配下の最終更新時刻（ナノ秒）と、登録済みworktreeの有無、実使用量を返す。リンクは辿らない。

    配下を含む最終更新を使うのは、中断後に再開した領域を古いと誤判定しないためである。
    記述子で辿れる環境では親の記述子からの相対名で走査し、連結長がPATH_MAXを超える木も調べる。
    """
    metadata = path.lstat()
    if _DESCRIPTOR_WALK:
        with permissions.directory(path) as opened:
            return _inspect_under(opened, metadata, path, path, now_ns, times)
    latest = times.activity(path, metadata, now_ns)
    size = _allocated_bytes(metadata)
    registered = False
    pending = [path]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as children:
            for child in children:
                metadata = child.stat(follow_symlinks=False)
                stamp = (
                    times.activity(pathlib.Path(child.path), metadata, now_ns)
                    if stat.S_ISDIR(metadata.st_mode) and not paths.is_link(metadata)
                    else update_time(metadata, now_ns)
                )
                latest = max(latest, stamp)
                size += _allocated_bytes(metadata)
                if child.name == ".git" and stat.S_ISREG(metadata.st_mode):
                    registered |= _registered_worktree(pathlib.Path(child.path), path)
                if stat.S_ISDIR(metadata.st_mode) and not paths.is_link(metadata):
                    pending.append(pathlib.Path(child.path))
    return latest, registered, size


def _inspect_under(
    directory: int, metadata: os.stat_result, current: pathlib.Path, area: pathlib.Path, now_ns: int, times: CleanupTimes
) -> tuple[int, bool, int]:
    """記述子が指すディレクトリの配下を相対名で辿り、`_inspect_tree`と同じ値を返す。"""
    size = _allocated_bytes(metadata)
    registered = False
    with os.scandir(directory) as children:
        listed = list(children)
    latest = times.activity(current, metadata, now_ns, children=lambda: {entry.name: entry.inode() for entry in listed})
    for child in listed:
        metadata = child.stat(follow_symlinks=False)
        child_path = current / child.name
        if child.name == ".git" and stat.S_ISREG(metadata.st_mode):
            registered |= _registered_worktree(child_path, area)
        if stat.S_ISDIR(metadata.st_mode) and not paths.is_link(metadata):
            with permissions.directory(child.name, dir_fd=directory, expected=metadata, display_path=child_path) as nested:
                nested_latest, nested_registered, nested_size = _inspect_under(
                    nested, metadata, child_path, area, now_ns, times
                )
            latest = max(latest, nested_latest)
            registered |= nested_registered
            size += nested_size
        else:
            latest = max(latest, update_time(metadata, now_ns))
            size += _allocated_bytes(metadata)
    return latest, registered, size


def _legacy_lock_path(state_root: pathlib.Path, path: pathlib.Path) -> pathlib.Path:
    """旧版が対象の回収中に保持するcleanupロックのパスを返す。"""
    return state_root / f"{hashlib.sha256(os.fsencode(path)).hexdigest()}{_LEGACY_CLEANUP_LOCK_SUFFIX}"


def _legacy_marker(area: pathlib.Path) -> dict[str, typing.Any] | None:
    """不在だけをNoneとする。読取不能なら削除に使う旧版のロックも特定できない。"""
    marker = area / paths.LEGACY_MARKER_NAME
    try:
        metadata = marker.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or paths.is_link(metadata):
        raise ManagedTempError(f"旧版の管理記録が通常ファイルではない: {marker}")
    try:
        record = json.loads(marker.read_text(encoding="utf-8"))
    except (UnicodeError, ValueError) as error:
        raise ManagedTempError(f"旧版の管理記録を読めない: {marker}: {error}") from error
    if not isinstance(record, dict):
        raise ManagedTempError(f"旧版の管理記録がオブジェクトではない: {marker}")
    return record


@contextlib.contextmanager
def _try_lock(path: pathlib.Path) -> typing.Generator[bool]:
    """競合時は待たずに偽を渡す。旧版の状態を個別に扱う場合は開けない対象も次回へ残す。"""
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


def _remove_legacy_area(state_root: pathlib.Path, area: pathlib.Path, marker: dict[str, typing.Any]) -> bool:
    """旧marker付きの領域を、旧版のcleanupロックを待たずに取れた場合だけ削除する。取れなければ偽を返す。"""
    recorded = marker.get("path")
    original = pathlib.Path(recorded) if isinstance(recorded, str) and pathlib.Path(recorded).is_absolute() else area
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
    for reference, target in _legacy_session_references(state_root, skip_unreadable=True):
        if os.path.lexists(target):
            continue
        lock = reference.with_suffix(".lock")
        with _try_lock(lock) as locked:
            if locked:
                reference.unlink(missing_ok=True)
        if locked:
            _unlink_released_lock(lock)


def _legacy_session_references(state_root: pathlib.Path, *, skip_unreadable: bool) -> typing.Iterator[tuple[pathlib.Path, str]]:
    """旧版で会話に割り当てた領域は任意のprefixを持つため、名前ではなく旧参照を読む。"""
    sessions = state_root / ".sessions"
    try:
        with os.scandir(sessions) as entries:
            references = sorted(sessions / entry.name for entry in entries if entry.is_file(follow_symlinks=False))
    except FileNotFoundError:
        return
    for reference in references:
        if "." in reference.name:
            continue
        try:
            record = json.loads(reference.read_text(encoding="utf-8"))
        except FileNotFoundError, UnicodeError, ValueError:
            continue
        except OSError:
            if skip_unreadable:
                continue
            raise
        target = record.get("path") if isinstance(record, dict) else None
        if isinstance(target, str):
            yield reference, target


def _sweep_entries(temp_root: pathlib.Path, state_root: pathlib.Path) -> list[pathlib.Path]:
    children = sorted(temp_root.iterdir())
    try:
        separate = not temp_root.samefile(state_root)
    except FileNotFoundError:
        # 一度も状態を書けないまま満杯になった場合、一時rootには回収対象だけがある。
        return children
    if separate:
        return children
    # Windowsでは一時領域も状態もLOCALAPPDATA配下に置く。旧参照・登録簿は割当先の有無で別途回収する。
    return [
        child
        for child in children
        if child.name not in {".sweep", ".sessions", ".sweep-schedule"}
        and not re.fullmatch(r"[0-9a-f]{64}\.json(?:\.cleanup\.lock|\.consuming-.+)?", child.name)
    ]


def _inspect_entry(child: pathlib.Path, metadata: os.stat_result, now_ns: int, times: CleanupTimes) -> tuple[int, bool, int]:
    if stat.S_ISDIR(metadata.st_mode):
        return _inspect_tree(child, now_ns, times)
    return update_time(metadata, now_ns), False, _allocated_bytes(metadata)


def _examine(
    state_root: pathlib.Path,
    child: pathlib.Path,
    metadata: os.stat_result,
    cutoff_ns: int,
    now_ns: int,
    protected_sessions: set[pathlib.Path],
    times: CleanupTimes,
    deleted_children: list[pathlib.Path],
    *,
    inspected: tuple[int, bool, int] | None = None,
) -> bool:
    """1件を調べ、削除した場合は真を返す。日次掃引ではworktreeの外の兄弟も期限で回収する。"""
    latest, has_git, _size = inspected if inspected is not None else _inspect_entry(child, metadata, now_ns, times)
    if latest >= cutoff_ns and not has_git:
        return False
    if child in protected_sessions or plugin_root_in_use(child) or has_live_owner(child):
        return False
    if has_git:
        # 登録worktree自身は木全体を保護し、包含する親では無関係な兄弟だけを期限判定する。
        with permissions.directory(child) if _DESCRIPTOR_WALK else contextlib.nullcontext():
            if _registered_worktree(child / ".git", child):
                return False
            for nested in child.iterdir():
                nested_metadata = nested.lstat()
                if paths.is_link(nested_metadata):
                    continue
                removed = _examine(
                    state_root, nested, nested_metadata, cutoff_ns, now_ns, protected_sessions, times, deleted_children
                )
                if removed:
                    deleted_children.append(nested)
        return False
    return _remove(state_root, child, metadata)


def _remove(state_root: pathlib.Path, child: pathlib.Path, metadata: os.stat_result) -> bool:
    """回収可と判定済みの1件を削除し、削除した場合は真を返す。"""
    if stat.S_ISDIR(metadata.st_mode):
        with permissions.directory(child, expected=metadata) if _DESCRIPTOR_WALK else contextlib.nullcontext():
            marker = _legacy_marker(child)
        if marker is not None:
            return _remove_legacy_area(state_root, child, marker)
    remove_tree(child)
    return True


def owner_token() -> str:
    """作成プロセスの識別（PIDと起動世代）を返す。PID再利用で別のプロセスを作成者と誤認しないため、起動世代を含める。"""
    return f"{os.getpid()} {process_identity.start_id(psutil.Process()) or ''}"


def owner_alive(token: str) -> bool:
    """記録した作成プロセスが同じ世代のまま実行中かを返す。判定できない場合は実行中として残す。"""
    pid_text, _, saved_id = token.strip().partition(" ")
    try:
        process = psutil.Process(int(pid_text))
        return process.is_running() and (not saved_id or process_identity.start_id(process) == saved_id)
    except psutil.NoSuchProcess:
        return False
    except ValueError, psutil.Error, OSError:
        return True


def has_live_owner(area: pathlib.Path) -> bool:
    """領域か直下の子が記録した作成プロセスが実行中、または記録を読めない場合に真を返す。

    セッションの領域は`atk-command-*`などの子に記録を持つため、直下の子まで調べる。
    """
    if not area.is_dir():
        return False
    # 期限走査と同じ所有・同一性検証付きの権限回復を使い、保持時は元modeへ戻す。
    with permissions.directory(area) if _DESCRIPTOR_WALK else contextlib.nullcontext():
        candidates = [area]
        with os.scandir(area) as children:
            candidates.extend(area / entry.name for entry in children if entry.is_dir(follow_symlinks=False))
        for candidate in candidates:
            with permissions.directory(candidate) if _DESCRIPTOR_WALK else contextlib.nullcontext():
                try:
                    token = (candidate / OWNER_FILE).read_text(encoding="ascii")
                except FileNotFoundError:
                    continue
                except OSError, UnicodeDecodeError:
                    return True
                if owner_alive(token):
                    return True
    return False


def _min_free_ratio() -> float:
    """空きの下限（容量比）を返す。環境変数が不正な値なら容量の10%を使う。"""
    try:
        return float(os.environ.get(MIN_FREE_RATIO_ENV, _MIN_FREE_RATIO))
    except ValueError:
        return _MIN_FREE_RATIO


def _below_floor(temp_root: pathlib.Path) -> bool:
    """一時rootを含むFSの空きが容量の10%を下回るかを返す。観測できない場合は偽を返す。"""
    try:
        usage = _disk_usage(temp_root)
    except OSError:
        return False
    return usage.free < usage.total * _min_free_ratio()


class _ReclaimScope(enum.Enum):
    EXPIRED = enum.auto()
    TAGGED_CACHE = enum.auto()
    CONTENTS = enum.auto()
    UNCLASSIFIED = enum.auto()


class _PressureCandidate(typing.NamedTuple):
    latest: int
    path: pathlib.Path
    metadata: os.stat_result
    scope: _ReclaimScope
    size: int


def _clean_regenerable(
    area: pathlib.Path,
    references: typing.Callable[[], list[pathlib.Path] | None],
    failed: list[tuple[pathlib.Path, str]],
    last_use_ns: int | None,
    times: CleanupTimes,
    now_ns: int,
) -> list[pathlib.Path]:
    cleaned = resources.cleanup_regenerable(
        area,
        references=references,
        observe_parent=(
            functools.partial(times.observe_parent, last_use_ns=last_use_ns, now_ns=now_ns) if last_use_ns is not None else None
        ),
    )
    failed.extend((pathlib.Path(row["path"]), row["reason"]) for row in cleaned["retained"] if row.get("failed"))
    resources.log_retained(cleaned["retained"])
    return [pathlib.Path(path) for path in cleaned["removed"]]


def _reclaim_space(
    state_root: pathlib.Path,
    temp_root: pathlib.Path,
    now_ns: int,
    protected_sessions: set[pathlib.Path],
    deleted: list[pathlib.Path],
    failed: list[tuple[pathlib.Path, str]],
    *,
    cutoff_ns: int,
    after_daily_sweep: bool,
    times: CleanupTimes,
) -> SpacePressure | None:
    """空きが下限を割る場合に限り、保持期間を待たず使用中でない対象を最終更新の古い順に回収する。

    使用中・保護セッション・登録済みworktree・保持配布物・リンクは、期限の掃引と同じ判定で残す。
    停止中の会話も通常期限までは再開できる。期限内の会話では日次掃引と同じ判定で再生成物だけを回収する。
    """
    try:
        usage = _disk_usage(temp_root)
    except OSError:
        return None
    floor = usage.total * _min_free_ratio()
    if usage.free >= floor:
        return None
    # 失敗や中断でも次の起動による即時の全走査を避ける。
    _save_sweep_time(state_root, now_ns, record_name=_PRESSURE_RECORD_NAME)
    try:
        legacy_sessions = {pathlib.Path(target) for _, target in _legacy_session_references(state_root, skip_unreadable=False)}
    except OSError as error:
        failed.append((pathlib.Path(error.filename or state_root / ".sessions"), _failure_reason(error)))
        legacy_sessions = None
    references = functools.cache(resources.process_references)

    def candidate(child: pathlib.Path) -> _PressureCandidate | None:
        metadata = child.lstat()
        if paths.is_link(metadata) or child in protected_sessions or plugin_root_in_use(child):
            return None
        directory = stat.S_ISDIR(metadata.st_mode)
        with permissions.directory(child, expected=metadata) if directory and _DESCRIPTOR_WALK else contextlib.nullcontext():
            session = (
                legacy_sessions is None
                or child in legacy_sessions
                or (
                    directory
                    and (
                        child.name.startswith(f"{creation.SESSION_TEMP_PREFIX}-")
                        or isinstance((_legacy_marker(child) or {}).get("session_id"), str)
                    )
                )
            )
            if (session and after_daily_sweep) or has_live_owner(child):
                return None
            latest, has_git, size = _inspect_entry(child, metadata, now_ns, times)
            if has_git or latest >= now_ns - _PRESSURE_GRACE_NS:
                return None
            if latest < cutoff_ns:
                scope = _ReclaimScope.EXPIRED
            elif session:
                scope = _ReclaimScope.CONTENTS
            elif directory and resources.is_tagged_cache(child):
                if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
                    return None
                scope = _ReclaimScope.TAGGED_CACHE
            else:
                scope = _ReclaimScope.UNCLASSIFIED
        return _PressureCandidate(latest, child, metadata, scope, size)

    candidates: list[_PressureCandidate] = []
    for child in _sweep_entries(temp_root, state_root):
        try:
            if (item := candidate(child)) is not None:
                candidates.append(item)
        except FileNotFoundError:
            continue
        except (OSError, ManagedTempError) as error:
            failed.append((child, _failure_reason(error)))
    # 期限切れと再生成指定のある対象を先に回収する。期限内の会話の記録は、不足分の見積もりにも含めない。
    candidates.sort(key=lambda item: (item.scope == _ReclaimScope.UNCLASSIFIED, item.latest, item.path.name))
    unclassified_bytes = sum(item.size for item in candidates if item.scope == _ReclaimScope.UNCLASSIFIED)
    sizes: list[tuple[str, int]] = []
    free = usage.free
    for latest, child, metadata, scope, size in candidates:
        if free >= floor:
            break
        contents_only = scope == _ReclaimScope.CONTENTS
        if scope == _ReclaimScope.UNCLASSIFIED:
            contents_only = unclassified_bytes < floor - free
            unclassified_bytes -= size
        try:
            if contents_only and (after_daily_sweep or not stat.S_ISDIR(metadata.st_mode)):
                continue
            # 順位と不足分は見積もりに過ぎない。消す対象だけ、先行する回収の間に利用や用途が変わっていないか調べる。
            current = candidate(child)
            if (
                current is None
                or not os.path.samestat(metadata, current.metadata)
                or (current.latest, current.scope) != (latest, scope)
            ):
                continue
            if contents_only:
                removed = _clean_regenerable(child, references, failed, latest, times, now_ns)
                if not removed:
                    continue
                deleted.extend(removed)
                size = max(0, size - _tree_bytes(child))
            else:
                if scope == _ReclaimScope.TAGGED_CACHE:
                    observed = resources.process_references()
                    if observed is None or any(reference.is_relative_to(child) for reference in observed):
                        continue
                if not _remove(state_root, child, current.metadata):
                    continue
                deleted.append(child)
        except (OSError, ManagedTempError) as error:
            if os.path.lexists(child):
                failed.append((child, _failure_reason(error)))
            continue
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


def _failure_reason(error: OSError | ManagedTempError) -> str:
    """失敗理由を文字列で返す形式を保ち、回復操作を持つ例外の案内も受信側へ渡す。"""
    if isinstance(error, ManagedTempError):
        return f"{error}。次の操作: {error.next_action}"
    return str(error)


@plugin_root_leases.reuse_process_observation()
def sweep_managed_temp(*, now: datetime.datetime, max_age_days: int = paths.MAX_AGE_DAYS, force: bool = False) -> SweepResult:
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
        state_root = paths.state_root(create=False)
    except ManagedTempError as error:
        return SweepResult([], ((paths.temp_root_path(), _failure_reason(error)),))
    if not force and not _sweep_due(state_root, now_ns) and not _below_floor(temp_root):
        return SweepResult([], ())
    try:
        with sweep_access(temp_root, state_root) as locked:
            if not locked:
                return SweepResult([], ())
            return _sweep_locked(temp_root, state_root, now_ns, cutoff_ns, daily=force or _sweep_due(state_root, now_ns))
    except OSError as error:
        return SweepResult([], ((pathlib.Path(error.filename or state_root), _failure_reason(error)),))


def _sweep_locked(
    temp_root: pathlib.Path, state_root: pathlib.Path, now_ns: int, cutoff_ns: int, *, daily: bool
) -> SweepResult:
    pressure_due = _sweep_due(state_root, now_ns, record_name=_PRESSURE_RECORD_NAME, interval_ns=_PRESSURE_INTERVAL_NS)
    if not daily and not pressure_due:
        return SweepResult([], ())
    times = CleanupTimes(state_root, cutoff_ns)
    # 終了したプロセスの使用記録も1日1回の掃引で回収する。間隔内の起動で走査しないことは掃引全体の契約である。
    if daily:
        with contextlib.suppress(OSError):
            plugin_root_leases.sweep_plugin_root_records()
    protected_sessions = {
        area
        for identifier in registry_index.pending_session_ids()
        if (area := creation.find_session_temp(identifier)) is not None
    }
    deleted: list[pathlib.Path] = []
    failed: list[tuple[pathlib.Path, str]] = []
    if daily:
        _daily_sweep(temp_root, state_root, now_ns, cutoff_ns, protected_sessions, times, deleted, failed)
    pressure = None
    if pressure_due:
        pressure = _reclaim_space(
            state_root,
            temp_root,
            now_ns,
            protected_sessions,
            deleted,
            failed,
            cutoff_ns=cutoff_ns,
            after_daily_sweep=daily,
            times=times,
        )
    if failed and deleted:
        # 再生成物の回収に失敗しても、続く期限・空き容量の掃引で親ごと回収できれば対処は不要。
        removed = set(deleted)
        failed = [(path, reason) for path, reason in failed if path not in removed and removed.isdisjoint(path.parents)]
    if daily:
        with contextlib.suppress(OSError):
            _sweep_legacy_state(state_root)
        _save_sweep_time(state_root, now_ns)
    return SweepResult(deleted, tuple(failed), pressure)


def _daily_sweep(
    temp_root: pathlib.Path,
    state_root: pathlib.Path,
    now_ns: int,
    cutoff_ns: int,
    protected_sessions: set[pathlib.Path],
    times: CleanupTimes,
    deleted: list[pathlib.Path],
    failed: list[tuple[pathlib.Path, str]],
) -> None:
    references = functools.cache(resources.process_references)
    for child in _sweep_entries(temp_root, state_root):
        try:
            metadata = child.lstat()
        except FileNotFoundError:
            continue
        if child in protected_sessions or paths.is_link(metadata):
            continue
        latest = None
        try:
            inspected = _inspect_entry(child, metadata, now_ns, times)
            latest = inspected[0]
            if _examine(
                state_root,
                child,
                metadata,
                cutoff_ns,
                now_ns,
                protected_sessions,
                times,
                deleted,
                inspected=inspected,
            ):
                deleted.append(child)
                continue
        except (OSError, ManagedTempError) as error:
            if os.path.lexists(child):
                failed.append((child, _failure_reason(error)))
        # 全体の走査・削除が失敗しても、別に確認できる未使用キャッシュは回収する。
        try:
            if (
                child.is_dir()
                and child not in protected_sessions
                and not plugin_root_in_use(child)
                and not has_live_owner(child)
            ):
                deleted.extend(_clean_regenerable(child, references, failed, latest, times, now_ns))
        except (OSError, ManagedTempError) as error:
            if os.path.lexists(child):
                failed.append((child, _failure_reason(error)))

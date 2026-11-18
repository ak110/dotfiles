"""session登録簿と結果待ちの記録の全件から、子・保留・所属の判定に使う小さい派生値を引く。

登録簿のレコードは起動本文を含み、1件が数十KBになる。子の検索、保留の判定、作業ツリーの使用判定が
そのたびに全件を解析すると、仕事量が登録簿の総量（7日分で数千件・数十MB）に比例する。
本モジュールは各ファイルの識別（inode・更新時刻・大きさ）と派生値を状態ディレクトリへ保存し、
識別が変わらないファイルは解析し直さない。1回の照会の仕事量は、ファイル数だけのstatと変わったファイルの解析になる。

派生値は内容だけで決まる値に限り、プロセスの生存のように時刻で変わる判定は持たない。
生存で結果が変わるレコード（再開予約を持つもの）は、照会のたびに`session_registry.resolve`で解決し直す。
書き換えから間もないファイルは、更新時刻の刻みの中で識別が変わらずに内容だけ変わり得るため、保存しない。
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import pathlib
import time
from collections.abc import Callable, Iterable
from typing import Any

from agent_toolkit._common import session_launchers, session_layout, session_registry
from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._common.atomic_file import atomic_write

_INDEX_VERSION = 1
_RECENT_NS = 2_000_000_000
"""これより新しい更新時刻のファイルは、派生値を保存せず次回も解析する（ナノ秒）。"""
_PENDING_INTERRUPTIONS = frozenset({session_registry.OWNER_PROCESSES_EXITED, session_registry.OWNER_SERVER_STOPPED})
_PENDING_RUNS = frozenset({"running", "published"})


@dataclasses.dataclass(frozen=True)
class Entry:
    """登録簿のレコード1件の派生値。"""

    launcher: str | None
    """sessionを作成した時点の委譲元session。"""
    cwd: str | None
    """`resolve`の`resume_info.cwd`。再開情報を持たない場合は`None`。"""
    server_instances: tuple[str, ...]
    """記録が参照する所有サーバーの起動単位（中断turnの履歴を含む）。"""
    readable: bool
    """`resolve`が`UNREADABLE`と解決しない。"""
    pending: bool
    """プロセスの生存によらず保留（実行中、または所有側の喪失による中断）と解決する。"""
    claimed: bool
    """再開予約を持ち、予約したプロセスの生存によって実行中と解決し得る。"""
    released: bool
    """所有側が解放したレコード（`resolve`は再開予約が生存しない限り`RELEASED`と解決する）。"""


def entries(state_root: pathlib.Path | None = None) -> dict[str, Entry]:
    """登録簿の全レコード（`*.json`）の派生値を、拡張子を除くファイル名ごとに返す。

    識別子の形式に合わない名前のレコードも含め、`readable`を偽とする（`resolve`はその名前を受け付けない）。
    """
    state_root = _state_paths.state_dir() if state_root is None else state_root
    listed = _listed(session_registry.registry_directory(state_root), depth=1, valid=bool)
    return _refreshed(
        state_root / "agents-server" / "sessions-index.json", listed, _derive_record, _encode_entry, _decode_entry
    )


def child_session_ids(launcher_session_id: str, *, state_root: pathlib.Path | None = None) -> tuple[str, ...]:
    """保存済みの直接の親子関係から、同じ親が起動した子の識別子を返す。"""
    if not session_layout.valid_session_id(launcher_session_id):
        raise ValueError(f"invalid session id: {launcher_session_id!r}")
    return tuple(
        sorted(
            sid
            for sid, entry in entries(state_root).items()
            if session_layout.valid_session_id(sid) and entry.launcher == launcher_session_id
        )
    )


def descendant_session_ids(roots: Iterable[str], *, state_root: pathlib.Path | None = None) -> set[str]:
    """`roots`と、その委譲元の連鎖で`roots`へ至る全sessionの識別子を返す。"""
    children: dict[str, list[str]] = {}
    for sid, entry in entries(state_root).items():
        if entry.launcher is not None:
            children.setdefault(entry.launcher, []).append(sid)
    found = set(roots)
    queue = list(found)
    while queue:
        for child in children.get(queue.pop(), ()):
            if child not in found:
                found.add(child)
                queue.append(child)
    return found


def pending_session_ids(state_root: pathlib.Path | None = None) -> set[str]:
    """復旧を要する会話と未回収結果、およびそれらの依頼を保持する親の識別子を返す。"""
    state_root = _state_paths.state_dir() if state_root is None else state_root
    records = {sid: entry for sid, entry in entries(state_root).items() if session_layout.valid_session_id(sid)}
    pending = {sid for sid, entry in records.items() if entry.pending}
    for sid, entry in records.items():
        if entry.claimed and not entry.pending:
            resolution = session_registry.resolve(sid, state_root=state_root)
            if resolution.state is session_registry.Resolution.RUNNING or resolution.interruption in _PENDING_INTERRUPTIONS:
                pending.add(sid)
    for root in session_layout.list_root_session_ids(state_root, strict=True):
        with contextlib.suppress(FileNotFoundError), os.scandir(session_layout.results_directory(root, state_root)) as listed:
            pending.update(
                name[: -len(".json")]
                for item in listed
                if (name := item.name).endswith(".json") and session_layout.valid_session_id(name[: -len(".json")])
            )
    for targets in _wait_targets(state_root).values():
        pending.update(targets)
    ancestors = list(pending)
    while ancestors:
        entry = records.get(ancestors.pop())
        parent = entry.launcher if entry is not None else None
        if parent is not None and parent not in pending:
            pending.add(parent)
            ancestors.append(parent)
    return pending


def recorded_server_instances(state_root: pathlib.Path | None = None) -> set[str]:
    """登録簿の全記録が参照する起動単位を返す。読めない記録は除く。"""
    return {instance for entry in entries(state_root).values() for instance in entry.server_instances}


def _wait_targets(state_root: pathlib.Path) -> dict[str, tuple[str, ...]]:
    """全rootの結果待ちの記録ごとに、未回収として保持する対象の識別子を返す。"""

    def listed() -> Iterable[tuple[str, pathlib.Path, list[int]]]:
        for root in session_layout.list_root_session_ids(state_root, strict=True):
            directory = session_layout.status_directory(root, state_root) / "wait-results"
            for name, path, identity in _listed(directory, depth=2, valid=bool):
                yield f"{root}/{name}", path, identity

    return _refreshed(
        state_root / "agents-server" / "wait-results-index.json",
        listed(),
        lambda _key, path: _derive_wait(path),
        lambda targets: [list(targets)],
        lambda fields: tuple(fields[0]),
    )


def _refreshed[T](
    index_path: pathlib.Path,
    listed: Iterable[tuple[str, pathlib.Path, list[int]]],
    derive: Callable[[str, pathlib.Path], T | None],
    encode: Callable[[T], list[Any]],
    decode: Callable[[list[Any]], T],
) -> dict[str, T]:
    """列挙したファイルの派生値を返す。識別が保存時と同じファイルは解析せず、`index_path`の値を使う。

    `derive`が`None`を返したファイル（列挙の後に消えたもの）は結果から除く。
    """
    saved = _load(index_path)
    recent = time.time_ns() - _RECENT_NS
    values: dict[str, T] = {}
    kept: dict[str, list[Any]] = {}
    changed = False
    for key, path, identity in listed:
        value = _decoded(saved.get(key), identity, decode)
        if value is None:
            changed = True
            if (value := derive(key, path)) is None:
                continue
        values[key] = value
        if identity[1] < recent:
            kept[key] = [*identity, *encode(value)]
    if changed or saved.keys() != kept.keys():
        # 索引は再生成できる派生値であり、書けない場合も照会の結果は変わらない。
        with contextlib.suppress(OSError):
            atomic_write(index_path, json.dumps({"version": _INDEX_VERSION, "entries": kept}, ensure_ascii=False))
    return values


def _decoded[T](cached: object, identity: list[int], decode: Callable[[list[Any]], T]) -> T | None:
    """保存された値のうち、識別が一致するものを復元する。一致しない値と形の合わない値は`None`を返す。"""
    if not isinstance(cached, list) or cached[:3] != identity:
        return None
    try:
        return decode(cached[3:])
    except TypeError, ValueError:
        # 別の版が保存した形の合わない値は、保存されていなかったものとして解析し直す。
        return None


def _encode_entry(entry: Entry) -> list[Any]:
    fields = dataclasses.astuple(entry)
    return [*fields[:2], list(entry.server_instances), *fields[3:]]


def _decode_entry(fields: list[Any]) -> Entry:
    launcher, cwd, instances, *flags = fields
    return Entry(launcher, cwd, tuple(instances), *flags)


def _load(path: pathlib.Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_bytes())
    except OSError, ValueError:
        return {}
    if (
        not isinstance(payload, dict)
        or payload.get("version") != _INDEX_VERSION
        or not isinstance(payload.get("entries"), dict)
    ):
        return {}
    return payload["entries"]


def _listed(
    directory: pathlib.Path, *, depth: int, valid: Callable[[str], bool]
) -> Iterable[tuple[str, pathlib.Path, list[int]]]:
    """`directory`の`depth`階層目で、拡張子を除く名前が`valid`を満たす`.json`を、名前・パス・識別で返す。

    識別はinode・更新時刻（ナノ秒）・大きさの組とする。名前は`depth`が2なら`<親>/<拡張子を除く名前>`とする。
    """
    try:
        with os.scandir(directory) as listed:
            items = list(listed)
    except FileNotFoundError, NotADirectoryError:
        return
    for item in items:
        if depth > 1:
            if item.is_dir(follow_symlinks=False):
                for name, path, identity in _listed(pathlib.Path(item.path), depth=depth - 1, valid=valid):
                    yield f"{item.name}/{name}", path, identity
            continue
        stem = item.name[: -len(".json")]
        if not item.name.endswith(".json") or not valid(stem):
            continue
        try:
            stat = item.stat(follow_symlinks=False)
        except FileNotFoundError:
            continue
        yield stem, pathlib.Path(item.path), [stat.st_ino, stat.st_mtime_ns, stat.st_size]


def _derive_record(session_id: str, path: pathlib.Path) -> Entry | None:
    """レコード1件を解析して派生値を返す。列挙の後に消えた場合は`None`を返す。"""
    try:
        payload: Any = json.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    except OSError, ValueError:
        payload = None
    if not isinstance(payload, dict):
        return Entry(None, None, (), readable=False, pending=False, claimed=False, released=False)
    resolution = session_registry.resolve_payload(session_id, payload)
    readable = session_layout.valid_session_id(session_id) and resolution.state is not session_registry.Resolution.UNREADABLE
    return Entry(
        # 委譲元と起動単位は、解決できないレコードからも読む（親子の連鎖と掃引の保護を途切れさせない）。
        launcher=session_launchers.valid_launcher(payload.get(session_registry.LAUNCHER_KEY)),
        cwd=resolution.resume_info.cwd if resolution.resume_info is not None else None,
        server_instances=tuple(sorted(session_registry.payload_server_instances(payload))),
        readable=readable,
        pending=readable
        and payload.get("version") == 2
        and (not payload["terminal"] or payload.get("interruption") in _PENDING_INTERRUPTIONS),
        claimed=isinstance(payload.get("resume_claim"), dict),
        released=readable and resolution.state is session_registry.Resolution.RELEASED,
    )


def _derive_wait(path: pathlib.Path) -> tuple[str, ...]:
    """結果待ちの記録1件が、未回収として保持する対象の識別子を返す。"""
    try:
        run: Any = json.loads(path.read_bytes())
    except OSError, ValueError:
        return ()
    if not isinstance(run, dict) or run.get("status") not in _PENDING_RUNS or not isinstance(run.get("targets"), list):
        return ()
    return tuple(target for target in run["targets"] if isinstance(target, str) and session_layout.valid_session_id(target))

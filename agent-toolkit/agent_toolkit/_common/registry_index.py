from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import pathlib
from collections.abc import Callable, Iterable
from typing import Any

from agent_toolkit._common import derived_index, session_launchers, session_layout, session_registry
from agent_toolkit._common import state_paths as _state_paths

_PENDING_INTERRUPTIONS = frozenset({session_registry.OWNER_PROCESSES_EXITED, session_registry.OWNER_SERVER_STOPPED})
_PENDING_RUNS = frozenset({"running", "published"})


@dataclasses.dataclass(frozen=True)
class Entry:
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
    state_root = _state_paths.state_dir() if state_root is None else state_root
    listed = _listed(session_registry.registry_directory(state_root), depth=1, valid=bool)
    return derived_index.refreshed(
        state_root / "agents-server" / "sessions-index.json",
        derived_index.code_version(__name__),
        listed,
        _derive_record,
        _encode_entry,
        _decode_entry,
    )


def child_session_ids(launcher_session_id: str, *, state_root: pathlib.Path | None = None) -> tuple[str, ...]:
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
                identifier
                for item in listed
                if (identifier := session_layout.result_session_id(pathlib.Path(item.name))) is not None
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


def result_collected(session_id: str, turn_seq: int, *, state_root: pathlib.Path | None = None) -> bool:
    """turn番号`turn_seq`の結果を公開した後に回収し、保留の対象にも残らないと観測できる時だけ真を返す。

    公開の印を持たない結果（所有側の記憶にだけある結果、所有側が直接受け渡した結果）は回収を確かめられないため偽とする。
    状態ディレクトリを走査できない場合は`OSError`を送出する。
    """
    state_root = _state_paths.state_dir() if state_root is None else state_root
    if session_id in pending_session_ids(state_root):
        return False
    prefix = f"{turn_seq}-"
    for root in session_layout.list_root_session_ids(state_root, strict=True):
        markers = session_layout.published_markers_directory(session_layout.results_directory(root, state_root), session_id)
        with contextlib.suppress(FileNotFoundError), os.scandir(markers) as listed:
            if any(item.name.startswith(prefix) for item in listed):
                return True
    return False


def recorded_server_instances(state_root: pathlib.Path | None = None) -> set[str]:
    return {instance for entry in entries(state_root).values() for instance in entry.server_instances}


def _wait_targets(state_root: pathlib.Path) -> dict[str, tuple[str, ...]]:

    def listed() -> Iterable[tuple[str, pathlib.Path, list[int]]]:
        for root in session_layout.list_root_session_ids(state_root, strict=True):
            directory = session_layout.status_directory(root, state_root) / "wait-results"
            for name, path, identity in _listed(directory, depth=2, valid=bool):
                yield f"{root}/{name}", path, identity

    return derived_index.refreshed(
        state_root / "agents-server" / "wait-results-index.json",
        derived_index.code_version(__name__),
        listed(),
        lambda _key, path: _derive_wait(path),
        lambda targets: [list(targets)],
        lambda fields: tuple(fields[0]),
    )


def _encode_entry(entry: Entry) -> list[Any]:
    fields = dataclasses.astuple(entry)
    return [*fields[:2], list(entry.server_instances), *fields[3:]]


def _decode_entry(fields: list[Any]) -> Entry:
    launcher, cwd, instances, *flags = fields
    return Entry(launcher, cwd, tuple(instances), *flags)


def _listed(
    directory: pathlib.Path, *, depth: int, valid: Callable[[str], bool]
) -> Iterable[tuple[str, pathlib.Path, list[int]]]:
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
        yield stem, pathlib.Path(item.path), derived_index.identity(stat)


def _derive_record(session_id: str, path: pathlib.Path) -> Entry | None:
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
    try:
        run: Any = json.loads(path.read_bytes())
    except OSError, ValueError:
        return ()
    if not isinstance(run, dict) or run.get("status") not in _PENDING_RUNS or not isinstance(run.get("targets"), list):
        return ()
    return tuple(target for target in run["targets"] if isinstance(target, str) and session_layout.valid_session_id(target))

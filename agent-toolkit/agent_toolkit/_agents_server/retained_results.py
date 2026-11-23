"""turn番号と終了時刻で区別した未回収結果の公開・読取・排他回収。"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import json
import logging
import pathlib
from collections.abc import Generator, Iterator
from typing import Any

from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.result_projection import (
    append_result_next_action,
    public_result,
    with_result_next_action,
)
from agent_toolkit._agents_server.shared_layout import result_session_id, results_directory, valid_session_id
from agent_toolkit._agents_server.turn_state import TERMINAL_STATUSES
from agent_toolkit._agents_server.wait_targets import release_wait_target
from agent_toolkit._common import session_registry
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.file_lock import acquire_lock, release_lock
from agent_toolkit._common.next_action import ActionableError

type ResultEnd = tuple[int, str]


def result_end(payload: dict[str, Any]) -> ResultEnd | None:
    seq, finalized = payload.get("turn_seq"), payload.get("finalized_at")
    return (seq, finalized) if isinstance(seq, int) and not isinstance(seq, bool) and isinstance(finalized, str) else None


def _suffix(end: ResultEnd) -> str:
    return f"{end[0]}-{hashlib.sha256(end[1].encode()).hexdigest()}"


def _published(path: pathlib.Path, end: ResultEnd) -> pathlib.Path:
    return path.parent / ".published" / path.stem / _suffix(end)


@contextlib.contextmanager
def _locked(path: pathlib.Path, *, blocking: bool = True) -> Generator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        try:
            acquire_lock(stream, blocking=blocking)
        except PermissionError as error:
            if blocking:
                raise
            # Windowsの非ブロック取得は他の所有者がいる場合にEACCESを返す。
            raise BlockingIOError(str(error)) from error
        try:
            yield
        finally:
            release_lock(stream)


class _UnreadableResult(ValueError):
    pass


def _read(path: pathlib.Path, *, allow_missing_status: bool = False) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError) as error:
        raise _UnreadableResult(f"{path}: {error}") from error
    if not isinstance(value, dict):
        raise _UnreadableResult(f"{path}: 最上位が辞書ではありません")
    if not isinstance(value.get("status"), str) and not (allow_missing_status and "status" not in value):
        raise _UnreadableResult(f"{path}: statusが文字列ではありません")
    return value


@functools.lru_cache(maxsize=128)
def _warn_unreadable(message: str) -> None:
    _LOG.warning("終了結果を読めないため原本を保持した: %s。権限と内容を確認してから再回収する", message)


def _entries(
    path: pathlib.Path,
    *,
    expected_turn_seq: int | None = None,
    end: ResultEnd | None = None,
    unreadable: list[str] | None = None,
) -> Iterator[tuple[pathlib.Path, dict[str, Any]]]:
    others = path.parent.glob(f"{path.stem}.*.json") if end is None else [path.with_suffix(f".{_suffix(end)}.json")]

    for candidate in (*others, path):
        try:
            payload = _read(candidate)
        except _UnreadableResult as error:
            message = str(error)
            if unreadable is not None:
                unreadable.append(message)
            else:
                _warn_unreadable(message)
            continue
        if payload is None or (expected_turn_seq is not None and payload.get("turn_seq") != expected_turn_seq):
            continue
        if end is None or result_end(payload) == end:
            yield candidate, payload


def mark_published(path: pathlib.Path, end: ResultEnd) -> None:
    marker = _published(path, end)
    if not marker.exists():
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()


def expose_legacy_slots(directory: pathlib.Path, owner: str, *, sessions: set[str] | None = None) -> None:
    for identifier in {result_session_id(entry) for entry in directory.glob("*.*.json")}:
        if identifier is None or (sessions is not None and identifier not in sessions):
            continue
        path = directory / f"{identifier}.json"
        if path.exists():
            continue
        with contextlib.ExitStack() as locks:
            try:
                locks.enter_context(_locked(directory / f".{identifier}.claim.lock", blocking=False))
            except BlockingIOError, PermissionError:
                # 回収中のCLIを待たず、次の保存で旧名を補う。
                continue
            with _locked(path.with_suffix(".publish.lock")):
                if not path.exists():
                    owned = (item for item in _entries(path) if _owned(item[1], owner))
                    oldest = min(owned, key=lambda item: result_end(item[1]) or (-1, ""), default=None)
                    if oldest is not None:
                        oldest[0].rename(path)


def legacy_publication(path: pathlib.Path, *, missing: ResultEnd | None = None) -> ResultEnd | None:
    try:
        payload = _read(path, allow_missing_status=True)
    except _UnreadableResult as error:
        _warn_unreadable(str(error))
        return None
    return result_end(payload) if payload is not None else missing


def result_state(path: pathlib.Path, end: ResultEnd) -> str:
    published = _published(path, end).exists()
    unreadable: list[str] = []
    if next(_entries(path, end=end, unreadable=unreadable), None) is not None:
        if not published:
            mark_published(path, end)
        return "published"
    if unreadable and (published or path.with_suffix(f".{_suffix(end)}.json").exists()):
        return "unreadable"

    return "consumed" if published else "unpublished"


def _owned(payload: dict[str, Any], owner: str) -> bool:
    recorded = payload.get("owner_status_file")
    return recorded == owner or (recorded is None and owner == "root.json")


def publish_result(path: pathlib.Path, payload: dict[str, Any]) -> None:
    end = result_end(payload)
    assert end is not None
    with _locked(path.with_suffix(".publish.lock")):
        if _published(path, end).exists():
            return
        if next(_entries(path, end=end), None) is None:
            destination = path.with_suffix(f".{_suffix(end)}.json") if path.exists() else path
            if destination.exists():
                raise _UnreadableResult(f"{destination}: 既存の原本を上書きしません")
            atomic_write(destination, json.dumps(payload, ensure_ascii=False) + "\n")
        mark_published(path, end)


def discard_result(path: pathlib.Path, owner: str, end: ResultEnd) -> bool:
    with _locked(path.parent / f".{path.stem}.claim.lock"), _locked(path.with_suffix(".publish.lock")):
        unreadable: list[str] = []
        entry = next(_entries(path, end=end, unreadable=unreadable), None)
        if entry is None and (unreadable or _published(path, end).exists()):
            return False
        if entry is not None and not _owned(entry[1], owner):
            return False
        mark_published(path, end)
        if entry is not None:
            entry[0].unlink()
        return True


def discard_stopped_session(
    root_session_id: str,
    session_id: str,
    owner: str,
    *,
    through_turn: int,
    finalized_at: str | None,
    state_root: pathlib.Path | None,
    expected_owner: tuple[str | None, int | None],
) -> bool:
    if (
        not valid_session_id(root_session_id)
        or not valid_session_id(session_id)
        or not (owner.endswith(".json") and valid_session_id(owner[:-5]))
    ):
        raise ValueError("invalid stopped session identity")
    path = results_directory(root_session_id, state_root) / f"{session_id}.json"
    with (
        _locked(path.parent / f".{session_id}.claim.lock"),
        _locked(path.with_suffix(".publish.lock")),
        session_registry.registry_lock(session_id),
    ):
        current = session_registry.resolve(session_id)
        still_stopped = current.state is session_registry.Resolution.RELEASED and expected_owner == (
            current.server_instance,
            current.recorded_turn_seq,
        )
        if still_stopped:
            release_wait_target(root_session_id, owner, session_id, state_root)
        forget_session = still_stopped
        for source, payload in _entries(path):
            if result_session_id(source) != session_id or not _owned(payload, owner):
                continue
            seq = payload.get("turn_seq")
            if isinstance(seq, int) and seq > through_turn:
                forget_session = False
                continue
            end = result_end(payload)

            if not still_stopped and (end is None or (seq == through_turn and end[1] != finalized_at)):
                continue
            if end is not None:
                mark_published(path, end)
            source.unlink()
            _LOG.info("result_deleted session_id=%s writer=%s collector=stop", path.stem, owner)
        if finalized_at is not None:
            mark_published(path, (through_turn, finalized_at))
        return forget_session


class ResultBeingCollected(ActionableError):
    pass


async def take_for_response(
    root_session_id: str,
    session_id: str,
    owner: str,
    *,
    collector: str,
    state_root: pathlib.Path | None,
    expected_turn_seq: int | None,
    expected_finalized_at: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    if not valid_session_id(session_id):
        raise ValueError(f"invalid session_id: {session_id}")
    path = results_directory(root_session_id, state_root) / f"{session_id}.json"
    with contextlib.ExitStack() as locks:

        def prepare() -> tuple[pathlib.Path, dict[str, Any]] | None:
            try:
                locks.enter_context(_locked(path.parent / f".{path.stem}.claim.lock", blocking=False))
                locks.enter_context(_locked(path.with_suffix(".publish.lock"), blocking=False))
            except BlockingIOError as error:
                raise ResultBeingCollected(
                    f"終了結果の公開または回収が進行中: {path.stem}",
                    next_action="先行する操作の後に`atk agents wait`で未受領の結果を回収する",
                ) from error
            turn_seq = _expected_turn(session_id, expected_turn_seq, state_root)
            unreadable: list[str] = []
            entry = _oldest_owned(path, owner, turn_seq, expected_finalized_at, unreadable=unreadable)
            if entry is None:
                if unreadable:
                    raise _UnreadableResult("; ".join(unreadable))
                return None
            for message in unreadable:
                _warn_unreadable(message)
            if (end := result_end(entry[1])) is not None:
                mark_published(path, end)
            return entry[0], _public_payload(entry[1])

        try:
            entry = await run_file_operation(prepare)
        except ResultBeingCollected:
            raise
        except (OSError, UnicodeError, ValueError) as error:
            return None, str(error)
        if entry is None:
            return None, None
        source, payload = entry
        source.unlink()
        _LOG.info("result_deleted session_id=%s writer=%s collector=%s", path.stem, owner, collector)
        return payload, None


def read_retained_result(
    root_session_id: str,
    session_id: str,
    state_root: pathlib.Path | None = None,
    *,
    expected_turn_seq: int | None = None,
    expected_finalized_at: str | None = None,
) -> dict[str, Any] | None:
    if not valid_session_id(session_id):
        return None
    path = results_directory(root_session_id, state_root) / f"{session_id}.json"
    try:
        return min(
            (
                payload
                for _, payload in _entries(path, expected_turn_seq=expected_turn_seq)
                if payload.get("status") in TERMINAL_STATUSES
                and (expected_finalized_at is None or expected_finalized_at == payload.get("finalized_at"))
            ),
            key=lambda payload: result_end(payload) or (-1, ""),
            default=None,
        )
    except OSError, UnicodeError, ValueError:
        return None


def _expected_turn(session_id: str, turn_seq: int | None, state_root: pathlib.Path | None) -> int | None:
    if turn_seq is None:
        resolution = session_registry.resolve(session_id, state_root=state_root)
        if resolution.resume_pending and resolution.resume_info is not None:
            return resolution.resume_info.turn_seq
    return turn_seq


def _oldest_owned(
    path: pathlib.Path,
    owner: str,
    turn_seq: int | None,
    finalized_at: str | None,
    *,
    unreadable: list[str],
) -> tuple[pathlib.Path, dict[str, Any]] | None:
    end = (turn_seq, finalized_at) if turn_seq is not None and finalized_at is not None else None
    entries = (
        item for item in _entries(path, expected_turn_seq=turn_seq, end=end, unreadable=unreadable) if _owned(item[1], owner)
    )
    return min(entries, key=lambda item: result_end(item[1]) or (-1, ""), default=None)


def take_result(
    root_session_id: str,
    session_id: str,
    owner_status_file: str,
    *,
    collector: str,
    state_root: pathlib.Path | None = None,
    stash_path: pathlib.Path | None = None,
    expected_turn_seq: int | None = None,
    expected_finalized_at: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if not valid_session_id(session_id):
        raise ValueError(f"invalid session_id: {session_id}")
    directory = results_directory(root_session_id, state_root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{session_id}.json"
    with _locked(directory / f".{session_id}.claim.lock"):
        try:
            end = (
                (expected_turn_seq, expected_finalized_at)
                if expected_turn_seq is not None and expected_finalized_at is not None
                else None
            )
            expected_turn_seq = _expected_turn(session_id, expected_turn_seq, state_root)
            stashed = _read(stash_path) if stash_path is not None else None
            if stashed is None:
                unreadable: list[str] = []
                chosen = _oldest_owned(
                    path, owner_status_file, expected_turn_seq, end[1] if end is not None else None, unreadable=unreadable
                )
                if chosen is None:
                    return None, "; ".join(unreadable) if unreadable else None
                for message in unreadable:
                    _warn_unreadable(message)
                source, payload = chosen
            else:
                payload = stashed
                if expected_turn_seq is not None and payload.get("turn_seq") != expected_turn_seq:
                    return None, None
                if end is not None and result_end(payload) != end:
                    return None, None
                source = next(
                    (candidate for candidate, original in _entries(path, end=result_end(payload)) if original == payload), None
                )
        except (OSError, UnicodeError, ValueError) as exc:
            return None, str(exc)
        if not _owned(payload, owner_status_file):
            return None, None
        if stashed is None and stash_path is not None:
            atomic_write(stash_path, json.dumps(payload, ensure_ascii=False) + "\n", fsync=True)
        if source is not None:
            with _locked(path.with_suffix(".publish.lock")):
                if (end := result_end(payload)) is not None:
                    mark_published(path, end)
                try:
                    unchanged = _read(source) == payload
                except _UnreadableResult as error:
                    return None, str(error)
                if unchanged:
                    source.unlink()
                    _LOG.info("result_deleted session_id=%s writer=%s collector=%s", session_id, owner_status_file, collector)
        return _public_payload(payload), None


def stashed_result_turn(path: pathlib.Path) -> int | None:
    try:
        payload = _read(path)
    except OSError, UnicodeError, ValueError, _UnreadableResult:
        return None
    end = result_end(payload) if payload is not None else None
    return end[0] if end is not None else None


def _public_payload(payload: dict[str, Any]) -> dict[str, Any]:
    from agent_toolkit._agents_server import handoff_next_action  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    session = payload.get("session")
    label = session.get("label") if isinstance(session, dict) else None
    if isinstance(label, str) and label:
        payload["label"] = label
    result = with_result_next_action(public_result(payload), label)
    next_action = handoff_next_action.result_next_action(result)
    if next_action is not None:
        result = append_result_next_action(result, next_action)
    return result


_LOG = logging.getLogger("agent-toolkit.agents-server.status-file")

"""agents_serverが保存した終端結果又は通知を待つ。"""

from __future__ import annotations

import datetime
import json
import logging
import os
import pathlib
import sys
import time
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import logging_config, session_registry, state, status_file
from agent_toolkit._common.file_lock import acquire_lock, release_lock

_WAIT_TIMEOUT_SECONDS = 3600.0
_LOG = logging.getLogger("agent-toolkit.agents-server.wait")


def _fail(message: str, code: int, *, session_id: str | None = None) -> int:
    """標準エラーへ理由を出力してから非0の終了コードで異常終了する。

    理由を伴わない異常終了をこの経路では表現できないよう、`message`を必須の引数とする。
    """
    _LOG.info("wait_return reason=abnormal code=%d session_id=%s", code, session_id or "none")
    print(message, file=sys.stderr)
    return code


def _target_origins(
    own_status_path: pathlib.Path,
    result_directory: pathlib.Path,
    root_session_id: str,
    owner_status_file: str,
    state_root: pathlib.Path | None,
) -> tuple[dict[str, set[str]], tuple[str, int] | None]:
    """現行の待機対象と由来を返し、解釈不能な入力は診断へ変換する。"""
    listed = _read_sessions(own_status_path)
    invalid = [session["session_id"] for session in listed or () if not status_file.valid_session_id(session["session_id"])]
    if invalid:
        return {}, (f"session_idの形式が不正です: {invalid[0]}", 5)
    listed_ids = {session["session_id"] for session in listed or ()}
    result_ids = _retained_result_session_ids(result_directory, owner_status_file=owner_status_file)
    registered_ids, registry_error = status_file.read_wait_targets(root_session_id, owner_status_file, state_root)
    if registry_error is not None:
        return {}, (f"待機対象登録簿を読めません: {registry_error}", 9)
    for session_id in set(registered_ids) - listed_ids - result_ids:
        if session_registry.resolve(session_id, state_root=state_root).state is session_registry.Resolution.MISSING:
            status_file.release_wait_target(root_session_id, owner_status_file, session_id, state_root)
            registered_ids.remove(session_id)
    origins: dict[str, set[str]] = {}
    for origin, identifiers in (
        ("status", listed_ids),
        ("result", result_ids),
        ("registered", registered_ids),
    ):
        for session_id in identifiers:
            origins.setdefault(session_id, set()).add(origin)
    return origins, None


def wait_for_result(
    *,
    environment: Mapping[str, str] | None = None,
    state_root: pathlib.Path | None = None,
) -> int:
    """自身が保持するsessionの最初の終端結果又は通知を1件返す。

    対象は、自身の書込主体の状態ファイルへ載るsessionと、終端結果ファイルが残るsessionの
    双方とする。後者を含めるのは、保持期限で一覧から外れたsessionの結果本文も回収するためである。
    対象集合は最初の待機の発行時点で登録簿へ保存し、再発行時も同じ集合を引き継ぐ。
    待機中に開始又は再稼働したsessionも、巡回ごとに取得して対象へ追加する。
    状態ファイルは投影であり、対象の不在から権威あるsessionの喪失を判定できない。
    終端結果と通知が無い場合は、投影が消失しても待機上限まで非終端として扱う。
    """
    logging_config.configure_logging()
    env = os.environ if environment is None else environment
    root_session_id = status_file.resolve_conversation_root_session_id(env, state_root)
    identity = status_file.resolve_status_file_identity(env)
    if root_session_id is None or identity is None:
        message = (
            "agents_serverの状態ディレクトリを解決できません。"
            "同じsessionで`atk agents list`と`atk agents wait`を実行してください。"
        )
        return _fail(message, 4)
    own_status_path = status_file.status_directory(root_session_id, state_root) / identity.file_name
    result_directory = status_file.results_directory(root_session_id, state_root)
    origins, target_error = _target_origins(
        own_status_path,
        result_directory,
        root_session_id,
        identity.file_name,
        state_root,
    )
    if target_error is not None:
        return _fail(*target_error)
    ordered_ids = sorted(origins)
    _LOG.info(
        "wait_start targets=%s origins=%s",
        ",".join(ordered_ids) or "none",
        ";".join(f"{session_id}:{','.join(sorted(origins[session_id]))}" for session_id in ordered_ids) or "none",
    )
    lock_directory = status_file.status_directory(root_session_id, state_root) / "wait-locks"
    lock_directory.mkdir(parents=True, exist_ok=True)
    lock_files: dict[str, Any] = {}
    try:
        for session_id in ordered_ids:
            lock_file = (lock_directory / f"{session_id}.lock").open("a+b")
            try:
                acquire_lock(lock_file, blocking=False)
            except OSError:
                lock_file.close()
                _LOG.info("wait_lock_failed phase=initial session_id=%s", session_id)
                return _fail(f"同じsessionの待機所有権を別の実行が保持しています: {session_id}", 8, session_id=session_id)
            lock_files[session_id] = lock_file
        status_file.retain_wait_targets(root_session_id, identity.file_name, ordered_ids, state_root)

        started_at = time.monotonic()
        deadline = started_at + _WAIT_TIMEOUT_SECONDS
        while True:
            current_origins, target_error = _target_origins(
                own_status_path,
                result_directory,
                root_session_id,
                identity.file_name,
                state_root,
            )
            if target_error is not None:
                return _fail(*target_error)
            for session_id in sorted(set(current_origins) - set(lock_files)):
                lock_file = (lock_directory / f"{session_id}.lock").open("a+b")
                try:
                    acquire_lock(lock_file, blocking=False)
                except OSError:
                    lock_file.close()
                    _LOG.info("wait_lock_failed phase=dynamic session_id=%s", session_id)
                    continue
                lock_files[session_id] = lock_file
                ordered_ids.append(session_id)
                ordered_ids.sort()
                status_file.retain_wait_targets(root_session_id, identity.file_name, [session_id], state_root)
                _LOG.info(
                    "wait_target_added session_id=%s origins=%s",
                    session_id,
                    ",".join(sorted(current_origins[session_id])),
                )
            status_paths = status_file.list_status_files(root_session_id, state_root)
            for session_id in ordered_ids:
                result_path = result_directory / f"{session_id}.json"
                result, read_error = _read_result(result_path)
                if read_error is not None:
                    return _fail(f"終端結果ファイルを読めません: {result_path}: {read_error}", 6, session_id=session_id)
                notices = status_file.take_notices(root_session_id, session_id, state_root)
                if result is not None:
                    result.pop("owner_status_file", None)
                    result["session_id"] = session_id
                    if notices:
                        result["notices"] = notices
                    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
                    result_path.unlink(missing_ok=True)
                    _LOG.info("result_deleted session_id=%s writer=wait collector=atk-agents-wait", session_id)
                    status_file.release_wait_target(root_session_id, identity.file_name, session_id, state_root)
                    _LOG.info("wait_return reason=terminal-result session_id=%s", session_id)
                    return 0
                if notices:
                    response = _running_response(session_id, _session_output_activity(status_paths, session_id))
                    response["notices"] = notices
                    print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
                    _LOG.info("wait_return reason=notice-only session_id=%s", session_id)
                    return 0

            retained = {session_id: _session_is_retained(status_paths, session_id) for session_id in ordered_ids}
            selected = next((session_id for session_id in ordered_ids if retained[session_id] is not False), None)
            if selected is None and ordered_ids:
                selected = ordered_ids[0]
            response = _running_response(
                selected,
                {} if selected is None else _session_output_activity(status_paths, selected),
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
                _LOG.info("wait_return reason=timeout session_id=%s", selected or "none")
                return 3
            time.sleep(min(1.0, remaining))
    finally:
        for lock_file in reversed(tuple(lock_files.values())):
            release_lock(lock_file)
            lock_file.close()


def _retained_result_session_ids(result_directory: pathlib.Path, *, owner_status_file: str) -> set[str]:
    """自身の書込主体が公開した終端結果のsession識別子を返す。"""
    try:
        paths = tuple(result_directory.iterdir())
    except OSError:
        return set()
    retained: set[str] = set()
    for path in paths:
        if path.suffix != ".json" or not path.is_file() or not status_file.valid_session_id(path.stem):
            continue
        result, error = _read_result(path)
        if error is not None:
            if owner_status_file == "root.json":
                retained.add(path.stem)
            continue
        if result is None:
            continue
        recorded_owner = result.get("owner_status_file")
        if recorded_owner == owner_status_file or recorded_owner is None and owner_status_file == "root.json":
            retained.add(path.stem)
    return retained


def _read_result(path: pathlib.Path) -> tuple[dict[str, Any] | None, str | None]:
    """終端結果を読み、不在と破損を区別して返す。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if not isinstance(value, dict):
        return None, "最上位が辞書ではありません"
    return value, None


def _session_is_retained(paths: list[pathlib.Path], session_id: str) -> bool | None:
    """状態ファイル群からsessionの保持有無を読み、全て解釈不能なら`None`を返す。"""
    parsed = False
    for path in paths:
        sessions = _read_sessions(path)
        if sessions is None:
            continue
        parsed = True
        if any(session["session_id"] == session_id for session in sessions):
            return True
    return False if parsed else None


def _session_output_activity(paths: list[pathlib.Path], session_id: str) -> dict[str, Any]:
    """状態ファイルから保持中sessionの最新テキスト出力活動を返す。"""
    for path in paths:
        sessions = _read_sessions(path)
        if sessions is None:
            continue
        for session in sessions:
            if session["session_id"] != session_id:
                continue
            output_updated_at = session.get("output_updated_at")
            reference = output_updated_at if isinstance(output_updated_at, str) else session.get("started_at")
            if not isinstance(reference, str):
                return {}
            try:
                timestamp = datetime.datetime.fromisoformat(reference)
            except ValueError:
                return {}
            if timestamp.utcoffset() is None:
                return {}
            elapsed = max(0, int((datetime.datetime.now(datetime.UTC) - timestamp).total_seconds()))
            activity: dict[str, Any] = {
                "output_updated_at": output_updated_at if isinstance(output_updated_at, str) else None,
                "seconds_since_output": elapsed,
            }
            if elapsed >= state.STALL_NOTICE_SECONDS:
                activity["stalled"] = True
            return activity
    return {}


def _read_sessions(path: pathlib.Path) -> list[dict[str, Any]] | None:
    """状態ファイルを解釈し、session一覧又は`None`を返す。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    sessions = value.get("sessions") if isinstance(value, dict) and value.get("version") == 1 else None
    if not isinstance(sessions, list):
        return None
    if any(not isinstance(session, dict) or not isinstance(session.get("session_id"), str) for session in sessions):
        return None
    return sessions


def _running_response(session_id: str | None, output_activity: Mapping[str, Any]) -> dict[str, Any]:
    """非終端の待機応答へ最新テキスト出力活動の観測値を加える。

    対象を1件も解決できない場合は`session_id`を省き、`status`だけを返す。
    """
    response: dict[str, Any] = {"status": "running"}
    if session_id is not None:
        response = {"session_id": session_id, "status": "running"}
    response.update(output_activity)
    return response

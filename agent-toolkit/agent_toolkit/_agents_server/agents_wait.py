"""agents_serverが保存した終端結果又は通知を待つ。"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import sys
import time
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import state, status_file
from agent_toolkit._common.file_lock import acquire_lock, release_lock


def _fail(message: str, code: int) -> int:
    """標準エラーへ理由を出力してから非0の終了コードで異常終了する。

    理由を伴わない異常終了をこの経路では表現できないよう、`message`を必須の引数とする。
    """
    print(message, file=sys.stderr)
    return code


def wait_for_result(
    timeout: float,
    *,
    environment: Mapping[str, str] | None = None,
    state_root: pathlib.Path | None = None,
) -> int:
    """自身が保持するsessionの最初の終端結果又は通知を1件返す。

    対象は、自身の書込主体の状態ファイルへ載るsessionと、終端結果ファイルが残るsessionの
    双方とする。後者を含めるのは、保持期限で一覧から外れたsessionの結果本文も回収するためである。
    対象集合は待機の発行時点で確定し、待機中に開始したsessionを含めない。
    終端結果と通知が無いまま状態ファイル全体から対象が消失した場合は、
    MCPのwaitと同じ`status: expired`を終了コード7で返す。既存の成功と
    エラーの終了コードから区別し、待機上限まで消失を見逃さないためである。
    """
    env = os.environ if environment is None else environment
    root_session_id = status_file.resolve_conversation_root_session_id(env, state_root)
    identity = status_file.resolve_status_file_identity(env)
    if root_session_id is None or identity is None:
        return _fail(
            "agents_serverの状態ディレクトリを解決できません。同じsessionを`agents_server`の`list`と`wait`で観測してください。",
            4,
        )
    own_status_path = status_file.status_directory(root_session_id, state_root) / identity.file_name
    result_directory = status_file.results_directory(root_session_id, state_root)
    listed = _read_sessions(own_status_path)
    invalid = [session["session_id"] for session in listed or () if not status_file.valid_session_id(session["session_id"])]
    if invalid:
        return _fail(f"session_idの形式が不正です: {invalid[0]}", 5)
    ordered_ids = sorted({session["session_id"] for session in listed or ()} | _retained_result_session_ids(result_directory))
    if not ordered_ids and listed is not None:
        print(json.dumps({"status": "expired"}, separators=(",", ":")))
        return 7

    lock_directory = status_file.status_directory(root_session_id, state_root) / "wait-locks"
    lock_directory.mkdir(parents=True, exist_ok=True)
    lock_files: list[Any] = []
    try:
        for session_id in ordered_ids:
            lock_file = (lock_directory / f"{session_id}.lock").open("a+b")
            try:
                acquire_lock(lock_file, blocking=False)
            except OSError:
                lock_file.close()
                return _fail(f"同じsessionの待機所有権を別の実行が保持しています: {session_id}", 8)
            lock_files.append(lock_file)

        started_at = time.monotonic()
        deadline = started_at + timeout
        while True:
            status_paths = status_file.list_status_files(root_session_id, state_root)
            for session_id in ordered_ids:
                result_path = result_directory / f"{session_id}.json"
                result, read_error = _read_result(result_path)
                if read_error is not None:
                    return _fail(f"終端結果ファイルを読めません: {result_path}: {read_error}", 6)
                notices = status_file.take_notices(root_session_id, session_id, state_root)
                if result is not None:
                    result["session_id"] = session_id
                    if notices:
                        result["notices"] = notices
                    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
                    result_path.unlink(missing_ok=True)
                    return 0
                if notices:
                    response = _running_response(session_id, _session_updated_at(status_paths, session_id))
                    response["notices"] = notices
                    print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
                    return 0

            retained = {session_id: _session_is_retained(status_paths, session_id) for session_id in ordered_ids}
            if ordered_ids and all(value is False for value in retained.values()):
                print(json.dumps({"session_id": ordered_ids[0], "status": "expired"}, separators=(",", ":")))
                return 7
            selected = next((session_id for session_id in ordered_ids if retained[session_id] is not False), None)
            response = _running_response(selected, None if selected is None else _session_updated_at(status_paths, selected))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
                return 3
            if response.get("stalled") and time.monotonic() - started_at >= state.STALL_NOTICE_SECONDS:
                print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
                return 3
            time.sleep(min(1.0, remaining))
    finally:
        for lock_file in reversed(lock_files):
            release_lock(lock_file)
            lock_file.close()


def _retained_result_session_ids(result_directory: pathlib.Path) -> set[str]:
    """終端結果ファイルが残るsession識別子を返す。"""
    try:
        paths = tuple(result_directory.iterdir())
    except OSError:
        return set()
    return {
        path.stem for path in paths if path.suffix == ".json" and path.is_file() and status_file.valid_session_id(path.stem)
    }


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


def _session_updated_at(paths: list[pathlib.Path], session_id: str) -> str | None:
    """状態ファイルから保持中sessionの最終活動時刻を返す。"""
    for path in paths:
        sessions = _read_sessions(path)
        if sessions is None:
            continue
        for session in sessions:
            if session["session_id"] != session_id:
                continue
            updated_at = session.get("updated_at")
            if not isinstance(updated_at, str):
                return None
            try:
                parsed_updated_at = datetime.datetime.fromisoformat(updated_at)
            except ValueError:
                return None
            if parsed_updated_at.utcoffset() is None:
                return None
            return updated_at
    return None


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


def _running_response(session_id: str | None, updated_at: str | None) -> dict[str, Any]:
    """非終端の待機応答へ最終活動時刻の観測値を加える。

    対象を1件も解決できない場合は`session_id`を省き、`status`だけを返す。
    """
    response: dict[str, Any] = {"status": "running"}
    if session_id is not None:
        response = {"session_id": session_id, "status": "running"}
    if updated_at is None:
        return response
    try:
        parsed_updated_at = datetime.datetime.fromisoformat(updated_at)
        if parsed_updated_at.utcoffset() is None:
            return response
        elapsed = (datetime.datetime.now(datetime.UTC) - parsed_updated_at).total_seconds()
    except (TypeError, ValueError):
        return response
    response["updated_at"] = updated_at
    response["seconds_since_update"] = elapsed
    if elapsed >= state.STALL_NOTICE_SECONDS:
        response["stalled"] = True
    return response

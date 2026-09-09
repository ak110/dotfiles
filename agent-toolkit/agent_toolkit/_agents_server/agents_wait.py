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


def wait_for_result(
    session_id: str,
    timeout: float,
    *,
    environment: Mapping[str, str] | None = None,
    state_root: pathlib.Path | None = None,
) -> int:
    """終端結果又は通知を標準出力へ書き、終了コードを返す。

    終端結果と通知が無いまま`root.json`からsessionが消失した場合は、
    MCPのwaitと同じ`status: expired`を終了コード7で返す。既存の成功と
    エラーの終了コードから区別し、待機上限まで消失を見逃さないためである。
    """
    if not status_file.valid_session_id(session_id):
        print(f"session_idの形式が不正です: {session_id}", file=sys.stderr)
        return 5
    root_session_id = status_file.resolve_conversation_root_session_id(
        os.environ if environment is None else environment, state_root
    )
    if root_session_id is None:
        print(
            "agents_serverの状態ディレクトリを解決できません。同じsessionを`agents_server`の`list`と`wait`で観測してください。",
            file=sys.stderr,
        )
        return 4
    result_path = status_file.results_directory(root_session_id, state_root) / f"{session_id}.json"
    root_status_path = status_file.status_directory(root_session_id, state_root) / "root.json"
    started_at = time.monotonic()
    deadline = time.monotonic() + timeout
    while True:
        result, read_error = _read_result(result_path)
        if read_error is not None:
            print(f"終端結果ファイルを読めません: {result_path}: {read_error}", file=sys.stderr)
            return 6
        notices = status_file.take_notices(root_session_id, session_id, state_root)
        if result is not None:
            if notices:
                result["notices"] = notices
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
            result_path.unlink(missing_ok=True)
            return 0
        if notices:
            response: dict[str, Any] = _running_response(session_id, _session_updated_at(root_status_path, session_id))
            response["notices"] = notices
            print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
            return 0
        retained = _session_is_retained(
            root_status_path,
            session_id,
        )
        if retained is False:
            response = {"session_id": session_id, "status": "expired"}
            print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
            return 7
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(
                json.dumps(
                    _running_response(session_id, _session_updated_at(root_status_path, session_id)),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            return 3
        updated_at = _session_updated_at(root_status_path, session_id)
        response = _running_response(session_id, updated_at)
        if response.get("stalled") and time.monotonic() - started_at >= state.STALL_NOTICE_SECONDS:
            print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
            return 3
        time.sleep(min(1.0, remaining))


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


def _session_is_retained(path: pathlib.Path, session_id: str) -> bool | None:
    """状態ファイルからsessionの保持有無を読み、解釈できない場合は`None`を返す。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    sessions = value.get("sessions") if isinstance(value, dict) and value.get("version") == 1 else None
    if not isinstance(sessions, list):
        return None
    if any(not isinstance(session, dict) or not isinstance(session.get("session_id"), str) for session in sessions):
        return None
    return any(session["session_id"] == session_id for session in sessions)


def _session_updated_at(path: pathlib.Path, session_id: str) -> str | None:
    """状態ファイルから保持中sessionの最終活動時刻を返す。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    sessions = value.get("sessions") if isinstance(value, dict) and value.get("version") == 1 else None
    if not isinstance(sessions, list):
        return None
    for session in sessions:
        if not isinstance(session, dict):
            return None
        if session.get("session_id") != session_id:
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


def _running_response(session_id: str, updated_at: str | None) -> dict[str, Any]:
    """非終端の待機応答へ最終活動時刻の観測値を加える。"""
    response: dict[str, Any] = {"session_id": session_id, "status": "running"}
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

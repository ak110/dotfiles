"""ルートごとに保持した終端結果（`results/<session_id>.json`）の読取と、所有者を確かめて一度だけ行う回収。"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import Any

from agent_toolkit._agents_server.result_projection import public_result, with_result_next_action
from agent_toolkit._agents_server.shared_layout import results_directory, valid_session_id
from agent_toolkit._agents_server.state import TERMINAL_STATUSES
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.file_lock import acquire_lock, release_lock


def read_retained_result(
    root_session_id: str, session_id: str, state_root: pathlib.Path | None = None
) -> dict[str, Any] | None:
    """同じルートsessionの未回収終端結果を削除せずに読む。"""
    if not valid_session_id(session_id):
        return None
    path = results_directory(root_session_id, state_root) / f"{session_id}.json"
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("status") not in TERMINAL_STATUSES:
        return None
    return payload


def take_result(
    root_session_id: str,
    session_id: str,
    owner_status_file: str,
    *,
    collector: str,
    state_root: pathlib.Path | None = None,
    stash_path: pathlib.Path | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """所有者が一致するか確かめ、CLI用の退避先があれば保存後に原本を回収する。

    回収後の公開境界で返す項目を限定し、内部の所有者・保持状態・時刻を保存へ残す。
    起動時の`label`は、依頼と結果の対応に使う。
    呼び出し元が`session_id`から依頼名への対応表を持たずに、どの依頼の結果かを判別できるようにするためである。
    レビューを目的とするsessionの完了結果へは採否確定の次の操作を加える。
    """
    if not valid_session_id(session_id):
        raise ValueError(f"invalid session_id: {session_id}")
    directory = results_directory(root_session_id, state_root)
    directory.mkdir(parents=True, exist_ok=True)
    lock_path = directory / f".{session_id}.claim.lock"
    with lock_path.open("a+b") as lock_file:
        acquire_lock(lock_file, blocking=True)
        try:
            path = directory / f"{session_id}.json"
            source = stash_path if stash_path is not None and stash_path.is_file() else path
            try:
                payload: Any = json.loads(source.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None, None
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                return None, str(exc)
            if not isinstance(payload, dict):
                return None, "最上位が辞書ではありません"
            recorded_owner = payload.get("owner_status_file")
            if recorded_owner != owner_status_file and not (recorded_owner is None and owner_status_file == "root.json"):
                return None, None
            if source == path and stash_path is not None:
                atomic_write(stash_path, json.dumps(payload, ensure_ascii=False) + "\n", fsync=True)
            if path.is_file():
                if source == path:
                    path.unlink()
                else:
                    try:
                        original = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        original = None
                    if original == payload:
                        path.unlink()
                if not path.is_file():
                    _LOG.info(
                        "result_deleted session_id=%s writer=%s collector=%s",
                        session_id,
                        owner_status_file,
                        collector,
                    )
            session = payload.get("session")
            label = session.get("label") if isinstance(session, dict) else None
            if isinstance(label, str) and label:
                payload["label"] = label
            return with_result_next_action(public_result(payload), label), None
        finally:
            release_lock(lock_file)


_LOG = logging.getLogger("agent-toolkit.agents-server.status-file")

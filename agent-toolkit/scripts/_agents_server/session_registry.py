"""agents_serverが起動したsessionの終端状態をプロセス間で共有する。"""

from __future__ import annotations

import contextlib
import datetime
import json
import pathlib
import re
from typing import Any

from _atk import config as _atk_config
from _common.atomic_file import atomic_write

_SESSION_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")


def registry_directory(state_root: pathlib.Path | None = None) -> pathlib.Path:
    """session登録簿のディレクトリを返す。"""
    root = _atk_config.state_dir() if state_root is None else state_root
    return root / "agents-server" / "sessions"


def publish(session_id: str, *, terminal: bool, state_root: pathlib.Path | None = None) -> None:
    """sessionの現在の終端状態を原子的に公開する。"""
    _validate_session_id(session_id)
    if not isinstance(terminal, bool):
        raise TypeError("terminal must be a bool")
    payload = {
        "version": 1,
        "session_id": session_id,
        "terminal": terminal,
        "updated_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    path = registry_directory(state_root) / f"{session_id}.json"
    atomic_write(path, json.dumps(payload, ensure_ascii=False) + "\n")


def is_terminal(session_id: str, *, state_root: pathlib.Path | None = None) -> bool:
    """登録済みsessionが終端済みである場合に真を返す。"""
    _validate_session_id(session_id)
    path = registry_directory(state_root) / f"{session_id}.json"
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(payload, dict)
        and payload.get("version") == 1
        and payload.get("session_id") == session_id
        and payload.get("terminal") is True
        and isinstance(payload.get("updated_at"), str)
    )


def remove(session_id: str, *, state_root: pathlib.Path | None = None) -> None:
    """観測済みsessionの登録を削除する。"""
    _validate_session_id(session_id)
    path = registry_directory(state_root) / f"{session_id}.json"
    path.unlink(missing_ok=True)
    directory = path.parent
    if directory.exists() and not any(directory.iterdir()):
        with contextlib.suppress(OSError):
            directory.rmdir()


def _validate_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or not _SESSION_ID_PATTERN.fullmatch(session_id):
        raise ValueError(f"invalid session_id: {session_id}")

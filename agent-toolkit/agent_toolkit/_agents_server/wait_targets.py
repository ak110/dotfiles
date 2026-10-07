"""`atk agents wait`が待つ対象として書込主体ごとに登録したsession（`wait-targets/`）の読み書き。"""

from __future__ import annotations

import contextlib
import json
import logging
import pathlib
from typing import Any

from agent_toolkit._agents_server.shared_layout import valid_session_id, wait_targets_directory
from agent_toolkit._common.atomic_file import atomic_write


def read_wait_targets(
    root_session_id: str,
    owner_status_file: str,
    state_root: pathlib.Path | None = None,
) -> tuple[set[str], str | None]:
    """未回収の待機対象を返し、破損した登録は解放して理由を返す。"""
    directory = wait_targets_directory(root_session_id, owner_status_file, state_root)
    try:
        paths = tuple(directory.iterdir())
    except FileNotFoundError:
        return set(), None
    except OSError as exc:
        return set(), str(exc)
    retained: set[str] = set()
    for path in paths:
        if path.suffix != ".json" or not path.is_file() or not valid_session_id(path.stem):
            continue
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
            return set(), f"{path}: {exc}"
        if not isinstance(payload, dict) or payload != {"version": 1, "session_id": path.stem}:
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)
            return set(), f"{path}: 必須項目が不正です"
        retained.add(path.stem)
    return retained, None


def retain_wait_targets(
    root_session_id: str,
    owner_status_file: str,
    session_ids: list[str],
    state_root: pathlib.Path | None = None,
) -> None:
    """待機対象を再発行後も復元できるよう書込主体の登録簿へ残す。"""
    if not session_ids:
        return
    directory = wait_targets_directory(root_session_id, owner_status_file, state_root)
    for session_id in session_ids:
        if not valid_session_id(session_id):
            raise ValueError(f"invalid session_id: {session_id}")
        atomic_write(
            directory / f"{session_id}.json",
            json.dumps({"version": 1, "session_id": session_id}, ensure_ascii=False) + "\n",
        )


def release_wait_target(
    root_session_id: str,
    owner_status_file: str,
    session_id: str,
    state_root: pathlib.Path | None = None,
) -> None:
    """回収または明示破棄したsessionを待機対象登録簿から除く。"""
    if not valid_session_id(session_id):
        raise ValueError(f"invalid session_id: {session_id}")
    directory = wait_targets_directory(root_session_id, owner_status_file, state_root)
    (directory / f"{session_id}.json").unlink(missing_ok=True)
    if directory.exists() and not any(directory.iterdir()):
        with contextlib.suppress(OSError):
            directory.rmdir()


_LOG = logging.getLogger("agent-toolkit.agents-server.status-file")

"""agents_serverが起動したsessionの終端状態をプロセス間で共有する。"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime
import enum
import json
import pathlib
import re
from typing import Any, Literal

from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common.atomic_file import atomic_write

_SESSION_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")
_STATUSES = frozenset({"running", "completed", "failed", "interrupted"})


class Resolution(enum.StrEnum):
    """登録簿からsessionを観測した区分。"""

    TERMINAL = "terminal"
    RUNNING = "running"
    MISSING = "missing"
    UNREADABLE = "unreadable"


@dataclasses.dataclass(frozen=True)
class ResumeInfo:
    """再起動後のsession再開へ必要な実行条件。"""

    engine: str
    cwd: str
    model: str | None
    effort: str | None
    model_type: str | None
    launch_kind: Literal["delegate", "explore", "shell"]
    turn_seq: int
    status: Literal["running", "completed", "failed", "interrupted"]


@dataclasses.dataclass(frozen=True)
class SessionResolution:
    """登録簿の解決結果と、利用可能な再開情報を保持する。"""

    state: Resolution
    resume_info: ResumeInfo | None = None


def registry_directory(state_root: pathlib.Path | None = None) -> pathlib.Path:
    """session登録簿のディレクトリを返す。"""
    root = _atk_config.state_dir() if state_root is None else state_root
    return root / "agents-server" / "sessions"


def publish(
    session_id: str,
    *,
    terminal: bool,
    engine: str = "codex",
    cwd: str = "",
    model: str | None = None,
    effort: str | None = None,
    model_type: str | None = None,
    launch_kind: Literal["delegate", "explore", "shell"] = "delegate",
    turn_seq: int = 0,
    status: Literal["running", "completed", "failed", "interrupted"] | None = None,
    state_root: pathlib.Path | None = None,
) -> None:
    """sessionの終端可否と再開条件を原子的に公開する。"""
    _validate_session_id(session_id)
    if not isinstance(terminal, bool):
        raise TypeError("terminal must be a bool")
    status = ("completed" if terminal else "running") if status is None else status
    if status not in _STATUSES:
        raise ValueError(f"invalid status: {status}")
    payload = {
        "version": 2,
        "session_id": session_id,
        "terminal": terminal,
        "engine": engine,
        "cwd": cwd,
        "model": model,
        "effort": effort,
        "model_type": model_type,
        "launch_kind": launch_kind,
        "turn_seq": turn_seq,
        "status": status,
        "updated_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    path = registry_directory(state_root) / f"{session_id}.json"
    atomic_write(path, json.dumps(payload, ensure_ascii=False) + "\n")


def resolve(session_id: str, *, state_root: pathlib.Path | None = None) -> SessionResolution:
    """登録済みsessionを終端・実行中・不在・読取不能へ区分して返す。"""
    _validate_session_id(session_id)
    path = registry_directory(state_root) / f"{session_id}.json"
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return SessionResolution(Resolution.MISSING)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return SessionResolution(Resolution.UNREADABLE)
    if (
        not isinstance(payload, dict)
        or payload.get("session_id") != session_id
        or not isinstance(payload.get("updated_at"), str)
    ):
        return SessionResolution(Resolution.UNREADABLE)
    if payload.get("version") == 1:
        if not isinstance(payload.get("terminal"), bool):
            return SessionResolution(Resolution.UNREADABLE)
        return SessionResolution(Resolution.TERMINAL if payload["terminal"] else Resolution.RUNNING)
    if payload.get("version") != 2 or not isinstance(payload.get("terminal"), bool):
        return SessionResolution(Resolution.UNREADABLE)
    info = _resume_info(payload)
    if info is None:
        return SessionResolution(Resolution.UNREADABLE)
    return SessionResolution(Resolution.TERMINAL if payload["terminal"] else Resolution.RUNNING, info)


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


def _resume_info(payload: dict[str, Any]) -> ResumeInfo | None:
    status = payload.get("status")
    launch_kind = payload.get("launch_kind")
    if (
        not isinstance(payload.get("engine"), str)
        or not isinstance(payload.get("cwd"), str)
        or payload.get("model") is not None
        and not isinstance(payload.get("model"), str)
        or payload.get("effort") is not None
        and not isinstance(payload.get("effort"), str)
        or payload.get("model_type") is not None
        and not isinstance(payload.get("model_type"), str)
        or launch_kind not in {"delegate", "explore", "shell"}
        or not isinstance(payload.get("turn_seq"), int)
        or isinstance(payload.get("turn_seq"), bool)
        or status not in _STATUSES
    ):
        return None
    return ResumeInfo(
        engine=payload["engine"],
        cwd=payload["cwd"],
        model=payload["model"],
        effort=payload["effort"],
        model_type=payload["model_type"],
        launch_kind=launch_kind,
        turn_seq=payload["turn_seq"],
        status=status,
    )

from __future__ import annotations

import contextvars
import json
import os
import pathlib
import re
import typing

LAUNCHER_KEY = "launcher_session_id"
"""sessionを作成した時点の委譲元sessionの識別子を保持する、登録簿のレコードの項目名。"""

START_OBSERVATION = contextvars.ContextVar[str | None]("start_observation", default=None)
START_OBSERVATION_KEY = "start_observation"


def valid_start_observation(value: object) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) else None


_SESSION_ID_PATTERN = re.compile(r"^[0-9A-Za-z_-]+$")


def registry_directory(state_root: pathlib.Path) -> pathlib.Path:
    return state_root / "agents-server" / "sessions"


def valid_launcher(value: object) -> str | None:
    return value if isinstance(value, str) and _SESSION_ID_PATTERN.fullmatch(value) else None


def read_launcher(path: pathlib.Path) -> str | None:
    try:
        payload: typing.Any = json.loads(path.read_text(encoding="utf-8"))
    except OSError, UnicodeError, json.JSONDecodeError:
        return None
    return valid_launcher(payload.get(LAUNCHER_KEY)) if isinstance(payload, dict) else None


def read_start_observation(path: pathlib.Path, launcher: str, cwd: str) -> str | None:
    """起動元と作業場所が一致する永続登録から起動識別子を読む。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, UnicodeError, json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get(LAUNCHER_KEY) != launcher or data.get("cwd") != cwd:
        return None
    return valid_start_observation(data.get(START_OBSERVATION_KEY))


def launcher_reader(state_root: pathlib.Path | None) -> typing.Callable[[str], str | None]:
    if state_root is None:
        return lambda _session_id: None
    directory = registry_directory(state_root)
    try:
        # 列挙の途中で失敗しても、ディレクトリのハンドルをその場で閉じる。
        with os.scandir(directory) as entries:
            names = {entry.name for entry in entries}
    except OSError:
        return lambda _session_id: None

    def read(session_id: str) -> str | None:
        name = f"{session_id}.json"
        if not _SESSION_ID_PATTERN.fullmatch(session_id) or name not in names:
            return None
        return read_launcher(directory / name)

    return read

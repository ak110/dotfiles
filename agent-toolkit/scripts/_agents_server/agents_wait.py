"""agents_serverが保存した指定turn以降の終端結果を待つ。"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
from collections.abc import Mapping
from typing import Any

from _agents_server import status_file


def wait_for_result(
    session_id: str,
    turn_seq: int,
    timeout: float,
    *,
    environment: Mapping[str, str] | None = None,
    state_root: pathlib.Path | None = None,
) -> int:
    """指定turn以降の終端結果を標準出力へ書き、終了コードを返す。"""
    if not status_file.valid_session_id(session_id):
        print(f"session_idの形式が不正です: {session_id}", file=sys.stderr)
        return 5
    identity = status_file.resolve_status_file_identity(os.environ if environment is None else environment)
    if identity is None:
        print("agents_serverの状態ディレクトリを解決できません。", file=sys.stderr)
        return 4
    result_path = status_file.results_directory(identity.root_session_id, state_root) / f"{session_id}.json"
    deadline = time.monotonic() + timeout
    while True:
        result = _read_result(result_path)
        if result is not None and _result_turn_seq(result) >= turn_seq:
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
            return 0
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(f"sessionの終端待機が上限へ到達しました: {session_id}", file=sys.stderr)
            return 3
        time.sleep(min(1.0, remaining))


def _read_result(path: pathlib.Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _result_turn_seq(result: dict[str, Any]) -> int:
    value = result.get("turn_seq")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0

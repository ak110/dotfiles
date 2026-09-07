"""agents_serverが保存した終端結果又は通知を待つ。"""

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
    timeout: float,
    *,
    environment: Mapping[str, str] | None = None,
    state_root: pathlib.Path | None = None,
) -> int:
    """終端結果又は通知を標準出力へ書き、終了コードを返す。"""
    if not status_file.valid_session_id(session_id):
        print(f"session_idの形式が不正です: {session_id}", file=sys.stderr)
        return 5
    root_session_id = status_file.resolve_root_session_id(os.environ if environment is None else environment)
    if root_session_id is None:
        print("agents_serverの状態ディレクトリを解決できません。", file=sys.stderr)
        return 4
    result_path = status_file.results_directory(root_session_id, state_root) / f"{session_id}.json"
    deadline = time.monotonic() + timeout
    while True:
        result = _read_result(result_path)
        notices = status_file.take_notices(root_session_id, session_id, state_root)
        if result is not None:
            if notices:
                result["notices"] = notices
            print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
            result_path.unlink(missing_ok=True)
            return 0
        if notices:
            response = {"session_id": session_id, "status": "running", "notices": notices}
            print(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
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

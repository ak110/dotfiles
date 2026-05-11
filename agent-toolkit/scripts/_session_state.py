"""Claude Code agent-toolkit: hook間で共有するセッション状態ファイルのアクセスヘルパー。

パス規則は`agent-toolkit/skills/claude-code-standards/references/claude-hooks.md`の
「セッション状態ファイル」節に記載がある。
"""

import json
import pathlib
import tempfile

_FILENAME_PREFIX = "claude-agent-toolkit-"
_FILENAME_SUFFIX = ".json"


def state_path(session_id: str) -> pathlib.Path:
    """セッション状態ファイルのパスを返す。"""
    return pathlib.Path(tempfile.gettempdir()) / f"{_FILENAME_PREFIX}{session_id}{_FILENAME_SUFFIX}"


def read_state(session_id: str) -> dict:
    """セッション状態を読む。session_idが無効・不在・破損時は空辞書を返す。"""
    if not isinstance(session_id, str) or not session_id:
        return {}
    try:
        return json.loads(state_path(session_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def write_state(session_id: str, state: dict) -> None:
    """セッション状態を書き込む。書き込み失敗は無視する（best-effort）。"""
    if not isinstance(session_id, str) or not session_id:
        return
    try:
        state_path(session_id).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return

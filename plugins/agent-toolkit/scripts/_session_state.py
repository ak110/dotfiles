"""Claude Code agent-toolkit: hook 間で共有するセッション状態ファイルのアクセスヘルパー。

`pretooluse.py` / `posttooluse.py` / `stop_advisor.py` から import して使う。
パス規則は `writing-standards/references/claude-hooks.md` の「セッション状態ファイル」節と、
`.claude/rules/plugins.md` の「agent-toolkit のセッション状態フラグ」節に記載がある。
PEP 723 ヘッダーなし（通常モジュールとして import 可能にするため）。
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
    """セッション状態を読む。session_id が無効・不在・破損時は空辞書を返す。"""
    if not isinstance(session_id, str) or not session_id:
        return {}
    try:
        return json.loads(state_path(session_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def write_state(session_id: str, state: dict) -> None:
    """セッション状態を書く。書き込み失敗は無視する（best-effort）。"""
    if not isinstance(session_id, str) or not session_id:
        return
    try:
        state_path(session_id).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return

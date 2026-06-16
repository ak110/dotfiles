#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Claude Code PostToolUseフック: dotfiles個人環境専用のSkill呼び出し記録。

`agent-toolkit-edit`スキル（dotfilesローカル配布対象外のため`agent-toolkit`プラグイン
本体からは検出できない）の呼び出しをセッション状態へ記録する。
書き込み先は`{tempdir}/claude-agent-toolkit-{session_id}.json`の
`agent_toolkit_edit_skill_invoked`キーで、値を`True`に書き込む。

PreToolUse側（`claude_hook_pretooluse.py`）が当該フラグを参照し、
`agent-toolkit/`配下の編集時にスキル未起動なら警告を発する。

exit codeは常に0（PostToolUseはブロック不可）。
"""

import json
import pathlib
import sys
import traceback

# agent-toolkit のセッション状態ヘルパーを sys.path 経由で再利用する。
sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parent.parent / "agent-toolkit" / "scripts"),
)
from _session_state import update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_AGENT_TOOLKIT_EDIT_SKILL = "agent-toolkit-edit"


def main() -> int:
    """エントリポイント。exit codeは常に0。"""
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return 0
    if payload.get("tool_name") != "Skill":
        return 0
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    if tool_input.get("skill") != _AGENT_TOOLKIT_EDIT_SKILL:
        return 0
    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        return 0

    def _set_invoked(state: dict) -> dict | None:
        if state.get("agent_toolkit_edit_skill_invoked", False):
            return None
        state["agent_toolkit_edit_skill_invoked"] = True
        return state

    update_state(session_id, _set_invoked)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 -- フックが破損して編集できなくなる事故を避けるため広範に捕捉
        traceback.print_exc()
        sys.exit(0)

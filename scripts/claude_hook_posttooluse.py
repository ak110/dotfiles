"""Claude Code PostToolUseフック: dotfiles個人環境専用のSkill呼び出し記録。

dotfilesローカル配布対象外のスキル呼び出しと、自律終了の呼び出しをセッション状態へ記録する。

書き込み先は`{tempdir}/claude-agent-toolkit-{session_id}.json`。
記録対象とキーは以下のとおり。

- `agent-toolkit-edit`スキル: `agent_toolkit_edit_skill_invoked`キーへ`True`を書き込む。
  PreToolUse側（`claude_hook_pretooluse.py`）が参照し、`agent-toolkit/`配下の編集時に
  スキル未起動なら警告を発する。
- `agent-toolkit:exit-session`スキル: `autonomous_exit_invoked`キーへ`True`を書き込む。
  個人フックStop hook（`claude_hook_autonomous_exit.py`）が参照し、
  `AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`環境下でのexit-session未呼出判定に使う。

exit codeは常に0（PostToolUseはブロック不可）。
"""

import json
import pathlib
import sys

# agent-toolkit のセッション状態ヘルパーを sys.path 経由で再利用する。
sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parent.parent / "agent-toolkit" / "scripts"),
)
from _session_state import update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_AGENT_TOOLKIT_EDIT_SKILL = "agent-toolkit-edit"
_AUTONOMOUS_EXIT_SKILL = "agent-toolkit:exit-session"


def _set_autonomous_exit_invoked(state: dict) -> dict | None:
    if state.get("autonomous_exit_invoked") is True:
        return None
    state["autonomous_exit_invoked"] = True
    return state


def main(payload_text: str) -> int:
    """エントリポイント。exit codeは常に0。"""
    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, ValueError):
        return 0
    if payload.get("tool_name") != "Skill":
        return 0
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    skill = tool_input.get("skill")
    if not isinstance(skill, str):
        return 0
    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        return 0

    if skill == _AGENT_TOOLKIT_EDIT_SKILL:

        def _set_edit_invoked(state: dict) -> dict | None:
            if state.get("agent_toolkit_edit_skill_invoked", False):
                return None
            state["agent_toolkit_edit_skill_invoked"] = True
            return state

        update_state(session_id, _set_edit_invoked)
        return 0

    if skill == _AUTONOMOUS_EXIT_SKILL:
        update_state(session_id, _set_autonomous_exit_invoked)
        return 0

    return 0

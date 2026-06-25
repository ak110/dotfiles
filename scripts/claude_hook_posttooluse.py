#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Claude Code PostToolUseフック: dotfiles個人環境専用のSkill呼び出し記録。

dotfilesローカル配布対象外のため`agent-toolkit`プラグイン本体からは検出できない
個人環境スキル呼び出しをセッション状態へ記録する。

書き込み先は`{tempdir}/claude-agent-toolkit-{session_id}.json`。
記録対象とキーは以下のとおり。

- `agent-toolkit-edit`スキル: `agent_toolkit_edit_skill_invoked`キーへ`True`を書き込む。
  PreToolUse側（`claude_hook_pretooluse.py`）が参照し、`agent-toolkit/`配下の編集時に
  スキル未起動なら警告を発する。
- `session-review-dotfiles`スキル: `session_review_invoked`辞書へスキル名をキーとして
  `True`を書き込む。Stop hook側（`claude_hook_stop.py`）が参照し、当該キーが真なら
  振り返り誘導を抑止する。当該辞書のEnterPlanMode観測時のリセットは配布物側
  （`agent-toolkit/scripts/posttooluse.py`）が担当する。
- `agent-toolkit:*`スキルおよび`session-review-dotfiles`スキル:
  `session_review_extension_pending`キーへ`True`を書き込む。
  配布物Stop hook（`agent-toolkit/scripts/stop_advisor.py`）が参照し、真の場合は
  自身の振り返り誘導を抑制する（個人フックStop hookとの誘導重複を防ぐため）。

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
_SESSION_REVIEW_DOTFILES_SKILL = "session-review-dotfiles"
_AGENT_TOOLKIT_PREFIX = "agent-toolkit:"


def _set_extension_pending(state: dict) -> dict | None:
    if state.get("session_review_extension_pending") is True:
        return None
    state["session_review_extension_pending"] = True
    return state


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

    if skill == _SESSION_REVIEW_DOTFILES_SKILL:

        def _set_review_invoked(state: dict) -> dict | None:
            invoked = state.get("session_review_invoked")
            if not isinstance(invoked, dict):
                invoked = {}
            if invoked.get(_SESSION_REVIEW_DOTFILES_SKILL) is True:
                return None
            invoked[_SESSION_REVIEW_DOTFILES_SKILL] = True
            state["session_review_invoked"] = invoked
            return state

        update_state(session_id, _set_review_invoked)
        update_state(session_id, _set_extension_pending)
        return 0

    if skill.startswith(_AGENT_TOOLKIT_PREFIX):
        update_state(session_id, _set_extension_pending)
        return 0

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 -- フックが破損して編集できなくなる事故を避けるため広範に捕捉
        traceback.print_exc()
        sys.exit(0)

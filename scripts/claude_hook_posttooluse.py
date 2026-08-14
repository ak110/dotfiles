"""Claude Code PostToolUseフック: dotfiles個人環境専用のSkill・Read呼び出し記録。

dotfilesローカル配布対象外のスキル呼び出し、自律終了及び参照文書のReadをセッション状態へ記録する。

書き込み先は`{tempdir}/claude-agent-toolkit-{session_id}.json`。
記録対象とキーは以下のとおり。

- `agent-toolkit-edit`スキル: `agent_toolkit_edit_skill_invoked`キーへ`True`を書き込む。
  PreToolUse側（`claude_hook_pretooluse.py`）が参照し、`agent-toolkit/`配下の編集時に
  スキル未起動なら警告を発する。
- `agent-toolkit:exit-session`スキル: `autonomous_exit_invoked`キーへ`True`を書き込む。
  個人フックStop hook（`claude_hook_autonomous_exit.py`）が参照し、
  `AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`環境下でのexit-session未呼出判定に使う。
- `docs/development/concepts.md`・`incidents.md`へのRead:
  `dotfiles_reference_docs_read`キーへ解決済み絶対パスを重複なしで記録する。
  PreToolUse側が同じチェックアウト内のコーディングエージェント向け文書の編集警告に使う。

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
_REFERENCE_DOC_SUFFIXES: tuple[tuple[str, ...], ...] = (
    ("docs", "development", "concepts.md"),
    ("docs", "development", "incidents.md"),
)


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
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    session_id = payload.get("session_id", "")
    if not isinstance(session_id, str) or not session_id:
        return 0

    if tool_name == "Read":
        file_path = tool_input.get("file_path")
        resolved = _reference_doc_path(file_path) if isinstance(file_path, str) else None
        if resolved is None:
            return 0

        def _record_reference_doc(state: dict) -> dict | None:
            existing = state.get("dotfiles_reference_docs_read", [])
            paths = [item for item in existing if isinstance(item, str)] if isinstance(existing, list) else []
            if resolved in paths:
                return None
            paths.append(resolved)
            state["dotfiles_reference_docs_read"] = paths
            return state

        update_state(session_id, _record_reference_doc)
        return 0

    if tool_name != "Skill":
        return 0
    skill = tool_input.get("skill")
    if not isinstance(skill, str):
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


def _reference_doc_path(file_path: str) -> str | None:
    """参照対象文書の解決済み絶対パスを返す。"""
    try:
        resolved = pathlib.Path(file_path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError, ValueError):
        return None
    if not any(resolved.parts[-len(suffix) :] == suffix for suffix in _REFERENCE_DOC_SUFFIXES):
        return None
    return str(resolved)

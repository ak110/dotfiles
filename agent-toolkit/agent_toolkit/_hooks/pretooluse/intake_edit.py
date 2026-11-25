"""AWIを起草するセッションの編集前に作業範囲の確認を促す。 pyfltr: allow-comment"""

from __future__ import annotations

import pathlib

# pylint: disable=protected-access  # 終了工程と同じ作業・実ユーザー発話・Gitルートを使う。
lazy from agent_toolkit._common import session_state
lazy from agent_toolkit._hooks import agent_id, termination_evidence_operations, termination_evidence_state
lazy from agent_toolkit._hooks.pretooluse import operation_skills
lazy from agent_toolkit._hooks.pretooluse.notices import _llm_notice

_WORK = "add-awi-by-user"
_SEEN_INPUT = "intake_edit_notice_input"


def _active(state: dict, session_id: str) -> dict | None:
    data = state.get(termination_evidence_state.STATE_KEY)
    if (
        isinstance(data, dict)
        and data.get("session_id") == session_id
        and data.get("version") == 1
        and data.get("entry_work") == _WORK
    ):
        return data
    return None


def _target(paths: list[str]) -> pathlib.Path | None:
    for value in paths:
        path = pathlib.Path(value)
        if not path.is_absolute():
            continue
        parent = next((ancestor for ancestor in path.parents if ancestor.is_dir()), None)
        if parent is not None and termination_evidence_operations._git_root(str(parent)) is not None:
            return path
    return None


def notice(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> str | None:
    if tool_name not in {"Edit", "Write", "NotebookEdit", "Bash"} or not agent_id.is_main_agent_context(payload):
        return None
    if _active(session_state.read_state(session_id), session_id) is None:
        return None
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        return None
    if tool_name == "NotebookEdit":
        notebook = tool_input.get("notebook_path")
        paths = [str((pathlib.Path(cwd) / notebook).resolve())] if isinstance(notebook, str) and notebook else []
    else:
        paths = operation_skills.written_paths(tool_name, tool_input, cwd)
    target = _target(paths)
    if target is None:
        return None

    delivered = False

    def update(state: dict) -> dict | None:
        nonlocal delivered
        data = _active(state, session_id)
        if data is None:
            return None
        position = (data.get("transcript_path"), data.get("queued_offset"))
        termination_evidence_state._sync_queued_inputs(data, payload.get("transcript_path"))
        current = data.get("last_human_input")
        if not isinstance(current, str) or current == data.get(_SEEN_INPUT):
            return state if position != (data.get("transcript_path"), data.get("queued_offset")) else None
        data[_SEEN_INPUT] = current
        delivered = True
        return state

    if not session_state.update_state(session_id, update) or not delivered:
        return None
    return _llm_notice(
        f"作業は `add-awi-by-user` です。編集対象: {str(target)!r}。"
        "`add-awi-by-user.md`「セッション境界」の例外か、ユーザーが今回の要求で実施を明示した変更かを"
        "確かめてください。どちらでもなければAWIとして起草し、明示があればその要求を作業契約へ記録してから進めてください。",
        tag="notice",
    )

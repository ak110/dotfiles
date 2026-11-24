"""コマンドhookからセッションの種別と自動名を管理する。"""

import os
import pathlib
import re

from agent_toolkit._common import process_loop_session, skill_modes
from agent_toolkit._common.session_state import read_state, update_state

_ALL_REPOS_TITLE = "process-wi all-repos"


def observe_add_awi(session_id: str, skill: str) -> None:
    if skill.removeprefix("agent-toolkit:") == "add-awi-by-user":
        update_state(session_id, lambda state: {**state, "add_awi_skill_invoked": True})


def observe_skill(session_id: str, skill: str, arguments: str | None = None) -> None:
    if skill.removeprefix("agent-toolkit:") != "process-wi":
        return
    mode, _ = skill_modes.split_mode(skill, arguments)
    update_state(session_id, lambda state: {**state, "session_title_kind": mode or "process-wi"})


def proposed_title(session_id: str, current: object) -> str | None:
    state = read_state(session_id)
    kind = state.get("session_title_kind", "")

    if process_loop_session.is_process_loop_session(session_id, os.environ) or kind == "all-repos":
        title = _ALL_REPOS_TITLE
    else:
        path = state.get("current_plan_file_path", "")
        plan = re.sub(r"^\d{2}-\d{4}_", "", pathlib.PurePosixPath(str(path).replace("\\", "/")).stem) if path else ""
        title = " ".join(value for value in (kind, plan) if isinstance(value, str) and value)
    if not title or title == current or current and current != state.get("last_hook_session_title"):
        return None

    def save(record: dict) -> dict:
        record["last_hook_session_title"] = title
        return record

    return title if update_state(session_id, save) else None

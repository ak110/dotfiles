"""コマンドhookからセッションの種別と自動名を管理する。"""

import os
import pathlib
import re

from agent_toolkit._common import process_loop_session, skill_modes
from agent_toolkit._common.session_state import read_state, update_state

_KINDS = {"process-wi": "process-wi", "add-awi-by-user": "add-awi"}

# 複数リポジトリを統括する会話の名前。統括は複数の個別計画を順に扱うため、最後の計画名を
# 連結すると会話全体の処理範囲を誤認させる。計画名を連結しない固定名とする。
_ALL_REPOS_TITLE = "process-wi all-repos"


def observe_skill(session_id: str, skill: str, arguments: str | None = None) -> None:
    """成功したスキル起動または手動起動の種別を記録する。"""
    kind = _KINDS.get(skill.removeprefix("agent-toolkit:"))
    if kind == "process-wi":
        mode, _ = skill_modes.split_mode(skill, arguments)
        kind = mode or kind
    if kind is not None:

        def save(state: dict) -> dict:
            state["session_title_kind"] = kind
            return state

        update_state(session_id, save)


def proposed_title(session_id: str, current: object) -> str | None:
    """手動名を保持し、自動名の更新が必要なときだけ新しい名前を返す。"""
    state = read_state(session_id)
    kind = state.get("session_title_kind", "")
    # 常駐process-loopの統括は、内部で単一リポジトリ処理の種別が記録されても統括名を保つ。
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

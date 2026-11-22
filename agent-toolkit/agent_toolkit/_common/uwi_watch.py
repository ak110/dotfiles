from collections.abc import Iterable

from agent_toolkit._common import wi_layout
from agent_toolkit._common.session_state import update_state

STATE_KEY = "uwi_awaiting_answer"
"""回答を待つUWIのファイル名の一覧を保持するセッション状態キー。

値は年月の固定保存先を名前から決められるファイル名（`wi_layout.DATED_NAME`）の昇順の一覧とする。
"""


def awaiting(state: dict) -> list[str]:
    names = state.get(STATE_KEY)
    if not isinstance(names, list):
        return []
    return [name for name in names if isinstance(name, str) and wi_layout.DATED_NAME.fullmatch(name) is not None]


def add(session_id: str, filenames: Iterable[str]) -> bool:
    added = {name for name in filenames if wi_layout.DATED_NAME.fullmatch(name) is not None}
    if not added:
        return False

    def _add(state: dict) -> dict:
        state[STATE_KEY] = sorted(added.union(awaiting(state)))
        return state

    return update_state(session_id, _add)

"""会話が投入したUWIのうち、回答を待っているもののファイル名をセッション状態へ記録する。

`atk wi add`が投入時に記録し、PostToolUseのhookがその一覧のファイルだけを確かめる。
回答の検出のために全WIを調べずに済むよう、確かめる対象を投入時に確定する。
"""

from collections.abc import Iterable

from agent_toolkit._common import wi_layout
from agent_toolkit._common.session_state import update_state

STATE_KEY = "uwi_awaiting_answer"
"""回答を待つUWIのファイル名の一覧を保持するセッション状態キー。

値は年月の固定保存先を名前から決められるファイル名（`wi_layout.DATED_NAME`）の昇順の一覧とする。
"""


def awaiting(state: dict) -> list[str]:
    """記録済みのファイル名を返す。"""
    names = state.get(STATE_KEY)
    if not isinstance(names, list):
        return []
    return [name for name in names if isinstance(name, str) and wi_layout.DATED_NAME.fullmatch(name) is not None]


def add(session_id: str, filenames: Iterable[str]) -> bool:
    """投入したUWIを会話の記録へ加える。書き込んだ場合だけ真を返す。"""
    added = {name for name in filenames if wi_layout.DATED_NAME.fullmatch(name) is not None}
    if not added:
        return False

    def _add(state: dict) -> dict:
        state[STATE_KEY] = sorted(added.union(awaiting(state)))
        return state

    return update_state(session_id, _add)


def discard(session_id: str, filenames: Iterable[str]) -> bool:
    """回答の通知または終了を確かめたUWIを会話の記録から外す。書き込んだ場合だけ真を返す。"""
    removed = set(filenames)

    def _discard(state: dict) -> dict:
        state[STATE_KEY] = [name for name in awaiting(state) if name not in removed]
        return state

    return update_state(session_id, _discard)

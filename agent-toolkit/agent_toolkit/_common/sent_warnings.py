"""`send_to_user`で送達した報告への警告を、呼出しごとに預けて同じ呼出しのPostToolUseで取り出す。

警告を送信結果の本文に入れると、モデルはツール結果の中身をデータとして読み、指示として扱いにくい。
MCPの`send_to_user`が預け、PostToolUseのhookが結果の外の添付として届ける。
会話内modの`tool.call`への登録はCode Modeの結果待ちを無効にするため使わない。
根拠は`docs/development/design-hooks-send-to-user.md`にある。 pyfltr: allow-comment
"""

from agent_toolkit._common import session_state

_STATE_KEY = "sent_report_warnings"
_HELD_LIMIT = 20
"""取り出されずに残る預かりの上限。PostToolUseが動かない環境でも状態が増え続けないようにする。 pyfltr: allow-comment"""


def stash(session_id: str, tool_use_id: str, warnings: list[str]) -> None:
    def put(state: dict) -> dict:
        held = state.get(_STATE_KEY)
        held = dict(held) if isinstance(held, dict) else {}
        held[tool_use_id] = list(warnings)
        state[_STATE_KEY] = dict(list(held.items())[-_HELD_LIMIT:])
        return state

    session_state.update_state(session_id, put)


def take(session_id: str, tool_use_id: str) -> list[str]:
    taken: list[str] = []

    def pop(state: dict) -> dict | None:
        held = state.get(_STATE_KEY)
        if not isinstance(held, dict) or tool_use_id not in held:
            return None
        value = held.pop(tool_use_id)
        if isinstance(value, list):
            taken.extend(text for text in value if isinstance(text, str))
        state[_STATE_KEY] = held
        return state

    session_state.update_state(session_id, pop)
    return taken

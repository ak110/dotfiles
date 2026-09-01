"""観測を試みていないagents_serverの作業をStop時に警告する。

`start`・`start_explore`と配送が成立した`send_message`は、委譲先に新しい作業を
発生させる。PostToolUseが当該応答と呼出主体を`agents_server_sessions`へ記録し、
本フックは`pending_observation`が真で、呼出主体が一致する記録だけを警告対象にする。

判定対象は結果の回収状態ではなく、観測を試みていない作業の有無である。
`wait`は応答の`status`を問わず観測を試みたことになり、`kill`は結果を意図的に
破棄するため、いずれも`pending_observation`を解消する。一度解消したsessionでも、
`send_message`が新しい作業を配送すれば再び警告対象になる。

警告は`hookSpecificOutput.additionalContext`で当該ターンを継続させる。
`stop_hook_active`が真の再呼び出し、payload不正、状態不在・破損時は何も出力せず
終了を許可し、警告の反復で終了不能になることを避ける。
"""

import json

from _hook_notice import formatter as _notice_formatter
from _session_state import read_state
from _stop_gate import parse_stop_session
from _transcript_agent_id import extract_transcript_agent_id

_HOOK_ID = "agent-toolkit/agents_server_session_advisor"
_SESSION_STATE_KEY = "agents_server_sessions"
_MAIN_AGENT_ID = "main"
_WARNING_BODY = (
    "agents_serverのsessionに、観測を試みていない作業が残っている。"
    "wait(session_id)で観測するか、結果が不要ならkill(session_id)で破棄してから終了する。"
    "send_messageは新しい作業を配送するだけで観測しないため、この警告は解消しない。"
    "観測しないまま終了すると、当該作業の成果を回収する主体が残らない。"
)

_notice = _notice_formatter(_HOOK_ID, default_tag="warn")


def _approve() -> None:
    """出力せず終了を許可する。"""


def _pending_session_ids(state: dict, owner_agent_id: str) -> list[str]:
    """呼出主体が観測すべき作業の残るsession識別子を返す。"""
    sessions = state.get(_SESSION_STATE_KEY)
    if not isinstance(sessions, dict):
        return []
    return sorted(
        session_id
        for session_id, record in sessions.items()
        if isinstance(session_id, str)
        and isinstance(record, dict)
        and record.get("pending_observation") is True
        and record.get("owner_agent_id") == owner_agent_id
    )


def main(payload_text: str) -> int:
    """Stop payloadとセッション状態から未観測作業を警告する。"""
    resolved = parse_stop_session(payload_text, _approve)
    if resolved is None:
        return 0
    session_id, payload = resolved
    if payload.get("stop_hook_active") is True:
        return 0

    owner_agent_id = extract_transcript_agent_id(payload.get("transcript_path")) or _MAIN_AGENT_ID
    pending_session_ids = _pending_session_ids(read_state(session_id), owner_agent_id)
    if not pending_session_ids:
        return 0

    body = f"{_WARNING_BODY}\n対象session: {', '.join(pending_session_ids)}"
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "Stop",
                    "additionalContext": _notice(body),
                }
            },
            ensure_ascii=False,
        )
    )
    return 0

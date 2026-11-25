"""Claude Codeの権限拒否について、次のツール呼び出しかStopで一度だけ案内する。  pyfltr: allow-comment

委譲先での実行可否: `agents_server`の委譲先でも実行し、委譲元への報告を案内する。
組み込みのサブエージェントの通知は同じ`agent_id`のツール呼び出しで扱い、メインのStopでは消費しない。
"""

import time
from typing import Literal

from agent_toolkit._common import session_layout, session_state
from agent_toolkit._hooks import notice
from agent_toolkit._hooks.agent_id import is_main_agent_context
from agent_toolkit._hooks.host import is_antigravity_payload, is_codex_payload
lazy from agent_toolkit._hooks import message_format, stop_session

_STATE_KEY = "pending_permission_denials"
_SOURCE = "permission_denial_notice"
_INTRO = "ホストがツール呼び出しを拒否した。"
_COMMON = "`agent-toolkit:atk-mode/playbooks/permission-denial.md`「共通判断」から始める。"
_MAIN = (
    _COMMON + " classifierの拒否を偽陽性と判断できる場合は、同書「偽陽性と判断できる拒否への対応」に従う。"
    "具体的な承認と実際の操作が一致し、既存の承認を適用できる時は質問しない。"
    "適用できなければ同じ操作を再試行せず、拒否の事実と偽陽性の根拠を伝え、"
    "`AskUserQuestion`で「偽陽性として実行を承認する」「該当の操作を撤回する」"
    "「サブエージェント側でレビューを終えてから判断する」の3択で認可だけを問う。"
)
_DELEGATED = _COMMON + " 拒否の対象・操作・診断と必要な認可を委譲元へ報告する。自分では質問や再試行をしない。"
_block_notice = notice.block_formatter(_SOURCE)


def _eligible(payload: dict, session_id: str) -> bool:
    return session_layout.valid_session_id(session_id) and not is_codex_payload(payload) and not is_antigravity_payload(payload)


def record(payload: dict, session_id: str, tool_name: object) -> None:
    if not _eligible(payload, session_id) or not isinstance(tool_name, str) or not tool_name:
        return
    tool_use_id = payload.get("tool_use_id")
    use_id = tool_use_id if isinstance(tool_use_id, str) else ""
    agent_id = payload.get("agent_id")
    actor = agent_id if isinstance(agent_id, str) else ""
    entry = {"session_id": session_id, "tool": tool_name, "time": time.time(), "tool_use_id": use_id, "agent_id": actor}

    def append(state: dict) -> dict | None:
        existing = state.get(_STATE_KEY)
        pending = (
            [row for row in existing if isinstance(row, dict) and row.get("session_id") == session_id]
            if isinstance(existing, list)
            else []
        )
        if use_id and any(row.get("tool_use_id") == use_id for row in pending):
            return None
        return {**state, _STATE_KEY: [*pending, entry]}

    session_state.update_state(session_id, append)


def next_notice(payload: dict, session_id: str, *, block: bool) -> str:
    if not _eligible(payload, session_id):
        return ""
    agent_id = payload.get("agent_id")
    actor = agent_id if isinstance(agent_id, str) else ""

    def applies(item: object) -> bool:
        return isinstance(item, dict) and item.get("session_id") == session_id and item.get("agent_id") == actor

    def claim(state: dict) -> dict | None:
        pending = state.get(_STATE_KEY)
        if not isinstance(pending, list):
            return None
        for index, item in enumerate(pending):
            if applies(item):
                return {**state, _STATE_KEY: pending[:index] + pending[index + 1 :]}
        return None

    pending = session_state.read_state(session_id).get(_STATE_KEY)
    if not isinstance(pending, list) or not any(map(applies, pending)) or not session_state.update_state(session_id, claim):
        return ""
    guidance = _MAIN if is_main_agent_context(payload) else _DELEGATED
    if block:
        return _block_notice(_INTRO, fix=guidance)
    return message_format.llm_notice(f"{_INTRO}\n次の操作: {guidance}", _SOURCE, tag="warn")


def evaluate(payload_text: str) -> tuple[Literal["approve", "block"], str]:
    resolved = stop_session.parse_stop_session(payload_text, lambda: None)
    if resolved is not None:
        session_id, payload = resolved
        if guidance := next_notice(payload, session_id, block=True):
            return "block", guidance
    return "approve", ""

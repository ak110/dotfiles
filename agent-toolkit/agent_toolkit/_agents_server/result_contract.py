"""exploreの完了状態行とtaskの続行不能返却を公開前に検査する。"""

from __future__ import annotations

import re
from typing import Any

from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._common import task_documents

_REASON_PREFIX = "続行できない理由:"
_CLASSIFICATION_PREFIX = "続行できない区分:"
RECOVERY_NEXT_ACTION = (
    "agents_server.send_messageのsession_idにこの結果のsession_idを指定し、promptで同じsessionへ、"
    "診断に従い、正常返却は所定の成功行、続行不能は同じインデントの有効な区分と非空の理由の"
    "連続する2行を用い、成果と追加報告を保持して再提出するよう指示する。"
    "送信できない場合は、session_id・返却本文・診断を委譲元へ報告する。"
)
_VALID_CLASSIFICATIONS = frozenset(
    {
        "user_decision",
        "authorization",
        "owner_input",
        "inaccessible_input",
        "permission_denied",
        "external_wait",
        "runtime_unavailable",
    }
)


def invalid_result_reason(session: SessionState, result: dict[str, Any]) -> str | None:
    """自由本文exploreの成功行と、explore・taskの続行不能2行を検査する。"""
    if result.get("status") != "completed":
        return None
    document = task_documents.launched_task_document(session.prompt)
    freeform_explore = session.launch_kind == "explore" and document is None
    if not freeform_explore and (document is None or not task_documents.is_agent_toolkit_task_document(document)):
        return None
    message = result.get("agent_message")
    if not isinstance(message, str):
        return "exploreの返却が完了を示しません。返却本文が必要です" if freeform_explore else None
    if re.fullmatch(r"待機中: [^\r\n]+", message.strip()):
        return None
    lines = message.splitlines()
    has_reason = False
    for index, line in enumerate(lines):
        match = re.fullmatch(r"(?P<indent>\s*)" + re.escape(_REASON_PREFIX) + r".*", line)
        if match is None:
            continue
        has_reason = True
        if not line.removeprefix(match.group("indent") + _REASON_PREFIX).strip():
            return f"{index + 1}行目の続行できない理由が空です"
        if index == 0:
            return f"{index + 1}行目の続行できない理由の直前に同じインデントの続行できない区分がありません"
        expected = match.group("indent") + _CLASSIFICATION_PREFIX
        previous = lines[index - 1]
        if not previous.startswith(expected):
            return f"{index + 1}行目の続行できない理由の直前に同じインデントの続行できない区分がありません"
        classification = previous.removeprefix(expected).strip()
        if classification not in _VALID_CLASSIFICATIONS:
            return f"{index}行目の続行できない区分が閉集合外です: {classification}"
    if freeform_explore and not has_reason:
        lines = message.rstrip().splitlines()
        if not lines or not (lines[0] == "状態: completed" or lines[-1] == "状態: completed"):
            return "exploreの返却が完了を示しません。先頭または最終行に状態: completedが必要です"
    return None


def hold_invalid_result(session: SessionState, result: dict[str, Any]) -> bool:
    """契約に違反した結果を1回だけ保留し、監視側の同一session継続へ渡す。"""
    reason = invalid_result_reason(session, result)
    if reason is None:
        return False
    if session.result_contract_recovery_consumed:
        error = result.get("error")
        details = dict(error) if isinstance(error, dict) else {}
        details["resultContract"] = reason
        details["nextAction"] = RECOVERY_NEXT_ACTION
        result["error"] = details
        if session.launch_kind == "explore":
            result["status"] = "failed"
        return False
    session.pending_result = result
    session.awaiting_auto_resume = True
    session.result_contract_recovery_pending = True
    session.result_contract_recovery_reason = reason
    session.status = "running"
    session.turn_completed = True
    session.turn_start_ambiguous = False
    session.touch()
    return True

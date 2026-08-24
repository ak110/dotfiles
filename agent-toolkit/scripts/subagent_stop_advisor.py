"""SubagentStop hook: 構造的に確定できる未完了状態と報告言語を検査する。

公式仕様の`last_assistant_message`を直参照し、空文字列だけの完了報告をblockする。
登録済みの委譲調整役では、当該サブエージェント自身の`agent_transcript_path`に
未消化の子エージェント起動が構造的に実在する場合もblockする。
報告本文のラベルや意味は解析せず、記述言語だけを機械判定する。
成果物と検証結果の妥当性は呼び出し元の実測と準拠系・盲検系のレビューへ委ねる。

正常許可と`stop_hook_active`真の再呼び出し時は、両ホスト共通でstdoutを空にする。
Codexではtranscriptを完了判定の契約へ利用できないため、調整役の子エージェント検査を実行せず、
安定入力の`last_assistant_message`による空報告検査だけを共有する。
言語ゲートはClaude Code専用とし、Codexでは`reason`の配送先と再提出の成立を確認できないため実行しない。
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import _hook_tool_input  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _hook_notice import formatter as _notice_formatter  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _response_language_check import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    SUBAGENT_REPORT_BLOCK_BODY,
    CheckOutcome,
    check_text,
)
from _session_state import read_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _stop_gate import pending_agent_launch_ids  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _transcript_agent_id import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    extract_transcript_agent_id as _extract_transcript_agent_id,
)

_HOOK_ID = "agent-toolkit/subagent-stop"

# `subagent_start_tracker.py`の同名定数と同一値を保つ。
_PLAN_IMPL_EXECUTOR_ACTIVE_KEY = "plan_impl_executor_active_subagent_sessions"


def _is_empty_completion_report(text: object) -> bool:
    """完了報告が空文字列だけで構成される場合に真を返す。"""
    return isinstance(text, str) and not text.strip()


_llm_notice = _notice_formatter(_HOOK_ID, default_tag="block")


def _resolve_payload_agent_id(payload: dict) -> str | None:
    """SubagentStop入力から対象サブエージェントの識別子を返す。"""
    agent_id = payload.get("agent_id")
    if isinstance(agent_id, str) and agent_id:
        return agent_id
    agent_id = _extract_transcript_agent_id(payload.get("agent_transcript_path"))
    if agent_id is not None:
        return agent_id
    # 旧版入力との互換性を保つ。現行仕様の`transcript_path`は親セッションを指す。
    return _extract_transcript_agent_id(payload.get("transcript_path"))


def _is_registered_orchestrator(payload: dict) -> bool:
    """対象がSubagentStartで登録済みの委譲調整役かを返す。"""
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return False
    agent_id = _resolve_payload_agent_id(payload)
    if agent_id is None:
        return False
    active = read_state(session_id).get(_PLAN_IMPL_EXECUTOR_ACTIVE_KEY)
    return isinstance(active, dict) and agent_id in active


def main(payload_text: str) -> int:
    """SubagentStop hookのエントリポイント。"""
    try:
        payload = json.loads(payload_text or "{}")
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0

    if payload.get("stop_hook_active") is True:
        return 0

    is_codex = _hook_tool_input.is_codex_payload(payload)
    transcript_path = payload.get("agent_transcript_path")
    if not isinstance(transcript_path, str) or not transcript_path:
        transcript_path = payload.get("transcript_path")
    session_id = payload.get("session_id")
    pending_ids = (
        pending_agent_launch_ids(
            transcript_path,
            session_id if isinstance(session_id, str) else "",
        )
        if not is_codex and isinstance(transcript_path, str)
        else set()
    )

    if pending_ids and _is_registered_orchestrator(payload):
        pending_id_list = ", ".join(sorted(pending_ids))
        reason = _llm_notice(
            "Complete or receive every child agent before stopping."
            " The registered orchestration agent still has an unfinished child agent launch."
            f" Pending child tool_use_id values: {pending_id_list}."
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0

    if _is_empty_completion_report(payload.get("last_assistant_message")):
        reason = _llm_notice(
            "Provide a non-empty completion report before stopping."
            " Restate the full report when resubmitting because the caller does not retain a blocked report body."
        )
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0

    report = payload.get("last_assistant_message")
    if not is_codex and isinstance(report, str):
        outcome, _ = check_text(report)
        if outcome is CheckOutcome.WARN:
            reason = _llm_notice(SUBAGENT_REPORT_BLOCK_BODY)
            print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
            return 0

    return 0

"""add-awi-by-userの作業完了報告に、`atk wi submission end`が作った報告用要約の行があるかをStopで検査する。

要約は正常な`end`だけが保存するため、中止・別の成果作業・add-awi-by-user以外の会話では判定しない。
検査するのは要約の各行の有無（空白を正規化した部分一致）だけで、自由な文章の意味の同一性は判定しない。
報告とみなす本文は、Stopの`last_assistant_message`と、Claude Codeでは要約の作成後にsend_to_userで送った本文に限る。
途中の地の文は作業完了報告ではないため数えない。
要約の作成後にユーザーの新しい発話があれば、作業完了報告の機会は過ぎたものとして検査せずに要約を消す。
新しい発話はホストごとのセッション記録の形式（Claude Codeの`user`、Codexの`event_msg`の`user_message`）で読む。
遮断で再報告を求める判定のため、非遮断通知を配送できないCodexのStopにも登録する。
委譲先での実行可否: ユーザーへ作業完了報告を送るメインだけを対象とし、hook入力で委譲先を除外する。
"""

from __future__ import annotations

import datetime
import json
from typing import Any

from agent_toolkit._hooks.notice import block_formatter as _block_formatter
lazy from agent_toolkit._common import automated_prompt, runtime_inserted, session_activity, transcript
lazy from agent_toolkit._hooks.agent_id import is_main_agent_context
lazy from agent_toolkit._hooks.host import is_codex_payload
lazy from agent_toolkit._hooks.stop_session import append_stop_log
lazy from agent_toolkit._hooks.stop_session import parse_stop_session as _parse_stop_session

_HOOK_ID = "submission_report_advisor"
_block = _block_formatter(_HOOK_ID)

_SEND_TO_USER_SUFFIX = "__send_to_user"
"""ホストが修飾するsend_to_userのツール名の末尾。プラグイン名の修飾はホストごとに異なる。"""


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _after(entry: dict[str, Any], created_at: datetime.datetime) -> bool:
    value = entry.get("timestamp")
    if not isinstance(value, str):
        return False
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")) > created_at
    except ValueError:
        return False


def _human_prompt(entry: dict[str, Any]) -> bool:
    """セッション記録のユーザー入力が人間の新しい発話かを返す。ツール結果と機械注入は除く。"""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isCompactSummary"):
        return False
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list) and any(isinstance(block, dict) and block.get("type") == "tool_result" for block in content):
        return False
    text = "\n".join(transcript.visible_text_blocks(content)) if isinstance(content, list) else content
    if not isinstance(text, str) or not text.strip():
        return False
    return not runtime_inserted.is_runtime_inserted_text(text) and not automated_prompt.contains(text)


def _codex_human_prompt(entry: dict[str, Any]) -> bool:
    """Codexのセッション記録の項目が人間の新しい発話かを返す。機械注入は除く。"""
    payload = entry.get("payload")
    if entry.get("type") != "event_msg" or not isinstance(payload, dict) or payload.get("type") != "user_message":
        return False
    text = payload.get("message")
    if not isinstance(text, str) or not text.strip():
        return False
    return not runtime_inserted.is_runtime_inserted_text(text) and not automated_prompt.contains(text)


def _send_to_user_bodies(entry: dict[str, Any]) -> list[str]:
    """Claude Codeのassistant項目からsend_to_userで送った本文を返す。"""
    message = entry.get("message")
    if entry.get("type") != "assistant" or not isinstance(message, dict):
        return []
    bodies: list[str] = []
    for block in message.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        if not str(block.get("name", "")).endswith(_SEND_TO_USER_SUFFIX) or not isinstance(block.get("input"), dict):
            continue
        body = block["input"].get("message")
        if isinstance(body, str):
            bodies.append(body)
    return bodies


def _reported_texts(payload: dict[str, Any], created_at: datetime.datetime) -> tuple[list[str], bool]:
    """要約の作成後の作業完了報告の本文と、作成後に人間の新しい発話があるかを返す。"""
    texts: list[str] = []
    last = payload.get("last_assistant_message")
    if isinstance(last, str):
        texts.append(last)
    path = payload.get("transcript_path")
    if not isinstance(path, str) or not path:
        return texts, False
    codex = is_codex_payload(payload)
    human = False
    for entry in transcript.read_transcript_entries(path):
        if not _after(entry, created_at):
            continue
        if codex:
            human = human or _codex_human_prompt(entry)
            continue
        human = human or _human_prompt(entry)
        texts.extend(_send_to_user_bodies(entry))
    return texts, human


def evaluate(payload_text: str) -> tuple[str, str]:
    """保存済みの報告用要約の行が最終応答に無ければ、欠けた行と再報告の方法を返してターンを続けさせる。"""
    resolved = _parse_stop_session(payload_text, lambda: None)
    if resolved is None:
        return "approve", ""
    session_id, payload = resolved
    if not is_main_agent_context(payload):
        return "approve", ""
    try:
        report = session_activity.read_submission_report(session_id)
    except ValueError:
        return "approve", ""
    if report is None:
        return "approve", ""
    created_text, lines = report
    created_at = datetime.datetime.fromisoformat(created_text)
    texts, human = _reported_texts(payload, created_at)
    if human:
        session_activity.clear_submission_report(session_id)
        append_stop_log(session_id, "submission_report_expired", {"created_at": created_text})
        return "approve", ""
    reported = _normalized("\n".join(texts))
    missing = [line for line in lines if _normalized(line) not in reported]
    if not missing:
        session_activity.clear_submission_report(session_id)
        append_stop_log(session_id, "submission_report_satisfied", {"created_at": created_text})
        return "approve", ""
    append_stop_log(session_id, "submission_report_missing", {"missing": json.dumps(missing, ensure_ascii=False)})
    return "block", _block(
        "add-awi-by-userの作業完了報告に、`atk wi submission end`が表示した報告用要約の次の行が無い:\n" + "\n".join(missing),
        fix="要約の全行を省略せず作業完了報告へ含めて、ユーザーへ再報告せよ。",
    )

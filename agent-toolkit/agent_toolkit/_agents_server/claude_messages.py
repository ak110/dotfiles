"""Claudeの出力とAPI再試行を共有状態へ反映する。"""

from __future__ import annotations

# Codexだけを使う場合はClaude Agent SDKを読み込まない。
# pylint: disable=import-outside-toplevel
import datetime
import re
import time
from typing import Any

from agent_toolkit._agents_server import child_environment, resume_waits, wait_output_tracking
from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._atk import claude_connection


def _assistant_text(message: Any) -> str:
    blocks = getattr(message, "content", ())
    return "\n".join(text for block in blocks if (text := getattr(block, "text", None)) is not None and isinstance(text, str))


def consume_api_retry(session: SessionState, data: dict[str, Any]) -> None:
    """CLIのturn内再試行を、活動時刻を保ったまま共通の失敗状態へ写す。"""
    delay = data.get("retry_delay_ms")
    retry_at = None
    if isinstance(delay, (int, float)) and not isinstance(delay, bool):
        retry_at = (datetime.datetime.now(datetime.UTC) + datetime.timedelta(milliseconds=delay)).isoformat()
    status = data.get("error_status")
    status = status if isinstance(status, int) and not isinstance(status, bool) else None
    attempt = data.get("attempt")
    attempt = attempt if isinstance(attempt, int) and not isinstance(attempt, bool) else None
    error = data.get("error")
    error_type = error.get("type") if isinstance(error, dict) else error
    limit = session.usage_limit
    waiting = status == 429 and limit is not None and limit.is_wait_target
    session.record_api_error(
        resume_waits.USAGE_LIMIT_ERROR_TYPE if waiting else str(error_type or "api_error"),
        status,
        retry_at=retry_at,
        attempt=attempt,
    )
    if waiting:
        assert limit is not None and session.api_error is not None
        session.api_error.update(limit_type=limit.limit_type, resets_at=limit.resets_at_iso() or retry_at)


def recover_api_retry(session: SessionState, data: dict[str, Any], *, record_notification: bool = True) -> None:
    """途中通知と終了失敗を同じ接続選択へ渡し、下位CLIの再試行終了への依存を解く。"""
    if record_notification:
        consume_api_retry(session, data)
    elif session.api_error is not None and session.usage_limit is not None and session.usage_limit.is_wait_target:
        session.api_error.update(
            type=resume_waits.USAGE_LIMIT_ERROR_TYPE,
            limit_type=session.usage_limit.limit_type,
            resets_at=session.usage_limit.resets_at_iso() or session.api_error.get("retry_at"),
        )
    if data.get("error_status") != 429 and session.usage_limit_resume_at is not None:
        resume_waits.clear_usage_limit_wait(session)
    if session.interrupt_requested or session.claude_api_resume_pending or session.usage_limit_resume_at is not None:
        return
    if session.usage_limit is None or not session.usage_limit.is_wait_target or data.get("error_status") != 429:
        return
    failure = {
        "status": "failed",
        "agent_message": session.agent_message,
        "error": {"message": session.usage_limit.describe(), "apiErrorStatus": 429},
    }
    resume_waits.begin_usage_limit_wait(session, failure)


def consume_assistant_message(session: SessionState, message: Any) -> None:
    """assistantメッセージをsessionの共有状態へ反映する。

    正常なメッセージではテキストの有無によらず活動時刻を進める。進めないと、ツール呼び出しだけを長時間続ける
    sessionへ停滞の印が付き、委譲元が不要な催促と巻き取りへ進む。
    ツール呼び出しの記録はテキストの反映後に行う。同じメッセージがテキストと
    ツール呼び出しの両方を持つ場合、後に発行したツール呼び出しを最後の行動とするためである。

    API失敗（429など）は`error`付きの合成メッセージとして`ResultMessage`より前に届くため、
    モデル出力と活動時刻の更新から除く。数えると、可用性失敗を確定する前に`start`の終了待ちを打ち切り、
    再試行が続く間も停滞を検出できなくなる。
    正常な起動では、完成したassistantメッセージより先に届くAPIの応答開始（`message_start`）で
    観測済みになる。完成したメッセージは思考と最初の内容ブロックの生成が終わるまで届かない。
    """
    error = getattr(message, "error", None)
    if error is not None:
        detail = _assistant_text(message)
        status_match = re.search(r"\bAPI Error:\s*(\d{3})\b", detail)
        status = int(status_match.group(1)) if status_match is not None else None
        if status is None and error == "rate_limit":
            status = 429
        error_type = "rate_limit_error" if error == "rate_limit" else str(error)
        session.record_api_error(error_type, status)
        return
    session.api_error = None
    session.record_turn_started()
    if session.usage_limit_resume_at is not None:
        resume_waits.clear_usage_limit_wait(session)
    session.model_output_observed = True
    # 正常なモデル出力は利用上限で拒否されていない証拠であり、以前の拒否の報告を待機の根拠から外す。
    if session.usage_limit is not None and session.usage_limit.is_wait_target:
        session.usage_limit = None
    text = _assistant_text(message)
    if text.strip():
        session.agent_message = text
        session.set_progress(text)
    wait_output_tracking.consume_claude_agents_server_message(session, message)
    session.touch()


def _returns_to_subscription(session: SessionState) -> bool:
    """利用上限でAPIへ移した委譲先が、解除予定時刻を過ぎ、使用順で現在の種別より前にサブスクリプションを持つかを返す。"""
    retry_at = session.claude_subscription_retry_at
    if retry_at is None or session.claude_connection not in claude_connection.API_KINDS or session.claude_role != "delegate":
        return False
    if time.time() < retry_at:
        return False
    kinds = [item.kind for item in claude_connection.candidates("delegate", env=child_environment.inherited()).connections]
    if "subscription" not in kinds:
        return False
    return session.claude_connection not in kinds or kinds.index("subscription") < kinds.index(session.claude_connection)


def _connection_kind(connection: claude_connection.Connection) -> str:
    """残高不足の並べ替えへ渡す接続の種別を返す。"""
    return connection.kind

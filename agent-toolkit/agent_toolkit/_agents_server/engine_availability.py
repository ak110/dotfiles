"""engineの可用性を理由に起動候補を除外するときの判定に使う関数と、除外理由の値。

engineごとの判定は各backendの`unavailable_reason`が本モジュールの関数を組み合わせて持つ。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

from agent_toolkit._agents_server.state import SessionState

# engineの可用性に起因し、別候補なら結果が変わり得る失敗の識別子。
# `codex app-server generate-json-schema`が出力する`CodexErrorInfo`列挙のうち、
# 利用枠超過、流量制限およびサーバー側過負荷に該当する区分へ限定する。
ENGINE_UNAVAILABLE_ERROR_INFO = frozenset({"usageLimitExceeded", "rateLimitExceeded", "serverOverloaded"})


# engineの可用性に起因し、別候補なら結果が変わり得るClaude APIのHTTPステータス。
# 429（rate_limit_error）と529（overloaded_error）は公式なエラーコード表が再試行可能とする。
# 401（authentication_error）と403（permission_error）は、認証情報の不備と権限の不足という
# 別々の原因を持つが、いずれも同じ候補での再試行では解消せず、別候補なら結果が変わり得る点で
# 前2者と一致するため同じ集合の要素とする。
# 500（api_error）はサービス内部の失敗であり、候補の変更で解決するとは限らないため含めない。
ENGINE_UNAVAILABLE_API_ERROR_STATUS = frozenset({401, 403, 429, 529})


# 旧版がClaudeの429へ記録した除外理由。Weekly limitと5時間の利用上限による拒否も含むため、
# 候補を除外する根拠にせず、その候補で起動して利用枠の報告から判定し直す。
# 現行版は解除待ちの対象を記録せず、対象外のClaudeの429を`429:<利用枠の種類>`として記録するため、旧版の記録と区別できる。
LEGACY_CLAUDE_RATE_LIMIT_REASON = "429"


# 接続先がCodex候補のモデルIDを受け付けなかった失敗へ付ける除外理由。
# Codex CLI 0.159.1の`codex app-server generate-json-schema`が出力する`CodexErrorInfo`の列挙にはモデルの不受理を表す値が無く、
# 接続先がモデルIDを拒否した失敗は`codexErrorInfo: other`で届く。その`message`はJSON文字列で、`error`オブジェクトが
# `type`・`code`・`param`を持ち、`param`が拒否した引数を示す（2026-09-30、`param: "model"`の`invalid_parameter_value`）。
# モデルIDの拒否は同じ候補で再試行しても解消せず、別候補なら結果が変わるため可用性失敗として扱う。
ENGINE_MODEL_REJECTED_REASON = "modelRejected"


def failure_error(session: SessionState) -> Mapping[str, Any] | None:
    """失敗で終端したsessionの`error`を返す。失敗でない場合と`error`が辞書でない場合は`None`を返す。"""
    if session.status != "failed" or not isinstance(session.error, dict):
        return None
    return session.error


def error_info_reason(error: Mapping[str, Any]) -> str | None:
    """`codexErrorInfo`が可用性の失敗の区分であれば、その値を除外理由として返す。"""
    error_info = error.get("codexErrorInfo")
    return str(error_info) if error_info in ENGINE_UNAVAILABLE_ERROR_INFO else None


def waits_for_usage_limit(error: Mapping[str, Any]) -> bool:
    """Weekly limitと5時間の利用上限による失敗かを返す。

    これらは解除まで待つ対象であり、別の候補へ切り替える理由にしない（ユーザー指示）。
    """
    return isinstance(error.get("usageLimit"), dict)


def unavailable_api_status(error: Mapping[str, Any]) -> Any:
    """`apiErrorStatus`が可用性の失敗のHTTPステータスであればその値を、そうでなければ`None`を返す。"""
    status = error.get("apiErrorStatus")
    return status if status in ENGINE_UNAVAILABLE_API_ERROR_STATUS else None


def rejected_parameter(error: Mapping[str, Any]) -> str | None:
    """失敗の`message`がJSONの`error`オブジェクトを持つ場合、拒否された引数名（`param`）を返す。"""
    message = error.get("message")
    if not isinstance(message, str):
        return None
    try:
        payload = json.loads(message)
    except ValueError:
        return None
    body = payload.get("error") if isinstance(payload, dict) else None
    param = body.get("param") if isinstance(body, dict) else None
    return param if isinstance(param, str) else None


def unavailable_reason(
    session: SessionState,
    *,
    api_status_reason: Callable[[Any], str] = str,
    other_reason: Callable[[Mapping[str, Any]], str | None] | None = None,
) -> str | None:
    """失敗で終端したsessionが、engineの可用性を理由に失敗した場合の除外理由を返す。

    `codexErrorInfo`の可用性の区分、利用上限（除外しない）、可用性のHTTPステータスの順に判定する。
    `api_status_reason`はHTTPステータスから除外理由を求め、`other_reason`はいずれにも当たらない失敗の`error`から
    engine固有の除外理由を判定する。
    """
    error = failure_error(session)
    if error is None:
        return None
    reason = error_info_reason(error)
    if reason is not None:
        return reason
    if waits_for_usage_limit(error):
        return None
    status = unavailable_api_status(error)
    if status is not None:
        return api_status_reason(status)
    return other_reason(error) if other_reason is not None else None

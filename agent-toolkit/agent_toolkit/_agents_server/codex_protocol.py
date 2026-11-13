"""Codexの通信例外と通知識別子を扱う。"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import typing
from typing import Any

from agent_toolkit._agents_server import session_errors
from agent_toolkit._agents_server.state import TERMINAL_STATUSES
from agent_toolkit._common import diagnostic_redaction

DEFAULT_WAIT_TIMEOUT = 300.0

# 拒否本文は名前の付かない鍵も反射するため、共通の規則に加えて連続32文字以上の鍵用文字を保守的に伏せる。
_DIAGNOSTIC_KEY = re.compile(r"[A-Za-z0-9_+/=-]{32,}")


def redact_diagnostic(message: str) -> str:
    """Codexの拒否診断から認証値と長い鍵状文字列を伏せ、1行へ整形する。"""
    message = _DIAGNOSTIC_KEY.sub(diagnostic_redaction.REDACTED, diagnostic_redaction.redact_secrets(message))
    return message.replace("\r", r"\r").replace("\n", r"\n")


class AppServerError(session_errors.DelegateBackendError):
    """App Serverとの通信または要求検証に失敗した。"""


class StartCandidateUnavailable(AppServerError):
    """モデルの実行開始前に候補の不成立が確定し、資源も回収済みであることを表す。"""

    def __init__(
        self,
        reason: str,
        *,
        scope: typing.Literal["candidate", "launch_engine"] = "candidate",
        next_action: str | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        # launch_engineは同じ起動要求内のengine共通能力の不成立で、永続記録へ渡さない。
        self.scope: typing.Literal["candidate", "launch_engine"] = scope
        # CLIの導入と認証の確認では解消しない原因（利用上限など）に限り、原因に応じた次の操作を持つ。
        self.next_action = next_action


class ResourceReleaseError(session_errors.ActionableRuntimeError):
    """委譲sessionが所有するApp Serverまたはその子孫の終了を確認できず、資源を解放済みにできない。"""

    def __init__(self, session_id: str, residual: list[str]) -> None:
        super().__init__(
            f"Codex App Server resources for session {session_id} are still running: {'; '.join(residual)}",
            next_action=(
                "資源は未解放のまま所有側に残っている。同じ`session_id`へ`stop`を再発行して解放を再試行する。"
                "再試行でも残る場合は、示したPIDのプロセスが終了できない原因を調べる"
            ),
        )
        self.session_id = session_id
        self.residual = residual


class JsonRpcResponseError(AppServerError):
    """App ServerがJSON-RPC error responseを返した。"""

    def __init__(self, method: str, code: Any, message: str, data: Any = None) -> None:
        super().__init__(f"{method}: {redact_diagnostic(message)}")
        self.method = method
        self.code = code
        self.data = data

    def diagnostic(self) -> tuple[str, str]:
        """拒否理由を、エラー本文に使う形とログに使う形で返す。

        `data`は任意の構造を持ちうるため、`codexErrorInfo`の値だけを含め、認証値を伏せる。
        """
        info = self.data.get("codexErrorInfo") if isinstance(self.data, dict) else None
        encoded = None if info is None else json.dumps(info, ensure_ascii=False)
        return (
            redact_diagnostic(f"code={self.code}: {self}" + ("" if encoded is None else f" (codexErrorInfo={encoded})")),
            redact_diagnostic(f"code={self.code} reason={self}" + ("" if encoded is None else f" codexErrorInfo={encoded}")),
        )


class TurnStartResponseError(AppServerError):
    """turn/startの応答形式が不正である。要求拒否を確認できないため、client生存中は受理状態が曖昧である。"""


@dataclasses.dataclass
class _WaitCompactionTurn:
    """担当の結果保留とは分けて観測する、圧縮操作の完了。"""

    original_turn_id: str
    done: asyncio.Event = dataclasses.field(default_factory=asyncio.Event)
    error: str | None = None


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _thread_id_from(params: Any) -> str | None:
    if not isinstance(params, dict):
        return None
    for key in ("threadId", "thread_id", "session_id"):
        value = params.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _turn_id_from(params: Any) -> str | None:
    if not isinstance(params, dict):
        return None
    for key in ("turnId", "turn_id"):
        value = params.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _response_turn_id(response: Any) -> str | None:
    """JSON-RPC responseからsteer対象turn IDを取り出す。"""
    if not isinstance(response, dict):
        return None
    turn_id = response.get("turnId")
    return turn_id if isinstance(turn_id, str) and turn_id else None


def _client_pid(client: Any) -> int | None:
    """App Serverのクライアントが起動した子プロセスのpidを返す。起動前と子プロセスを持たない代替では`None`を返す。"""
    pid = getattr(getattr(client, "process", None), "pid", None)
    return pid if isinstance(pid, int) and not isinstance(pid, bool) else None


def _public_turn_status(status: Any) -> str:
    if status == "inProgress":
        return "running"
    if status in TERMINAL_STATUSES:
        return status
    return "failed"

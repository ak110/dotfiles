"""backend所有接続を使い、獲得済みCodex resetを排他・冪等に消費する。"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import hashlib
import json
import logging
import subprocess
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_toolkit._agents_server import session_errors
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._atk import config, reset_reservations
from agent_toolkit._common import codex_weekly
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.file_lock import acquire_lock, release_lock
from agent_toolkit._common.session_layout import CODEX_AUTO_RESET_DIRECTORY_NAME

_LOG = logging.getLogger(__name__)

type Request = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
type Outcome = Literal["reset", "alreadyRedeemed", "nothingToReset", "noCredit"]


@dataclass(frozen=True)
class AutoResetResult:
    reset_confirmed: bool = False
    rate_limits: dict[str, Any] | None = None
    diagnostic: str = ""
    outcome: Outcome | None = None


class _Boundary(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False)


# 外部応答は、対象の選別に必要な構造・判別項目を先に検証し、消費に使う値だけへ利用時の制約を課す。
# 未利用の別bucket・非週次window・対象外のaccountやcreditは表示形式が変わり得るため、
# 応答全体へ消費用の制約を課すと、使わない値の形式差で自動resetが止まる。


class _AccountKind(_Boundary):
    type: str


class _AccountResponse(_Boundary):
    account: dict[str, Any] | None


class _AccountIdentity(_Boundary):
    email: str | None = None


class _Credit(_Boundary):
    id: str = Field(min_length=1)
    resetType: str
    status: str


class _Credits(_Boundary):
    availableCount: int = Field(ge=0)
    credits: list[Any] | None = None


def _available_credit(reset_credits: dict[str, Any]) -> _Credit | None:
    for item in _Credits.model_validate(reset_credits).credits or []:
        if isinstance(item, dict) and item.get("status") == "available" and item.get("resetType") == "codexRateLimits":
            return _Credit.model_validate(item)
    return None


class _Consume(_Boundary):
    outcome: Outcome


class _Attempt(_Boundary):
    model_config = ConfigDict(strict=True, extra="forbid")

    resets_at: int = Field(gt=0)
    idempotency_key: str
    status: Literal["pending", "success", "closed"]
    credit_id: str | None = None
    permission_id: str | None = None
    used_permissions: dict[str, int] = Field(default_factory=dict)
    # 共有先の予約台帳へ端末内の使用数を移した許可。移した後は端末内の数を合計に加えない。  # pyfltr: allow-comment
    carried_permissions: list[str] = Field(default_factory=list)


def enabled() -> bool:
    return config.resolve_mutable_setting("codex_auto_reset") == "true"


def eligible_account(account: Any) -> bool:
    return (
        isinstance(account, dict)
        and account.get("type") == "chatgpt"
        and isinstance(account.get("email"), str)
        and bool(account["email"].strip())
    )


async def check_auto_reset(
    request: Request,
    *,
    state_dir: Path,
    rpc_timeout: float = 30.0,
    configured: bool,
    active: bool = True,
    pace: float | None = None,
    on_permission_confirmed: Callable[[], None] | None = None,
    shared_notes: Path | None = None,
) -> AutoResetResult:
    """1回の確認を行う。消費確定後の再取得失敗は成功と利用枠未確認を分けて返す。  pyfltr: allow-comment

    state_dirは既存のstate_dirそのものを受け取り、配下の専用領域だけを使う。
    RPCはrequestが返すresult本文を受け取る。設定の有効性は呼出元で判定する。
    設定が有効で、共有先の予約台帳が試行を受理したときだけ消費を送る。
    その他の通信失敗・不正応答・状態破損は呼出元へ伝播する。
    """
    if not 0 < rpc_timeout < float("inf"):
        raise ValueError("rpc_timeoutには有限の正数が必要")
    root = state_dir / "agents-server" / CODEX_AUTO_RESET_DIRECTORY_NAME
    async with _transaction_lock(root) as locked:
        if not locked:
            return AutoResetResult(diagnostic="別serverが自動resetを確認中")
        account = _AccountResponse.model_validate(
            await _rpc(request, "account/read", {"refreshToken": False}, rpc_timeout)
        ).account
        if account is None or _AccountKind.model_validate(account).type != "chatgpt":
            return AutoResetResult(diagnostic="ChatGPTアカウントではないため自動resetを見送った")
        email = _AccountIdentity.model_validate(account).email
        raw = await _rpc(request, "account/rateLimits/read", {}, rpc_timeout)
        selected = codex_weekly.select_weekly(raw)
        weekly = selected.window
        if weekly is None:
            return AutoResetResult(rate_limits=raw, diagnostic=selected.diagnostic)
        if not email or not email.strip():
            return AutoResetResult(
                rate_limits=raw, diagnostic="安定したアカウント識別用emailを取得できないため自動resetを見送った"
            )
        identity = hashlib.sha256(email.encode("utf-8")).hexdigest()
        path = root / f"{identity}.json"
        previous = await run_file_operation(_read_attempt, path)
        same_window = previous is not None and previous.resets_at == weekly.resetsAt
        if same_window and previous is not None and previous.status == "success":
            return AutoResetResult(reset_confirmed=True, rate_limits=raw, diagnostic="同じweekly窓のresetは成功確認済み")
        if not configured or shared_notes is None:
            return AutoResetResult(rate_limits=raw, diagnostic="共有予約によるCodexの自動リセットは無効")
        if previous is not None and previous.status == "pending":
            attempt = previous
        else:
            seconds = weekly.resetsAt - time.time()
            remaining = 100 - weekly.usedPercent
            needed = remaining <= 0 or (pace is not None and remaining < pace * max(0, seconds))
            if not active or seconds <= 300 or not needed or weekly.usedPercent < 90:
                return AutoResetResult(rate_limits=raw, diagnostic="稼働・自然回復・消費ペースからresetはまだ不要")
            reset_credits = selected.reset_credits
            if reset_credits is None or _Credits.model_validate(reset_credits).availableCount == 0:
                return AutoResetResult(rate_limits=raw, diagnostic="利用可能なreset creditがない")
            credit = _available_credit(reset_credits)
            attempt = _Attempt(
                resets_at=weekly.resetsAt,
                idempotency_key=str(uuid.uuid4()),
                credit_id=credit.id if credit is not None else None,
                status="pending",
            )
            await run_file_operation(_write_attempt, path, attempt)
        try:
            await asyncio.to_thread(
                reset_reservations.reserve_weekly,
                shared_notes,
                key=attempt.idempotency_key,
                account=identity,
                resets_at=attempt.resets_at,
                credit_id=attempt.credit_id,
                legacy_permission_id=attempt.permission_id,
            )
        except reset_reservations.ReservationDenied as error:
            if attempt.permission_id is None:
                await run_file_operation(_write_attempt, path, attempt.model_copy(update={"status": "closed"}))
            return AutoResetResult(rate_limits=raw, diagnostic=f"共有予約を確認できないため消費を見送った: {error}")
        except (reset_reservations.ReservationError, OSError, ValueError, subprocess.SubprocessError) as error:
            return AutoResetResult(rate_limits=raw, diagnostic=f"共有予約を確認できないため消費を見送った: {error}")
        if on_permission_confirmed is not None:
            on_permission_confirmed()
        params = {"idempotencyKey": attempt.idempotency_key}
        if attempt.credit_id is not None:
            params["creditId"] = attempt.credit_id
        outcome = _Consume.model_validate(
            await _rpc(request, "account/rateLimitResetCredit/consume", params, rpc_timeout)
        ).outcome
        confirmed = outcome in ("reset", "alreadyRedeemed")
        status = "success" if confirmed else "closed"
        try:
            await asyncio.to_thread(reset_reservations.finish, shared_notes, attempt.idempotency_key, status)
        except (reset_reservations.ReservationError, OSError, ValueError, subprocess.SubprocessError) as error:
            _LOG.warning("Codex自動resetの結果を共有先へ記録できない: %s", error)
        else:
            await run_file_operation(_write_attempt, path, attempt.model_copy(update={"status": status}))
        if confirmed:
            try:
                raw = await _rpc(request, "account/rateLimits/read", {}, rpc_timeout)
                codex_weekly.select_weekly(raw)
            except (session_errors.DelegateBackendError, OSError, TimeoutError, ValueError) as error:
                return AutoResetResult(
                    reset_confirmed=True,
                    outcome=outcome,
                    diagnostic=f"reset成功後の利用枠を再取得できないため次の定期確認を待つ: {error}",
                )
        return AutoResetResult(reset_confirmed=confirmed, rate_limits=raw, outcome=outcome)


async def _rpc(request: Request, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    scope = asyncio.timeout(timeout)
    try:
        async with scope:
            return await request(method, params)
    except TimeoutError as error:
        if not scope.expired():
            raise
        raise TimeoutError(f"{method}が{timeout:g}秒以内に完了しない") from error


@contextlib.asynccontextmanager
async def _transaction_lock(root: Path) -> AsyncGenerator[bool]:

    stack = contextlib.ExitStack()
    try:
        locked = await run_file_operation(_acquire, root, stack)
        yield locked
    finally:
        await run_file_operation(stack.close)


def _acquire(root: Path, stack: contextlib.ExitStack) -> bool:
    root.mkdir(parents=True, exist_ok=True)
    handle = stack.enter_context((root / "transaction.lock").open("a+", encoding="utf-8"))
    try:
        acquire_lock(handle, blocking=False)
    except OSError as error:
        if error.errno in (errno.EAGAIN, errno.EACCES):
            return False
        raise
    stack.callback(release_lock, handle)
    return True


def _read_attempt(path: Path) -> _Attempt | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    attempt = _Attempt.model_validate(json.loads(raw))
    if str(uuid.UUID(attempt.idempotency_key)) != attempt.idempotency_key:
        raise ValueError("保存されたidempotencyKeyが不正")
    return attempt


def _write_attempt(path: Path, attempt: _Attempt) -> None:

    atomic_write(path, attempt.model_dump_json(), fsync=True)

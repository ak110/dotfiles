"""backend所有接続を使い、獲得済みCodex resetを排他・冪等に消費する。"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import hashlib
import json
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_toolkit._agents_server import session_errors
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.file_lock import acquire_lock, release_lock

type Request = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
type Outcome = Literal["reset", "alreadyRedeemed", "nothingToReset", "noCredit"]


@dataclass(frozen=True)
class AutoResetResult:
    """消費成功の確認と、確認時点の利用枠を分けて返す。"""

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
    """ChatGPTアカウントだけで検証する、アカウント別の冪等記録に使う識別値。"""

    email: str | None = None


class _LimitsEnvelope(_Boundary):
    rateLimits: dict[str, Any] | None = None
    rateLimitsByLimitId: dict[str, Any] | None = None
    rateLimitResetCredits: dict[str, Any] | None = None


class _BucketShape(_Boundary):
    limitId: str | None = None
    primary: dict[str, Any] | None = None
    secondary: dict[str, Any] | None = None


class _WindowDuration(_Boundary):
    windowDurationMins: int = Field(gt=0)


class _Window(_Boundary):
    usedPercent: float = Field(ge=0, le=100)
    windowDurationMins: int = Field(gt=0)
    resetsAt: int = Field(gt=0)


class _Credit(_Boundary):
    id: str = Field(min_length=1)
    resetType: str
    status: str


class _Credits(_Boundary):
    availableCount: int = Field(ge=0)
    credits: list[Any] | None = None


@dataclass(frozen=True)
class _Weekly:
    """選別したcodex枠の週次windowと、消費時にだけ検証するcredit在庫の生データ。"""

    window: _Window | None
    reset_credits: dict[str, Any] | None
    diagnostic: str = ""


def _select_weekly(raw: dict[str, Any]) -> _Weekly:
    """codex枠の週次windowを選び、選別と消費判断に使う値だけを検証する。"""
    limits = _LimitsEnvelope.model_validate(raw)
    by_id = limits.rateLimitsByLimitId
    candidate = by_id.get("codex") if by_id is not None else limits.rateLimits
    if candidate is None:
        return _Weekly(None, limits.rateLimitResetCredits, "codex利用枠を識別できない")
    bucket = _BucketShape.model_validate(candidate)
    if bucket.limitId not in (None, "codex") or (by_id is None and bucket.limitId != "codex"):
        return _Weekly(None, limits.rateLimitResetCredits, "codex利用枠を識別できない")
    for window in (bucket.primary, bucket.secondary):
        if window is not None and _WindowDuration.model_validate(window).windowDurationMins == 10080:
            return _Weekly(_Window.model_validate(window), limits.rateLimitResetCredits)
    return _Weekly(None, limits.rateLimitResetCredits, "weekly利用枠がない")


def _available_credit(reset_credits: dict[str, Any]) -> _Credit | None:
    """消費対象の種類・状態のcreditだけを検証し、対象外creditの詳細は使わない。"""
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


async def check_auto_reset(request: Request, *, state_dir: Path, rpc_timeout: float = 30.0) -> AutoResetResult:
    """1回の確認を行う。消費確定後の再取得失敗は成功と利用枠未確認を分けて返す。

    state_dirは既存のstate_dirそのものを受け取り、配下の専用領域だけを使う。
    RPCはrequestが返すresult本文を受け取る。設定の有効性は呼出元で判定する。
    その他の通信失敗・不正応答・状態破損は呼出元へ伝播する。
    """
    if not 0 < rpc_timeout < float("inf"):
        raise ValueError("rpc_timeoutには有限の正数が必要")
    root = state_dir / "agents-server" / "codex-auto-reset"
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
        selected = _select_weekly(raw)
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
        # 未確定の消費は、在庫や使用率が変わっていても同じキーで結果を回収する。
        if same_window and previous is not None and previous.status == "pending":
            attempt = previous
        else:
            if weekly.usedPercent < 99:
                return AutoResetResult(rate_limits=raw)
            reset_credits = selected.reset_credits
            if reset_credits is None or _Credits.model_validate(reset_credits).availableCount == 0:
                return AutoResetResult(rate_limits=raw, diagnostic="利用可能なreset creditがない")
            credit = _available_credit(reset_credits)
            attempt = _Attempt(
                resets_at=weekly.resetsAt,
                idempotency_key=str(uuid.uuid4()),
                status="pending",
                credit_id=credit.id if credit is not None else None,
            )
            await run_file_operation(_write_attempt, path, attempt)
        params = {"idempotencyKey": attempt.idempotency_key}
        if attempt.credit_id is not None:
            params["creditId"] = attempt.credit_id
        outcome = _Consume.model_validate(
            await _rpc(request, "account/rateLimitResetCredit/consume", params, rpc_timeout)
        ).outcome
        confirmed = outcome in ("reset", "alreadyRedeemed")
        await run_file_operation(
            _write_attempt, path, attempt.model_copy(update={"status": "success" if confirmed else "closed"})
        )
        if confirmed:
            try:
                raw = await _rpc(request, "account/rateLimits/read", {}, rpc_timeout)
                _select_weekly(raw)
            except (session_errors.DelegateBackendError, OSError, TimeoutError, ValueError) as error:
                # 消費は確定済みなので、利用枠の再取得だけを所有monitorへ引き継ぐ。
                return AutoResetResult(
                    reset_confirmed=True,
                    outcome=outcome,
                    diagnostic=f"reset成功後の利用枠を再取得できないため次の定期確認を待つ: {error}",
                )
        return AutoResetResult(reset_confirmed=confirmed, rate_limits=raw, outcome=outcome)


async def _rpc(request: Request, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    async with asyncio.timeout(timeout):
        return await request(method, params)


@contextlib.asynccontextmanager
async def _transaction_lock(root: Path) -> AsyncGenerator[bool]:
    # worker完了時に所有権を反映するため、取得直後の取消でもfinallyで回収できる。
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
    # RPC開始前のキー保存を耐障害境界にし、応答喪失後も論理試行を維持する。
    atomic_write(path, attempt.model_dump_json(), fsync=True)

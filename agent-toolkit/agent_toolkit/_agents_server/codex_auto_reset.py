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


class _Account(_Boundary):
    type: str
    email: str | None = None


class _AccountResponse(_Boundary):
    account: _Account | None


class _Window(_Boundary):
    usedPercent: float = Field(ge=0, le=100)
    windowDurationMins: int = Field(gt=0)
    resetsAt: int = Field(gt=0)


class _Bucket(_Boundary):
    limitId: str | None = None
    primary: _Window | None = None
    secondary: _Window | None = None


class _Credit(_Boundary):
    id: str = Field(min_length=1)
    resetType: str
    status: str


class _Credits(_Boundary):
    availableCount: int = Field(ge=0)
    credits: list[_Credit] | None = None


class _Limits(_Boundary):
    rateLimits: _Bucket | None = None
    rateLimitsByLimitId: dict[str, _Bucket] | None = None
    rateLimitResetCredits: _Credits | None = None


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
        if account is None or account.type != "chatgpt":
            return AutoResetResult(diagnostic="ChatGPTアカウントではないため自動resetを見送った")
        raw = await _rpc(request, "account/rateLimits/read", {}, rpc_timeout)
        limits = _Limits.model_validate(raw)
        bucket = limits.rateLimitsByLimitId.get("codex") if limits.rateLimitsByLimitId is not None else limits.rateLimits
        if (
            bucket is None
            or bucket.limitId not in (None, "codex")
            or (limits.rateLimitsByLimitId is None and bucket.limitId != "codex")
        ):
            return AutoResetResult(rate_limits=raw, diagnostic="codex利用枠を識別できない")
        weekly = next(
            (
                window
                for window in (bucket.primary, bucket.secondary)
                if window is not None and window.windowDurationMins == 10080
            ),
            None,
        )
        if weekly is None:
            return AutoResetResult(rate_limits=raw, diagnostic="weekly利用枠がない")
        if not account.email or not account.email.strip():
            return AutoResetResult(
                rate_limits=raw, diagnostic="安定したアカウント識別用emailを取得できないため自動resetを見送った"
            )
        identity = hashlib.sha256(account.email.encode("utf-8")).hexdigest()
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
            reset_credits = limits.rateLimitResetCredits
            if reset_credits is None or reset_credits.availableCount == 0:
                return AutoResetResult(rate_limits=raw, diagnostic="利用可能なreset creditがない")
            credit = next(
                (
                    item
                    for item in reset_credits.credits or []
                    if item.status == "available" and item.resetType == "codexRateLimits"
                ),
                None,
            )
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
                _Limits.model_validate(raw)
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

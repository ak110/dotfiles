"""模擬RPCと専用状態領域でreset消費の境界・排他・冪等性を検証する。

RPC標本: https://learn.chatgpt.com/docs/app-server のアカウント・利用枠・earned reset節
（2026-11-05取得）。実在アカウントとcreditには接続しない。
"""

from __future__ import annotations

import asyncio
import copy
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_toolkit._agents_server import codex_auto_reset
from agent_toolkit._agents_server.codex_auto_reset import check_auto_reset


class _RPC:
    def __init__(self, used: float = 100, count: int = 1) -> None:
        self.account: dict[str, Any] = {"account": {"type": "chatgpt", "email": "example@example.invalid"}}
        self.limits: dict[str, Any] = {
            "rateLimits": {
                "limitId": "codex",
                "primary": None,
                "secondary": {"usedPercent": used, "windowDurationMins": 10080, "resetsAt": 1900000000},
            },
            "rateLimitResetCredits": {"availableCount": count, "credits": None},
        }
        self.outcomes: list[str | Exception] = ["reset"]
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.entered = asyncio.Event()
        self.release: asyncio.Event | None = None
        self.refresh_error = False
        self.refreshed = {
            "rateLimits": {
                "limitId": "codex",
                "primary": None,
                "secondary": {"usedPercent": 0, "windowDurationMins": 10080, "resetsAt": 1900000000},
            }
        }

    async def __call__(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, dict(params)))
        if method == "account/read":
            assert params == {"refreshToken": False}
            return copy.deepcopy(self.account)
        if method == "account/rateLimits/read":
            assert not params
            if self.calls[-2:][0][0] == "account/rateLimitResetCredit/consume":
                if self.refresh_error:
                    raise OSError("refresh lost")
                return copy.deepcopy(self.refreshed)
            return copy.deepcopy(self.limits)
        assert method == "account/rateLimitResetCredit/consume"
        uuid.UUID(params["idempotencyKey"])
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return {"outcome": outcome}

    @property
    def consumptions(self) -> list[dict[str, Any]]:
        return [params for method, params in self.calls if method == "account/rateLimitResetCredit/consume"]


def _permission(**updates: Any) -> dict[str, Any]:
    return {
        "id": "permitted-period",
        "engine": "codex",
        "account": "example@example.invalid",
        "scope": "codex",
        "count": 1,
        "from": "1970-01-01T00:00:00+00:00",
        "until": "1970-01-01T01:00:00+00:00",
        **updates,
    }


@pytest.mark.parametrize(
    "updates",
    [
        {"engine": "claude"},
        {"account": "other"},
        {"scope": "other"},
        {"count": 0},
        {"from": "1970-01-01T00:10:00+00:00"},
        {"until": "1970-01-01T00:00:30+00:00"},
    ],
)
@pytest.mark.asyncio
async def test_automatic_allocation_never_consumes_outside_permission(tmp_path, monkeypatch, updates):
    monkeypatch.setattr(codex_auto_reset.time, "time", lambda: 100.0)
    rpc = _RPC(used=100)
    result = await check_auto_reset(rpc, state_dir=tmp_path, permissions=[_permission(**updates)])
    assert not result.reset_confirmed
    assert not rpc.consumptions


@pytest.mark.asyncio
async def test_permission_count_survives_window_change(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_auto_reset.time, "time", lambda: 100.0)
    rpc = _RPC(used=100)
    assert (await check_auto_reset(rpc, state_dir=tmp_path, permissions=[_permission()])).reset_confirmed
    rpc.limits["rateLimits"]["secondary"]["resetsAt"] += 1
    result = await check_auto_reset(rpc, state_dir=tmp_path, permissions=[_permission()])
    assert not result.reset_confirmed
    assert len(rpc.consumptions) == 1


@pytest.mark.asyncio
async def test_permission_retry_reuses_reserved_count_and_key(tmp_path, monkeypatch):
    monkeypatch.setattr(codex_auto_reset.time, "time", lambda: 100.0)
    rpc = _RPC(used=100)
    rpc.outcomes = [OSError("response lost"), "alreadyRedeemed"]
    with pytest.raises(OSError, match="response lost"):
        await check_auto_reset(rpc, state_dir=tmp_path, permissions=[_permission()])
    rpc.limits["rateLimitResetCredits"]["availableCount"] = 0
    assert (await check_auto_reset(rpc, state_dir=tmp_path, permissions=[_permission()])).reset_confirmed
    assert rpc.consumptions[0] == rpc.consumptions[1]


@pytest.mark.parametrize("active,reset", [(False, 1900000000), (True, 200)])
@pytest.mark.asyncio
async def test_idle_or_near_natural_recovery_does_not_spend(tmp_path, monkeypatch, active, reset):
    monkeypatch.setattr(codex_auto_reset.time, "time", lambda: 100.0)
    rpc = _RPC(used=100)
    rpc.limits["rateLimits"]["secondary"]["resetsAt"] = reset
    assert not (await check_auto_reset(rpc, state_dir=tmp_path, permissions=[_permission()], active=active)).reset_confirmed
    assert not rpc.consumptions


async def _check_permitted(rpc: _RPC, *, state_dir: Path, **kwargs: Any) -> codex_auto_reset.AutoResetResult:
    permission = _permission(count=10, **{"from": "2000-01-01T00:00:00+00:00", "until": "2100-01-01T00:00:00+00:00"})
    return await check_auto_reset(rpc, state_dir=state_dir, permissions=[permission], **kwargs)


@pytest.mark.parametrize(("used", "count", "expected"), [(89, 1, False), (99, 1, False), (100, 1, True), (100, 0, False)])
@pytest.mark.asyncio
async def test_threshold_and_inventory(tmp_path: Path, used: float, count: int, expected: bool) -> None:
    rpc = _RPC(used, count)
    result = await _check_permitted(rpc, state_dir=tmp_path)
    assert result.reset_confirmed is expected
    assert len(rpc.consumptions) == int(expected)
    assert result.rate_limits == (rpc.refreshed if expected else rpc.limits)
    if expected:
        assert rpc.calls[-1][0] == "account/rateLimits/read"


@pytest.mark.parametrize("outcome", ["nothingToReset", "noCredit"])
@pytest.mark.asyncio
async def test_definitive_non_consumption_closes_attempt(tmp_path: Path, outcome: str) -> None:
    rpc = _RPC()
    rpc.outcomes = [outcome, "reset"]
    first = await _check_permitted(rpc, state_dir=tmp_path)
    assert not first.reset_confirmed
    assert first.outcome == outcome
    second = await _check_permitted(rpc, state_dir=tmp_path)
    assert second.reset_confirmed
    assert rpc.consumptions[0]["idempotencyKey"] != rpc.consumptions[1]["idempotencyKey"]


@pytest.mark.asyncio
async def test_unknown_consumption_reuses_persisted_key_even_without_inventory(tmp_path: Path) -> None:
    rpc = _RPC()
    rpc.outcomes = [OSError("response lost"), "alreadyRedeemed"]
    with pytest.raises(OSError, match="response lost"):
        await _check_permitted(rpc, state_dir=tmp_path)
    rpc.limits["rateLimitResetCredits"]["availableCount"] = 0
    rpc.limits["rateLimits"]["secondary"]["usedPercent"] = 0
    result = await _check_permitted(rpc, state_dir=tmp_path)
    assert result.reset_confirmed
    assert result.outcome == "alreadyRedeemed"
    assert result.rate_limits == rpc.refreshed
    assert rpc.consumptions[0] == rpc.consumptions[1]


@pytest.mark.asyncio
async def test_successful_window_is_not_consumed_again_with_stale_limits(tmp_path: Path) -> None:
    rpc = _RPC()
    assert (await _check_permitted(rpc, state_dir=tmp_path)).reset_confirmed
    assert (await _check_permitted(rpc, state_dir=tmp_path)).reset_confirmed
    assert len(rpc.consumptions) == 1
    paths = list((tmp_path / "agents-server" / "codex-auto-reset").glob("*.json"))
    assert len(paths) == 1
    assert "example@example.invalid" not in paths[0].read_text(encoding="utf-8")
    assert "example" not in paths[0].name
    rpc.limits["rateLimits"]["secondary"]["resetsAt"] += 604800
    rpc.outcomes = ["reset"]
    assert (await _check_permitted(rpc, state_dir=tmp_path)).reset_confirmed
    assert rpc.consumptions[0]["idempotencyKey"] != rpc.consumptions[1]["idempotencyKey"]


@pytest.mark.asyncio
async def test_refresh_failure_preserves_success_guard(tmp_path: Path) -> None:
    rpc = _RPC()
    rpc.refresh_error = True
    result = await _check_permitted(rpc, state_dir=tmp_path)
    assert result.reset_confirmed and result.rate_limits is None
    assert "refresh lost" in result.diagnostic
    rpc.limits = rpc.refreshed
    assert (await _check_permitted(rpc, state_dir=tmp_path)).reset_confirmed
    assert len(rpc.consumptions) == 1


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.asyncio
async def test_two_servers_and_cancellation_release_lock(tmp_path: Path, cancel: bool) -> None:
    first, second = _RPC(), _RPC()
    first.release = asyncio.Event()
    task = asyncio.create_task(_check_permitted(first, state_dir=tmp_path))
    try:
        await asyncio.wait_for(first.entered.wait(), timeout=5)
        result = await _check_permitted(second, state_dir=tmp_path)
        assert not result.reset_confirmed
        assert not second.calls
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            second.outcomes = ["alreadyRedeemed"]
        else:
            first.release.set()
            assert (await task).reset_confirmed
        resumed = await _check_permitted(second, state_dir=tmp_path)
        assert resumed.reset_confirmed
        assert len(second.consumptions) == int(cancel)
        if cancel:
            assert first.consumptions == second.consumptions
    finally:
        first.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_timeout_keeps_attempt_for_next_check(tmp_path: Path) -> None:
    rpc = _RPC()
    rpc.release = asyncio.Event()
    with pytest.raises(TimeoutError):
        await _check_permitted(rpc, state_dir=tmp_path, rpc_timeout=0.02)
    assert rpc.entered.is_set()
    rpc.release.set()
    rpc.outcomes = ["alreadyRedeemed"]
    assert (await _check_permitted(rpc, state_dir=tmp_path)).reset_confirmed
    assert rpc.consumptions[0] == rpc.consumptions[1]


@pytest.mark.parametrize("account", [None, {"type": "apiKey"}, {"type": "bedrock"}, {"type": "chatgpt", "email": None}])
@pytest.mark.asyncio
async def test_unidentified_accounts_never_consume(tmp_path: Path, account: dict[str, Any] | None) -> None:
    rpc = _RPC()
    rpc.account = {"account": account}
    result = await _check_permitted(rpc, state_dir=tmp_path)
    assert not result.reset_confirmed
    assert result.diagnostic
    assert not rpc.consumptions


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("usedPercent", True),
        ("usedPercent", -1),
        ("usedPercent", 101),
        ("usedPercent", float("nan")),
        ("usedPercent", "99"),
        ("windowDurationMins", True),
        ("resetsAt", True),
        ("resetsAt", 0),
    ],
)
@pytest.mark.asyncio
async def test_invalid_window_never_consumes(tmp_path: Path, field: str, value: Any) -> None:
    rpc = _RPC()
    rpc.limits["rateLimits"]["secondary"][field] = value
    with pytest.raises(ValidationError):
        await _check_permitted(rpc, state_dir=tmp_path)
    assert not rpc.consumptions


@pytest.mark.parametrize("count", [True, -1, "1", 1.5])
@pytest.mark.asyncio
async def test_invalid_inventory_never_consumes(tmp_path: Path, count: Any) -> None:
    rpc = _RPC()
    rpc.limits["rateLimitResetCredits"]["availableCount"] = count
    with pytest.raises(ValidationError):
        await _check_permitted(rpc, state_dir=tmp_path)
    assert not rpc.consumptions


@pytest.mark.asyncio
async def test_multi_bucket_selects_codex_and_passes_available_credit(tmp_path: Path) -> None:
    rpc = _RPC()
    codex = copy.deepcopy(rpc.limits["rateLimits"])
    rpc.limits["rateLimits"]["secondary"]["usedPercent"] = 0
    rpc.limits["rateLimitsByLimitId"] = {"codex": codex}
    rpc.limits["rateLimitResetCredits"]["credits"] = [{"id": "credit-1", "resetType": "codexRateLimits", "status": "available"}]
    assert (await _check_permitted(rpc, state_dir=tmp_path)).reset_confirmed
    assert rpc.consumptions[0]["creditId"] == "credit-1"


@pytest.mark.asyncio
async def test_unused_formats_do_not_block_selection_or_refresh(tmp_path: Path) -> None:
    """未利用bucket・非週次window・対象外creditの形式差では、初回確認と成功後の再取得を止めない。"""
    rpc = _RPC()
    codex = copy.deepcopy(rpc.limits["rateLimits"])
    codex["primary"] = {"usedPercent": "unknown", "windowDurationMins": 300, "resetsAt": None}
    other = {"limitId": "other", "secondary": {"usedPercent": "n/a", "windowDurationMins": True}}
    rpc.limits = {
        "rateLimitsByLimitId": {"codex": codex, "other": other},
        "rateLimitResetCredits": {
            "availableCount": 1,
            "credits": [
                {"id": "", "resetType": "otherRateLimits", "status": "available"},
                {"id": None, "resetType": "codexRateLimits", "status": "redeemed"},
                "not a credit",
                {"id": "credit-1", "resetType": "codexRateLimits", "status": "available"},
            ],
        },
    }
    refreshed = copy.deepcopy(rpc.limits)
    refreshed["rateLimitsByLimitId"]["codex"]["secondary"]["usedPercent"] = 0
    rpc.refreshed = refreshed
    result = await _check_permitted(rpc, state_dir=tmp_path)
    assert result.reset_confirmed
    assert result.rate_limits == refreshed
    assert [params.get("creditId") for params in rpc.consumptions] == ["credit-1"]


@pytest.mark.asyncio
async def test_non_chatgpt_account_details_are_not_validated(tmp_path: Path) -> None:
    rpc = _RPC()
    rpc.account = {"account": {"type": "apiKey", "email": 123}}
    result = await _check_permitted(rpc, state_dir=tmp_path)
    assert not result.reset_confirmed and result.diagnostic
    assert not rpc.consumptions


@pytest.mark.parametrize("change", ["chatgpt_email", "selected_credit_id", "codex_window_duration", "bucket_shape", "limit_id"])
@pytest.mark.asyncio
async def test_values_used_for_selection_or_consumption_are_still_validated(tmp_path: Path, change: str) -> None:
    rpc = _RPC()
    if change == "chatgpt_email":
        rpc.account = {"account": {"type": "chatgpt", "email": 123}}
    elif change == "selected_credit_id":
        rpc.limits["rateLimitResetCredits"]["credits"] = [{"id": "", "resetType": "codexRateLimits", "status": "available"}]
    elif change == "codex_window_duration":
        rpc.limits["rateLimits"]["primary"] = {"usedPercent": 0, "windowDurationMins": "300", "resetsAt": 1}
    elif change == "bucket_shape":
        rpc.limits["rateLimitsByLimitId"] = {"codex": "not a bucket"}
    else:
        rpc.limits["rateLimits"]["limitId"] = 1
    with pytest.raises(ValidationError):
        await _check_permitted(rpc, state_dir=tmp_path)
    assert not rpc.consumptions


@pytest.mark.parametrize("change", ["other_bucket", "no_weekly", "missing_bucket", "missing_inventory"])
@pytest.mark.asyncio
async def test_ineligible_limits_do_not_consume(tmp_path: Path, change: str) -> None:
    rpc = _RPC()
    if change == "other_bucket":
        rpc.limits["rateLimits"]["limitId"] = "codex_other"
    elif change == "no_weekly":
        rpc.limits["rateLimits"]["secondary"]["windowDurationMins"] = 300
    elif change == "missing_bucket":
        rpc.limits["rateLimitsByLimitId"] = {}
    else:
        rpc.limits["rateLimitResetCredits"] = None
    result = await _check_permitted(rpc, state_dir=tmp_path)
    assert not result.reset_confirmed
    assert not rpc.consumptions


@pytest.mark.asyncio
async def test_invalid_consume_outcome_preserves_pending_key(tmp_path: Path) -> None:
    rpc = _RPC()
    rpc.outcomes = ["unknown", "alreadyRedeemed"]
    with pytest.raises(ValidationError):
        await _check_permitted(rpc, state_dir=tmp_path)
    assert (await _check_permitted(rpc, state_dir=tmp_path)).reset_confirmed
    assert rpc.consumptions[0] == rpc.consumptions[1]


@pytest.mark.parametrize("corruption", ["not json", "{}", '{"resets_at": true}'])
@pytest.mark.asyncio
async def test_corrupt_state_is_not_empty_state(tmp_path: Path, corruption: str) -> None:
    rpc = _RPC()
    rpc.outcomes = [OSError("response lost")]
    with pytest.raises(OSError):
        await _check_permitted(rpc, state_dir=tmp_path)
    path = next((tmp_path / "agents-server" / "codex-auto-reset").glob("*.json"))
    path.write_text(corruption, encoding="utf-8")
    rpc.calls.clear()
    with pytest.raises((json.JSONDecodeError, ValidationError)):
        await _check_permitted(rpc, state_dir=tmp_path)
    assert not rpc.consumptions

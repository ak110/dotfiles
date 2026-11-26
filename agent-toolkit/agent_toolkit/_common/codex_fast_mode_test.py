"""Codex自動速度の保持状態を有界の観測列で検証する。  pyfltr: allow-comment"""

import asyncio
import datetime
import itertools

import pytest

from agent_toolkit._common import codex_fast_mode, state_paths

# pylint: disable=protected-access


def test_confirmed_observations_cannot_be_reversed_by_transient_failures():
    start = datetime.datetime(2026, 11, 26, tzinfo=datetime.UTC)
    valid_until = start + datetime.timedelta(minutes=4)
    events = {
        "business-a": ("a", "priority", True),
        "business-b": ("b", "priority", True),
        "plus-a": ("a", "default", True),
        "plus-unidentified": (None, "default", False),
        "logout": (None, "default", False),
        "offer-ended": ("a", "default", False),
        "api": (None, "default", False),
        "failed-a": ("a", None, False),
        "failed-b": ("b", None, False),
        "failed-unidentified": (None, None, False),
    }
    for length in range(1, 5):
        for sequence in itertools.product(events, repeat=length):
            retained = None
            expected_tier = None
            expected_workspace = None
            expected_until = start
            for offset, event in enumerate(sequence):
                now = start + datetime.timedelta(minutes=offset * 2)
                workspace, confirmed, reusable = events[event]
                allowed = (
                    expected_tier
                    if confirmed is None and workspace is not None and workspace == expected_workspace and now < expected_until
                    else None
                )
                if confirmed is not None:
                    expected_tier = confirmed if reusable and now < valid_until else None
                    expected_workspace = workspace
                    expected_until = min(now + datetime.timedelta(minutes=5), valid_until)
                elif now >= expected_until or workspace is not None and expected_workspace != workspace:
                    expected_tier = None
                decision = codex_fast_mode.FastTier(confirmed, event) if confirmed else None
                retained, result = codex_fast_mode._transition(
                    retained, workspace, now, decision, valid_until if reusable else None
                )
                assert (result.tier if result else None) == allowed, sequence
                assert (retained["tier"] if retained else None) == expected_tier, sequence
                if retained:
                    assert datetime.datetime.fromisoformat(retained["until"]) == expected_until, sequence


@pytest.mark.asyncio
@pytest.mark.parametrize("plan_identity", [True, False])
@pytest.mark.parametrize("failure", ["error", "timeout"])
@pytest.mark.parametrize("workspace", ["same", "other", None])
async def test_confirmed_ineligible_plan_replaces_success(tmp_path, monkeypatch, plan_identity, failure, workspace):
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(codex_fast_mode, "_RETRY_ATTEMPT_SECONDS", 0.005)
    now = datetime.datetime(2026, 11, 26, tzinfo=datetime.UTC)
    routing = {"workspaceRouting": {"chatgptAccountId": "same"}}
    business = {**routing, "account": {"type": "chatgpt", "planType": "business"}}
    plus = {"account": {"type": "chatgpt", "planType": "plus"}, **(routing if plan_identity else {})}

    async def request(method, _params):
        if method == "account/rateLimits/read":
            return {"accountId": workspace}
        assert method == "account/read"
        if failure == "timeout":
            await asyncio.sleep(60)
        raise OSError("account/read")

    async def decide(auth):
        return await codex_fast_mode.auto_service_tier(request, ".", subscription=True, provider="openai", now=now, auth=auth)

    assert (await decide(business)).tier == "priority"
    assert (await decide(plus)).tier == "default"
    recovered = await decide(None)
    assert recovered.tier == "default"
    assert recovered.source == ("previous" if plan_identity and workspace == "same" else "unavailable")

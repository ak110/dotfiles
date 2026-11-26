"""Codex自動速度の保持状態を有界の観測列で検証する。  pyfltr: allow-comment"""

import asyncio
import datetime
import itertools
import threading

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
                live_until = retained.get("until") if retained else None
                live = live_until is not None and now < datetime.datetime.fromisoformat(live_until)
                assert (retained.get("tier") if retained is not None and live else None) == expected_tier, sequence
                if live and live_until is not None:
                    assert datetime.datetime.fromisoformat(live_until) == expected_until, sequence


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


def test_out_of_order_saves_do_not_replace_newer_confirmed_observation():
    start = datetime.datetime(2026, 11, 26, tzinfo=datetime.UTC)
    end = start + datetime.timedelta(minutes=1)
    observations = (("a", "priority"), ("b", "priority"), ("a", "default"), (None, "default"))
    for length in range(1, 5):
        for sequence in itertools.product(observations, repeat=length):
            expected_identity, expected_tier = sequence[-1]
            for order in itertools.permutations(range(length)):
                record = None
                for index in order:
                    identity, tier = sequence[index]
                    at = start + datetime.timedelta(seconds=index)
                    record, _ = codex_fast_mode._transition(record, identity, at, codex_fast_mode.FastTier(tier, tier), end)
                assert record is not None
                assert record["at"] == (start + datetime.timedelta(seconds=length - 1)).isoformat(), (sequence, order)
                for candidate in ("a", "b", None):
                    _, reused = codex_fast_mode._transition(
                        record, candidate, start + datetime.timedelta(seconds=length), None, None
                    )
                    assert (reused.tier if reused is not None else None) == (
                        expected_tier if candidate is not None and candidate == expected_identity else None
                    ), (sequence, order, candidate)


@pytest.mark.asyncio
@pytest.mark.parametrize("newer", ["logout", "api", "plus"])
@pytest.mark.parametrize("older_last", [True, False])
@pytest.mark.parametrize("failure", ["exception", "timeout"])
async def test_confirmed_invalidation_cannot_be_undone_by_delayed_write(tmp_path, monkeypatch, newer, older_last, failure):
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(codex_fast_mode, "_RETRY_ATTEMPT_SECONDS", 0.01)
    now = datetime.datetime(2026, 11, 26, tzinfo=datetime.UTC)
    latest = now + datetime.timedelta(seconds=1)
    started, unblock = threading.Event(), threading.Event()
    persist = codex_fast_mode._cache_decision

    def delayed(provider, workspace, instant, decision=None, deadline=None, **kwargs):
        if older_last and decision is not None and decision.tier == "priority":
            started.set()
            assert unblock.wait(5)
        return persist(provider, workspace, instant, decision, deadline, **kwargs)

    async def fail(method, _params):
        if method == "account/rateLimits/read":
            return {"accountId": "same"}
        if failure == "timeout":
            await asyncio.sleep(60)
        raise OSError("account/read")

    async def invalidate():
        if newer == "plus":
            result = await codex_fast_mode.auto_service_tier(
                fail,
                ".",
                subscription=True,
                provider="openai",
                now=latest,
                auth={"account": {"type": "chatgpt", "planType": "plus"}},
            )
            assert result.tier == "default"
        else:
            discovered = {"account": None if newer == "logout" else {"type": "apiKey"}}
            await codex_fast_mode.observe_selection_auth(("openai",), discovered, latest)

    monkeypatch.setattr(codex_fast_mode, "_cache_decision", delayed)
    auth = {"account": {"type": "chatgpt", "planType": "business"}, "workspaceRouting": {"chatgptAccountId": "same"}}
    pending = asyncio.create_task(
        codex_fast_mode.auto_service_tier(fail, ".", subscription=True, provider="openai", now=now, auth=auth)
    )
    if older_last:
        try:
            assert await asyncio.to_thread(started.wait, 5)
            await invalidate()
        finally:
            unblock.set()
            await pending
    else:
        await pending
        await invalidate()
    recovered = await codex_fast_mode.auto_service_tier(
        fail, ".", subscription=True, provider="openai", now=latest + datetime.timedelta(seconds=1)
    )
    assert (recovered.tier, recovered.source) == ("default", "unavailable")
    await codex_fast_mode.auto_service_tier(
        fail, ".", subscription=True, provider="openai", now=latest, auth=auth, auth_observed_at=now
    )
    recovered = await codex_fast_mode.auto_service_tier(
        fail, ".", subscription=True, provider="openai", now=latest + datetime.timedelta(seconds=1)
    )
    assert (recovered.tier, recovered.source) == ("default", "unavailable")

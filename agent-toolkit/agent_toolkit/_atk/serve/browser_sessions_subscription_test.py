"""セッション一覧の初期取得とSSE購読の前後関係を確かめる実ブラウザー試験。"""

import asyncio
import functools

import playwright.async_api
import pytest

from agent_toolkit._atk.serve import sessions as serve_sessions
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _hold_route,
    _observe_screen_requests,
    _screen_harness_fixture,
    _ScreenHarness,
    _wait_screen_requests_consumed,
)

pytestmark = [pytest.mark.browser]


@pytest.mark.asyncio(loop_scope="session")
async def test_initial_resync_preserves_earlier_selection_and_scroll(screen_harness: _ScreenHarness) -> None:
    """購読登録で初期一覧を再取得する間の先行選択と閲覧位置を、初期URL復元で上書きしない。"""
    page = screen_harness.page
    await _observe_screen_requests(page)
    subscription_started = asyncio.Event()
    subscription_release = asyncio.Event()
    first_started = asyncio.Event()
    first_release = asyncio.Event()
    second_started = asyncio.Event()
    second_release = asyncio.Event()
    calls = 0

    async def held_list(route: playwright.async_api.Route) -> None:
        nonlocal calls
        calls += 1
        response = await route.fetch()
        if calls == 1:
            first_started.set()
            await first_release.wait()
        elif calls == 2:
            second_started.set()
            await second_release.wait()
        await route.fulfill(response=response)

    await page.route(
        "**/api/sessions/events",
        functools.partial(_hold_route, started=subscription_started, release=subscription_release),
    )
    await page.route("**/api/sessions/list", held_list)
    try:
        await page.goto(screen_harness.base_url + "/sessions", wait_until="domcontentloaded")
        await asyncio.wait_for(first_started.wait(), timeout=10)
        subscription_release.set()
        await page.wait_for_function("window.__screenRequests.subscribed['/api/sessions/events'] > 0")
        first_release.set()
        await asyncio.wait_for(second_started.wait(), timeout=10)
        await page.locator("#sessions .session-item").last.click()
        await page.locator("#detail .event").first.wait_for(state="visible")
        selected_url = page.url
        # 閲覧位置を機械や文字列の折り返しに依存させず、スクロール可能な本文を用意する。
        await page.add_style_tag(content="#detail .event { min-height: 1000px; }")
        main = page.locator("#screen-sessions main")
        await main.evaluate("element => { element.scrollTop = 240; }")
        assert await main.evaluate("element => element.scrollTop") == 240
        second_release.set()
        await page.wait_for_function("window.__screenRequests.pending === 0")
        await page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        assert page.url == selected_url
        assert await main.evaluate("element => element.scrollTop") == 240
    finally:
        subscription_release.set()
        first_release.set()
        second_release.set()


@pytest.mark.asyncio(loop_scope="session")
async def test_late_subscription_resynchronizes_the_initial_session_list(screen_harness: _ScreenHarness) -> None:
    """初期取得後に購読が登録されても、未購読中の一覧更新を取り込む。"""
    page = screen_harness.page
    started = asyncio.Event()
    release = asyncio.Event()
    hold_subscription = functools.partial(_hold_route, started=started, release=release)
    await page.route("**/api/sessions/events", hold_subscription)
    try:
        async with page.expect_response("**/api/sessions/list") as initial:
            await page.goto(screen_harness.base_url + "/sessions", wait_until="domcontentloaded")
        payload = await (await initial.value).json()
        await asyncio.wait_for(started.wait(), timeout=10)
        await playwright.async_api.expect(page.locator("#sessions .session-item")).to_have_count(2)
        # 未購読中の変更は通知を受け取れない。一覧APIだけが更新済みの状態を制御する。
        additional = {**payload["sessions"][0], "session_id": "late-session", "path": "late-session.jsonl"}
        latest = {**payload, "sessions": [*payload["sessions"], additional]}

        async def updated_list(route: playwright.async_api.Route) -> None:
            await route.fulfill(json=latest)

        await page.route("**/api/sessions/list", updated_list)
        release.set()
        await playwright.async_api.expect(page.locator("#sessions .session-item")).to_have_count(3)
    finally:
        release.set()


@pytest.mark.asyncio(loop_scope="session")
async def test_observation_waits_for_registered_subscription(
    screen_harness: _ScreenHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HTTP接続後も購読が未登録なら、更新通知を送る前の準備完了にしない。"""
    started = asyncio.Event()
    release = asyncio.Event()
    subscribe = serve_sessions.subscribe

    async def held_subscription(state: serve_sessions.SessionsState) -> asyncio.Queue[str]:
        started.set()
        await release.wait()
        return await subscribe(state)

    monkeypatch.setattr(serve_sessions, "subscribe", held_subscription)
    page = screen_harness.page
    await _observe_screen_requests(page)
    await page.goto(screen_harness.base_url + "/sessions", wait_until="domcontentloaded")
    await asyncio.wait_for(started.wait(), timeout=10)
    await page.wait_for_function(
        "window.__screenRequests.opened['/api/sessions/events'] > 0 && window.__screenRequests.pending === 0"
    )
    waiting = asyncio.create_task(_wait_screen_requests_consumed(page, ("/api/sessions/events",)))
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(waiting), timeout=0.1)
    finally:
        release.set()
        await asyncio.wait_for(waiting, timeout=10)
    assert screen_harness.sessions_context.state.subscribers

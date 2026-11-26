import asyncio
import urllib.parse

import playwright.async_api
import pytest

from agent_toolkit._atk.serve import runtime as serve_runtime
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _browser_harness_fixture,
    _hold_route,
    _screen_harness_fixture,
    _ScreenHarness,
)
from agent_toolkit._testing.serve_browser_shell_support import (
    _SCREEN_STREAMS,
    _assert_other_streams_unrequested,
    _open_screen,
    _request_count,
    _resynchronize_visible,
)

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.cpu_limit(
    1.5,
    reason=(
        "初期接続または初期取得を保留している間は測定へ進まず、解放後の可視化更新を検証できる。実ブラウザ"
        "ーの観測とtrace回収が必要（CPU最大実測0.600秒）。"
    ),
)
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("delayed_path", ["/api/events", "/api/entries"])
async def test_initial_screen_acquisition_is_consumed_before_observation(
    screen_harness: _ScreenHarness,
    delayed_path: str,
) -> None:
    page = screen_harness.page
    started = asyncio.Event()
    release = asyncio.Event()

    async def delay_first(route: playwright.async_api.Route) -> None:
        if started.is_set():
            await route.continue_()
        else:
            await _hold_route(route, started=started, release=release)

    await page.route(lambda url: urllib.parse.urlsplit(url).path == delayed_path, delay_first)
    ready = asyncio.create_task(_open_screen(screen_harness, "wi"))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        if delayed_path == "/api/entries":
            await page.wait_for_function("window.__screenRequests.opened['/api/events'] > 0")
        _assert_other_streams_unrequested(screen_harness, "wi")
        assert not ready.done(), "初期処理の消費前に観測区間へ進んだ"
    finally:
        release.set()
        await ready
    await _resynchronize_visible(page, "wi")


@pytest.mark.cpu_limit(
    1.5,
    reason=(
        "対象画面の可視化経路を無効にすると、別画面や初期取得の成功だけでは更新を受理しない。実ブラウザー"
        "の観測とtrace回収が必要（CPU最大実測0.509秒）。"
    ),
)
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("screen", tuple(_SCREEN_STREAMS))
async def test_visibility_observation_rejects_a_disabled_update_path(
    screen_harness: _ScreenHarness,
    screen: str,
) -> None:
    page = screen_harness.page

    async def disable_visibility(route: playwright.async_api.Route) -> None:
        response = await route.fetch()
        source = await response.text()
        marker = "  resyncWhenVisible("
        assert source.count(marker) == 1
        await route.fulfill(response=response, body=source.replace(marker, "  (() => {})("))

    await page.route(f"**/static/{screen}.js", disable_visibility)
    await _open_screen(screen_harness, screen)
    with pytest.raises(playwright.async_api.TimeoutError):
        await _resynchronize_visible(page, screen, timeout=1_000)


@pytest.mark.asyncio(loop_scope="session")
async def test_revisiting_screens_resubscribes_without_repeating_initial_sync(screen_harness: _ScreenHarness) -> None:
    harness = screen_harness
    page = harness.page
    await _open_screen(harness, "wi")
    await page.wait_for_function("window.__screenRequests.completed['/api/plans/files'] >= 1")
    before_sync = _request_count(harness, "/api/sync")
    for screen, label in (
        ("plans", "計画ファイル"),
        ("sessions", "セッション"),
        ("wi", "ワークアイテム"),
        ("plans", "計画ファイル"),
        ("sessions", "セッション"),
    ):
        events, listing = _SCREEN_STREAMS[screen]
        before = await page.evaluate(
            "([events, listing]) => [window.__screenRequests.subscribed[events] || 0, "
            "window.__screenRequests.completed[listing] || 0]",
            [events, listing],
        )
        inactive = {path: _request_count(harness, path) for path, _ in _SCREEN_STREAMS.values() if path != events}
        await page.locator("nav.app-nav").get_by_role("link", name=label).click()
        await page.wait_for_function(
            "([events, listing, opens, completed]) => window.__screenRequests.subscribed[events] > opens "
            "&& window.__screenRequests.completed[listing] > completed",
            arg=[events, listing, *before],
        )
        assert {path: _request_count(harness, path) for path in inactive} == inactive

    assert _request_count(harness, "/api/sync") == before_sync


@pytest.mark.asyncio(loop_scope="session")
async def test_initial_screen_request_does_not_block_other_screen_prefetch(
    screen_harness: _ScreenHarness,
) -> None:
    harness = screen_harness
    sessions_release = asyncio.Event()
    sessions_handled = asyncio.Event()
    sync_requested = asyncio.Event()
    plans_requested = asyncio.Event()

    async def delay_sessions(route: playwright.async_api.Route) -> None:
        try:
            await sessions_release.wait()
            await route.continue_()
        finally:
            sessions_handled.set()

    def record_requests(request: playwright.async_api.Request) -> None:
        if request.url.endswith("/api/sync"):
            sync_requested.set()
        if request.url.endswith("/api/plans/files"):
            plans_requested.set()

    harness.page.on("request", record_requests)
    await harness.page.route("**/api/sessions/list", delay_sessions)
    try:
        await harness.page.goto(harness.base_url + "/sessions")
        await playwright.async_api.expect(harness.page.locator("#screen-sessions")).to_be_visible()
        await asyncio.wait_for(plans_requested.wait(), timeout=5)
        assert not sync_requested.is_set()
    finally:
        sessions_release.set()
        await asyncio.wait_for(sessions_handled.wait(), timeout=5)
        await harness.page.unroute("**/api/sessions/list", delay_sessions)


@pytest.mark.cpu_limit(
    1.5,
    reason=(
        "通知もheartbeatも届かない時間が閾値を超えると、3画面とも再接続して一覧を取り直す。実ブ"
        "ラウザーの観測とtrace回収が必要（CPU最大実測0.521秒）。"
    ),
)
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("screen", tuple(_SCREEN_STREAMS))
async def test_silent_sse_is_reconnected_and_lists_are_refetched(
    screen_harness: _ScreenHarness, monkeypatch: pytest.MonkeyPatch, screen: str
) -> None:
    monkeypatch.setattr(serve_runtime, "SSE_HEARTBEAT_SEC", 3600.0)
    monkeypatch.setattr(serve_runtime, "SSE_STALL_SEC", 1.0)
    await _open_screen(screen_harness, screen)
    events, listing = _SCREEN_STREAMS[screen]
    page = screen_harness.page
    before = await page.evaluate(
        "([events, listing]) => [window.__screenRequests.subscribed[events], window.__screenRequests.completed[listing]]",
        [events, listing],
    )
    await page.wait_for_function(
        "([events, listing, subscribed, completed]) => window.__screenRequests.subscribed[events] > subscribed "
        "&& window.__screenRequests.completed[listing] > completed",
        arg=[events, listing, *before],
    )
    _assert_other_streams_unrequested(screen_harness, screen)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("screen", tuple(_SCREEN_STREAMS))
async def test_regular_heartbeat_keeps_sse_without_refetching(
    screen_harness: _ScreenHarness, monkeypatch: pytest.MonkeyPatch, screen: str
) -> None:
    monkeypatch.setattr(serve_runtime, "SSE_HEARTBEAT_SEC", 0.2)
    monkeypatch.setattr(serve_runtime, "SSE_STALL_SEC", 1.0)
    await _open_screen(screen_harness, screen)
    events, listing = _SCREEN_STREAMS[screen]
    page = screen_harness.page
    before = {path: _request_count(screen_harness, path) for path in (events, listing)}
    beats = await page.evaluate("path => window.__screenRequests.heartbeats[path] || 0", events)
    await page.wait_for_function(
        "([path, beats]) => window.__screenRequests.heartbeats[path] >= beats + 15",
        arg=[events, beats],
    )
    assert {path: _request_count(screen_harness, path) for path in before} == before
    _assert_other_streams_unrequested(screen_harness, screen)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("screen", tuple(_SCREEN_STREAMS))
async def test_visible_tab_and_bfcache_restore_resynchronize_screens(screen_harness: _ScreenHarness, screen: str) -> None:
    page = screen_harness.page
    await _open_screen(screen_harness, screen)
    await _resynchronize_visible(page, screen)
    events, listing = _SCREEN_STREAMS[screen]
    before = await page.evaluate(
        "([events, listing]) => [window.__screenRequests.subscribed[events], window.__screenRequests.completed[listing]]",
        [events, listing],
    )
    await page.evaluate(
        """() => {
            window.dispatchEvent(new PageTransitionEvent("pagehide", {persisted: true}));
            window.dispatchEvent(new PageTransitionEvent("pageshow", {persisted: true}));
        }"""
    )
    await page.wait_for_function(
        "([events, listing, subscribed, completed]) => window.__screenRequests.subscribed[events] > subscribed "
        "&& window.__screenRequests.completed[listing] > completed",
        arg=[events, listing, *before],
    )
    _assert_other_streams_unrequested(screen_harness, screen)

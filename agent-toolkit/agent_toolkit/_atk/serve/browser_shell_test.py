"""serveのbrowser_shellに関するsynchronizationの試験。"""

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
    _wait_screen_requests_consumed,
)
from agent_toolkit._testing.serve_browser_shell_support import (
    _SCREEN_STREAMS,
    _open_all_screens,
    _request_count,
    _resynchronize_visible,
)

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("delayed_path", ["/api/events", "/api/entries"])
async def test_initial_screen_acquisition_is_consumed_before_observation(
    screen_harness: _ScreenHarness,
    delayed_path: str,
) -> None:
    """初期接続または初期取得を保留している間は測定へ進まず、解放後の可視化更新を検証できる。"""
    page = screen_harness.page
    started = asyncio.Event()
    release = asyncio.Event()

    async def delay_first(route: playwright.async_api.Route) -> None:
        if started.is_set():
            await route.continue_()
        else:
            await _hold_route(route, started=started, release=release)

    await page.route(lambda url: urllib.parse.urlsplit(url).path == delayed_path, delay_first)
    ready = asyncio.create_task(_open_all_screens(screen_harness))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        await playwright.async_api.expect(page.locator("#sessions .session-item")).to_have_count(2)
        await page.wait_for_function(
            """delayed => {
              const probe = window.__screenRequests;
              return probe.opened['/api/plans/events'] > 0 && probe.opened['/api/sessions/events'] > 0 &&
                (delayed === '/api/events' ? probe.completed['/api/entries'] > 0 : probe.opened['/api/events'] > 0);
            }""",
            arg=delayed_path,
        )
        assert not ready.done(), "初期処理の消費前に観測区間へ進んだ"
    finally:
        release.set()
        await ready
    await _resynchronize_visible(page)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("screen", ["wi", "sessions"])
async def test_visibility_observation_rejects_a_disabled_update_path(
    screen_harness: _ScreenHarness,
    screen: str,
) -> None:
    """対象画面の可視化経路を無効にすると、別画面や初期取得の成功だけでは更新を受理しない。"""
    page = screen_harness.page

    async def disable_visibility(route: playwright.async_api.Route) -> None:
        response = await route.fetch()
        source = await response.text()
        marker = "  resyncWhenVisible("
        assert source.count(marker) == 1
        await route.fulfill(response=response, body=source.replace(marker, "  (() => {})("))

    await page.route(f"**/static/{screen}.js", disable_visibility)
    await _open_all_screens(screen_harness)
    with pytest.raises(playwright.async_api.TimeoutError):
        await _resynchronize_visible(page, timeout=1_000)


@pytest.mark.asyncio(loop_scope="session")
async def test_revisiting_screens_does_not_repeat_initial_requests(screen_harness: _ScreenHarness) -> None:
    """初期化済みの画面へ再訪しても、同期と一覧の初期要求を繰り返さない。"""
    harness = screen_harness
    page = harness.page
    await _open_all_screens(harness)
    # 計画一覧は購読の登録の合図の後に1回だけ取得する。その前段のhost/root取得は一覧要求のpendingに数えないため、
    # 一覧の本文消費と全画面の購読の確立を待って再訪前の基準を確定する。
    await page.wait_for_function("window.__screenRequests.completed['/api/plans/files'] >= 1")
    await _wait_screen_requests_consumed(page, tuple(events for events, _ in _SCREEN_STREAMS.values()))
    tracked_suffixes = ("/api/sync", "/api/plans/files", "/api/sessions/list")
    before = {suffix: sum(url.endswith(suffix) for url in harness.requests) for suffix in tracked_suffixes}

    await page.locator("nav.app-nav").get_by_role("link", name="ワークアイテム").click()
    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
    await _wait_screen_requests_consumed(page, tuple(events for events, _ in _SCREEN_STREAMS.values()))

    assert {suffix: sum(url.endswith(suffix) for url in harness.requests) for suffix in tracked_suffixes} == before


@pytest.mark.asyncio(loop_scope="session")
async def test_initial_screen_request_does_not_block_other_screen_prefetch(
    screen_harness: _ScreenHarness,
) -> None:
    """初期画面の応答待ち中にも他画面の初回要求を開始する。"""
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


@pytest.mark.asyncio(loop_scope="session")
async def test_silent_sse_is_reconnected_and_lists_are_refetched(
    screen_harness: _ScreenHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通知もheartbeatも届かない時間が閾値を超えると、3画面とも再接続して一覧を取り直す。"""
    monkeypatch.setattr(serve_runtime, "SSE_HEARTBEAT_SEC", 3600.0)
    monkeypatch.setattr(serve_runtime, "SSE_STALL_SEC", 1.0)
    await _open_all_screens(screen_harness)
    before = {
        name: (_request_count(screen_harness, stream), _request_count(screen_harness, listing))
        for name, (stream, listing) in _SCREEN_STREAMS.items()
    }

    for name, (events, listing) in _SCREEN_STREAMS.items():
        await screen_harness.page.wait_for_function(
            """expected => {
                const probe = window.__screenRequests;
                return probe.opened[expected.events] > expected.opened &&
                  probe.completed[expected.listing] > expected.completed;
            }""",
            arg={"events": events, "listing": listing, "opened": before[name][0], "completed": before[name][1]},
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_regular_heartbeat_keeps_sse_without_refetching(
    screen_harness: _ScreenHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """heartbeatが届き続ける間は、閾値を超えて待っても再接続も一覧の再取得も起きない。"""
    monkeypatch.setattr(serve_runtime, "SSE_HEARTBEAT_SEC", 0.2)
    monkeypatch.setattr(serve_runtime, "SSE_STALL_SEC", 1.0)
    await _open_all_screens(screen_harness)
    before = {path: _request_count(screen_harness, path) for pair in _SCREEN_STREAMS.values() for path in pair}
    heartbeats = await screen_harness.page.evaluate("window.__screenRequests.heartbeats")
    await screen_harness.page.wait_for_function(
        "expected => Object.keys(expected).every(path => window.__screenRequests.heartbeats[path] >= expected[path] + 15)",
        arg={stream: heartbeats.get(stream, 0) for stream, _ in _SCREEN_STREAMS.values()},
    )

    assert {path: _request_count(screen_harness, path) for path in before} == before


@pytest.mark.asyncio(loop_scope="session")
async def test_visible_tab_and_bfcache_restore_resynchronize_screens(screen_harness: _ScreenHarness) -> None:
    """タブが表示へ戻るとWI画面とセッション画面が一覧を取り直し、bfcacheからの復帰では再接続する。"""
    page = screen_harness.page
    await _open_all_screens(screen_harness)
    await _resynchronize_visible(page)
    await _wait_screen_requests_consumed(page, tuple(events for events, _ in _SCREEN_STREAMS.values()))
    before = await page.evaluate("window.__screenRequests.opened")
    await page.evaluate(
        """() => {
            window.dispatchEvent(new PageTransitionEvent("pagehide", {persisted: true}));
            window.dispatchEvent(new PageTransitionEvent("pageshow", {persisted: true}));
        }"""
    )

    await page.wait_for_function(
        "expected => Object.entries(expected).every(([path, count]) => window.__screenRequests.opened[path] > count)",
        arg=before,
    )
    await _wait_screen_requests_consumed(page, tuple(events for events, _ in _SCREEN_STREAMS.values()))

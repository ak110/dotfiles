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
    _open_screen,
    _request_count,
    _resynchronize_visible,
)

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.cpu_limit(1.5, reason="実Chromiumの2タブでHTTP接続の共有と購読の引き継ぎを確認する")
@pytest.mark.asyncio(loop_scope="session")
async def test_browser_tabs_share_event_streams_and_transfer_ownership(screen_harness: _ScreenHarness) -> None:
    first = screen_harness.page
    context = first.context
    await _open_screen(screen_harness, "wi")
    lock_names = [f"atk serve SSE: {path}" for path, _ in _SCREEN_STREAMS.values()]
    await first.wait_for_function(
        "async names => { const {held} = await navigator.locks.query(); "
        "return names.every(name => held.some(lock => lock.name === name)); }",
        arg=lock_names,
    )
    opened_streams: list[str] = []
    transferred = asyncio.Event()
    second = await context.new_page()

    def observe(request: playwright.async_api.Request) -> None:
        path = urllib.parse.urlsplit(request.url).path
        if any(path == stream for stream, _ in _SCREEN_STREAMS.values()) and request.frame.page is second:
            opened_streams.append(path)
        if len(opened_streams) == len(lock_names):
            transferred.set()

    context.on("request", observe)
    try:
        await second.goto(screen_harness.base_url + "/?filename=awi.md")
        await playwright.async_api.expect(second.locator("#detail-filename")).to_have_text("awi.md")
        await playwright.async_api.expect(second.locator("#connection-status")).to_have_attribute("data-connected", "true")
        assert not opened_streams
        await first.close()
        await asyncio.wait_for(transferred.wait(), timeout=5)
        for stream in (path for path, _ in _SCREEN_STREAMS.values()):
            assert opened_streams.count(stream) == 1
    finally:
        await second.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_browser_tabs_can_open_work_item_links_on_plain_http(screen_harness: _ScreenHarness) -> None:
    first = screen_harness.page
    base = screen_harness.base_url.replace("127.0.0.1", "0.0.0.0")
    expect = playwright.async_api.expect
    await first.goto(base + "/")
    assert await first.evaluate("window.isSecureContext") is False
    await expect(first.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await expect(first.locator("#preview")).to_contain_text("本文-初回")
    await expect(first.locator("#detail .event-kind").first).to_be_attached()
    second = await first.context.new_page()
    try:
        await second.goto(base + "/?filename=awi.md")
        await expect(second.locator("#detail-filename")).to_have_text("awi.md")
        await expect(second.locator("#connection-status")).to_have_attribute("data-connected", "true")
    finally:
        await second.close()


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
async def test_switching_and_revisiting_screens_keeps_the_prepared_views(
    screen_harness: _ScreenHarness,
) -> None:
    harness = screen_harness
    page = harness.page
    expect = playwright.async_api.expect
    await _open_screen(harness, "wi")
    targets = (
        ("plans", "計画ファイル", "#preview :text-is('本文-初回')"),
        ("sessions", "セッション", "#detail .event-kind"),
        ("agents", "エージェント", "#agents-hosts section"),
        ("usage", "利用枠", "#usage-content .metric-section"),
        ("settings", "設定", "#settings-content [data-setting]"),
        ("wi", "ワークアイテム", "#entry-list .entry-row"),
    )
    for _, _, ready in targets:
        await expect(page.locator(ready).first).to_be_attached(timeout=10_000)
    paths = {path for pair in _SCREEN_STREAMS.values() for path in pair}
    paths.update(
        {
            "/api/plans/file",
            "/api/sessions/detail",
            "/api/agents/hosts",
            "/api/agents/list",
            "/api/usage",
            "/api/usage/refresh",
            "/api/settings",
            "/api/settings/prepare",
            "/api/sync",
        }
    )
    await page.wait_for_function("window.__screenRequests.pending === 0")
    before = {path: _request_count(harness, path) for path in paths}
    for _ in range(2):
        for screen, label, ready in targets:
            await page.locator("nav.app-nav").get_by_role("link", name=label).click()
            await expect(page.locator(f"#screen-{screen}")).to_be_visible()
            await expect(page.locator(ready).first).to_be_visible()
    await page.go_back()
    await expect(page.locator("#screen-settings")).to_be_visible()
    await page.go_forward()
    await expect(page.locator("#screen-wi")).to_be_visible()
    assert {path: _request_count(harness, path) for path in paths} == before


@pytest.mark.asyncio(loop_scope="session")
async def test_switching_reuses_a_delayed_background_request(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    expect = playwright.async_api.expect
    started, release = asyncio.Event(), asyncio.Event()
    path = "/api/settings"

    async def hold_settings(route: playwright.async_api.Route) -> None:
        await _hold_route(route, started=started, release=release)

    await page.route(lambda url: urllib.parse.urlsplit(url).path == path, hold_settings)
    try:
        await page.goto(screen_harness.base_url + "/")
        await asyncio.wait_for(started.wait(), timeout=5)
        navbar = page.get_by_role("navigation", name="画面")
        await navbar.get_by_role("link", name="設定").click()
        await expect(page.locator("#screen-settings")).to_be_visible()
        await navbar.get_by_role("link", name="ワークアイテム").click()
        await navbar.get_by_role("link", name="設定").click()
        assert _request_count(screen_harness, path) == 1
    finally:
        release.set()
    await expect(page.locator("#settings-content [data-setting]").first).to_be_visible()
    assert _request_count(screen_harness, path) == 1


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


@pytest.mark.parametrize(
    ("screen", "endpoint", "resolved"),
    [
        ("plans", "**/api/plans/files", "#files .file"),
        ("sessions", "**/api/sessions/list", "#sessions .session-item"),
        ("wi", "**/api/entries?*", "#entry-list .entry-select"),
        ("settings", "**/api/settings*", "#settings-content [data-setting]"),
        ("agents", "**/api/agents/hosts", "#agents-hosts section"),
    ],
)
@pytest.mark.asyncio(loop_scope="session")
async def test_read_recovery_does_not_offer_an_action_after_one_failure(
    screen_harness: _ScreenHarness, screen: str, endpoint: str, resolved: str
) -> None:
    page = screen_harness.page
    first = asyncio.Event()
    attempts = 0

    async def fail_first(route: playwright.async_api.Route) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await route.fulfill(status=503, json={"error": "一時失敗"})
            first.set()
        else:
            await route.continue_()

    await page.route(endpoint, fail_first)
    await page.goto(screen_harness.base_url + ("/" if screen == "wi" else f"/{screen}"))
    await asyncio.wait_for(first.wait(), 5)
    status = page.locator("#update-status")
    await playwright.async_api.expect(status).to_be_hidden()
    await playwright.async_api.expect(page.locator(resolved).first).to_be_visible()
    assert attempts >= 2
    await playwright.async_api.expect(status).to_be_hidden()

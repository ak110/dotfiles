"""serveのbrowser_wiに関するsearch_loadingの試験。"""

import asyncio
import functools

import playwright.async_api
import pytest

from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _browser_harness_fixture,
    _BrowserHarness,
    _hold_route,
    _observe_screen_requests,
    _open_filters,
    _screen_harness_fixture,
    _wait_screen_requests_consumed,
)
from agent_toolkit._testing.serve_browser_wi_support import _entry_path

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
async def test_user_filter_announcement_survives_same_state_sse_repo_request(
    browser_harness: _BrowserHarness,
) -> None:
    """同じ状態の対象リポジトリ候補の要求がSSEで後から発行されても、エンドユーザーの操作による件数通知を完了する。"""
    harness = browser_harness
    page = harness.page
    await _observe_screen_requests(page)
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#result-status")).to_have_text("")
    await playwright.async_api.expect(page.locator('#target-filter option[value="example/repo"]')).to_have_count(1)
    await _wait_screen_requests_consumed(page, ("/api/events",))
    await _open_filters(page)
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()
    release_background = asyncio.Event()
    request_count = 0

    async def delay_first_repo_request(route: playwright.async_api.Route) -> None:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            first_started.set()
            await release_first.wait()
        else:
            second_started.set()
            await release_background.wait()
        await route.continue_()

    await page.route("**/api/repos?status=all", delay_first_repo_request)
    await page.locator("#result-status").evaluate("element => { element.textContent = '変更前の通知'; }")
    await page.locator("#kind-filter").evaluate("element => { element.value = 'awi'; }")
    # 状態フィルター変更時の処理を起動し、対象リポジトリの再取得を伴う一覧の更新を実行する。
    await page.locator("#state-filter").dispatch_event("change")
    await asyncio.wait_for(first_started.wait(), timeout=5)
    try:
        harness.current_state.publish()
        release_first.set()
        await playwright.async_api.expect(page.locator("#result-status")).to_have_text("10件を表示")
        await playwright.async_api.expect(page.locator("#entry-list .entry-select[data-kind=awi]")).to_have_count(10)
        await asyncio.wait_for(second_started.wait(), timeout=5)
        release_background.set()
        await _wait_screen_requests_consumed(page, ("/api/events",))
        await playwright.async_api.expect(page.locator("#result-status")).to_have_text("10件を表示")
        async with page.expect_response("**/api/repos?status=all") as response:
            await page.evaluate("window.dispatchEvent(new Event('focus'))")
        await (await response.value).finished()
        await _wait_screen_requests_consumed(page, ("/api/events",))
        await playwright.async_api.expect(page.locator("#result-status")).to_have_text("10件を表示")
    finally:
        release_first.set()
        release_background.set()


@pytest.mark.asyncio(loop_scope="session")
async def test_manual_sync_stays_busy_until_entries_render(browser_harness: _BrowserHarness) -> None:
    """一覧の新着が描画されるまで同期ボタンと結果表示を完了させない。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    (_entry_path(browser_harness.root, "delayed-sync.md")).write_text(
        "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\n---\n\n遅延した同期結果\n",
        encoding="utf-8",
    )
    entries_started = asyncio.Event()
    release_entries = asyncio.Event()

    await page.route("**/api/entries?*", functools.partial(_hold_route, started=entries_started, release=release_entries))
    try:
        await page.locator("#refresh-button").click()
        await asyncio.wait_for(entries_started.wait(), timeout=5)
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
        await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "true")
        await playwright.async_api.expect(page.locator("#refresh-button")).to_be_disabled()
        await playwright.async_api.expect(page.locator("#operation-notice")).to_be_hidden()
        await playwright.async_api.expect(page.locator('.entry-select[data-key="delayed-sync.md"]')).to_have_count(0)
    finally:
        release_entries.set()
    await playwright.async_api.expect(page.locator('.entry-select[data-key="delayed-sync.md"]')).to_be_visible()
    await playwright.async_api.expect(page.locator("#operation-notice")).to_be_hidden()
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "false")
    await playwright.async_api.expect(page.locator("#refresh-button")).to_be_enabled()


@pytest.mark.asyncio(loop_scope="session")
async def test_clear_filters_stays_busy_through_repos_and_entries(browser_harness: _BrowserHarness) -> None:
    """条件クリア後の候補取得と一覧取得の両方を処理中として表示する。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await _open_filters(page)
    repos_started = asyncio.Event()
    release_repos = asyncio.Event()
    entries_started = asyncio.Event()
    release_entries = asyncio.Event()

    await page.route("**/api/repos?*", functools.partial(_hold_route, started=repos_started, release=release_repos))
    await page.route("**/api/entries?*", functools.partial(_hold_route, started=entries_started, release=release_entries))
    try:
        await page.locator("#clear-filters-button").click()
        await asyncio.wait_for(repos_started.wait(), timeout=5)
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
        await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "true")
        release_repos.set()
        await asyncio.wait_for(entries_started.wait(), timeout=5)
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
        await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "true")
    finally:
        release_repos.set()
        release_entries.set()
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "false")


@pytest.mark.asyncio(loop_scope="session")
async def test_search_stays_busy_during_debounce_and_entries(browser_harness: _BrowserHarness) -> None:
    """検索入力直後から遅延取得の描画完了まで処理中を保つ。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    entries_started = asyncio.Event()
    release_entries = asyncio.Event()

    await page.route("**/api/entries?*", functools.partial(_hold_route, started=entries_started, release=release_entries))
    try:
        await page.locator("#search-input").fill("awi")
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
        await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "true")
        await asyncio.wait_for(entries_started.wait(), timeout=5)
        browser_harness.current_state.publish()
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
        await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "true")
    finally:
        release_entries.set()
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "false")

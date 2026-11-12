"""serveのbrowser_wiに関するloadingの試験。"""

import asyncio
import functools

import playwright.async_api
import pytest

from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _browser_harness_fixture,
    _BrowserHarness,
    _hold_route,
    _screen_harness_fixture,
)
from agent_toolkit._testing.serve_browser_wi_support import _entry_path

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
async def test_initial_sync_shows_work_item_loading(browser_harness: _BrowserHarness) -> None:
    """初回表示はGit同期を待たず、一覧取得中だけナビゲーションへ表示する。"""
    page = browser_harness.page
    entries_started = asyncio.Event()
    release_entries = asyncio.Event()
    sync_calls: list[str] = []

    async def track_sync(route: playwright.async_api.Route) -> None:
        sync_calls.append(route.request.url)
        await route.continue_()

    await page.route("**/api/sync", track_sync)
    await page.route("**/api/entries?*", functools.partial(_hold_route, started=entries_started, release=release_entries))
    try:
        await page.goto(browser_harness.base_url + "/")
        await asyncio.wait_for(entries_started.wait(), timeout=5)
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
    finally:
        release_entries.set()
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    assert not sync_calls


@pytest.mark.asyncio(loop_scope="session")
async def test_external_work_item_refresh_keeps_existing_list(browser_harness: _BrowserHarness) -> None:
    """SSEによる背景更新は取得中も既存一覧を保ち、新着を表示する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()

    async def delay_background_entries(route: playwright.async_api.Route) -> None:
        if "status=active" in route.request.url:
            fetch_started.set()
            await release_fetch.wait()
        await route.continue_()

    await page.route("**/api/entries?*", delay_background_entries)
    (_entry_path(harness.root, "background.md")).write_text(
        "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\n---\n\n背景更新\n",
        encoding="utf-8",
    )
    harness.current_state.publish()
    try:
        await asyncio.wait_for(fetch_started.wait(), timeout=5)
        await playwright.async_api.expect(page.locator('.entry-select[data-key="awi.md"]')).to_be_visible()
    finally:
        release_fetch.set()
    await page.locator('.entry-select[data-key="background.md"]').wait_for(state="visible")


@pytest.mark.asyncio(loop_scope="session")
async def test_manual_sync_refreshes_work_items_without_page_reload(browser_harness: _BrowserHarness) -> None:
    """同期後の一覧取得で新着がページ再読込なしに現れる。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    navigation_count = await page.evaluate("performance.getEntriesByType('navigation').length")
    (_entry_path(browser_harness.root, "new-item.md")).write_text(
        "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\nsource: browser\n---\n\n新着の本文\n",
        encoding="utf-8",
    )

    await page.locator("#refresh-button").click()

    await page.locator('.entry-select[data-key="new-item.md"]').wait_for(state="visible")
    assert await page.evaluate("performance.getEntriesByType('navigation').length") == navigation_count

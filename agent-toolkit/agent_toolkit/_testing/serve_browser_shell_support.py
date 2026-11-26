import asyncio
import urllib.parse

import playwright.async_api

from agent_toolkit._atk.serve.browser_support_test import (
    _observe_screen_requests,
    _ScreenHarness,
    _wait_screen_requests_consumed,
)

_NAV_LINK_METRICS = """links => links.map(link => {
  const rect = link.getBoundingClientRect();
  const style = getComputedStyle(link);
  return {
    x: Math.round(rect.x * 100) / 100,
    y: Math.round(rect.y * 100) / 100,
    width: Math.round(rect.width * 100) / 100,
    height: Math.round(rect.height * 100) / 100,
    style: [style.fontFamily, style.fontSize, style.fontWeight, style.lineHeight, style.padding,
      style.borderWidth, style.boxSizing].join('|'),
  };
})"""


_SCREEN_STREAMS = {
    "wi": ("/api/events", "/api/entries"),
    "plans": ("/api/plans/events", "/api/plans/files"),
    "sessions": ("/api/sessions/events", "/api/sessions/list"),
}


def _request_count(harness: _ScreenHarness, path: str) -> int:
    return sum(1 for url in harness.requests if urllib.parse.urlsplit(url).path == path)


async def _open_screen(harness: _ScreenHarness, screen: str) -> None:
    page = harness.page
    events, listing = _SCREEN_STREAMS[screen]
    await _observe_screen_requests(page)
    await page.goto(harness.base_url + ("/" if screen == "wi" else f"/{screen}"))
    await playwright.async_api.expect(page.locator(f"#screen-{screen}")).to_be_visible()
    await _wait_screen_requests_consumed(page, (events,))
    await page.wait_for_function(
        "([events, listing]) => window.__screenRequests.subscribed[events] > 0 "
        "&& window.__screenRequests.completed[listing] > 0",
        arg=[events, listing],
    )
    _assert_other_streams_unrequested(harness, screen)


def _assert_other_streams_unrequested(harness: _ScreenHarness, screen: str) -> None:
    assert {events: _request_count(harness, events) for name, (events, _) in _SCREEN_STREAMS.items() if name != screen} == {
        events: 0 for name, (events, _) in _SCREEN_STREAMS.items() if name != screen
    }


async def _resynchronize_visible(page: playwright.async_api.Page, screen: str, *, timeout: float = 10_000) -> None:
    listing = _SCREEN_STREAMS[screen][1]
    before = await page.evaluate("path => window.__screenRequests.completed[path]", listing)
    await page.evaluate(
        """() => {
            for (const state of ["hidden", "visible"]) {
                Object.defineProperty(document, "visibilityState", {value: state, configurable: true});
                document.dispatchEvent(new Event("visibilitychange"));
            }
        }"""
    )
    await page.wait_for_function(
        "([path, count]) => window.__screenRequests.completed[path] > count",
        arg=[listing, before],
        timeout=timeout,
    )


class _DelayedResponse:
    """要求を受けた後、解放されるまで応答を保留するrouteの処理。失敗を模す場合は解析できないJSONを返す。"""

    def __init__(self, succeeds: bool) -> None:
        self.succeeds = succeeds
        self.requested = asyncio.Event()
        self.release = asyncio.Event()
        self.fulfilled = asyncio.Event()
        self.url = ""

    async def __call__(self, route: playwright.async_api.Route) -> None:
        self.url = route.request.url
        response = await route.fetch() if self.succeeds else None
        self.requested.set()
        await self.release.wait()
        if response is None:
            await route.fulfill(status=200, content_type="application/json", body="{")
        else:
            await route.fulfill(response=response)
        self.fulfilled.set()

    async def finish(self, page: playwright.async_api.Page) -> None:

        if urllib.parse.urlsplit(self.url).path.startswith("/api/entries/"):
            row = page.locator('.entry-select[data-key="awi.md"]').locator("xpath=..")
            await playwright.async_api.expect(row).to_have_attribute("aria-busy", "true")

            def completed():
                return playwright.async_api.expect(row).not_to_have_attribute("aria-busy", "true", timeout=5000)
        else:
            status = page.locator("#search-status")
            await playwright.async_api.expect(status).to_have_text("検索中")

            def completed():
                return playwright.async_api.expect(status).not_to_have_text("検索中", timeout=5000)

        self.release.set()
        await asyncio.wait_for(self.fulfilled.wait(), timeout=5)
        await completed()

"""serveのbrowser_shell試験が共有する入力と補助処理。"""

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


async def _open_all_screens(harness: _ScreenHarness) -> None:
    """3画面を初期化し、各画面のSSE購読と初期取得が済むまで待つ。"""
    await _observe_screen_requests(harness.page)
    await harness.page.goto(harness.base_url + "/sessions")
    await playwright.async_api.expect(harness.page.locator("#sessions .session-item")).to_have_count(2)
    await _wait_screen_requests_consumed(harness.page, tuple(events for events, _ in _SCREEN_STREAMS.values()))
    await playwright.async_api.expect(harness.page.locator("#entry-list .entry-select").first).to_have_count(1)
    await playwright.async_api.expect(harness.page.locator("#files .file").first).to_have_count(1)


async def _resynchronize_visible(page: playwright.async_api.Page, *, timeout: float = 10_000) -> None:
    """可視化の操作後に、WI・セッション双方の取得本文が消費されることを確かめる。"""
    paths = [_SCREEN_STREAMS[name][1] for name in ("wi", "sessions")]
    before = await page.evaluate(
        "paths => Object.fromEntries(paths.map(path => [path, window.__screenRequests.completed[path] || 0]))", paths
    )
    await page.evaluate(
        """() => {
            Object.defineProperty(document, "visibilityState", {value: "visible", configurable: true});
            document.dispatchEvent(new Event("visibilitychange"));
        }"""
    )
    await page.wait_for_function(
        "before => Object.entries(before).every(([path, count]) => window.__screenRequests.completed[path] > count)",
        arg=before,
        timeout=timeout,
    )


class _DelayedResponse:
    """要求を受けた後、解放されるまで応答を保留するrouteの処理。失敗を模す場合は解析できないJSONを返す。"""

    def __init__(self, succeeds: bool) -> None:
        self.succeeds = succeeds
        self.requested = asyncio.Event()
        self.release = asyncio.Event()
        self.fulfilled = asyncio.Event()

    async def __call__(self, route: playwright.async_api.Route) -> None:
        response = await route.fetch() if self.succeeds else None
        self.requested.set()
        await self.release.wait()
        if response is None:
            await route.fulfill(status=200, content_type="application/json", body="{")
        else:
            await route.fulfill(response=response)
        self.fulfilled.set()

    async def finish(self, page: playwright.async_api.Page) -> None:
        """応答を返し、画面が応答を処理するまで待つ。"""
        self.release.set()
        await asyncio.wait_for(self.fulfilled.wait(), timeout=5)
        await page.wait_for_timeout(50)

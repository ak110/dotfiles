"""`atk serve`の3画面が共有するナビゲーション、体裁、SSEの購読と共通モジュールの実ブラウザー統合テスト。"""

import asyncio
import functools
import json
import urllib.parse
from pathlib import Path
from typing import Any

import playwright.async_api
import pytest

from agent_toolkit._atk.serve import app as serve_app

# pytestがテストの引数名で参照するfixtureを、このモジュールへ登録する。
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _BROWSER_TEST_ENV,
    _browser_fixture,
    _browser_harness_fixture,
    _browser_tests_enabled,
    _BrowserHarness,
    _hold_route,
    _open_filters,
    _open_question,
    _screen_harness_fixture,
    _ScreenHarness,
)

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        not _browser_tests_enabled(),
        reason=f"{_BROWSER_TEST_ENV}=1の場合のみ実行する",
    ),
]


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


# 3画面のSSEの購読先と、再同期で取り直す一覧APIの対応。
_SCREEN_STREAMS = {
    "wi": ("/api/events", "/api/entries"),
    "plans": ("/api/plans/events", "/api/plans/files"),
    "sessions": ("/api/sessions/events", "/api/sessions/list"),
}


def _request_count(harness: _ScreenHarness, path: str) -> int:
    return sum(1 for url in harness.requests if urllib.parse.urlsplit(url).path == path)


async def _wait_until(predicate: Any, timeout: float = 10.0) -> bool:
    """条件が成立するまで短い間隔で待つ。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return bool(predicate())


async def _open_all_screens(harness: _ScreenHarness) -> None:
    """3画面を初期化し、各画面のSSE購読と初期取得が済むまで待つ。"""
    await harness.page.goto(harness.base_url + "/sessions")
    await playwright.async_api.expect(harness.page.locator("#sessions .session-item")).to_have_count(2)
    assert await _wait_until(
        lambda: all(
            _request_count(harness, events) >= 1 and _request_count(harness, listing) >= 1
            for events, listing in _SCREEN_STREAMS.values()
        )
    )


@pytest.mark.asyncio
async def test_navigation_switches_three_screens_in_declared_order(screen_harness: _ScreenHarness) -> None:
    """同一のベースパスから3画面へ遷移でき、ナビゲーションの表示順と表記が指定どおりである。"""
    harness = screen_harness
    await harness.page.goto(harness.base_url + "/")

    navigation = harness.page.locator(".screen:not([hidden]) nav.app-nav")
    await navigation.wait_for(state="visible")
    assert await navigation.locator("a").all_inner_texts() == ["ワークアイテム", "計画ファイル", "セッション"]
    assert await navigation.locator('a[aria-current="page"]').inner_text() == "ワークアイテム"

    await navigation.get_by_role("link", name="計画ファイル").click()
    await harness.page.locator("#preview h1", has_text="初回").wait_for(state="visible")
    assert harness.page.url == harness.base_url + "/plans"
    assert await harness.page.locator('.screen:not([hidden]) nav.app-nav a[aria-current="page"]').inner_text() == "計画ファイル"

    await harness.page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
    await harness.page.locator("#sessions .session-item").first.wait_for(state="visible")
    assert harness.page.url == harness.base_url + "/sessions"
    assert await harness.page.locator('.screen:not([hidden]) nav.app-nav a[aria-current="page"]').inner_text() == "セッション"

    await harness.page.locator("nav.app-nav").get_by_role("link", name="ワークアイテム").click()
    await harness.page.locator("#entry-list").wait_for(state="visible")
    assert harness.page.url == harness.base_url + "/"


@pytest.mark.asyncio
async def test_navigation_does_not_reload_document(screen_harness: _ScreenHarness) -> None:
    """3画面を順に移動してもドキュメントを再読み込みせず、URLだけが切り替わる。"""
    harness = screen_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list").wait_for(state="visible")
    # 再読み込みが起きると失われる値を置き、遷移後も同じドキュメントが生きていることを判定する。
    await page.evaluate("() => { window.__atkReloadMarker = 'kept'; }")

    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    await page.locator("#preview h1", has_text="初回").wait_for(state="visible")
    assert await page.title() == "計画ファイル - atk serve"
    assert await page.evaluate("() => window.__atkReloadMarker") == "kept"
    assert await page.evaluate("() => location.pathname") == "/plans"

    await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
    await page.locator("#sessions .session-item").first.wait_for(state="visible")
    assert await page.evaluate("() => window.__atkReloadMarker") == "kept"
    assert await page.evaluate("() => location.pathname") == "/sessions"


@pytest.mark.asyncio
async def test_navigation_uses_one_html_document_across_all_screens(screen_harness: _ScreenHarness) -> None:
    """起動時のHTMLだけを使い、3画面の切り替えで文書を追加取得しない。"""
    harness = screen_harness
    page = harness.page
    screen_urls = [harness.base_url + path for path in ("/", "/plans", "/sessions")]
    await page.goto(screen_urls[0])
    await page.locator("#entry-list").wait_for(state="visible")
    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    await page.locator("#preview h1", has_text="初回").wait_for(state="visible")
    await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
    await page.locator("#sessions .session-item").first.wait_for(state="visible")

    assert [harness.requests.count(url) for url in screen_urls] == [1, 0, 0]


@pytest.mark.asyncio
async def test_navigation_preserves_filters_and_screen_styles(screen_harness: _ScreenHarness) -> None:
    """3画面の入力値とワークアイテム画面の算出フォントサイズを往復後も保持する。"""
    harness = screen_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list").wait_for(state="visible")
    await _open_filters(page)
    initial_font_size = await page.locator("body").evaluate("element => getComputedStyle(element).fontSize")
    await page.locator("#search-input").fill("ワークアイテム条件")

    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    await page.locator("#preview h1", has_text="初回").wait_for(state="visible")
    await page.locator("#plans-filter").fill("計画条件")
    await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
    await page.locator("#sessions .session-item").first.wait_for(state="visible")
    await page.locator("#sessions-filter").fill("セッション条件")

    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    assert await page.locator("#plans-filter").input_value() == "計画条件"
    await page.locator("nav.app-nav").get_by_role("link", name="ワークアイテム").click()
    assert await page.locator("#search-input").input_value() == "ワークアイテム条件"
    assert await page.locator("body").evaluate("element => getComputedStyle(element).fontSize") == initial_font_size
    await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
    assert await page.locator("#sessions-filter").input_value() == "セッション条件"


@pytest.mark.asyncio
async def test_navigation_uses_one_stylesheet(screen_harness: _ScreenHarness) -> None:
    """図の追加スタイルがあっても3画面で有効なapp.cssを1枚だけ共有する。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/")
    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
    await page.locator("nav.app-nav").get_by_role("link", name="ワークアイテム").click()

    await page.add_style_tag(content=":root { --diagram-style-test: 1; }")
    assert await page.evaluate(
        """() => {
          const appSheets = [...document.styleSheets].filter(sheet => sheet.href?.endsWith('/static/app.css'));
          return appSheets.length === 1 && !appSheets[0].disabled;
        }"""
    )


@pytest.mark.asyncio
async def test_revisiting_screens_does_not_repeat_initial_requests(screen_harness: _ScreenHarness) -> None:
    """初期化済みの画面へ再訪しても、同期と一覧の初期要求を繰り返さない。"""
    harness = screen_harness
    page = harness.page
    plans_resynced = asyncio.Event()
    plans_request_count = 0

    def record_initial_plan_requests(request: playwright.async_api.Request) -> None:
        nonlocal plans_request_count
        if request.url.endswith("/api/plans/files"):
            plans_request_count += 1
            if plans_request_count >= 2:
                plans_resynced.set()

    page.on("request", record_initial_plan_requests)
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    await page.locator("#files .file").first.wait_for(state="visible")
    await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
    await page.locator("#sessions .session-item").first.wait_for(state="visible")
    await asyncio.wait_for(plans_resynced.wait(), timeout=5)
    tracked_suffixes = ("/api/sync", "/api/plans/files", "/api/sessions/list")
    before = {suffix: sum(url.endswith(suffix) for url in harness.requests) for suffix in tracked_suffixes}

    await page.locator("nav.app-nav").get_by_role("link", name="ワークアイテム").click()
    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()

    assert {suffix: sum(url.endswith(suffix) for url in harness.requests) for suffix in tracked_suffixes} == before


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_three_screen_bodies_use_matching_typography(screen_harness: _ScreenHarness) -> None:
    """3画面の本文コンテナーで書体、文字サイズ、行高、字間および文字色をそろえる。"""
    harness = screen_harness
    page = harness.page
    read_styles = "(element, names) => Object.fromEntries(names.map((name) => [name, getComputedStyle(element)[name]]))"
    typography = ["fontFamily", "fontSize", "lineHeight", "letterSpacing", "color"]
    gutters = ["maxWidth", "paddingLeft", "paddingRight"]

    await page.goto(harness.base_url + "/")
    await _open_question(page)
    work_item_styles = await page.locator("#detail-content").evaluate(read_styles, typography)
    await page.keyboard.press("Escape")

    await page.goto(harness.base_url + "/plans")
    await page.locator("#preview h1", has_text="初回").wait_for(state="visible")
    plan_styles = await page.locator("#preview").evaluate(read_styles, typography)
    plan_gutters = await page.locator("#preview").evaluate(read_styles, gutters)
    plan_toolbar = await page.locator("#screen-plans main > .toolbar").evaluate(read_styles, ["padding"])

    await page.goto(harness.base_url + "/sessions")
    await page.locator("#sessions .session-item").first.click()
    await page.locator("#detail details").first.wait_for(state="visible")
    session_styles = await page.locator("#detail").evaluate(read_styles, typography)
    session_gutters = await page.locator("#detail").evaluate(read_styles, gutters)
    session_toolbar = await page.locator("#screen-sessions main > .toolbar").evaluate(read_styles, ["padding"])

    assert work_item_styles == plan_styles == session_styles
    assert session_gutters == plan_gutters
    assert session_toolbar == plan_toolbar


@pytest.mark.asyncio
async def test_plan_and_session_toolbars_have_matching_heights(screen_harness: _ScreenHarness) -> None:
    """計画とセッションの詳細ツールバーを同じ高さで描画する。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/plans")
    plans_toolbar = page.locator("#screen-plans main > .toolbar")
    await plans_toolbar.wait_for(state="visible")
    plans_height = await plans_toolbar.evaluate("element => element.getBoundingClientRect().height")
    await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
    sessions_toolbar = page.locator("#screen-sessions main > .toolbar")
    await sessions_toolbar.wait_for(state="visible")
    sessions_height = await sessions_toolbar.evaluate("element => element.getBoundingClientRect().height")

    assert sessions_height == plans_height


@pytest.mark.asyncio
async def test_three_screens_scroll_below_the_fixed_header(screen_harness: _ScreenHarness) -> None:
    """3画面ともビューポートではなくヘッダー下の本文要素をスクロールする。"""
    screen_harness.plan_path.write_text(
        "# 長い計画\n\n" + "\n\n".join(f"計画の段落{index}" for index in range(60)) + "\n",
        encoding="utf-8",
    )
    session_path = next((screen_harness.root.parent / "claude").rglob("*.jsonl"))
    with session_path.open("a", encoding="utf-8") as stream:
        for index in range(40):
            stream.write(
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": f"2026-09-01T00:20:{index:02d}Z",
                        "message": {"content": f"スクロール用の発話{index}"},
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    page = screen_harness.page
    await page.set_viewport_size({"width": 900, "height": 240})
    for path, selector in (
        ("/", "#screen-wi .wi-screen-scroll"),
        ("/plans", "#screen-plans main"),
        ("/sessions", "#screen-sessions main"),
    ):
        await page.goto(screen_harness.base_url + path)
        scrollable = page.locator(selector)
        await scrollable.wait_for(state="visible")
        if path == "/plans":
            await page.locator("#preview h1", has_text="長い計画").wait_for(state="visible")
        elif path == "/sessions":
            await page.locator("#sessions .session-item").first.click()
            await page.locator("#detail details").first.wait_for(state="visible")
        metrics = await scrollable.evaluate(
            """element => {
              element.scrollTop = element.scrollHeight;
              const header = element.closest('.screen').querySelector('.app-header').getBoundingClientRect();
              const rect = element.getBoundingClientRect();
              return {
                documentClientHeight: document.scrollingElement.clientHeight,
                documentScrollHeight: document.scrollingElement.scrollHeight,
                clientHeight: element.clientHeight,
                scrollHeight: element.scrollHeight,
                headerTop: header.top,
                headerBottom: header.bottom,
                contentTop: rect.top
              };
            }"""
        )
        assert metrics["documentScrollHeight"] <= metrics["documentClientHeight"], path
        assert metrics["scrollHeight"] > metrics["clientHeight"], path
        assert abs(metrics["headerTop"]) <= 1, path
        assert metrics["contentTop"] >= metrics["headerBottom"] - 1, path


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


@pytest.mark.asyncio
async def test_navigation_ignores_delayed_detail_and_search_results(
    screen_harness: _ScreenHarness,
) -> None:
    """詳細取得と全文検索の成功・失敗が遷移後に届いても、旧DOMへ書き込まない。"""
    harness = screen_harness
    page = harness.page
    errors: list[str] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(str(error)))

    async def exercise_detail(succeeds: bool) -> None:
        delayed = _DelayedResponse(succeeds)
        await page.route("**/api/entries/inbox/awi.md", delayed)
        await page.goto(harness.base_url + "/")
        await page.locator('.entry-select[data-key="inbox/awi.md"]').click()
        await asyncio.wait_for(delayed.requested.wait(), timeout=5)
        await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
        await page.locator("#preview h1", has_text="初回").wait_for(state="visible")
        await delayed.finish(page)
        await page.unroute("**/api/entries/inbox/awi.md", delayed)

    async def exercise_search(succeeds: bool) -> None:
        delayed = _DelayedResponse(succeeds)
        await page.route("**/api/plans/search?*", delayed)
        await page.goto(harness.base_url + "/plans")
        await page.locator("#preview h1", has_text="初回").wait_for(state="visible")
        await page.locator("#plans-filter").fill("初回")
        await asyncio.wait_for(delayed.requested.wait(), timeout=5)
        await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
        await page.locator("#sessions .session-item").first.wait_for(state="visible")
        await delayed.finish(page)
        await page.unroute("**/api/plans/search?*", delayed)

    for succeeds in (True, False):
        await exercise_detail(succeeds)
        await exercise_search(succeeds)

    assert not errors


@pytest.mark.asyncio
async def test_back_navigation_restores_previous_screen(screen_harness: _ScreenHarness) -> None:
    """ブラウザーの戻る操作で直前の画面が再び描画される。"""
    harness = screen_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")

    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    await page.locator("#preview h1", has_text="初回").wait_for(state="visible")

    await page.go_back()
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    assert await page.evaluate("() => location.pathname") == "/"


@pytest.mark.asyncio
async def test_direct_load_of_each_screen(screen_harness: _ScreenHarness) -> None:
    """3画面のURLへ直接アクセスしても各画面が描画される。"""
    harness = screen_harness
    for path, selector in (
        ("/", "#entry-list .entry-select"),
        ("/plans", "#preview h1"),
        ("/sessions", "#sessions .session-item"),
    ):
        await harness.page.goto(harness.base_url + path)
        await harness.page.locator(selector).first.wait_for(state="visible")
        assert (
            await harness.page.title()
            == {
                "/": "ワークアイテム - atk serve",
                "/plans": "計画ファイル - atk serve",
                "/sessions": "セッション - atk serve",
            }[path]
        )


@pytest.mark.asyncio
async def test_detail_loading_keeps_list_and_navigation_positions(browser_harness: _BrowserHarness) -> None:
    """詳細取得中のアイコンをナビゲーション内の左右列境界へ置き、リンクと一覧を動かさない。"""
    page = browser_harness.page
    await page.set_viewport_size({"width": 1440, "height": 1000})
    await page.goto(browser_harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    entry = page.locator('.entry-select[data-key="inbox/awi.md"]')
    await entry.wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "false")
    nav = page.locator("#screen-wi .app-nav a")
    before_nav = await nav.evaluate_all(
        "links => links.map(link => [link.getBoundingClientRect().x, link.getBoundingClientRect().y])"
    )
    before_row = await entry.bounding_box()
    assert before_row is not None
    started = asyncio.Event()
    release = asyncio.Event()
    await page.route("**/api/entries/inbox/awi.md", functools.partial(_hold_route, started=started, release=release))
    try:
        await entry.click()
        await asyncio.wait_for(started.wait(), timeout=5)
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
        spinner = await page.locator("#loading-indicator").bounding_box()
        filters = await page.locator("#screen-wi .filters").bounding_box()
        pane = await page.locator("#screen-wi .entry-pane").bounding_box()
        assert spinner is not None and filters is not None and pane is not None
        spinner_center = spinner["x"] + spinner["width"] / 2
        assert filters["x"] + filters["width"] <= spinner_center <= pane["x"]
        # 未定義の色変数を参照すると枠線を描画しないため、位置に加えて描画される状態も確認する。
        spinner_visible = await page.locator("#loading-indicator").evaluate(
            "element => { const style = getComputedStyle(element, '::before'); "
            "return style.borderRightStyle !== 'none' && parseFloat(style.borderRightWidth) > 0 "
            "&& style.animationName !== 'none'; }"
        )
        assert spinner_visible
        assert (
            await nav.evaluate_all(
                "links => links.map(link => [link.getBoundingClientRect().x, link.getBoundingClientRect().y])"
            )
            == before_nav
        )
        assert await entry.bounding_box() == before_row
    finally:
        release.set()
    await page.get_by_role("dialog", name="詳細").wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()


@pytest.mark.asyncio
async def test_slow_work_item_filter_and_plan_preview_show_loading(
    screen_harness: _ScreenHarness,
) -> None:
    """WI一覧と計画プレビューの処理中表示を確認する。"""
    harness = screen_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await _open_filters(page)

    async def delay_entries(route: playwright.async_api.Route) -> None:
        if "status=adopted" in route.request.url:
            await asyncio.sleep(0.65)
        await route.continue_()

    await page.route("**/api/entries**", delay_entries)
    await page.locator("#state-filter").select_option("adopted")
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
    await page.locator('.entry-select[data-key="adopted/adopted.md"]').wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    await page.unroute("**/api/entries**", delay_entries)

    async def delay_plan(route: playwright.async_api.Route) -> None:
        await asyncio.sleep(0.65)
        await route.continue_()

    await page.route("**/api/plans/file?*", delay_plan)
    await page.goto(harness.base_url + "/plans")
    await page.locator("#plans-loading-indicator").wait_for(state="visible")
    await page.get_by_role("heading", name="初回").wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#plans-loading-indicator")).to_be_hidden()


@pytest.mark.asyncio
async def test_plan_and_session_sidebars_match_and_session_opens_first_detail(
    screen_harness: _ScreenHarness,
) -> None:
    """二画面の左幅をそろえ、セッションの先頭詳細をデスクトップで自動表示する。"""
    page = screen_harness.page
    await page.set_viewport_size({"width": 1280, "height": 800})
    await page.goto(screen_harness.base_url + "/plans")
    plan_width = await page.locator("#screen-plans aside").evaluate("element => element.getBoundingClientRect().width")
    plan_link = page.locator("#files .file").first
    plan_link_width = await plan_link.evaluate("element => element.getBoundingClientRect().width")
    await plan_link.hover(position={"x": plan_link_width - 2, "y": 2})
    plan_link_background = await plan_link.evaluate("element => getComputedStyle(element).backgroundColor")

    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator("#detail .event").first.wait_for(state="visible")
    session_width = await page.locator("#screen-sessions aside").evaluate("element => element.getBoundingClientRect().width")

    assert plan_width == session_width == 320
    assert plan_link_width <= plan_width
    assert plan_link_background == "rgb(238, 242, 255)"
    assert await page.locator('#sessions .session-item[aria-current="true"]').count() == 1


@pytest.mark.asyncio
async def test_header_navigation_is_centered_on_three_screens(screen_harness: _ScreenHarness) -> None:
    """ヘッダーの子要素の数が画面ごとに異なっても、3画面ともナビゲーションを画面中央へ置く。"""
    harness = screen_harness
    await harness.page.set_viewport_size({"width": 1280, "height": 800})

    for path, screen in (("/", "#screen-wi"), ("/plans", "#screen-plans"), ("/sessions", "#screen-sessions")):
        await harness.page.goto(harness.base_url + path)
        navigation = harness.page.locator(f"{screen} nav.app-nav")
        await navigation.wait_for(state="visible")
        box = await navigation.bounding_box()
        assert box is not None
        viewport_width = await harness.page.evaluate("document.documentElement.clientWidth")
        # 小数の丸めだけを許容し、片側へ寄る配置を検出する。
        assert abs((box["x"] + box["width"] / 2) - viewport_width / 2) <= 1, path


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [1920, 1440, 1280, 900, 600])
async def test_navigation_links_match_on_three_screens(screen_harness: _ScreenHarness, width: int) -> None:
    """3画面で「ワークアイテム・計画ファイル・セッション」の各リンクの位置、大きさおよび算出スタイルが一致する。

    navの箱の中心だけを比べると、画面固有の要素がnavの内側でリンク群をずらしても検出できないため、
    リンク単位で比べる。現在の画面の強調が字幅を変える場合もここで検出する。
    """
    page = screen_harness.page
    await page.set_viewport_size({"width": width, "height": 800})
    links = page.locator(".screen:not([hidden]) nav.app-nav a")

    direct: dict[str, object] = {}
    for path in ("/", "/plans", "/sessions"):
        await page.goto(screen_harness.base_url + path)
        await links.first.wait_for(state="visible")
        direct[path] = await links.evaluate_all(_NAV_LINK_METRICS)
    assert direct["/plans"] == direct["/"]
    assert direct["/sessions"] == direct["/"]

    clicked: dict[str, object] = {}
    await page.goto(screen_harness.base_url + "/")
    for name, path in (("計画ファイル", "/plans"), ("セッション", "/sessions"), ("ワークアイテム", "/")):
        await page.locator(".screen:not([hidden]) nav.app-nav").get_by_role("link", name=name).click()
        await playwright.async_api.expect(
            page.locator('.screen:not([hidden]) nav.app-nav a[aria-current="page"]')
        ).to_have_text(name)
        clicked[path] = await links.evaluate_all(_NAV_LINK_METRICS)
    assert clicked == direct


@pytest.mark.asyncio
async def test_header_layout_matches_on_three_screens(screen_harness: _ScreenHarness) -> None:
    """3画面のヘッダーの高さと文字サイズがそろい、折り返しが起きる幅でも見出しとナビゲーションの大きさと水平位置がそろう。"""
    harness = screen_harness
    await harness.page.set_viewport_size({"width": 1400, "height": 800})
    headers: dict[str, dict[str, float | str]] = {}

    for path, screen in (("/", "#screen-wi"), ("/plans", "#screen-plans"), ("/sessions", "#screen-sessions")):
        await harness.page.goto(harness.base_url + path)
        header = harness.page.locator(f"{screen} .app-header")
        await header.wait_for(state="visible")
        header_box = await header.bounding_box()
        assert header_box is not None, path
        headers[path] = {
            "height": round(header_box["height"], 1),
            # ヘッダー内の要素が継承する文字サイズは共有ヘッダーの指定であり、各画面の本文用の指定は適用されない。
            "font_size": await header.evaluate("(element) => getComputedStyle(element).fontSize"),
        }

    assert headers["/plans"] == headers["/"]
    assert headers["/sessions"] == headers["/"]

    await harness.page.set_viewport_size({"width": 600, "height": 800})
    layouts: dict[str, dict[str, float | str]] = {}

    for path, screen in (("/", "#screen-wi"), ("/plans", "#screen-plans"), ("/sessions", "#screen-sessions")):
        await harness.page.goto(harness.base_url + path)
        header = harness.page.locator(f"{screen} .app-header")
        await header.wait_for(state="visible")
        title = harness.page.locator(f"{screen} .app-header .header-title")
        navigation = harness.page.locator(f"{screen} nav.app-nav")
        await navigation.wait_for(state="visible")
        header_box = await header.bounding_box()
        title_box = await title.bounding_box()
        navigation_box = await navigation.bounding_box()
        assert header_box is not None and title_box is not None and navigation_box is not None, path
        # 折り返す幅ではWI画面だけが同期操作の欄を別の行に置くため、ヘッダー全体の高さと絶対位置は画面ごとに異なる。
        # 3画面で共通する各要素について、大きさとヘッダー左端からの水平位置が一致するかを比較する。
        layouts[path] = {
            "title_height": round(title_box["height"], 1),
            "nav_height": round(navigation_box["height"], 1),
            "nav_offset_x": round(navigation_box["x"] - header_box["x"], 1),
            "title_font_size": await harness.page.locator(f"{screen} .app-header h1").evaluate(
                "(element) => getComputedStyle(element).fontSize"
            ),
        }

    assert layouts["/plans"] == layouts["/"]
    assert layouts["/sessions"] == layouts["/"]


@pytest.mark.asyncio
async def test_panes_follow_header_height_on_narrow_width(screen_harness: _ScreenHarness) -> None:
    """ヘッダーが折り返す幅でも、主要領域の上端がヘッダーの下端と、下端がビューポートの下端と一致する。"""
    harness = screen_harness
    await harness.page.set_viewport_size({"width": 600, "height": 800})

    for path, screen, selector in (
        ("/", "#screen-wi", "#screen-wi .wi-screen-scroll"),
        ("/plans", "#screen-plans", "#plans-app"),
        ("/sessions", "#screen-sessions", "#sessions-app"),
    ):
        await harness.page.goto(harness.base_url + path)
        header = harness.page.locator(f"{screen} .app-header")
        await header.wait_for(state="visible")
        pane = harness.page.locator(selector)
        await pane.wait_for(state="visible")
        header_box = await header.bounding_box()
        pane_box = await pane.bounding_box()
        assert header_box is not None and pane_box is not None, path
        viewport_height = await harness.page.evaluate("document.documentElement.clientHeight")
        # 小数の丸めだけを許容し、固定値の見積もりによるずれを検出する。
        assert abs(pane_box["y"] - (header_box["y"] + header_box["height"])) <= 1, path
        assert abs((pane_box["y"] + pane_box["height"]) - viewport_height) <= 1, path


@pytest.mark.asyncio
async def test_shell_parts_share_computed_style_across_three_screens(screen_harness: _ScreenHarness) -> None:
    """共有ヘッダーとツールバーの計算スタイルを3画面と反復遷移の前後で維持する。"""
    harness = screen_harness
    page = harness.page
    properties = ["fontFamily", "fontSize", "lineHeight", "letterSpacing", "color"]
    read_styles = "(element, names) => Object.fromEntries(names.map((name) => [name, getComputedStyle(element)[name]]))"
    screens = (
        ("ワークアイテム", "#screen-wi"),
        ("計画ファイル", "#screen-plans"),
        ("セッション", "#screen-sessions"),
    )

    async def shell_styles(screen: str) -> dict[str, dict[str, str]]:
        await page.locator(screen).wait_for(state="visible")
        return {
            "header": await page.locator(f"{screen} .app-header").evaluate(read_styles, properties),
            "title": await page.locator(f"{screen} .app-header h1").evaluate(read_styles, properties),
            "nav": await page.locator(f"{screen} .app-nav").evaluate(read_styles, properties),
        }

    await page.goto(harness.base_url + "/")
    expected: dict[str, dict[str, str]] | None = None
    for _ in range(2):
        for link_name, screen in screens:
            await page.locator("nav.app-nav").get_by_role("link", name=link_name).click()
            current = await shell_styles(screen)
            if expected is None:
                expected = current
            else:
                assert current == expected

    await page.goto(harness.base_url + "/plans")
    plan_toolbar = await page.locator("#screen-plans main > .toolbar").evaluate(read_styles, properties)
    await page.goto(harness.base_url + "/sessions")
    session_toolbar = await page.locator("#screen-sessions main > .toolbar").evaluate(read_styles, properties)
    assert session_toolbar == plan_toolbar

    await page.goto(harness.base_url + "/")
    dialog_styles = [
        await page.locator(selector).evaluate(read_styles, properties)
        for selector in ("#detail-dialog", "#create-dialog", "#delete-dialog")
    ]
    assert dialog_styles[1:] == dialog_styles[:-1]


@pytest.mark.asyncio
async def test_buttons_share_the_common_style_on_three_screens(screen_harness: _ScreenHarness) -> None:
    """3画面のボタンを共通の配色・境界・角丸で表示し、無効なボタンは不透明度を下げる。"""
    harness = screen_harness
    properties = [
        "backgroundColor",
        "color",
        "borderTopColor",
        "borderTopWidth",
        "borderRadius",
        "paddingTop",
        "paddingRight",
        "paddingBottom",
        "paddingLeft",
        "fontSize",
    ]
    # 一覧を開くボタンは狭い画面でだけ表示するため、3画面とも同じ幅で比較する。
    await harness.page.set_viewport_size({"width": 600, "height": 800})
    styles: dict[str, dict[str, str]] = {}

    for path, selector in (
        ("/", "#refresh-button"),
        ("/plans", "#plans-menu-btn"),
        ("/sessions", "#sessions-menu-btn"),
    ):
        await harness.page.goto(harness.base_url + path)
        button = harness.page.locator(selector)
        await button.wait_for(state="visible")
        styles[path] = await button.evaluate(
            "(element, names) => Object.fromEntries(names.map((name) => [name, getComputedStyle(element)[name]]))",
            properties,
        )

    assert styles["/plans"] == styles["/"]
    assert styles["/sessions"] == styles["/"]

    await harness.page.goto(harness.base_url + "/plans")
    # 計画ファイルが1件だけの構成では、前のファイルへ移動するボタンが無効のまま表示される。
    previous_button = harness.page.locator("#prev-btn")
    await previous_button.wait_for(state="visible")
    assert await previous_button.is_disabled()
    enabled = await harness.page.locator("#plans-menu-btn").evaluate("(element) => getComputedStyle(element).opacity")
    disabled = await previous_button.evaluate("(element) => getComputedStyle(element).opacity")
    assert float(enabled) == 1
    assert float(disabled) < 1


@pytest.mark.asyncio
async def test_sidebar_items_share_style_between_plans_and_sessions(screen_harness: _ScreenHarness) -> None:
    """計画ファイル画面とセッション画面の左ペイン項目を、同じ共通クラスと算出スタイルで描画する。"""
    harness = screen_harness
    page = harness.page
    item_properties = [
        "borderTopLeftRadius",
        "borderTopRightRadius",
        "borderBottomLeftRadius",
        "borderBottomRightRadius",
        "paddingTop",
        "paddingRight",
        "paddingBottom",
        "paddingLeft",
        "backgroundColor",
        "boxShadow",
        "borderTopWidth",
        "borderLeftWidth",
        "textAlign",
    ]
    text_properties = ["fontSize", "fontWeight", "color", "marginTop"]
    script = """(element, names) => {
      const pick = (target, keys) => Object.fromEntries(keys.map((key) => [key, getComputedStyle(target)[key]]));
      const row = element.closest('.session-tree-row') || element;
      return {
        classes: [...element.classList].filter((name) => name.startsWith('pane-')),
        item: pick(element, names.item),
        title: pick(element.querySelector('.pane-item-title'), names.text),
        meta: pick(element.querySelector('.pane-item-meta'), names.text),
        separator: getComputedStyle(row).borderBottomWidth,
      };
    }"""
    names = {"item": item_properties, "text": text_properties}
    styles = {}
    for path, selector in (
        ("/plans", '#files .file[aria-current="true"]'),
        ("/sessions", '#sessions .session-item[aria-current="true"]'),
    ):
        await page.goto(harness.base_url + path)
        if path == "/sessions":
            await page.locator("#sessions .session-item").first.click()
        item = page.locator(selector).first
        await item.wait_for(state="visible")
        # hoverの背景と選択の背景を区別せず比べるため、ポインターを項目の外へ置く。
        await page.mouse.move(0, 0)
        styles[path] = await item.evaluate(script, names)

    assert styles["/plans"]["classes"] == ["pane-item"]
    assert styles["/sessions"] == styles["/plans"]
    assert styles["/plans"]["item"]["borderTopLeftRadius"] == "0px"


@pytest.mark.asyncio
async def test_plan_and_session_drawers_share_the_768px_boundary(screen_harness: _ScreenHarness) -> None:
    """700・701・768pxでは一覧をメニューで開き、769pxから通常配置へ戻す。"""
    page = screen_harness.page
    for width in (700, 701, 768, 769):
        mobile = width <= 768
        await page.set_viewport_size({"width": width, "height": 800})

        await page.goto(screen_harness.base_url + "/plans")
        await page.locator("#files .file").first.wait_for(state="visible")
        assert not await page.locator("#screen-plans").evaluate("element => element.classList.contains('drawer-open')")
        if mobile:
            await page.locator("#plans-menu-btn").click()
            assert await page.locator("#plans-menu-btn").get_attribute("aria-expanded") == "true"
            assert await page.locator("#copy-btn .short-label").is_visible()
            assert not await page.locator("#copy-btn .wide-label").is_visible()
            assert (
                await page.locator("#screen-plans main .toolbar").evaluate("element => getComputedStyle(element).flexWrap")
                == "nowrap"
            )
            await page.locator("#files .file").first.click()
            await page.locator("#preview h1").wait_for(state="visible")
            assert not await page.locator("#screen-plans").evaluate("element => element.classList.contains('drawer-open')")
        else:
            await page.locator("#preview h1").wait_for(state="visible")

        await page.goto(screen_harness.base_url + "/sessions")
        await page.locator("#sessions .session-item").first.wait_for(state="visible")
        assert not await page.locator("#screen-sessions").evaluate("element => element.classList.contains('drawer-open')")
        if mobile:
            await page.locator("#sessions-menu-btn").click()
        await page.locator("#sessions .session-item").first.click()
        await page.locator("#detail .event").first.wait_for(state="visible")
        assert not await page.locator("#screen-sessions").evaluate("element => element.classList.contains('drawer-open')")


@pytest.mark.asyncio
async def test_attached_plan_navigation_is_symmetric(screen_harness: _ScreenHarness) -> None:
    """左一覧に付属ファイルを表示せず、右ペインから同じstemの5ファイルを相互に開ける。"""
    harness = screen_harness
    (harness.root / "plan.detail.md").write_text("# 詳細ページ\n", encoding="utf-8")
    (harness.root / "plan.bugs.md").write_text("# バグページ\n", encoding="utf-8")
    review_row = '"1"\t"implementation-review"\t"a.py:1"\t"指摘"\t"要"\t"対応済み"\t""\n'
    (harness.root / "plan.plan-review.tsv").write_text(review_row, encoding="utf-8")
    (harness.root / "plan.exec-review.tsv").write_text(review_row, encoding="utf-8")
    await harness.page.goto(harness.base_url + "/plans")

    await harness.page.get_by_role("heading", name="初回").wait_for(state="visible")
    for attached in ("plan.detail.md", "plan.bugs.md", "plan.plan-review.tsv", "plan.exec-review.tsv"):
        assert await harness.page.locator("#files").get_by_text(attached, exact=True).count() == 0
    detail_link = harness.page.locator('a[data-plan-path="plan.detail.md"]')
    assert await detail_link.inner_text() == "詳細"
    detail_href = await detail_link.get_attribute("href")
    assert detail_href is not None
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(detail_href).query)["path"] == ["plan.detail.md"]

    await harness.page.locator('a[data-plan-path="plan.detail.md"]').click()
    await harness.page.get_by_role("heading", name="詳細ページ").wait_for(state="visible")
    assert await harness.page.title() == "計画ファイル - atk serve"

    await harness.page.locator('a[data-plan-path="plan.bugs.md"]').click()
    await harness.page.get_by_role("heading", name="バグページ").wait_for(state="visible")
    assert await harness.page.title() == "計画ファイル - atk serve"

    await harness.page.locator('a[data-plan-path="plan.plan-review.tsv"]').click()
    await harness.page.get_by_role("columnheader", name="ラウンド").wait_for(state="visible")
    await harness.page.get_by_role("cell", name="implementation-review").wait_for(state="visible")
    assert await harness.page.title() == "計画ファイル - atk serve"

    await harness.page.locator('a[data-plan-path="plan.exec-review.tsv"]').click()
    await harness.page.get_by_role("columnheader", name="対応不要理由").wait_for(state="visible")
    assert await harness.page.title() == "計画ファイル - atk serve"

    await harness.page.locator('a[data-plan-path="plan.md"]').click()
    await harness.page.get_by_role("heading", name="初回").wait_for(state="visible")
    assert await harness.page.title() == "計画ファイル - atk serve"


@pytest.mark.asyncio
async def test_mobile_drawers_labels_titles_and_reduced_motion(screen_harness: _ScreenHarness) -> None:
    """狭幅の一覧は閉じた状態でTab対象から外れ、開閉と画面切替の焦点を保つ。"""
    page = screen_harness.page
    await page.set_viewport_size({"width": 320, "height": 720})
    await page.emulate_media(reduced_motion="reduce")
    await page.goto(screen_harness.base_url + "/plans")
    for name, heading in (("plans", "計画ファイル"), ("sessions", "セッション")):
        if name == "sessions":
            await page.locator('#screen-plans nav.app-nav a[href$="/sessions"]').click()
            await playwright.async_api.expect(page.locator("#screen-sessions h1")).to_be_focused()
        assert await page.title() == f"{heading} - atk serve"
        sidebar = page.locator(f"#{name}-sidebar")
        menu = page.locator(f"#{name}-menu-btn")
        search = page.locator(f"#{name}-filter")
        await playwright.async_api.expect(sidebar).to_have_attribute("inert", "")
        await playwright.async_api.expect(menu).to_have_attribute("aria-expanded", "false")
        assert await search.get_attribute("id") == await page.get_by_label(f"{heading}を検索").get_attribute("id")
        await menu.click()
        await playwright.async_api.expect(menu).to_have_attribute("aria-expanded", "true")
        await playwright.async_api.expect(search).to_be_focused()
        await page.keyboard.press("Escape")
        await playwright.async_api.expect(menu).to_be_focused()
        await playwright.async_api.expect(sidebar).to_have_attribute("inert", "")
        await playwright.async_api.expect(menu).to_have_attribute("aria-expanded", "false")
        await page.evaluate("() => window.scrollTo(0, 0)")
        duration = await menu.evaluate("element => getComputedStyle(element).transitionDuration")
        assert duration == "0s"


@pytest.mark.asyncio
async def test_plan_and_session_lists_expand_after_hundred_items(
    screen_harness: _ScreenHarness,
    tmp_path: Path,
) -> None:
    """50件は全件表示し、100件を超えた一覧だけスクロールに応じて追加描画する。"""
    page = screen_harness.page
    for index in range(49):
        (screen_harness.root / f"bulk-{index:03d}.md").write_text(f"# 計画 {index}\n", encoding="utf-8")
    await page.goto(screen_harness.base_url + "/plans")
    await playwright.async_api.expect(page.locator("#files .file")).to_have_count(50)
    for index in range(49, 110):
        (screen_harness.root / f"bulk-{index:03d}.md").write_text(f"# 計画 {index}\n", encoding="utf-8")
    await page.reload()
    await playwright.async_api.expect(page.locator("#files .file")).to_have_count(100)
    await page.locator("#files-sentinel").scroll_into_view_if_needed()
    await playwright.async_api.expect(page.locator("#files .file")).to_have_count(111)

    project = tmp_path / "claude" / "projects" / "bulk"
    project.mkdir(parents=True)
    for index in range(110):
        record = {"type": "user", "timestamp": "2026-09-02T00:00:00Z", "message": {"content": f"会話 {index}"}}
        (project / f"bulk-{index:03d}.jsonl").write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    await page.goto(screen_harness.base_url + "/sessions")
    await playwright.async_api.expect(page.locator("#sessions .session-item")).to_have_count(100)
    await page.locator("#sessions-sentinel").scroll_into_view_if_needed()
    await playwright.async_api.expect(page.locator("#sessions .session-item")).to_have_count(112)


@pytest.mark.asyncio
async def test_silent_sse_is_reconnected_and_lists_are_refetched(
    screen_harness: _ScreenHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通知もheartbeatも届かない時間が閾値を超えると、3画面とも再接続して一覧を取り直す。"""
    monkeypatch.setattr(serve_app, "SSE_HEARTBEAT_SEC", 3600.0)
    monkeypatch.setattr(serve_app, "SSE_STALL_SEC", 1.0)
    await _open_all_screens(screen_harness)
    before = {
        name: (_request_count(screen_harness, stream), _request_count(screen_harness, listing))
        for name, (stream, listing) in _SCREEN_STREAMS.items()
    }

    for name, (events, listing) in _SCREEN_STREAMS.items():
        assert await _wait_until(
            lambda events=events, listing=listing, name=name: (
                _request_count(screen_harness, events) > before[name][0]
                and _request_count(screen_harness, listing) > before[name][1]
            )
        ), name


@pytest.mark.asyncio
async def test_regular_heartbeat_keeps_sse_without_refetching(
    screen_harness: _ScreenHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """heartbeatが届き続ける間は、閾値を超えて待っても再接続も一覧の再取得も起きない。"""
    monkeypatch.setattr(serve_app, "SSE_HEARTBEAT_SEC", 0.2)
    monkeypatch.setattr(serve_app, "SSE_STALL_SEC", 1.0)
    await _open_all_screens(screen_harness)
    await asyncio.sleep(0.5)
    before = {path: _request_count(screen_harness, path) for pair in _SCREEN_STREAMS.values() for path in pair}

    await asyncio.sleep(3.0)

    assert {path: _request_count(screen_harness, path) for path in before} == before


@pytest.mark.asyncio
async def test_visible_tab_and_bfcache_restore_resynchronize_screens(screen_harness: _ScreenHarness) -> None:
    """タブが表示へ戻るとWI画面とセッション画面が一覧を取り直し、bfcacheからの復帰では再接続する。"""
    page = screen_harness.page
    await _open_all_screens(screen_harness)
    await asyncio.sleep(0.5)
    lists = {name: _request_count(screen_harness, _SCREEN_STREAMS[name][1]) for name in ("wi", "sessions")}

    await page.evaluate(
        """() => {
            Object.defineProperty(document, "visibilityState", {value: "visible", configurable: true});
            document.dispatchEvent(new Event("visibilitychange"));
        }"""
    )

    for name, count in lists.items():
        assert await _wait_until(
            lambda name=name, count=count: _request_count(screen_harness, _SCREEN_STREAMS[name][1]) > count
        ), name

    events = {name: _request_count(screen_harness, stream) for name, (stream, _listing) in _SCREEN_STREAMS.items()}
    await page.evaluate(
        """() => {
            window.dispatchEvent(new PageTransitionEvent("pagehide", {persisted: true}));
            window.dispatchEvent(new PageTransitionEvent("pageshow", {persisted: true}));
        }"""
    )

    for name, count in events.items():
        assert await _wait_until(
            lambda name=name, count=count: _request_count(screen_harness, _SCREEN_STREAMS[name][0]) > count
        ), name

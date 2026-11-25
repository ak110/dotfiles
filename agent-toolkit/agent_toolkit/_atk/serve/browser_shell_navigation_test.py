import asyncio
import functools
import json
import urllib.parse
from pathlib import Path

import playwright.async_api
import pytest

from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _browser_harness_fixture,
    _BrowserHarness,
    _hold_route,
    _open_filters,
    _screen_harness_fixture,
    _ScreenHarness,
)
from agent_toolkit._testing.serve_browser_shell_support import _NAV_LINK_METRICS, _DelayedResponse

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
async def test_navigation_switches_screens_in_declared_order(screen_harness: _ScreenHarness) -> None:
    harness = screen_harness
    await harness.page.goto(harness.base_url + "/")

    navigation = harness.page.locator(".screen:not([hidden]) nav.app-nav")
    await navigation.wait_for(state="visible")
    assert await navigation.locator("a").all_inner_texts() == [
        "ワークアイテム",
        "計画ファイル",
        "エージェント",
        "セッション",
        "利用枠",
        "設定",
    ]
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


@pytest.mark.asyncio(loop_scope="session")
async def test_navigation_does_not_reload_document(screen_harness: _ScreenHarness) -> None:
    harness = screen_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list").wait_for(state="visible")
    screen_urls = [harness.base_url + path for path in ("/", "/plans", "/sessions")]

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
    assert [harness.requests.count(url) for url in screen_urls] == [1, 0, 0]


@pytest.mark.cpu_limit(
    2.75,
    reason=(
        "詳細取得と全文検索の成功・失敗が遷移後に届いても、旧DOMへ書き込まない。実ブラウザーの観測とt"
        "race回収が必要（CPU最大実測1.182秒）。"
    ),
)
@pytest.mark.asyncio(loop_scope="session")
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
        await page.route("**/api/entries/awi.md", delayed)
        await page.goto(harness.base_url + "/")
        await page.locator('.entry-select[data-key="awi.md"]').click()
        await asyncio.wait_for(delayed.requested.wait(), timeout=5)
        await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
        await page.locator("#preview h1", has_text="初回").wait_for(state="visible")
        await delayed.finish(page)
        await page.unroute("**/api/entries/awi.md", delayed)

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


@pytest.mark.cpu_limit(
    1.75,
    reason=("ブラウザーの戻る操作で直前の画面が再び描画される。実ブラウザーの観測とtrace回収が必要（CPU最大実測0.703秒）。"),
)
@pytest.mark.asyncio(loop_scope="session")
async def test_back_navigation_restores_previous_screen(screen_harness: _ScreenHarness) -> None:
    harness = screen_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")

    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    await page.locator("#preview h1", has_text="初回").wait_for(state="visible")

    await page.go_back()
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    assert await page.evaluate("() => location.pathname") == "/"


@pytest.mark.cpu_limit(
    1.5,
    reason=("各画面のURLへ直接アクセスしても各画面が描画される。実ブラウザーの観測とtrace回収が必要（CPU最大実測0.591秒）。"),
)
@pytest.mark.asyncio(loop_scope="session")
async def test_direct_load_of_each_screen(screen_harness: _ScreenHarness) -> None:
    harness = screen_harness
    for path, selector in (
        ("/", "#entry-list .entry-select"),
        ("/plans", "#preview h1"),
        ("/sessions", "#sessions .session-item"),
        ("/agents", "#screen-agents h1"),
        ("/settings", "#screen-settings h1"),
    ):
        await harness.page.goto(harness.base_url + path)
        await harness.page.locator(selector).first.wait_for(state="visible")
        assert (
            await harness.page.title()
            == {
                "/": "ワークアイテム - atk serve",
                "/plans": "計画ファイル - atk serve",
                "/sessions": "セッション - atk serve",
                "/agents": "エージェント - atk serve",
                "/settings": "設定 - atk serve",
            }[path]
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_detail_loading_keeps_list_and_navigation_positions(browser_harness: _BrowserHarness) -> None:
    page = browser_harness.page
    await page.set_viewport_size({"width": 1440, "height": 1000})
    await page.goto(browser_harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    entry = page.locator('.entry-select[data-key="awi.md"]')
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
    await page.route("**/api/entries/awi.md", functools.partial(_hold_route, started=started, release=release))
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


@pytest.mark.cpu_limit(
    1.5, reason=("WI一覧と計画プレビューの処理中表示を確認する。実ブラウザーの観測とtrace回収が必要（CPU最大実測0.514秒）。")
)
@pytest.mark.asyncio(loop_scope="session")
async def test_slow_work_item_filter_and_plan_preview_show_loading(
    screen_harness: _ScreenHarness,
) -> None:
    harness = screen_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await _open_filters(page)

    entries_release = asyncio.Event()

    async def delay_entries(route: playwright.async_api.Route) -> None:
        if "status=adopted" in route.request.url:
            await asyncio.wait_for(entries_release.wait(), timeout=5)
        await route.continue_()

    await page.route("**/api/entries**", delay_entries)
    await page.locator("#state-filter").select_option("adopted")
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
    entries_release.set()
    await page.locator('.entry-select[data-key="adopted.md"]').wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    await page.unroute("**/api/entries**", delay_entries)

    plan_release = asyncio.Event()

    async def delay_plan(route: playwright.async_api.Route) -> None:
        await asyncio.wait_for(plan_release.wait(), timeout=5)
        await route.continue_()

    await page.route("**/api/plans/file?*", delay_plan)
    await page.goto(harness.base_url + "/plans")
    await page.locator("#plans-loading-indicator").wait_for(state="visible")
    plan_release.set()
    await page.get_by_role("heading", name="初回").wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#plans-loading-indicator")).to_be_hidden()


@pytest.mark.asyncio(loop_scope="session")
async def test_plan_and_session_sidebars_match_and_session_opens_first_detail(
    screen_harness: _ScreenHarness,
) -> None:
    """計画ファイル画面とセッション画面の左ペインの幅をそろえ、デスクトップの幅ではセッションの先頭の詳細を自動で表示する。"""
    page = screen_harness.page
    await page.set_viewport_size({"width": 1280, "height": 800})
    await page.goto(screen_harness.base_url + "/plans")
    plan_width = await page.locator("#screen-plans aside").evaluate("element => element.getBoundingClientRect().width")
    plan_link = page.locator("#files .file").first
    plan_link_width = await plan_link.evaluate("element => element.getBoundingClientRect().width")
    await plan_link.hover(position={"x": plan_link_width - 2, "y": 2})
    plan_link_highlighted = await plan_link.evaluate(
        """element => {
          const probe = document.createElement("span");
          probe.style.backgroundColor = "var(--color-primary-quiet)";
          element.append(probe);
          return getComputedStyle(element).backgroundColor === getComputedStyle(probe).backgroundColor;
        }"""
    )

    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator("#detail .event").first.wait_for(state="visible")
    session_width = await page.locator("#screen-sessions aside").evaluate("element => element.getBoundingClientRect().width")

    assert plan_width == session_width == 320
    assert plan_link_width <= plan_width
    assert plan_link_highlighted
    assert await page.locator('#sessions .session-item[aria-current="true"]').count() == 1


@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "全画面でナビゲーションの各リンクの位置、大きさおよび算出スタイルが"
        "一致する。実ブラウザーの観測とtrace回収が必要（CPU最大実測0.801秒）。"
    ),
)
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("width", [1920, 1440, 1280, 900, 600])
async def test_navigation_links_match_on_all_screens(screen_harness: _ScreenHarness, width: int) -> None:
    page = screen_harness.page
    await page.set_viewport_size({"width": width, "height": 800})
    links = page.locator(".screen:not([hidden]) nav.app-nav a")

    direct: dict[str, object] = {}
    for path in ("/", "/plans", "/agents", "/sessions", "/settings"):
        await page.goto(screen_harness.base_url + path)
        await links.first.wait_for(state="visible")
        direct[path] = await links.evaluate_all(_NAV_LINK_METRICS)
    for path, metrics in direct.items():
        assert metrics == direct["/"], path

    clicked: dict[str, object] = {}
    await page.goto(screen_harness.base_url + "/")
    for name, path in (
        ("計画ファイル", "/plans"),
        ("エージェント", "/agents"),
        ("セッション", "/sessions"),
        ("設定", "/settings"),
        ("ワークアイテム", "/"),
    ):
        await page.locator(".screen:not([hidden]) nav.app-nav").get_by_role("link", name=name).click()
        await playwright.async_api.expect(
            page.locator('.screen:not([hidden]) nav.app-nav a[aria-current="page"]')
        ).to_have_text(name)
        clicked[path] = await links.evaluate_all(_NAV_LINK_METRICS)
    assert clicked == direct


@pytest.mark.cpu_limit(
    1.5,
    reason=(
        "左一覧に付属ファイルを表示せず、右ペインから同じstemの5ファイルを相互に開ける。実ブラウザー"
        "の観測とtrace回収が必要（CPU最大実測0.534秒）。"
    ),
)
@pytest.mark.asyncio(loop_scope="session")
async def test_attached_plan_navigation_is_symmetric(screen_harness: _ScreenHarness) -> None:
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


@pytest.mark.cpu_limit(
    2.75,
    reason=(
        "50件は全件表示し、100件を超えた一覧だけスクロールに応じて追加描画する。実ブラウザーの観測と"
        "trace回収が必要（CPU最大実測1.194秒）。"
    ),
)
@pytest.mark.asyncio(loop_scope="session")
async def test_plan_and_session_lists_expand_after_hundred_items(
    screen_harness: _ScreenHarness,
    tmp_path: Path,
) -> None:
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

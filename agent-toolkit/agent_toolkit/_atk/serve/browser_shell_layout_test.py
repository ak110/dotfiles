"""serveのbrowser_shellに関するlayoutの試験。"""

import json

import playwright.async_api
import pytest

from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _browser_harness_fixture,
    _open_filters,
    _screen_harness_fixture,
    _ScreenHarness,
)

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
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


@pytest.mark.asyncio(loop_scope="session")
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


@pytest.mark.asyncio(loop_scope="session")
async def test_three_screens_scroll_below_the_fixed_header(screen_harness: _ScreenHarness) -> None:
    """3画面ともヘッダー下の本文要素をスクロールする。"""
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


@pytest.mark.asyncio(loop_scope="session")
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


@pytest.mark.asyncio(loop_scope="session")
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


@pytest.mark.asyncio(loop_scope="session")
async def test_mobile_drawers_labels_titles_and_reduced_motion(screen_harness: _ScreenHarness) -> None:
    """狭い画面の一覧は閉じた状態でTab移動の対象から外れ、開閉と画面切替の前後でフォーカスを保つ。"""
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

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
    harness = screen_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list").wait_for(state="visible")
    await _open_filters(page)
    initial_font_size = await page.locator("body").evaluate("element => getComputedStyle(element).fontSize")
    await page.locator("#search-input").fill("ワークアイテム条件")

    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    await page.locator("#preview h1", has_text="初回").wait_for(state="visible")
    await page.get_by_text("計画ファイルを検索", exact=True).wait_for(state="visible")
    await page.get_by_role("searchbox", name="計画ファイルを検索").fill("計画条件")
    await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
    await page.locator("#sessions .session-item").first.wait_for(state="visible")
    session_search = page.get_by_role("searchbox", name="セッションを検索")
    scope = page.locator("#sessions-filter-scope")
    await scope.wait_for(state="visible")
    await playwright.async_api.expect(session_search).to_have_accessible_description(
        "検索対象: ホスト・作業ディレクトリ・識別子・パス・ラベル"
    )
    await session_search.fill("セッション条件")
    await scope.wait_for(state="visible")

    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    assert await page.locator("#plans-filter").input_value() == "計画条件"
    await page.locator("nav.app-nav").get_by_role("link", name="ワークアイテム").click()
    assert await page.locator("#search-input").input_value() == "ワークアイテム条件"
    assert await page.locator("body").evaluate("element => getComputedStyle(element).fontSize") == initial_font_size
    await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
    assert await page.locator("#sessions-filter").input_value() == "セッション条件"


@pytest.mark.asyncio(loop_scope="session")
async def test_navigation_uses_one_stylesheet(screen_harness: _ScreenHarness) -> None:
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


@pytest.mark.cpu_limit(
    1.75, reason=("3画面ともヘッダー下の本文要素をスクロールする。実ブラウザーの観測とtrace回収が必要（CPU最大実測0.686秒）。")
)
@pytest.mark.asyncio(loop_scope="session")
async def test_three_screens_scroll_below_the_fixed_header(screen_harness: _ScreenHarness) -> None:
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


@pytest.mark.cpu_limit(
    1.75,
    reason=(
        "ヘッダーが折り返す幅でも、主要領域の上端がヘッダーの下端と、下端がビューポートの下端と一致する。"
        "実ブラウザーの観測とtrace回収が必要（CPU最大実測0.635秒）。"
    ),
)
@pytest.mark.asyncio(loop_scope="session")
async def test_panes_follow_header_height_on_narrow_width(screen_harness: _ScreenHarness) -> None:
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

        assert abs(pane_box["y"] - (header_box["y"] + header_box["height"])) <= 1, path
        assert abs((pane_box["y"] + pane_box["height"]) - viewport_height) <= 1, path


@pytest.mark.cpu_limit(
    3.0,
    reason=(
        "700・701・768pxでは一覧をメニューで開き、769pxから通常配置へ戻す。実ブラウザーの"
        "観測とtrace回収が必要（CPU最大実測1.259秒）。"
    ),
)
@pytest.mark.asyncio(loop_scope="session")
async def test_plan_and_session_drawers_share_the_768px_boundary(screen_harness: _ScreenHarness) -> None:
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
            await page.locator("#files .file").first.click()
            await page.locator("#preview h1").wait_for(state="visible")
            assert not await page.locator("#screen-plans").evaluate("element => element.classList.contains('drawer-open')")
        else:
            await page.locator("#preview h1").wait_for(state="visible")

        toolbar = page.locator("#screen-plans .detail-toolbar")
        for control in ("#copy-btn", "#copy-path-btn", "#prev-btn", "#next-btn"):
            assert await toolbar.evaluate(
                """(toolbar, selector) => {
                    const available = toolbar.getBoundingClientRect();
                    const button = toolbar.querySelector(selector).getBoundingClientRect();
                    return button.width > 0 && button.left >= available.left && button.right <= available.right;
                }""",
                control,
            )

        await page.goto(screen_harness.base_url + "/sessions")
        await page.locator("#sessions .session-item").first.wait_for(state="visible")
        assert not await page.locator("#screen-sessions").evaluate("element => element.classList.contains('drawer-open')")
        if mobile:
            await page.locator("#sessions-menu-btn").click()
        await page.locator("#sessions .session-item").first.click()
        await page.locator("#detail .event").first.wait_for(state="visible")
        assert not await page.locator("#screen-sessions").evaluate("element => element.classList.contains('drawer-open')")


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("width", [320, 768, 769, 900, 1000, 1280])
async def test_plan_toolbar_keeps_actions_in_view_while_scrolling(screen_harness: _ScreenHarness, width: int) -> None:
    page = screen_harness.page
    await page.set_viewport_size({"width": width, "height": 800})
    screen_harness.plan_path.write_text("# 先頭\n\n" + "計画の段落\n\n" * 100 + "最後の段落\n", encoding="utf-8")
    await page.goto(screen_harness.base_url + "/plans")
    main = page.locator("#screen-plans main")
    toolbar = main.locator(".toolbar")
    first = toolbar.get_by_role("button", name="先頭へ", exact=True)
    last = toolbar.get_by_role("button", name="末尾へ", exact=True)
    controls = [toolbar.locator(f"#{name}") for name in ("copy-btn", "copy-path-btn", "prev-btn", "next-btn")]
    if width <= 768:
        menu = toolbar.locator("#plans-menu-btn")
        await menu.click()
        await page.locator("#files .file").first.click()
        controls.append(menu)
    controls.extend((first, last))
    expect = playwright.async_api.expect
    await expect(last).to_be_enabled()
    await expect(controls[0]).to_be_enabled()
    await expect(controls[1]).to_be_enabled()

    async def check_toolbar() -> float:
        pane = await main.bounding_box()
        toolbar_box = await toolbar.bounding_box()
        assert pane is not None and toolbar_box is not None
        assert await toolbar.evaluate("element => getComputedStyle(element).position") == "sticky"
        rows = []
        for control in controls:
            box = await control.bounding_box()
            assert box is not None, control
            assert pane["x"] <= box["x"] and box["x"] + box["width"] <= pane["x"] + pane["width"], (control, box, pane)
            assert toolbar_box["y"] <= box["y"] < box["y"] + box["height"] <= min(toolbar_box["y"] + toolbar_box["height"], 800)
            rows.append(box["y"])
        if width == 1280:
            assert max(rows) - min(rows) <= 1, rows
        return toolbar_box["y"]

    toolbar_top = await check_toolbar()
    await last.click()
    await expect(main.get_by_text("最後の段落")).to_be_in_viewport()
    assert await check_toolbar() == pytest.approx(toolbar_top, abs=1)
    await first.click()
    assert await main.evaluate("element => element.scrollTop") == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_mobile_drawers_labels_titles_and_reduced_motion(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    await page.set_viewport_size({"width": 320, "height": 720})
    await page.emulate_media(reduced_motion="reduce")
    await page.goto(screen_harness.base_url + "/plans")
    for name, heading in (("plans", "計画ファイル"), ("sessions", "セッション")):
        if name == "sessions":
            await page.get_by_role("combobox", name="画面を切り替える").select_option("sessions")
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


@pytest.mark.asyncio(loop_scope="session")
async def test_detail_toolbars_use_the_same_tokens(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    observed = {}
    for screen in ("plans", "sessions"):
        await page.goto(screen_harness.base_url + "/" + screen)
        toolbar = page.locator(f"#screen-{screen} .detail-toolbar").first
        await toolbar.wait_for()
        observed[screen] = await toolbar.evaluate(
            """toolbar => {
                const style = getComputedStyle(toolbar);
                const button = getComputedStyle([...toolbar.querySelectorAll('button')]
                    .find(button => button.getClientRects().length));
                const bounds = toolbar.getBoundingClientRect();
                return {
                    gap: style.columnGap, background: style.backgroundColor,
                    font: button.fontSize, height: button.minBlockSize,
                    contentStart: bounds.left + parseFloat(style.paddingLeft)
                };
            }"""
        )
        assert float(observed[screen]["font"].removesuffix("px")) >= 16
    session_start = observed["sessions"].pop("contentStart")
    observed["plans"].pop("contentStart")
    assert observed["plans"] == observed["sessions"]
    title = page.locator("#detail-title")
    await title.wait_for()
    title_start = await title.evaluate("element => element.getBoundingClientRect().left")
    assert session_start == pytest.approx(title_start, abs=1)


@pytest.mark.asyncio(loop_scope="session")
async def test_workspace_headings_use_the_same_hierarchy(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    await page.route("**/api/usage**", lambda route: route.abort())
    observed = []
    for screen, heading in (
        ("/", "#entry-heading"),
        ("/agents", "#agents-app .page-heading"),
        ("/usage", "#usage-heading"),
        ("/settings", "#settings-heading"),
    ):
        await page.goto(screen_harness.base_url + screen)
        observed.append(
            await page.locator(heading).evaluate(
                """element => {
                    const heading = getComputedStyle(element);
                    const body = getComputedStyle(document.body);
                    return {
                        larger: parseFloat(heading.fontSize) > parseFloat(body.fontSize),
                        size: heading.fontSize,
                        weight: heading.fontWeight,
                        line: heading.lineHeight
                    };
                }"""
            )
        )
    assert all(item == observed[0] and item["larger"] for item in observed)

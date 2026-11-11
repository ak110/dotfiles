"""serveのbrowser_shellに関するlayoutの試験。"""

import json

import playwright.async_api
import pytest

from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _browser_harness_fixture,
    _open_filters,
    _open_question,
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


@pytest.mark.asyncio(loop_scope="session")
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


@pytest.mark.asyncio(loop_scope="session")
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


@pytest.mark.asyncio(loop_scope="session")
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


@pytest.mark.asyncio(loop_scope="session")
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

"""serveのbrowser_wiに関するcopyの試験。"""

import asyncio
import re
import urllib.parse

import playwright.async_api
import pytest

from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _browser_harness_fixture,
    _BrowserHarness,
    _observe_screen_requests,
    _open_filters,
    _screen_harness_fixture,
    _ScreenHarness,
    _shift_click_default_prevented,
    _wait_screen_requests_consumed,
)
from agent_toolkit._testing.serve_browser_wi_support import _entry_path

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    ("width", "list_width"),
    [
        (320, None),
        (700, None),
        (1024, None),
        (1600, None),
        (1279, None),
        (1280, None),
        (1281, None),
        (1320, None),
        (1920, 1168),
        (1920, 1169),
        (1920, None),
    ],
)
async def test_entry_copy_button_copies_filename_and_summary_without_selecting(
    screen_harness: _ScreenHarness,
    width: int,
    list_width: int | None,
) -> None:
    """狭幅・一覧自身の切替境界・広幅で、コピーの前後も題名を読めて横幅を超えない。"""
    page = screen_harness.page
    await page.set_viewport_size({"width": width, "height": 800})
    await page.goto(screen_harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    if list_width is not None:
        await page.locator(".entry-pane").evaluate(
            "(element, width) => { element.style.boxSizing = 'content-box'; element.style.width = `${width}px`; }",
            list_width,
        )
    row = page.locator("#entry-list .entry-row").first
    await row.locator(".entry-copy").wait_for(state="visible")
    await row.locator(".entry-copy").scroll_into_view_if_needed()
    filename = await row.locator(".filename-cell").inner_text()
    summary = await row.locator(".summary-cell").inner_text()
    original_selection = await row.locator(".entry-select").bounding_box()
    original_summary = await row.locator(".summary-cell").bounding_box()

    async def assert_readable() -> None:
        assert await row.locator(".entry-select").bounding_box() == original_selection
        assert await row.locator(".summary-cell").bounding_box() == original_summary
        assert await row.locator(".entry-copy").evaluate("element => element.scrollWidth <= element.clientWidth")
        metrics = await row.evaluate("""row => {
          const summary = row.querySelector('.summary-cell');
          const canvas = document.createElement('canvas');
          const context = canvas.getContext('2d');
          context.font = getComputedStyle(summary).font;
          return {
            width: summary.getBoundingClientRect().width,
            prefixWidth: context.measureText(summary.textContent.slice(0, 6) + '…').width,
            summaryTop: summary.getBoundingClientRect().top,
            statusTop: row.querySelector('.status-cell').getBoundingClientRect().top
          };
        }""")
        assert metrics["width"] >= metrics["prefixWidth"], metrics
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        columns_visible = await page.locator(".entry-metadata-label").first.is_visible()
        assert columns_visible is (width == 1920 and list_width != 1168)
        if not columns_visible:
            assert metrics["summaryTop"] < metrics["statusTop"]

    await assert_readable()

    await row.locator(".entry-copy").click()

    # ボタンの表示が2秒間だけ変わることは確かめない。害の小さい一時的な表示であり、時間に頼る確認は負荷で偶然に失敗する。
    assert await page.evaluate("() => navigator.clipboard.readText()") == f"{filename} {summary}"
    await playwright.async_api.expect(page.locator("#result-status")).to_contain_text("コピーしました")
    await assert_readable()
    assert await page.locator("#detail-dialog").evaluate("element => element.open") is False


@pytest.mark.asyncio(loop_scope="session")
async def test_work_item_filename_link_supports_get_and_shift_click(screen_harness: _ScreenHarness) -> None:
    """WI名は詳細を復元するGET URLを持ち、Shiftクリックをブラウザーへ委ねる。"""
    harness = screen_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    link = page.locator('.entry-select[data-key="awi.md"]')
    await link.wait_for(state="visible")
    href = await link.get_attribute("href")
    assert href == "/?filename=awi.md"

    await link.click()
    await page.get_by_role("dialog", name="詳細").wait_for(state="visible")
    assert urllib.parse.urlsplit(page.url).query == "filename=awi.md"

    await page.go_back()
    await playwright.async_api.expect(page.get_by_role("dialog", name="詳細")).to_be_hidden()
    await page.go_forward()
    await page.get_by_role("dialog", name="詳細").wait_for(state="visible")
    assert await page.locator("#detail-filename").inner_text() == "awi.md"
    await page.keyboard.press("Escape")

    assert not await _shift_click_default_prevented(link)


@pytest.mark.asyncio(loop_scope="session")
async def test_work_item_copy_failure_survives_screen_switch(screen_harness: _ScreenHarness) -> None:
    """コピーの失敗は画面を切り替えて戻った後も保存先・対象と理由を保持する。"""
    harness = screen_harness
    page = harness.page
    await page.add_init_script(
        "Object.defineProperty(navigator, 'clipboard', {value: {writeText: async () => {throw new Error('書込拒否')}}})"
    )
    await page.goto(harness.base_url + "/")
    button = page.locator("#entry-list .entry-copy").first
    await button.wait_for(state="visible")
    target = await button.get_attribute("data-key")
    assert target is not None
    await button.click()
    notice = page.locator("#operation-notice")
    await playwright.async_api.expect(notice).to_contain_text(f"WI / ブラウザー / {harness.root.parent}")
    await playwright.async_api.expect(notice).to_contain_text(target)
    await page.locator('#screen-wi nav a[href="/plans"]').click()
    await page.locator('#screen-plans nav a[href="/"]').click()
    await playwright.async_api.expect(notice).to_be_visible()
    await playwright.async_api.expect(notice).to_contain_text("書込拒否")


@pytest.mark.parametrize("width", [320, 1920])
@pytest.mark.asyncio(loop_scope="session")
async def test_copy_all_is_summary_column_header(browser_harness: _BrowserHarness, width: int) -> None:
    """全てコピーは要約ラベルの右側、個別コピーの上に表示される。"""
    page = browser_harness.page
    await page.set_viewport_size({"width": width, "height": 900})
    await _observe_screen_requests(page)
    await page.goto(browser_harness.base_url + "/")
    await _wait_screen_requests_consumed(page, ("/api/events",))
    button = page.get_by_role("button", name="全てコピー", exact=True)
    label = page.locator(".summary-column-label")
    individual = page.locator("#entry-list .entry-copy").first
    await playwright.async_api.expect(button).to_be_visible()
    await playwright.async_api.expect(label).to_be_visible()
    await playwright.async_api.expect(individual).to_be_visible()
    button_box = await button.bounding_box()
    label_box = await label.bounding_box()
    individual_box = await individual.bounding_box()
    assert button_box is not None and label_box is not None and individual_box is not None
    assert label_box["x"] + label_box["width"] <= button_box["x"]
    assert label_box["y"] < button_box["y"] + button_box["height"]
    assert button_box["y"] < label_box["y"] + label_box["height"]
    assert button_box["y"] + button_box["height"] <= individual_box["y"]
    assert abs(button_box["x"] - individual_box["x"]) <= 1
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.asyncio(loop_scope="session")
async def test_copy_all_work_items_preserves_filters_across_pages(browser_harness: _BrowserHarness) -> None:
    """重要・検索・対象条件に一致する全ページを取得し、個別コピー形式でコピーする。"""
    harness = browser_harness
    page = harness.page
    expected = set()
    for index in range(101):
        filename = f"copy-all-{index:03}.md"
        summary = f"コピー対象 {index}"
        expected.add(f"{filename} {summary}")
        path = _entry_path(harness.root, filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\ntype: awi\nstate: inbox\ntarget_repo: copy/repo\nimportant: true\n---\n\n{summary}\n", encoding="utf-8"
        )
    _entry_path(harness.root, "copy-excluded.md").write_text(
        "---\ntype: awi\nstate: inbox\ntarget_repo: copy/repo\n---\n\nコピー対象の非重要項目\n", encoding="utf-8"
    )
    await harness.context.grant_permissions(["clipboard-read", "clipboard-write"], origin=harness.base_url)
    await page.set_viewport_size({"width": 320, "height": 800})
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await _open_filters(page)
    await page.get_by_label("重要", exact=True).select_option("yes")
    await page.get_by_label("target-repo", exact=True).select_option("copy/repo")
    await page.get_by_label("検索", exact=True).fill("コピー対象")
    await playwright.async_api.expect(page.locator("#pagination-status")).to_have_text("ページ 1 / 2（全101件）")
    await page.get_by_role("button", name=re.compile("^次のページ")).click()
    await playwright.async_api.expect(page.locator("#pagination-status")).to_have_text("ページ 2 / 2（全101件）")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(1)
    started = asyncio.Event()
    release = asyncio.Event()

    async def delay_copy(route: playwright.async_api.Route) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(route.request.url).query)
        if query.get("target_repo") == ["copy/repo"] and "page" not in query:
            started.set()
            await release.wait()
        await route.continue_()

    await page.route("**/api/entries?*", delay_copy)
    try:
        await page.get_by_role("button", name="全てコピー", exact=True).click()
        await asyncio.wait_for(started.wait(), timeout=5)
        await playwright.async_api.expect(page.locator("#copy-all-button")).to_be_disabled()
        await playwright.async_api.expect(page.locator("#copy-all-status")).to_contain_text("全ページを取得中")
    finally:
        release.set()
    await playwright.async_api.expect(page.locator("#copy-all-status")).to_have_text("101件をコピーしました。")
    copied = (await page.evaluate("navigator.clipboard.readText()")).splitlines()
    assert len(copied) == 101
    assert set(copied) == expected
    await playwright.async_api.expect(page.locator("#pagination-status")).to_have_text("ページ 2 / 2（全101件）")
    await playwright.async_api.expect(page.locator("#copy-all-button")).to_be_enabled()
    assert await page.locator("#copy-all-button").evaluate("""element => {
        const label = document.createRange();
        label.selectNodeContents(element);
        return label.getClientRects().length === 1;
    }""")
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    await page.unroute("**/api/entries?*", delay_copy)


@pytest.mark.asyncio(loop_scope="session")
async def test_copy_all_work_items_reports_empty_and_clipboard_failure(browser_harness: _BrowserHarness) -> None:
    """0件ではコピーを行わず、権限拒否は再操作できるエラーとして表示する。"""
    page = browser_harness.page
    await page.add_init_script(
        "Object.defineProperty(navigator, 'clipboard', {value: {writeText: async () => {throw new Error('書込拒否')}}})"
    )
    await page.goto(browser_harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await _open_filters(page)
    await page.get_by_label("検索", exact=True).fill("一致しない検索")
    await playwright.async_api.expect(page.locator("#empty-state")).to_be_visible()
    await page.get_by_role("button", name="全てコピー", exact=True).click()
    await playwright.async_api.expect(page.locator("#copy-all-status")).to_contain_text("項目は0件")
    await playwright.async_api.expect(page.locator("#operation-notice")).to_be_hidden()
    await page.get_by_role("button", name="条件をクリア", exact=True).first.click()
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(4)
    await page.get_by_role("button", name="全てコピー", exact=True).click()
    await playwright.async_api.expect(page.locator("#operation-notice")).to_contain_text("書込拒否")
    await playwright.async_api.expect(page.locator("#operation-notice")).to_contain_text("再度実行してください")
    await playwright.async_api.expect(page.locator("#copy-all-button")).to_be_enabled()

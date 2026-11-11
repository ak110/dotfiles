"""serveのbrowser_wiに関するsearchの試験。"""

import asyncio
import datetime
import re

import playwright.async_api
import pytest

from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _browser_harness_fixture,
    _BrowserHarness,
    _observe_screen_requests,
    _open_filters,
    _open_question,
    _screen_harness_fixture,
    _wait_screen_requests_consumed,
)
from agent_toolkit._testing.serve_browser_wi_support import _entry_path
from agent_toolkit._testing.serve_support import _disable_wi_git

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
async def test_accessible_workflows_filters_warnings_and_sse_status(browser_harness: _BrowserHarness) -> None:
    """回答・削除の操作のフォーカス、条件に応じた表示、警告と、エンドユーザーの操作による更新だけに出す件数通知を検証する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    assert await page.locator(".filters details").evaluate("element => element.open")
    await _open_filters(page)

    # 警告は件数を示す見出しだけを常に表示し、全文は開いて読む。
    warning = page.get_by_role("alert").filter(has_text="一覧から除外したファイルの警告")
    await warning.wait_for(state="visible")
    await playwright.async_api.expect(warning.locator("summary")).to_contain_text("一覧から除外したファイルの警告: 1件")
    await warning.locator("summary").click()
    await playwright.async_api.expect(page.locator("#list-warning")).to_be_visible()
    warning_text = await warning.inner_text()
    assert "UTF-8として読み取れません" in warning_text
    # 状態を読めない項目も、年月の保存先と修正操作を警告から特定できる。
    assert str(_entry_path(harness.root, "invalid.md")) in warning_text
    assert "次の操作: " in warning_text
    assert "UTF-8として保存し直す" in warning_text
    assert await page.locator('#entry-list .entry-select[data-kind="unknown"]').count() == 1

    awi_row = page.locator('#entry-list .entry-select[data-kind="awi"]').filter(has_text="awi.md")
    assert await awi_row.locator(".entry-kind").text_content() == "awi"
    assert await awi_row.locator(".plan-badge").text_content() == "plan"
    assert await awi_row.locator(".state-badge").text_content() == "inbox"
    assert await awi_row.locator(".filename-cell").text_content() == "awi.md"
    assert await awi_row.locator(".summary-cell").text_content() == "編集対象の本文"

    empty_row = page.locator("#entry-list .entry-select").filter(has_text="empty.md")
    assert await empty_row.locator(".plan-badge").count() == 0
    await empty_row.click()
    empty_dialog = page.get_by_role("dialog", name="詳細")
    assert await empty_dialog.locator("#detail-content").inner_html() == ""
    assert await empty_dialog.locator("#detail-content table.frontmatter").count() == 0
    await page.keyboard.press("Escape")

    harness.operations.enable_file_mutations()
    row = await _open_question(page)
    dialog = page.get_by_role("dialog", name="詳細")
    assert await dialog.locator("#detail-content").is_visible()
    await dialog.get_by_role("button", name="A", exact=True).click()
    answer = dialog.locator("#answer-input")
    assert await answer.input_value() == "A"
    await answer.fill("Aを補足")
    assert await answer.input_value() == "Aを補足"
    await dialog.get_by_role("button", name="回答を保存").click()
    await playwright.async_api.expect(dialog).to_be_hidden()
    await page.get_by_role("status").filter(has_text="回答しました").wait_for(state="visible")
    await playwright.async_api.expect(row).to_be_focused()
    assert harness.operations.answer_calls == 1

    await awi_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="編集", exact=True).click()
    edit_input = detail.locator("#edit-content")
    await edit_input.fill((await edit_input.input_value()) + "\n追記")
    await detail.get_by_role("button", name="保存", exact=True).click()
    await page.get_by_role("status").filter(has_text="保存しました").wait_for(state="visible")
    await playwright.async_api.expect(detail).to_be_hidden()
    await awi_row.click()
    detail = page.get_by_role("dialog", name="詳細")

    await detail.get_by_role("button", name="削除").click()
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    await delete_dialog.wait_for(state="visible")
    await playwright.async_api.expect(delete_dialog.get_by_role("button", name="閉じる")).to_be_focused()
    await page.keyboard.press("Escape")
    assert await page.get_by_role("dialog", name="詳細").is_visible()
    await page.keyboard.press("Escape")

    await page.locator("#kind-filter").select_option("awi")
    await page.locator("#state-filter").select_option("active")
    await playwright.async_api.expect(page.locator("#answer-filter")).to_be_enabled()
    assert await page.locator("#answer-filter").input_value() == "all"
    assert await page.locator("#source-filter option").all_text_contents() == ["all", "human", "agent"]
    async with page.expect_response(
        lambda response: response.url.endswith(
            "/api/entries?type=awi&status=active&answered=all&period=all&source_kind=agent&page=1"
        )
    ):
        await page.locator("#source-filter").select_option("agent")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(2)
    await playwright.async_api.expect(awi_row).to_be_visible()
    await playwright.async_api.expect(empty_row).to_be_visible()
    async with page.expect_response(
        lambda response: response.url.endswith(
            "/api/entries?type=awi&status=active&answered=all&period=all&source_kind=human&page=1"
        )
    ):
        await page.locator("#source-filter").select_option("human")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(0)
    await page.locator("#clear-filters-button").click()

    await page.locator("#target-filter").select_option("example/repo")
    await page.locator("#state-filter").select_option("adopted")
    await playwright.async_api.expect(page.locator("#target-filter")).to_have_value("example/repo")
    await playwright.async_api.expect(page.locator("#target-filter option")).to_have_text(
        ["all", "adopted/repo", "example/repo"]
    )
    await playwright.async_api.expect(page.locator("#empty-state")).to_be_visible()

    async with page.expect_response(
        lambda response: (
            response.request.method == "GET"
            and response.url.endswith("/api/entries?type=all&status=active&answered=all&period=2w&page=1")
        )
    ):
        await page.locator("#clear-filters-button").click()
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(4)
    await playwright.async_api.expect(page.locator("#result-status")).to_have_text("4件を表示")

    async with page.expect_response(
        lambda response: (
            response.request.method == "GET"
            and response.url.endswith(
                "/api/entries?type=all&status=all&answered=all&period=all&q=%E7%B7%A8%E9%9B%86%E5%AF%BE%E8%B1%A1&page=1"
            )
        )
    ):
        await page.locator("#search-input").fill("編集対象")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(1)
    await playwright.async_api.expect(awi_row).to_be_visible()
    await playwright.async_api.expect(page.locator("#result-status")).to_have_text("1件を表示")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await playwright.async_api.expect(page.locator("#connection-status")).to_be_hidden()
    (_entry_path(harness.root, "sse.md")).write_text(
        "---\ntype: awi\nstate: inbox\ntarget_repo: sse/repo\nsource: browser\n---\n\n編集対象の外部追加\n",
        encoding="utf-8",
    )
    harness.current_state.publish()
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(2)
    await page.locator("#entry-list .entry-select").filter(has_text="sse.md").wait_for(state="visible")
    await playwright.async_api.expect(page.locator('#target-filter option[value="sse/repo"]')).to_have_count(1)
    await playwright.async_api.expect(page.locator("#result-status")).to_have_text("1件を表示")


@pytest.mark.asyncio(loop_scope="session")
async def test_work_item_search_modes_preserve_conditions_and_clear(browser_harness: _BrowserHarness) -> None:
    """古い終了項目を検索し、手動条件を保持して通常表示と往復し、全消去できる。"""
    page = browser_harness.page
    old_name = f"{datetime.datetime.now() - datetime.timedelta(days=40):%Y%m%d-%H%M%S}-001.md"
    _entry_path(browser_harness.root, old_name).write_text(
        "---\ntype: awi\nstate: adopted\ntarget_repo: adopted/repo\n---\n\n過去の採用項目\n",
        encoding="utf-8",
    )
    await page.set_viewport_size({"width": 320, "height": 800})
    await page.goto(browser_harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await playwright.async_api.expect(page.locator("#wi-mode-normal")).to_be_checked()
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(4)
    await _open_filters(page)
    await playwright.async_api.expect(page.get_by_label("state", exact=True)).to_have_value("all")
    await playwright.async_api.expect(page.get_by_label("period", exact=True)).to_have_value("all")
    await page.get_by_label("検索", exact=True).fill("過去の採用項目")
    await playwright.async_api.expect(page.locator(f'.entry-select[data-key="{old_name}"]')).to_be_visible()
    await playwright.async_api.expect(page.locator("#wi-mode-search")).to_be_checked()
    await page.get_by_label("state", exact=True).select_option("rejected")
    await playwright.async_api.expect(page.locator("#empty-state")).to_be_visible()
    await page.get_by_label("state", exact=True).select_option("adopted")
    await page.get_by_label("period", exact=True).select_option("8w")
    await page.get_by_label("target-repo", exact=True).select_option("adopted/repo")
    await playwright.async_api.expect(page.locator(f'.entry-select[data-key="{old_name}"]')).to_be_visible()
    await page.locator(".filters summary").click()
    await page.get_by_role("radio", name="通常表示", exact=True).check()
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(4)
    await playwright.async_api.expect(page.locator("#wi-effective-conditions")).to_have_text(
        "通常表示: state=active、直近2週間"
    )
    await page.get_by_role("radio", name="条件検索", exact=True).check()
    await playwright.async_api.expect(page.locator(f'.entry-select[data-key="{old_name}"]')).to_be_visible()
    await page.get_by_role("button", name="今すぐ同期", exact=True).click()
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    await _open_filters(page)
    await playwright.async_api.expect(page.get_by_label("target-repo", exact=True)).to_have_value("adopted/repo")
    await playwright.async_api.expect(page.get_by_label("period", exact=True)).to_have_value("8w")
    await page.get_by_label("検索", exact=True).fill("")
    await playwright.async_api.expect(page.locator("#wi-mode-search")).to_be_checked()
    await playwright.async_api.expect(page.locator("#pagination-status")).to_contain_text("全2件")
    await page.locator(".filters summary").click()
    await page.locator("#clear-filters-button").click()
    await playwright.async_api.expect(page.locator("#wi-mode-normal")).to_be_checked()
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(4)
    await _open_filters(page)
    for field in ("kind", "state", "period", "answer", "important"):
        await playwright.async_api.expect(page.locator(f"#{field}-filter")).to_have_value("all")
    for field in ("target", "source"):
        await playwright.async_api.expect(page.locator(f"#{field}-filter")).to_have_value("")
    await playwright.async_api.expect(page.get_by_label("検索", exact=True)).to_have_value("")
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.asyncio(loop_scope="session")
async def test_work_item_search_ime_and_pending_input_return_to_normal(browser_harness: _BrowserHarness) -> None:
    """IME未確定入力では取得せず、確定後の連続入力や保留中の応答を通常表示への復帰で無効化する。"""
    page = browser_harness.page
    await page.clock.install()
    await _observe_screen_requests(page)
    await page.goto(browser_harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await _open_filters(page)
    search = page.get_by_label("検索", exact=True)
    request_urls: list[str] = []
    page.on("request", lambda request: request_urls.append(request.url) if "/api/entries?" in request.url else None)
    await search.dispatch_event("compositionstart")
    await search.fill("未確定")
    await page.clock.fast_forward(300)
    assert not [url for url in request_urls if "q=" in url]
    await search.evaluate("element => { element.value = '採用済み'; }")
    await search.dispatch_event("compositionend")
    await page.clock.fast_forward(300)
    await playwright.async_api.expect(page.locator('.entry-select[data-key="adopted.md"]')).to_be_visible()
    await search.fill("不採用")
    await page.get_by_role("radio", name="通常表示", exact=True).check()
    await page.clock.fast_forward(300)
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(4)
    await playwright.async_api.expect(search).to_have_value("不採用")
    held = asyncio.Event()
    release = asyncio.Event()

    async def hold_search(route: playwright.async_api.Route) -> None:
        if "q=" not in route.request.url:
            await route.continue_()
            return
        response = await route.fetch()
        held.set()
        await release.wait()
        await route.fulfill(response=response)

    await page.route("**/api/entries?*", hold_search)
    try:
        await page.get_by_role("radio", name="条件検索", exact=True).check()
        await asyncio.wait_for(held.wait(), timeout=10)
        await page.get_by_role("radio", name="通常表示", exact=True).check()
        await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(4)
        release.set()
        await _wait_screen_requests_consumed(page, ("/api/events",))
        await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(4)
        await playwright.async_api.expect(page.locator("#wi-mode-normal")).to_be_checked()
    finally:
        release.set()
        await page.unroute("**/api/entries?*", hold_search)


@pytest.mark.asyncio(loop_scope="session")
async def test_period_filter_limits_work_items_to_recent_by_default(browser_harness: _BrowserHarness) -> None:
    """期間の初期値は直近2週間で、期間を広げると古いWIが現れ、条件をクリアすると直近2週間へ戻る。

    初期値で期間を限定しないと古いWIが一覧を埋め、期間を広げる操作が無いと古いWIへ到達できない。
    """
    harness = browser_harness
    page = harness.page
    now = datetime.datetime.now()
    names = {days: f"{now - datetime.timedelta(days=days):%Y%m%d-%H%M%S}-001.md" for days in (1, 20, 40)}
    for name in names.values():
        (_entry_path(harness.root, name)).write_text(
            f"---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\nsource: browser\n---\n\n期間の確認 {name}\n",
            encoding="utf-8",
        )
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await _open_filters(page)
    period = page.locator("#period-filter")
    rows = page.locator("#entry-list .entry-select")
    await playwright.async_api.expect(period).to_have_value("all")
    await playwright.async_api.expect(rows.filter(has_text=names[1])).to_have_count(1)
    await playwright.async_api.expect(rows.filter(has_text=names[20])).to_have_count(0)

    await period.select_option("4w")
    await playwright.async_api.expect(rows.filter(has_text=names[20])).to_have_count(1)
    await playwright.async_api.expect(rows.filter(has_text=names[40])).to_have_count(0)
    await period.select_option("8w")
    await playwright.async_api.expect(rows.filter(has_text=names[40])).to_have_count(1)
    await period.select_option("all")
    await playwright.async_api.expect(rows.filter(has_text=names[40])).to_have_count(1)
    # 作成日時を読めない名前の項目は期間で隠さない。
    await playwright.async_api.expect(rows.filter(has_text="awi.md")).to_have_count(1)

    await page.locator("#clear-filters-button").click()
    await playwright.async_api.expect(period).to_have_value("all")
    await playwright.async_api.expect(rows.filter(has_text=names[20])).to_have_count(0)
    await playwright.async_api.expect(rows.filter(has_text=names[1])).to_have_count(1)
    await playwright.async_api.expect(page.locator("#entry-period")).to_have_text("直近2週間に作成")


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("state", ["active", "processable", "all", "inbox", "processing", "hold", "adopted", "rejected"])
async def test_empty_list_limited_by_period_offers_all_periods(browser_harness: _BrowserHarness, state: str) -> None:
    """期間の内側に対応中の項目が無い空状態から、全期間の一覧へ1操作で進め、狭い幅でも期間の限定を確認できる。

    空状態に期間を広げる操作が無いと、古い対応中の項目を探すエンドユーザーがフィルター欄を探して開く必要がある。
    """
    harness = browser_harness
    page = harness.page
    await page.set_viewport_size({"width": 320, "height": 800})
    old_name = f"{datetime.datetime.now() - datetime.timedelta(days=40):%Y%m%d-%H%M%S}-001.md"
    stored_state = "inbox" if state in {"active", "processable", "all"} else state
    (_entry_path(harness.root, old_name)).write_text(
        f"---\ntype: awi\nstate: {stored_state}\ntarget_repo: example/repo\nsource: browser\n---\n\n古い対応中の項目\n",
        encoding="utf-8",
    )

    async def empty_recent(route: playwright.async_api.Route) -> None:
        await route.fulfill(
            json={"entries": [], "warnings": [], "pagination": {"page": 1, "page_size": 100, "page_count": 1, "total_count": 0}}
        )

    await page.route("**/api/entries?*period=2w*", empty_recent)
    await page.goto(harness.base_url + "/")
    await _open_filters(page)
    await page.locator("#state-filter").select_option(state)
    await page.locator("#period-filter").select_option("2w")
    await playwright.async_api.expect(page.locator("#empty-state")).to_be_visible()
    await playwright.async_api.expect(page.locator("#entry-period")).to_be_visible()
    await playwright.async_api.expect(page.locator("#entry-period")).to_have_text("直近2週間に作成")
    await page.get_by_role("button", name="全期間を表示").click()

    await playwright.async_api.expect(page.locator("#entry-list .entry-select").filter(has_text=old_name)).to_have_count(1)
    await playwright.async_api.expect(page.locator("#period-filter")).to_have_value("all")
    await playwright.async_api.expect(page.locator("#state-filter")).to_have_value(state)
    await playwright.async_api.expect(page.locator("#entry-period")).to_be_hidden()


@pytest.mark.asyncio(loop_scope="session")
async def test_detail_focus_falls_back_after_answer_filter_and_delete(
    browser_harness: _BrowserHarness,
) -> None:
    """回答後に詳細を閉じたときと削除・0件化の後は、フォーカスが操作できる一覧の要素へ戻る。"""
    harness = browser_harness
    page = harness.page
    harness.operations.enable_file_mutations()
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await playwright.async_api.expect(page.locator("#connection-status")).to_be_hidden()
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await _open_filters(page)

    await page.locator("#answer-filter").select_option("no")
    question_row = page.locator('#entry-list .entry-select[data-kind="uwi"]')
    await question_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.locator("#answer-input").fill("回答済みにする")
    await detail.get_by_role("button", name="回答を保存").click()
    await page.get_by_role("status").filter(has_text="回答しました").wait_for(state="visible")
    await page.keyboard.press("Escape")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(0)
    await playwright.async_api.expect(page.locator("#empty-clear-button")).to_be_focused()

    await page.locator("#empty-clear-button").click()
    awi_row = page.locator('#entry-list .entry-select[data-kind="awi"]').filter(has_text="awi.md")
    await awi_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="削除").click()
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    await delete_dialog.get_by_role("button", name="削除する").click()
    await playwright.async_api.expect(awi_row).to_have_count(0)
    if await detail.is_visible():
        await page.keyboard.press("Escape")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select").first).to_be_focused()


@pytest.mark.asyncio(loop_scope="session")
async def test_work_item_filter_fit_order_and_computed_contrast(browser_harness: _BrowserHarness) -> None:
    """標準幅でフィルター値が収まり、操作順と文字・入力境界のコントラストを保つ。"""
    page = browser_harness.page
    await page.set_viewport_size({"width": 1280, "height": 800})
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    metrics = await page.evaluate("""() => {
      const select = document.querySelector('#state-filter');
      const label = document.querySelector('label[for="state-filter"]');
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      ctx.font = getComputedStyle(select).font;
      const valueWidth = ctx.measureText(select.selectedOptions[0].textContent).width;
      const rgb = value => value.match(/[\\d.]+/g).slice(0, 3).map(Number);
      const luminance = value => rgb(value).map(channel => {
        const v = channel / 255;
        return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
      }).reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0);
      const contrast = (first, second) => {
        const values = [luminance(first), luminance(second)].sort((a, b) => b - a);
        return (values[0] + 0.05) / (values[1] + 0.05);
      };
      const secondary = document.querySelector('#entry-count');
      const search = document.querySelector('#search-input');
      const row = document.querySelector('#entry-list .entry-select');
      return {
        fits: select.clientWidth >= valueWidth + 32,
        clientWidth: select.clientWidth,
        valueWidth,
        sidebarWidth: select.closest('.filters').clientWidth,
        labelAbove: label.getBoundingClientRect().bottom <= select.getBoundingClientRect().top,
        filterBeforeList: Boolean(search.compareDocumentPosition(row) & Node.DOCUMENT_POSITION_FOLLOWING),
        textContrast: contrast(getComputedStyle(secondary).color, getComputedStyle(secondary.closest('.card')).backgroundColor),
        borderContrast: contrast(getComputedStyle(select).borderTopColor, getComputedStyle(select).backgroundColor)
      };
    }""")
    assert metrics["fits"] and metrics["labelAbove"] and metrics["filterBeforeList"], metrics
    assert metrics["textContrast"] >= 4.5
    assert metrics["borderContrast"] >= 3


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("filename", ["awi.md", "question.md", "adopted.md"])
async def test_work_item_important_toggle_badge_and_filter(
    browser_harness: _BrowserHarness, monkeypatch: pytest.MonkeyPatch, filename: str
) -> None:
    """AWI・UWI・終了項目の重要設定と解除が一覧と重要・非重要の条件指定へ反映される。"""
    _disable_wi_git(monkeypatch)
    page = browser_harness.page
    await page.set_viewport_size({"width": 320, "height": 800})
    await page.goto(browser_harness.base_url + "/?filename=" + filename)
    detail = page.get_by_role("dialog", name="詳細", exact=True)
    await playwright.async_api.expect(detail).to_be_visible()
    await page.get_by_role("button", name="重要設定", exact=True).click()
    await playwright.async_api.expect(detail).to_be_hidden()
    await playwright.async_api.expect(page.locator("#operation-notice")).to_contain_text("重要設定しました")
    await _open_filters(page)
    await page.get_by_label("state", exact=True).select_option("all")
    await page.get_by_label("重要", exact=True).select_option("yes")
    row = page.locator(f'.entry-select[data-key="{filename}"]')
    await playwright.async_api.expect(row.locator(".important-badge")).to_have_text("重要")
    await playwright.async_api.expect(row).to_have_attribute("aria-label", re.compile("、重要、"))
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    await row.focus()
    await page.keyboard.press("Enter")
    await page.get_by_role("button", name="重要解除", exact=True).click()
    await playwright.async_api.expect(detail).to_be_hidden()
    await playwright.async_api.expect(row).to_have_count(0)
    await page.get_by_label("重要", exact=True).select_option("no")
    await playwright.async_api.expect(row).to_be_visible()
    await playwright.async_api.expect(row.locator(".important-badge")).to_have_count(0)
    saved_paths = list(browser_harness.root.rglob(filename))
    assert len(saved_paths) == 1
    assert "important:" not in saved_paths[0].read_text(encoding="utf-8")
    await page.get_by_role("button", name="条件をクリア", exact=True).first.click()
    await playwright.async_api.expect(page.get_by_label("重要", exact=True)).to_have_value("all")

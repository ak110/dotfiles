import datetime
import socket

import playwright.async_api
import pytest

from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _LONG_UNKNOWN_FRONTMATTER_KEY,
    _browser_fixture,
    _browser_harness_fixture,
    _BrowserHarness,
    _open_filters,
    _open_question,
    _screen_harness_fixture,
    _ScreenHarness,
)
from agent_toolkit._testing.serve_browser_wi_support import _entry_path

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("width", [320, 1280])
async def test_narrow_work_items_keep_unique_identifiers_visible(browser_harness: _BrowserHarness, width: int) -> None:
    page = browser_harness.page
    names = [f"same-prefix-unique-00{index}.md" for index in (1, 2)]
    for index, name in enumerate(names):
        (_entry_path(browser_harness.root, name)).write_text(
            f"---\ntype: awi\nstate: inbox\ntarget_repo: example/repo-{index}\n---\n\n" + "同じ要約の接頭辞" * 30 + "\n",
            encoding="utf-8",
        )
    await page.set_viewport_size({"width": width, "height": 800})
    await page.goto(browser_harness.base_url + "/")
    for name in names:
        row = page.locator(".entry-select").filter(has=page.locator(".filename-cell", has_text=name))
        for selector in (".filename-cell", ".target-repo-cell"):
            cell = row.locator(selector)
            await playwright.async_api.expect(cell).to_be_visible()
            assert await cell.evaluate("element => element.scrollWidth <= element.clientWidth")
        await row.click()
        await playwright.async_api.expect(page.locator("#detail-filename")).to_have_text(name)
        await page.locator("#detail-close-button").click()
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.cpu_limit(
    1.75,
    reason=(
        "カード・列表示の画面幅で横overflow、固定領域、タッチ寸法、Markdown表示を検証する"
        "。実ブラウザーの観測とtrace回収が必要（CPU最大実測0.669秒）。"
    ),
)
@pytest.mark.asyncio(loop_scope="session")
async def test_responsive_layout_dialog_scroll_and_markdown(browser_harness: _BrowserHarness) -> None:
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await _open_filters(page)

    for width, height in [(390, 844), (768, 1024), (1280, 800), (1920, 800)]:
        await page.set_viewport_size({"width": width, "height": height})
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert await page.locator(".entry-copy").first.evaluate("element => element.scrollWidth <= element.clientWidth")
        row = await _open_question(page)
        dialog = page.get_by_role("dialog", name="詳細")
        await playwright.async_api.expect(dialog.locator("#detail-metadata")).to_contain_text("priority")
        await playwright.async_api.expect(dialog.locator("#detail-metadata")).to_contain_text('"branch": "main"')
        assert await dialog.locator("#detail-metadata dt").filter(has_text=_LONG_UNKNOWN_FRONTMATTER_KEY).count() == 1
        metadata_labels = await dialog.locator("#detail-metadata dt").all_text_contents()
        assert metadata_labels.index("z_key") < metadata_labels.index("int: 1")
        assert metadata_labels.index("int: 1") < metadata_labels.index("1")
        assert metadata_labels.index("1") < metadata_labels.index("a_key")
        metadata_columns = await dialog.locator("#detail-metadata").evaluate(
            "element => getComputedStyle(element).gridTemplateColumns.trim().split(/\\s+/).length"
        )
        assert metadata_columns == (1 if width < 1024 else 2)
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        close_button = dialog.get_by_role("button", name="閉じる")
        close_box = await close_button.bounding_box()
        assert close_box is not None
        assert close_box["width"] >= 44
        assert close_box["height"] >= 44

        body = dialog.locator(".dialog-body")
        scroll_metrics = await body.evaluate("element => ({client: element.clientHeight, scroll: element.scrollHeight})")
        assert scroll_metrics["scroll"] > scroll_metrics["client"]
        header_before = await dialog.locator(".dialog-header").bounding_box()
        footer_before = await dialog.locator(".dialog-footer").bounding_box()
        await body.evaluate("element => { element.scrollTop = 300; }")
        header_after = await dialog.locator(".dialog-header").bounding_box()
        footer_after = await dialog.locator(".dialog-footer").bounding_box()
        assert header_before == header_after
        assert footer_before == footer_after
        assert await dialog.locator("pre").evaluate("element => getComputedStyle(element).whiteSpace") == "pre-wrap"
        inline_background = await dialog.locator("p code").first.evaluate(
            "element => getComputedStyle(element).backgroundColor"
        )
        assert inline_background not in {"rgba(0, 0, 0, 0)", "transparent"}
        await page.keyboard.press("Escape")
        await playwright.async_api.expect(row).to_be_focused()
        await row.click()
        await playwright.async_api.expect(dialog).to_be_visible()
        assert await body.evaluate("element => element.scrollTop") == 0
        await page.keyboard.press("Escape")
        await playwright.async_api.expect(row).to_be_focused()


@pytest.mark.asyncio(loop_scope="session")
async def test_mobile_wi_list_starts_with_identifiable_entries(browser_harness: _BrowserHarness) -> None:
    page = browser_harness.page
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.goto(browser_harness.base_url + "/")
    row = page.locator("#entry-list .entry-row").first
    await row.wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")

    assert not await page.locator(".filters details").evaluate("element => element.open")
    summary_box = await row.locator(".summary-cell").bounding_box()
    status_box = await row.locator(".status-cell").bounding_box()
    filename_box = await row.locator(".filename-cell").bounding_box()
    target_box = await row.locator(".target-repo-cell").bounding_box()
    row_box = await row.bounding_box()
    assert summary_box is not None
    assert status_box is not None
    assert filename_box is not None
    assert target_box is not None
    assert row_box is not None
    assert summary_box["y"] + summary_box["height"] <= status_box["y"]
    assert status_box["y"] + status_box["height"] <= filename_box["y"]
    assert filename_box["y"] + filename_box["height"] <= target_box["y"]
    assert row_box["y"] >= 0
    assert row_box["y"] + row_box["height"] <= 844

    summary = page.get_by_role("alert").locator("summary")
    await playwright.async_api.expect(summary).to_be_visible()
    await playwright.async_api.expect(summary).to_contain_text("一覧から除外したファイルの警告: 1件")
    full_text = page.locator("#list-warning")
    await playwright.async_api.expect(full_text).to_be_hidden()
    await summary.click()
    await playwright.async_api.expect(full_text).to_be_visible()
    for expected in (
        str(_entry_path(browser_harness.root, "invalid.md")),
        "UTF-8として読み取れません",
        "UTF-8として保存し直す",
    ):
        await playwright.async_api.expect(full_text).to_contain_text(expected, use_inner_text=True)
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.asyncio(loop_scope="session")
async def test_global_error_can_be_closed_and_redisplayed_on_narrow_screen(
    browser_harness: _BrowserHarness,
) -> None:
    """共通エラーをキーボードで消去し、後続の失敗で再表示できることを検証する。"""
    page = browser_harness.page
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    error_region = page.locator("#operation-notice")
    error_message = page.locator("#operation-notice-message")
    close_button = page.get_by_role("button", name="操作通知を閉じる")

    async def fail_first_list_request(route: playwright.async_api.Route) -> None:
        await route.fulfill(
            status=500,
            content_type="application/json",
            body='{"error":"一覧取得失敗"}',
        )

    await page.route("**/api/sync", fail_first_list_request)
    await page.locator("#refresh-button").click()
    await playwright.async_api.expect(error_message).to_contain_text(
        f"［WI / {socket.gethostname()} / {browser_harness.root} / Git同期］一覧取得失敗"
    )
    await playwright.async_api.expect(error_region).to_be_visible()
    await page.unroute("**/api/sync", fail_first_list_request)
    await close_button.focus()
    await playwright.async_api.expect(close_button).to_be_focused()
    await page.keyboard.press("Enter")
    await playwright.async_api.expect(error_region).to_be_hidden()
    await playwright.async_api.expect(page.locator("#refresh-button")).to_be_focused()

    async def fail_second_list_request(route: playwright.async_api.Route) -> None:
        await route.fulfill(
            status=500,
            content_type="application/json",
            body='{"error":"後続のエラー"}',
        )

    await page.route("**/api/sync", fail_second_list_request)
    await page.locator("#refresh-button").click()
    await playwright.async_api.expect(error_message).to_contain_text(
        f"［WI / {socket.gethostname()} / {browser_harness.root} / Git同期］後続のエラー"
    )
    await playwright.async_api.expect(error_region).to_be_visible()

    metrics = await error_region.evaluate(
        """element => {
          const message = document.getElementById('operation-notice-message').getBoundingClientRect();
          const close = document.getElementById('operation-notice-close-button').getBoundingClientRect();
          return {
            scrollWidth: document.documentElement.scrollWidth,
            viewportWidth: window.innerWidth,
            regionRight: element.getBoundingClientRect().right,
            messageRight: message.right,
            closeLeft: close.left,
            closeRight: close.right,
            closeWidth: close.width,
            closeHeight: close.height
          };
        }"""
    )
    assert metrics["scrollWidth"] <= metrics["viewportWidth"]
    assert metrics["regionRight"] <= metrics["viewportWidth"]
    assert metrics["messageRight"] <= metrics["closeLeft"]
    assert metrics["closeRight"] <= metrics["viewportWidth"]
    assert metrics["closeWidth"] >= 44
    assert metrics["closeHeight"] >= 44
    await page.unroute("**/api/sync", fail_second_list_request)


@pytest.mark.asyncio(loop_scope="session")
async def test_long_unknown_metadata_key_wraps_at_narrow_viewport(
    browser_harness: _BrowserHarness,
) -> None:
    page = browser_harness.page
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await _open_question(page)

    dialog = page.get_by_role("dialog", name="詳細")
    long_key_term = dialog.locator("#detail-metadata dt").filter(has_text=_LONG_UNKNOWN_FRONTMATTER_KEY)
    await playwright.async_api.expect(long_key_term).to_have_count(1)

    metrics_handle = await page.wait_for_function(
        """key => {
          const metadata = document.querySelector('#detail-metadata');
          const terms = [...(metadata?.querySelectorAll('dt') ?? [])]
            .filter(element => element.textContent === key);
          if (terms.length !== 1) return false;
          const term = terms[0];
          if (!term.checkVisibility()) return false;
          return {
            termWidth: term.getBoundingClientRect().width,
            metadataWidth: metadata.getBoundingClientRect().width,
            termScrollWidth: term.scrollWidth,
            termClientWidth: term.clientWidth,
            documentScrollWidth: document.documentElement.scrollWidth,
            viewportWidth: window.innerWidth
          };
        }""",
        arg=_LONG_UNKNOWN_FRONTMATTER_KEY,
    )
    metrics = await metrics_handle.json_value()
    await metrics_handle.dispose()
    assert metrics["termWidth"] <= metrics["metadataWidth"]
    assert metrics["termScrollWidth"] <= metrics["termClientWidth"]
    assert metrics["documentScrollWidth"] <= metrics["viewportWidth"]


@pytest.mark.cpu_limit(
    1.75,
    reason=(
        "最長の状態表示を含む一覧でも固定列を折り返さず、全行を同じ高さで描画する。実ブラウザーの観測とt"
        "race回収が必要（CPU最大実測0.632秒）。"
    ),
)
@pytest.mark.asyncio(loop_scope="session")
async def test_work_item_rows_keep_fixed_columns_and_equal_heights(screen_harness: _ScreenHarness) -> None:

    _entry_path(screen_harness.plan_path.parent.parent, f"{datetime.datetime.now():%Y%m%d}-123456-001.md").write_text(
        "---\ntype: uwi\nstate: processing\ntarget_repo: example/long-repository\nsource: human\n"
        "plan_file: /tmp/plan.md\n---\n\n長い要約を持つ確認事項 "
        + "要約" * 80
        + "\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    page = screen_harness.page
    await page.set_viewport_size({"width": 1920, "height": 800})
    await page.goto(screen_harness.base_url + "/")
    rows = page.locator("#entry-list .entry-row")
    await playwright.async_api.expect(rows).to_have_count(5)
    await playwright.async_api.expect(page.locator(".entry-columns")).to_be_visible()

    metrics = await rows.evaluate_all(
        """rows => rows.map(row => {
          const filename = row.querySelector('.filename-cell');
          const status = row.querySelector('.status-cell');
          const childHeights = Array.from(status.children, child => child.getBoundingClientRect().height);
          return {
            height: row.getBoundingClientRect().height,
            filenameClientWidth: filename.clientWidth,
            filenameScrollWidth: filename.scrollWidth,
            statusHeight: status.getBoundingClientRect().height,
            tallestStatusChild: Math.max(...childHeights)
          };
        })"""
    )
    assert len({round(metric["height"], 1) for metric in metrics}) == 1
    assert all(metric["filenameScrollWidth"] <= metric["filenameClientWidth"] for metric in metrics)
    assert all(metric["statusHeight"] <= metric["tallestStatusChild"] + 1 for metric in metrics)


@pytest.mark.asyncio(loop_scope="session")
async def test_wi_rows_are_readable_and_keep_geometry_on_text_only_update(browser_harness: _BrowserHarness) -> None:
    harness = browser_harness
    page = harness.page
    prefix = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d")
    heading = "# 確認する項目"
    for index in range(1, 34):
        name = f"{prefix}-120000-{index:03d}.md"
        _entry_path(harness.root, name).write_text(
            f"---\ntype: awi\nstate: inbox\ntarget_repo: github.com/owner/project\n---\n\n{heading} {index}\n",
            encoding="utf-8",
        )
    await page.goto(harness.base_url + "/")
    rows = page.locator("#entry-list .entry-row")
    await playwright.async_api.expect(rows.filter(has_text="確認する項目")).to_have_count(33)
    summary = rows.filter(has_text="確認する項目").first.locator(".summary-cell")
    style = await summary.evaluate(
        "element => ({size: parseFloat(getComputedStyle(element).fontSize), "
        "line: parseFloat(getComputedStyle(element).lineHeight)})"
    )
    assert style["size"] >= 16
    assert style["line"] >= 1.5 * style["size"]
    await rows.last.scroll_into_view_if_needed()
    await playwright.async_api.expect(rows.last.locator(".entry-select")).to_be_in_viewport()
    entry = rows.filter(has=page.locator(f'.entry-select[data-key="{prefix}-120000-033.md"]'))
    await playwright.async_api.expect(entry.locator(".summary-cell")).to_have_text("確認する項目 33")
    await playwright.async_api.expect(entry.locator(".target-repo-cell")).to_have_text("owner/project")
    await playwright.async_api.expect(entry.locator(".target-repo-cell")).to_have_attribute("title", "github.com/owner/project")
    geometry = "rows => rows.map(row => [row.offsetTop, row.offsetHeight])"
    before = await rows.evaluate_all(geometry)
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    path = _entry_path(harness.root, f"{prefix}-120000-033.md")
    original = path.read_text(encoding="utf-8")
    for title in ("長い要約" * 80, "短い要約", "さらに長い要約" * 60, "さらに短い要約"):
        path.write_text(original.replace(heading, "# " + title), encoding="utf-8")
        harness.current_state.publish()
        await playwright.async_api.expect(entry.locator(".summary-cell")).to_contain_text(title[:30])
        assert await rows.evaluate_all(geometry) == before
    additional = _entry_path(harness.root, f"{prefix}-120000-034.md")
    additional.write_text(original.replace(heading, "# 新規行"), encoding="utf-8")
    harness.current_state.publish()
    await playwright.async_api.expect(rows.filter(has_text="新規行")).to_have_count(1)
    additional.unlink()
    harness.current_state.publish()
    await playwright.async_api.expect(rows.filter(has_text="新規行")).to_have_count(0)
    assert await rows.evaluate_all(geometry) == before


@pytest.mark.parametrize("width", [320, 390])
@pytest.mark.asyncio(loop_scope="session")
async def test_processing_state_remains_readable_with_long_repository_on_narrow_screen(
    browser_harness: _BrowserHarness, browser: playwright.async_api.Browser, width: int
) -> None:
    prefix = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d")
    name = f"{prefix}-120500-001.md"
    _entry_path(browser_harness.root, name).write_text(
        "---\ntype: awi\nstate: processing\nimportant: true\ntarget_repo: github.com/example/a-very-long-repository-name\n"
        "---\n\n# 現在の状態を確認する\n",
        encoding="utf-8",
    )
    context = await browser.new_context(
        viewport={"width": width, "height": 844}, is_mobile=True, has_touch=True, color_scheme="dark"
    )
    try:
        page = await context.new_page()
        await page.goto(browser_harness.base_url + "/")
        row = page.locator("#entry-list .entry-row").filter(has_text=name)
        badge = row.locator(".state-badge")
        await playwright.async_api.expect(badge).to_have_text("processing")
        assert await page.evaluate("matchMedia('(any-pointer: coarse)').matches")
        limits = await row.evaluate(
            """row => {
              const badge = row.querySelector('.state-badge');
              const filename = row.querySelector('.filename-cell');
              const copy = row.querySelector('.entry-copy').getBoundingClientRect();
              return {
                afterBadge: Math.min(copy.left, row.getBoundingClientRect().right, innerWidth)
                  - badge.getBoundingClientRect().right,
                overflowBadge: badge.scrollWidth - badge.clientWidth,
                overflowFilename: filename.scrollWidth - filename.clientWidth,
              };
            }"""
        )
        assert limits["afterBadge"] >= 0, limits
        assert limits["overflowBadge"] <= 0 and limits["overflowFilename"] <= 0, limits

        question = page.locator("#entry-list .entry-row").filter(has=page.locator('.entry-select[data-key="question.md"]'))
        unanswered = question.locator(".unanswered-badge")
        await playwright.async_api.expect(unanswered).to_have_text("未回答")
        assert await unanswered.evaluate(
            """badge => {
              const box = badge.getBoundingClientRect();
              const status = badge.parentElement.getBoundingClientRect();
              const copy = badge.closest('.entry-row').querySelector('.entry-copy').getBoundingClientRect();
              return badge.scrollWidth <= badge.clientWidth &&
                box.right <= Math.min(status.right, copy.left, innerWidth);
            }"""
        )
    finally:
        await context.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_active_awi_detail_highlights_adoption_without_highlighting_edit(browser_harness: _BrowserHarness) -> None:
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await page.locator('.entry-select[data-key="awi.md"]').click()
    dialog = page.get_by_role("dialog", name="詳細")
    expected = playwright.async_api.expect
    adopt = dialog.get_by_role("button", name="採用", exact=True)
    edit = dialog.get_by_role("button", name="編集", exact=True)
    await expected(adopt).to_have_class("button-primary")
    await expected(edit).to_have_class("button-secondary")
    await edit.click()
    await expected(dialog.get_by_role("button", name="保存", exact=True)).to_have_class("button-primary")

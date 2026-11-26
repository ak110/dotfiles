from urllib.parse import parse_qs, urlsplit

import playwright.async_api
import pytest

from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _browser_harness_fixture,
    _BrowserHarness,
)
from agent_toolkit._testing import wi_entry_files

pytestmark = [pytest.mark.browser]
_SOURCE = "reference-source.md"
_TARGET = "awi.md"
_UNREGISTERED = "20260102-030405-123.md"
_RENAMED = "20260102-030405-099.md"


async def _assert_middle_click(page: playwright.async_api.Page, link: playwright.async_api.Locator, filename: str) -> None:
    async with page.context.expect_page() as opened:
        await link.click(button="middle")
    popup = await opened.value
    try:
        await playwright.async_api.expect(popup.locator("#detail-filename")).to_have_text(filename)
        assert parse_qs(urlsplit(popup.url).query)["filename"] == [filename]
    finally:
        await popup.close()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("which", ["reference", "row"])
async def test_work_item_summary_keeps_both_destinations(browser_harness: _BrowserHarness, which: str) -> None:
    wi_entry_files.entry_path(browser_harness.root, _SOURCE).write_text(
        f"---\ntype: awi\nstate: inbox\n---\n\n# <b>要約</b> {_SOURCE} / {_TARGET}\n", encoding="utf-8"
    )
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    expect = playwright.async_api.expect
    own = page.locator(f'.entry-select[data-key="{_SOURCE}"]')
    row = own.locator("..")
    summary = row.locator(".summary-cell")
    reference = summary.get_by_role("link", name=_TARGET, exact=True)
    await expect(reference).to_be_visible()
    await expect(summary).to_contain_text("<b>要約</b>")
    await expect(summary.locator("b")).to_have_count(0)
    await expect(summary.get_by_role("link", name=_SOURCE)).to_have_count(0)
    assert await reference.evaluate("link => !link.parentElement.closest('a, button')")
    if which == "reference":
        await _assert_middle_click(page, reference, _TARGET)
        await reference.click()
        await expect(page.locator(".wi-reference-dialog[open]")).to_contain_text("編集対象の本文")
        await expect(page.locator("#detail-dialog")).to_be_hidden()
    else:
        await _assert_middle_click(page, own, _SOURCE)
        await own.click()
        await expect(page.locator("#detail-filename")).to_have_text(_SOURCE)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("batch", [False, True])
async def test_work_item_creation_notice_links_only_registered_names(browser_harness: _BrowserHarness, batch: bool) -> None:
    page = browser_harness.page
    await page.clock.install()
    await page.goto(browser_harness.base_url + "/")
    expect = playwright.async_api.expect
    await page.get_by_role("button", name="新規追加").click()
    dialog = page.get_by_role("dialog", name="新規追加")
    if batch:
        wi_entry_files.entry_path(browser_harness.root, _RENAMED).write_text(
            "---\ntype: awi\nstate: adopted\n---\n\n採用済みの改名先\n", encoding="utf-8"
        )
        await dialog.locator("#create-kind").select_option("batch")

        async def registered(route: playwright.async_api.Route) -> None:
            await route.fulfill(
                status=201,
                json={
                    "filenames": [_RENAMED],
                    "skipped": [_TARGET],
                    "mapping": {_UNREGISTERED: _RENAMED},
                    "warnings": [f"<b>登録結果</b> {_TARGET}"],
                },
            )

        await page.route("**/api/entries/batch", registered)
        await dialog.locator("#create-content").fill("一括登録する本文")
    else:
        await dialog.locator("#create-content").fill("---\ntarget_repo: github.com/frontmatter/repo\n---\n\n単体登録の参照先")
    await dialog.get_by_role("button", name="追加").click()
    notice = page.locator("#operation-notice-message")
    if batch:
        await expect(notice).to_contain_text(f"{_UNREGISTERED} -> {_RENAMED}")
        await expect(notice.get_by_role("link", name=_UNREGISTERED)).to_have_count(0)
        await expect(notice.get_by_role("link", name=_TARGET, exact=True)).to_be_visible()
        await expect(notice).to_contain_text("<b>登録結果</b>")
        await expect(notice.locator("b")).to_have_count(0)
        filename = _RENAMED
        ref = notice.get_by_role("link", name=filename, exact=True).first
        body = "採用済みの改名先"
    else:
        ref = notice.get_by_role("link")
        await expect(ref).to_be_visible()
        filename = await ref.inner_text()
        body = "単体登録の参照先"
    await _assert_middle_click(page, ref, filename)
    await ref.focus()
    await page.clock.fast_forward(6_100)
    await expect(ref).to_be_focused()
    await page.keyboard.press("Enter")
    await expect(page.locator(".wi-reference-dialog[open]")).to_contain_text(body)

from urllib.parse import parse_qs, urlencode, urlsplit

import playwright.async_api
import pytest

from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _isolate_creation_time_index,
    _screen_harness_fixture,
    _ScreenHarness,
)
from agent_toolkit._testing import wi_entry_files

pytestmark = [pytest.mark.browser]
_FILENAME = "20260102-030405-001.md"
_BODY = "参照先の作業項目本文"
_LABEL = f"検索用 <b>記録</b> {_FILENAME}"
_PREVIEW = f"<b>抜粋</b> {_FILENAME} / 20260102-030405-00…"


async def _add_session_references(page: playwright.async_api.Page) -> None:
    async def listing(route: playwright.async_api.Route) -> None:
        response = await route.fetch()
        payload = await response.json()
        for entry in payload["sessions"]:
            if entry["engine"] == "claude" and not entry.get("parent_path"):
                entry["label"] = _LABEL
        await route.fulfill(response=response, json=payload)

    async def detail(route: playwright.async_api.Route) -> None:
        response = await route.fetch()
        payload = await response.json()
        if payload.get("engine") == "claude" and payload.get("subagents"):
            payload["events"] = [
                {"kind": "tool_call", "name": "Read", "detail": {"input": {"file_path": _PREVIEW}}},
                {"kind": "tool_result", "text": _PREVIEW},
                {"kind": "injected", "text": _PREVIEW},
                {"kind": "injected", "initial_work": True, "text": f'<atk-auto file="{_FILENAME}">\n配送本文\n</atk-auto>'},
            ]
            payload["subagents"][0]["description"] = f"{_PREVIEW} question.md"
            payload["subagents"][1]["description"] = f"記録がない委譲 {_FILENAME}"
        await route.fulfill(response=response, json=payload)

    await page.route("**/api/sessions/list", listing)
    await page.route("**/api/sessions/detail?*", detail)


def _write_reference(harness: _ScreenHarness) -> None:
    wi_entry_files.entry_path(harness.root.parent, _FILENAME).write_text(
        f"---\ntype: awi\nstate: inbox\n---\n\n{_BODY}\n", encoding="utf-8"
    )


async def _open_reference(page: playwright.async_api.Page, link: playwright.async_api.Locator, *, touch: bool = False) -> None:
    expect = playwright.async_api.expect
    await expect(link).to_be_visible()
    if touch:
        await link.tap()
    else:
        await link.click()
    dialog = page.locator(".wi-reference-dialog[open]")
    await expect(dialog).to_contain_text(_BODY)
    await dialog.get_by_role("button", name="閉じる").click()
    await expect(dialog).to_have_count(0)
    assert await link.evaluate("element => !element.parentElement.closest('a, button')")


async def _open_reference_in_new_page(page: playwright.async_api.Page, link: playwright.async_api.Locator) -> None:
    original_url = page.url
    async with page.context.expect_page() as opened:
        await link.click(button="middle")
    popup = await opened.value
    try:
        await playwright.async_api.expect(popup.locator("#detail-dialog")).to_contain_text(_BODY)
        assert parse_qs(urlsplit(popup.url).query)["filename"] == [_FILENAME]
        assert page.url == original_url
        assert await page.locator(".wi-reference-dialog[open]").count() == 0
    finally:
        await popup.close()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("surface", ["label", "tool_call", "tool_result", "injected", "payload", "delegate", "missing"])
async def test_session_reference_links_keep_their_own_actions(screen_harness: _ScreenHarness, surface: str) -> None:
    _write_reference(screen_harness)
    page = screen_harness.page
    await _add_session_references(page)
    url = screen_harness.base_url + "/sessions"
    if surface != "label":
        (record,) = (screen_harness.root.parent / "claude" / "projects").glob("*/*.jsonl")
        url += "?" + urlencode({"host": "browser-test", "engine": "claude", "path": record.as_posix()})
    await page.goto(url)
    expect = playwright.async_api.expect

    if surface == "label":
        await page.locator("#sessions-filter").fill("検索用")
        await expect(page.locator('.session-item[data-engine="codex"]')).to_have_count(0)
        row = page.locator(".session-tree-row", has_text=_LABEL)
        assert await row.locator("b").count() == 0
        link = row.get_by_role("link", name=_FILENAME, exact=True)
        original_url = page.url
        await _open_reference(page, link)
        await _open_reference_in_new_page(page, link)
        assert page.url == original_url
        return

    if surface in {"tool_call", "tool_result", "injected"}:
        block = page.locator(f"#detail > .kind-{surface}:not(:has(.event-payload))")
        summary = block.locator(":scope > summary")
        await expect(summary).to_contain_text(_PREVIEW)
        assert await summary.locator("b").count() == 0
        link = summary.get_by_role("link", name=_FILENAME, exact=True)
        await expect(summary.get_by_role("link", name="20260102-030405-00…")).to_have_count(0)
        assert not await block.evaluate("element => element.open")
        await _open_reference(page, link)
        await _open_reference_in_new_page(page, link)
        assert not await block.evaluate("element => element.open")
        await summary.get_by_text("時刻なし").click()
        await expect(block).to_have_attribute("open", "")
        return

    if surface == "payload":
        payload = page.locator("#detail .event-payload")
        await payload.locator("summary").click()
        source = payload.locator("pre").get_by_role("link", name=_FILENAME, exact=True)
        await _open_reference(page, source)
        await _open_reference_in_new_page(page, source)
        await expect(payload).to_have_attribute("open", "")
        return

    delegates = page.locator("#detail .subagents")
    await delegates.locator(":scope > summary").click()
    rows = delegates.locator(".subagent-reference-row")
    await expect(rows).to_have_count(2)
    await expect(rows.first.get_by_role("link", name="question.md", exact=True)).to_be_visible()
    row = rows.first if surface == "delegate" else rows.last
    description = row.get_by_role("link", name=_FILENAME, exact=True)
    await _open_reference(page, description)
    await _open_reference_in_new_page(page, description)
    assert await page.locator(".detail-back").count() == 0
    if surface == "missing":
        await expect(row.get_by_role("button")).to_be_disabled()
    else:
        await row.get_by_role("button").click()
        await expect(page.locator(".detail-back")).to_be_visible()
        await expect(page.locator("#detail")).to_contain_text("サブエージェントの発話")


@pytest.mark.asyncio(loop_scope="session")
async def test_session_reference_links_are_independent_on_touch(screen_harness: _ScreenHarness) -> None:
    _write_reference(screen_harness)
    browser = screen_harness.page.context.browser
    assert browser is not None
    touch = await browser.new_context(has_touch=True, is_mobile=True, viewport={"width": 412, "height": 915})
    try:
        page = await touch.new_page()
        await _add_session_references(page)
        await page.goto(screen_harness.base_url + "/sessions")
        summary = page.locator("#sessions .session-tree-row", has_text=_LABEL)
        await _open_reference(page, summary.get_by_role("link", name=_FILENAME, exact=True), touch=True)
        await summary.get_by_role("button", name=f"セッションを開く: {_LABEL}").tap()
        block = page.locator("#detail .kind-tool_call")
        heading = block.locator(":scope > summary")
        await _open_reference(page, heading.get_by_role("link", name=_FILENAME, exact=True), touch=True)
        assert not await block.evaluate("element => element.open")
        await heading.get_by_text("時刻なし").tap()
        await playwright.async_api.expect(block).to_have_attribute("open", "")
    finally:
        await touch.close()

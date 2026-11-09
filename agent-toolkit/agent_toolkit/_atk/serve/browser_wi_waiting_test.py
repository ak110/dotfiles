"""判断待ちの連続確認を実ブラウザーとWI APIで検証する。"""

import pathlib

import playwright.async_api
import pytest

# pytestの引数で参照する共有fixtureを登録する。
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _browser_harness_fixture,
    _BrowserHarness,
)
from agent_toolkit._testing import wi_entry_files

pytestmark = [pytest.mark.browser]


def _question(root: pathlib.Path, filename: str, *, important: bool = False, repo: str = "a") -> pathlib.Path:
    path = wi_entry_files.entry_path(root, filename)
    path.write_text(
        f"---\ntype: uwi\nstate: hold\ntarget_repo: github.com/example/{repo}\n"
        f"important: {str(important).lower()}\nquestion_type: yes-no\n---\n\n"
        "## 質問\n\nこの方針で進めてよいか？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    return path


@pytest.mark.asyncio(loop_scope="session")
async def test_waiting_keyboard_save_skip_page_boundary_and_last_item(browser_harness: _BrowserHarness) -> None:
    """重要順の確認からページ横断・明示保存・最後の見送りまで標準キーで操作できる。"""
    harness = browser_harness
    names = [f"20200101-000000-{index:03d}.md" for index in range(101)]
    for index, name in enumerate(names):
        _question(harness.root, name, repo="a" if index % 2 == 0 else "b")
    important = _question(harness.root, "20210101-000000-001.md", important=True, repo="b")
    harness.operations.enable_file_mutations()
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.get_by_role("radio", name="判断待ち", exact=True).check()
    await playwright.async_api.expect(page.locator(".entry-select").first).to_have_attribute("data-key", important.name)
    await page.locator(".entry-select").first.focus()
    await page.keyboard.press("Enter")
    await playwright.async_api.expect(page.locator("#detail-filename")).to_have_text(important.name)
    await page.get_by_role("button", name="はい", exact=True).click()
    assert harness.operations.answer_calls == 0
    await page.get_by_role("button", name="保存して次へ", exact=True).focus()
    await page.keyboard.press("Enter")
    await playwright.async_api.expect(page.locator("#detail-filename")).to_have_text(names[0])
    assert harness.operations.answer_calls == 1
    assert important.read_text(encoding="utf-8").endswith("はい\n")
    await page.keyboard.press("Escape")
    await page.locator(".entry-select").last.click()
    await playwright.async_api.expect(page.locator("#detail-filename")).to_have_text(names[99])
    await page.get_by_role("button", name="保存せず次へ", exact=True).click()
    await playwright.async_api.expect(page.locator("#detail-filename")).to_have_text(names[100])
    await playwright.async_api.expect(page.locator("#waiting-progress")).to_contain_text("101 / 102")
    # fixtureの既存UWIも判断待ち集合へ含まれるため、repo限定で最後の1件を観測する。
    await page.keyboard.press("Escape")
    await page.get_by_label("判断待ちのtarget-repo", exact=True).select_option("github.com/example/b")
    await playwright.async_api.expect(page.locator(".entry-select")).to_have_count(50)
    await page.locator(".entry-select").last.click()
    await page.get_by_role("button", name="保存せず次へ", exact=True).focus()
    await page.keyboard.press("Enter")
    await playwright.async_api.expect(page.get_by_role("dialog", name="詳細", exact=True)).not_to_be_visible()
    assert harness.operations.answer_calls == 1
    await page.get_by_role("radio", name="通常表示", exact=True).check()
    await playwright.async_api.expect(page.locator("#wi-effective-conditions")).to_contain_text("直近2週間")


@pytest.mark.asyncio(loop_scope="session")
async def test_waiting_save_failure_and_external_change_keep_current_input(browser_harness: _BrowserHarness) -> None:
    """保存409・外部編集・消失後も回答を別項目へ送らず見送りで再開できる。"""
    harness = browser_harness
    first = _question(harness.root, "20200101-000000-001.md", important=True)
    second = _question(harness.root, "20200101-000000-002.md")
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.get_by_role("radio", name="判断待ち", exact=True).check()
    await page.locator(".entry-select").first.click()
    await page.get_by_label("UWIへの回答（必須）", exact=True).fill("入力中の回答")

    async def fail_save(route: playwright.async_api.Route) -> None:
        await route.fulfill(status=409, json={"code": "edit_conflict", "error": "外部更新"})

    await page.route("**/api/entries/answer", fail_save)
    await page.get_by_role("button", name="保存して次へ", exact=True).click()
    await playwright.async_api.expect(page.locator("#detail-alert")).to_contain_text("外部で更新")
    await playwright.async_api.expect(page.locator("#detail-filename")).to_have_text(first.name)
    await playwright.async_api.expect(page.locator("#answer-input")).to_have_value("入力中の回答")
    await page.unroute("**/api/entries/answer", fail_save)
    first.unlink()
    harness.current_state.publish("changed")
    await playwright.async_api.expect(page.locator("#detail-alert")).to_contain_text("削除されました")
    await playwright.async_api.expect(page.locator("#answer-input")).to_have_value("入力中の回答")
    page.once("dialog", lambda dialog: dialog.accept())
    await page.get_by_role("button", name="保存せず次へ", exact=True).click()
    await playwright.async_api.expect(page.locator("#detail-filename")).to_have_text(second.name)
    await playwright.async_api.expect(page.locator("#answer-input")).to_have_value("")
    assert harness.operations.answer_calls == 0

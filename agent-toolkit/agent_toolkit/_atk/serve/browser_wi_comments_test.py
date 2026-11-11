"""serveのbrowser_wiに関するcommentsの試験。"""

import asyncio

import playwright.async_api
import pytest

from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _browser_harness_fixture,
    _BrowserHarness,
    _screen_harness_fixture,
)
from agent_toolkit._testing.serve_browser_wi_support import (
    _entry_path,
    _release_save_when_waiting,
    _set_entry_state,
    _wait_and_close_operation_notice,
)

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
async def test_user_comment_ui_empty_save_removes_section(browser_harness: _BrowserHarness) -> None:
    """コメント欄を空にして保存すると節が消え、節が無い状態の空保存も成功として伝える。"""
    harness = browser_harness
    page = harness.page
    path = _entry_path(harness.root, "awi.md")
    kept = "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\nsource: session-review\n---\n\n通常本文\n"
    path.write_text(kept + "\n## ユーザーコメント\n\n削除対象のコメント\n", encoding="utf-8")
    await page.goto(harness.base_url + "/")
    detail = page.get_by_role("dialog", name="詳細")

    await page.locator('.entry-select[data-key="awi.md"]').click()
    await detail.wait_for(state="visible")
    await playwright.async_api.expect(detail).to_contain_text("削除対象のコメント")
    await detail.get_by_role("button", name="ユーザーコメント", exact=True).click()
    comment_input = detail.locator("#user-comment-input")
    await playwright.async_api.expect(comment_input).to_have_value("削除対象のコメント")
    await playwright.async_api.expect(detail.locator("#user-comment-input-hint")).to_be_visible()
    await comment_input.fill("")
    await detail.get_by_role("button", name="コメントを保存").click()
    await _wait_and_close_operation_notice(page, "ユーザーコメントを削除しました")
    await playwright.async_api.expect(detail).to_be_hidden()
    assert path.read_text(encoding="utf-8") == kept

    await page.locator('.entry-select[data-key="awi.md"]').click()
    await detail.wait_for(state="visible")
    await playwright.async_api.expect(detail).not_to_contain_text("削除対象のコメント")
    await detail.get_by_role("button", name="ユーザーコメント", exact=True).click()
    await playwright.async_api.expect(comment_input).to_have_value("")
    await detail.get_by_role("button", name="コメントを保存").click()
    await _wait_and_close_operation_notice(page, "ユーザーコメントが無いため、変更していません")
    assert path.read_text(encoding="utf-8") == kept


@pytest.mark.asyncio(loop_scope="session")
async def test_user_comment_ui_appends_replaces_and_recovers_from_external_updates(
    browser_harness: _BrowserHarness,
) -> None:
    """エージェント由来UIの追記・置換・pending・SSE・競合復旧を実ブラウザーで検証する。"""
    harness = browser_harness
    page = harness.page
    path = _entry_path(harness.root, "awi.md")
    original = "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\nsource: session-review\n---\n\n通常本文\n"
    path.write_text(original, encoding="utf-8")
    await page.goto(harness.base_url + "/")
    detail = page.get_by_role("dialog", name="詳細")

    await page.locator('.entry-select[data-key="empty.md"]').click()
    await playwright.async_api.expect(detail).to_be_visible()
    await playwright.async_api.expect(detail.locator("#user-comment-button")).to_be_visible()
    await page.keyboard.press("Escape")
    await playwright.async_api.expect(detail).to_be_hidden()

    await page.locator('.entry-select[data-key="awi.md"]').click()
    comment_button = detail.get_by_role("button", name="ユーザーコメント", exact=True)
    await playwright.async_api.expect(comment_button).to_be_visible()
    await comment_button.click()
    comment_input = detail.locator("#user-comment-input")
    await playwright.async_api.expect(comment_input).to_be_focused()
    await playwright.async_api.expect(comment_input).to_have_value("")
    await page.set_viewport_size({"width": 390, "height": 700})
    input_box = await comment_input.bounding_box()
    assert input_box is not None
    assert input_box["x"] >= 0
    assert input_box["x"] + input_box["width"] <= 390

    await comment_input.fill("最初のコメント")
    async with page.expect_request("**/api/entries/user-comment") as first_request_info:
        await detail.get_by_role("button", name="コメントを保存").click()
    first_payload = (await first_request_info.value).post_data_json
    assert first_payload == {
        "state": "inbox",
        "filename": "awi.md",
        "comment": "最初のコメント",
        "expected_content": original,
    }
    await _wait_and_close_operation_notice(page, "ユーザーコメントを保存しました")
    assert "## ユーザーコメント\n\n最初のコメント" in path.read_text(encoding="utf-8")
    await playwright.async_api.expect(detail).to_be_hidden()

    await page.locator('.entry-select[data-key="awi.md"]').click()
    await detail.wait_for(state="visible")
    comment_button = detail.get_by_role("button", name="ユーザーコメント", exact=True)
    await comment_button.click()
    await playwright.async_api.expect(comment_input).to_have_value("最初のコメント")
    await comment_input.fill("置換後のコメント")
    harness.operations.arm_user_comment_delay()
    await page.evaluate(
        "document.getElementById('save-user-comment-button').click(); "
        "document.getElementById('save-user-comment-button').click()"
    )
    assert await asyncio.to_thread(harness.operations.user_comment_started.wait, 5)
    await playwright.async_api.expect(comment_input).to_be_disabled()
    await playwright.async_api.expect(detail.locator("#save-user-comment-button")).to_be_disabled()
    assert harness.operations.user_comment_calls == 2
    await _release_save_when_waiting(page, harness.operations.user_comment_release)
    await _wait_and_close_operation_notice(page, "ユーザーコメントを保存しました")
    await playwright.async_api.expect(detail).to_be_hidden()
    saved = path.read_text(encoding="utf-8")
    assert "置換後のコメント" in saved
    assert "最初のコメント" not in saved

    await page.locator('.entry-select[data-key="awi.md"]').click()
    await detail.wait_for(state="visible")
    comment_button = detail.get_by_role("button", name="ユーザーコメント", exact=True)
    await comment_button.click()
    await comment_input.fill("SSE中も保持する入力")
    path.write_text(saved.replace("通常本文", "SSE外部更新本文"), encoding="utf-8")
    harness.current_state.publish()
    await detail.get_by_role("alert").filter(has_text="最新内容を再取得しました").wait_for(state="visible")
    await playwright.async_api.expect(comment_input).to_have_value("SSE中も保持する入力")
    await playwright.async_api.expect(comment_input).to_be_focused()
    await playwright.async_api.expect(detail.locator("#save-user-comment-button")).to_be_enabled()

    latest = path.read_text(encoding="utf-8").replace("SSE外部更新本文", "競合後の最新本文")
    path.write_text(latest, encoding="utf-8")
    await comment_input.fill("競合後も保持する入力")
    await detail.get_by_role("button", name="コメントを保存").click()
    await detail.get_by_role("alert").filter(has_text="内容を確認して再度保存してください").wait_for(state="visible")
    await playwright.async_api.expect(comment_input).to_have_value("競合後も保持する入力")
    await playwright.async_api.expect(comment_input).to_be_focused()
    harness.operations.arm_user_comment_delay()
    async with page.expect_request("**/api/entries/user-comment") as retry_request_info:
        await detail.get_by_role("button", name="コメントを保存").click()
    retry_payload = (await retry_request_info.value).post_data_json
    assert isinstance(retry_payload, dict)
    assert retry_payload["expected_content"] == latest
    assert await asyncio.to_thread(harness.operations.user_comment_started.wait, 5)
    await _release_save_when_waiting(page, harness.operations.user_comment_release)
    await _wait_and_close_operation_notice(page, "ユーザーコメントを保存しました")
    await playwright.async_api.expect(detail).to_be_hidden()
    assert "競合後も保持する入力" in path.read_text(encoding="utf-8")
    assert harness.operations.user_comment_calls == 4


@pytest.mark.asyncio(loop_scope="session")
async def test_user_comment_ui_keeps_input_when_sse_moves_entry_to_processing(
    browser_harness: _BrowserHarness,
) -> None:
    """processingへのSSE移動後も入力へ到達でき、保存だけを無効にする。"""
    harness = browser_harness
    page = harness.page
    path = _entry_path(harness.root, "awi.md")
    path.write_text(
        "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\nsource: session-review\n---\n\n通常本文\n",
        encoding="utf-8",
    )
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    detail = page.get_by_role("dialog", name="詳細")
    await page.locator('.entry-select[data-key="awi.md"]').click()
    await detail.get_by_role("button", name="ユーザーコメント", exact=True).click()
    comment_input = detail.locator("#user-comment-input")
    await comment_input.fill("processing移動後も保持する入力")

    _set_entry_state(path, "processing")
    harness.current_state.publish()

    await detail.get_by_role("alert").filter(has_text="processingへ移動したため").wait_for(state="visible")
    await playwright.async_api.expect(detail).to_be_visible()
    await playwright.async_api.expect(comment_input).to_have_value("processing移動後も保持する入力")
    await playwright.async_api.expect(comment_input).to_be_focused()
    await playwright.async_api.expect(detail.locator("#save-user-comment-button")).to_be_disabled()

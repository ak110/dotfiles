"""serveのbrowser_wiに関するreconciliationの試験。"""

import asyncio

import playwright.async_api
import pytest

from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _browser_harness_fixture,
    _BrowserHarness,
    _screen_harness_fixture,
)
from agent_toolkit._testing.serve_browser_wi_support import _entry_path, _set_entry_state

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
async def test_sse_refreshes_open_detail_preserves_input_and_closes_missing_entry(
    browser_harness: _BrowserHarness,
) -> None:
    """SSE更新時の詳細再取得、入力保持、消失時の閉鎖と復帰先を検証する。"""
    harness = browser_harness
    page = harness.page
    awi_path = _entry_path(harness.root, "awi.md")
    await page.goto(harness.base_url + "/")
    awi_row = page.locator('#entry-list .entry-select[data-kind="awi"]').filter(has_text="awi.md")
    await awi_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.wait_for(state="visible")

    awi_path.write_text(
        "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\nsource: browser\n---\n\n外部更新後の本文\n",
        encoding="utf-8",
    )
    harness.current_state.publish()
    await playwright.async_api.expect(detail.locator("#detail-content")).to_contain_text("外部更新後の本文")

    await detail.get_by_role("button", name="編集", exact=True).click()
    edit_input = detail.locator("#edit-content")
    await edit_input.fill("ユーザーの未保存本文")
    awi_path.write_text(
        "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\nsource: browser\n---\n\n編集中の外部更新\n",
        encoding="utf-8",
    )
    harness.current_state.publish()
    await detail.get_by_role("alert").filter(has_text="外部で項目が更新されました").wait_for(state="visible")
    assert await edit_input.input_value() == "ユーザーの未保存本文"
    await playwright.async_api.expect(detail.get_by_role("button", name="保存", exact=True)).to_be_disabled()

    awi_path.unlink()
    harness.current_state.publish()
    await detail.wait_for(state="hidden")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select").first).to_be_focused()


@pytest.mark.asyncio(loop_scope="session")
async def test_sse_reconciliation_preserves_identity_and_owned_dialogs(
    browser_harness: _BrowserHarness,
) -> None:
    """状態が異なる項目の識別、状態変更、削除確認中の対象消失を実ブラウザーで扱う。"""
    harness = browser_harness
    page = harness.page
    inbox_same = _entry_path(harness.root, "same.md")
    processing_same = _entry_path(harness.root, "processing-same.md")
    moving = _entry_path(harness.root, "moving.md")
    inbox_same.write_text("---\ntype: awi\nstate: inbox\n---\n\n未処理の同名本文\n", encoding="utf-8")
    processing_same.write_text("---\ntype: awi\nstate: processing\n---\n\n処理中の同名本文\n", encoding="utf-8")
    moving.write_text("---\ntype: awi\nstate: inbox\n---\n\n移動対象\n", encoding="utf-8")
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await playwright.async_api.expect(page.locator("#connection-status")).to_be_hidden()

    processing_row = page.locator('.entry-select[data-key="processing-same.md"]')
    await processing_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("awi / processing")
    await playwright.async_api.expect(detail.locator("#detail-content")).to_contain_text("処理中の同名本文")
    harness.current_state.publish()
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("awi / processing")
    await playwright.async_api.expect(detail.locator("#detail-content")).to_contain_text("処理中の同名本文")

    await detail.get_by_role("button", name="編集", exact=True).click()
    edit_input = detail.locator("#edit-content")
    await edit_input.fill((await edit_input.input_value()) + "\n処理中だけを保存")
    async with page.expect_request(
        lambda request: request.method == "PUT" and request.url.endswith("/api/entries/processing-same.md")
    ):
        await detail.get_by_role("button", name="保存", exact=True).click()
    await page.get_by_role("status").filter(has_text="保存しました").wait_for(state="visible")
    await detail.wait_for(state="hidden")

    await processing_row.click()
    await detail.wait_for(state="visible")
    _set_entry_state(processing_same, "adopted")
    harness.current_state.publish()
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("awi / adopted")
    await playwright.async_api.expect(detail.locator("#detail-filename")).to_have_text("processing-same.md")
    await page.keyboard.press("Escape")

    moving_row = page.locator('.entry-select[data-key="moving.md"]')
    await moving_row.click()
    await detail.get_by_role("button", name="削除").click()
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    _set_entry_state(moving, "processing")
    harness.current_state.publish()
    await delete_dialog.wait_for(state="hidden")
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("awi / processing")
    await playwright.async_api.expect(detail.locator("#detail-dialog-body")).to_be_focused()

    await detail.get_by_role("button", name="削除").click()
    await playwright.async_api.expect(delete_dialog.locator("#force-delete-row")).to_be_visible()
    moving.unlink()
    harness.current_state.publish()
    await delete_dialog.wait_for(state="hidden")
    await detail.wait_for(state="hidden")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select").first).to_be_focused()


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_and_sse_completion_orders_close_owned_dialogs_once(
    browser_harness: _BrowserHarness,
) -> None:
    """削除応答とSSEの到着順にかかわらず、詳細と削除確認のダイアログを閉じて一覧へ戻す。

    応答より先に届いた更新通知は自分の削除によるものを含むため、応答まで詳細の再読込を保留し、
    応答の後に両方のダイアログを閉じて削除の成功を通知する。
    """
    harness = browser_harness
    page = harness.page
    for filename in ("sse-first.md", "response-first.md"):
        (_entry_path(harness.root, filename)).write_text(
            "---\ntype: awi\nstate: inbox\n---\n\n削除順序の検証\n",
            encoding="utf-8",
        )
    harness.operations.enable_file_mutations()
    await page.goto(harness.base_url + "/")

    detail = page.get_by_role("dialog", name="詳細")
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    sse_first = page.locator('.entry-select[data-key="sse-first.md"]')
    await sse_first.click()
    await detail.get_by_role("button", name="削除").click()
    harness.operations.arm_remove_delay()
    await delete_dialog.get_by_role("button", name="削除する").click()
    assert await asyncio.to_thread(harness.operations.remove_started.wait, 5)
    async with page.expect_response(lambda response: "/api/entries?type=" in response.url):
        harness.current_state.publish()
    await page.wait_for_timeout(300)
    harness.operations.remove_release.set()
    await delete_dialog.wait_for(state="hidden")
    await detail.wait_for(state="hidden")
    await page.get_by_role("status").filter(has_text="削除しました").wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select").first).to_be_focused()

    response_first = page.locator('.entry-select[data-key="response-first.md"]')
    await response_first.click()
    await detail.get_by_role("button", name="削除").click()
    await delete_dialog.get_by_role("button", name="削除する").click()
    await delete_dialog.wait_for(state="hidden")
    await detail.wait_for(state="hidden")
    harness.current_state.publish()
    await playwright.async_api.expect(page.locator("#entry-list .entry-select").first).to_be_focused()


@pytest.mark.asyncio(loop_scope="session")
async def test_external_update_recovery_survives_save_and_answer_failures(
    browser_harness: _BrowserHarness,
) -> None:
    """送信中に外部更新の通知が届いて更新APIが失敗した場合は、保留した再読込を実行し、入力と復旧手順を維持する。

    送信中は詳細の再読込を保留するため、警告は失敗の応答の後に、失敗の文と復旧手順として表示する。
    """
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    detail = page.get_by_role("dialog", name="詳細")

    async def exercise(
        filename: str,
        mutation_pattern: str,
        submit_name: str,
        input_selector: str,
        updated_content: str,
    ) -> None:
        await page.locator(f'.entry-select[data-key="{filename}"]').click()
        if input_selector != "#answer-input":
            await detail.get_by_role("button", name="編集", exact=True).click()
        field = detail.locator(input_selector)
        user_input = "保持する回答" if input_selector == "#answer-input" else "保持する編集本文"
        await field.fill(user_input)
        started = asyncio.Event()
        release = asyncio.Event()

        async def fail_mutation(route: playwright.async_api.Route) -> None:
            if route.request.method not in {"POST", "PUT"}:
                await route.continue_()
                return
            started.set()
            await release.wait()
            await route.fulfill(
                status=500,
                content_type="application/json",
                body='{"error":"Git同期に失敗しました"}',
            )

        await page.route(mutation_pattern, fail_mutation)
        await detail.get_by_role("button", name=submit_name, exact=True).click()
        await asyncio.wait_for(started.wait(), timeout=5)
        (_entry_path(harness.root, filename)).write_text(updated_content, encoding="utf-8")
        # 更新通知による一覧の再取得が終わり、詳細の再読込が保留へ回るまで応答を返さない。
        async with page.expect_response(lambda response: "/api/entries?type=" in response.url):
            harness.current_state.publish()
        await page.wait_for_timeout(300)
        release.set()
        await detail.get_by_role("alert").filter(has_text="Git同期に失敗しました").wait_for(state="visible")
        assert "詳細を閉じて開き直してから保存してください" in await detail.locator("#detail-alert").inner_text()
        await playwright.async_api.expect(page.locator("#operation-notice")).to_be_hidden()
        assert await field.input_value() == user_input
        await playwright.async_api.expect(detail.get_by_role("button", name=submit_name, exact=True)).to_be_disabled()
        await page.unroute(mutation_pattern, fail_mutation)
        async with page.expect_event("dialog") as confirmation:
            press = asyncio.create_task(page.keyboard.press("Escape"))
        await (await confirmation.value).accept()
        await press

    await exercise(
        "awi.md",
        "**/api/entries/awi.md",
        "保存",
        "#edit-content",
        "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\n---\n\n保存中の外部更新\n",
    )
    await exercise(
        "question.md",
        "**/api/entries/answer",
        "回答を保存",
        "#answer-input",
        "---\ntype: uwi\nstate: inbox\ntarget_repo: example/repo\nquestion_type: free-form\n---\n\n"
        "## 質問\n\n回答中の外部更新ですか？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n",
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_confirmation_closes_on_detail_failure_and_content_conflict(
    browser_harness: _BrowserHarness,
) -> None:
    """一覧の更新後に詳細の再取得が失敗した場合と、削除確認の後に内容が競合した場合は、古い削除確認を閉じる。"""
    harness = browser_harness
    page = harness.page
    harness.operations.enable_file_mutations()
    failure_path = _entry_path(harness.root, "dialog-failure.md")
    conflict_path = _entry_path(harness.root, "delete-conflict.md")
    original = "---\ntype: awi\nstate: inbox\ntarget_repo: example/old\n---\n\n変更前の要約\n"
    failure_path.write_text(original, encoding="utf-8")
    conflict_path.write_text(original, encoding="utf-8")
    await page.goto(harness.base_url + "/")
    detail = page.get_by_role("dialog", name="詳細")
    delete_dialog = page.get_by_role("dialog", name="削除の確認")

    await page.locator('.entry-select[data-key="dialog-failure.md"]').click()
    await detail.get_by_role("button", name="削除").click()

    async def fail_detail(route: playwright.async_api.Route) -> None:
        await route.fulfill(
            status=500,
            content_type="application/json",
            body='{"error":"詳細取得に失敗しました"}',
        )

    await page.route("**/api/entries/dialog-failure.md", fail_detail)
    failure_path.write_text(
        "---\ntype: awi\nstate: inbox\ntarget_repo: example/new\n---\n\n変更後の要約\n",
        encoding="utf-8",
    )
    harness.current_state.publish()
    await delete_dialog.wait_for(state="hidden")
    await detail.get_by_role("alert").filter(has_text="詳細取得に失敗しました").wait_for(state="visible")
    assert "削除操作をやり直してください" in await detail.locator("#detail-alert").inner_text()
    await playwright.async_api.expect(detail.locator("#detail-dialog-body")).to_be_focused()
    await page.unroute("**/api/entries/dialog-failure.md", fail_detail)
    await page.keyboard.press("Escape")

    await page.locator('.entry-select[data-key="delete-conflict.md"]').click()
    await detail.get_by_role("button", name="削除").click()
    conflict_path.write_text(original.replace("変更前", "確認後の外部更新"), encoding="utf-8")
    await delete_dialog.get_by_role("button", name="削除する").click()
    await delete_dialog.wait_for(state="hidden")
    await detail.get_by_role("alert").filter(has_text="削除できませんでした").wait_for(state="visible")
    assert "詳細を閉じて開き直してから削除してください" in await detail.locator("#detail-alert").inner_text()
    await playwright.async_api.expect(detail.locator("#detail-dialog-body")).to_be_focused()
    await playwright.async_api.expect(detail.get_by_role("button", name="削除")).to_be_disabled()
    await playwright.async_api.expect(detail.get_by_role("button", name="編集")).to_be_disabled()
    assert conflict_path.exists()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("external_change", ["delete", "move"])
async def test_selecting_deleted_entry_refreshes_list_without_error(
    browser_harness: _BrowserHarness,
    external_change: str,
) -> None:
    """一覧表示後に外部で削除・移動された行を選んでも、エラーを表示せず最新状態を示して一覧を更新する。"""
    harness = browser_harness
    page = harness.page
    path = _entry_path(harness.root, "stale.md")
    path.write_text("---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\n---\n\n外部で変わる本文\n", encoding="utf-8")
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    stale_row = page.locator('.entry-select[data-key="stale.md"]')
    await stale_row.wait_for(state="visible")

    # SSEの変更通知を発行せず、一覧が古いままの状態で行を選ぶ。
    if external_change == "delete":
        path.unlink()
    else:
        _set_entry_state(path, "processing")
    await stale_row.click()

    notice = page.locator("#operation-notice")
    detail = page.get_by_role("dialog", name="詳細")
    if external_change == "delete":
        await playwright.async_api.expect(page.locator("#operation-notice-message")).to_have_text(
            "stale.mdは削除されたため表示できません。一覧を更新しました。"
        )
        await playwright.async_api.expect(notice).to_have_attribute("data-error", "false")
        await playwright.async_api.expect(detail).to_be_hidden()
        await playwright.async_api.expect(page.locator('.entry-select[data-key="stale.md"]')).to_have_count(0)
    else:
        await playwright.async_api.expect(detail).to_be_visible()
        await playwright.async_api.expect(detail.locator("#detail-state")).to_have_attribute("data-state", "processing")
        await playwright.async_api.expect(notice).to_be_hidden()
        await playwright.async_api.expect(page.locator('.entry-select[data-key="stale.md"] .state-badge')).to_have_text(
            "processing"
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_work_item_focus_recovers_change_without_sse_event(browser_harness: _BrowserHarness) -> None:
    """SSE通知を受信できなかった場合も、フォーカス復帰時に一覧を再取得する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator('.entry-select[data-key="awi.md"]').wait_for(state="visible")
    (_entry_path(harness.root, "missed.md")).write_text(
        "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\n---\n\n切断中の変更\n", encoding="utf-8"
    )
    await page.evaluate("window.dispatchEvent(new Event('focus'))")
    await page.locator('.entry-select[data-key="missed.md"]').wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#connection-status")).to_be_hidden()

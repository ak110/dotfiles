"""serveのbrowser_wiに関するerrorsの試験。"""

import asyncio
import subprocess

import playwright.async_api
import pytest

from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _browser_harness_fixture,
    _BrowserHarness,
    _open_filters,
    _screen_harness_fixture,
    _ScreenHarness,
)
from agent_toolkit._testing.serve_browser_wi_support import _entry_path

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
async def test_global_error_closed_during_sync_restores_refresh_focus(
    browser_harness: _BrowserHarness,
) -> None:
    """同期進行中に共通エラーを閉じても、同期完了時に同期ボタンへフォーカスが戻る。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    refresh_button = page.locator("#refresh-button")
    close_button = page.get_by_role("button", name="操作通知を閉じる")

    sync_started = asyncio.Event()
    release_sync = asyncio.Event()

    async def delay_sync(route: playwright.async_api.Route) -> None:
        response = await route.fetch()
        sync_started.set()
        await release_sync.wait()
        await route.fulfill(response=response)

    async def fail_entries(route: playwright.async_api.Route) -> None:
        await route.fulfill(
            status=500,
            content_type="application/json",
            body='{"error":"一覧取得失敗"}',
        )

    await page.route("**/api/sync", delay_sync)
    await refresh_button.click()
    await asyncio.wait_for(sync_started.wait(), timeout=5)
    await playwright.async_api.expect(refresh_button).to_be_disabled()

    await page.route("**/api/entries?*", fail_entries)
    # 種別フィルター変更時の処理を、値を変えずに起動する。
    await page.locator("#kind-filter").dispatch_event("change")
    await playwright.async_api.expect(page.locator("#operation-notice")).to_be_visible()
    await page.unroute("**/api/entries?*", fail_entries)

    await close_button.focus()
    await close_button.click()
    await playwright.async_api.expect(page.locator("#operation-notice")).to_be_hidden()
    await playwright.async_api.expect(refresh_button).to_be_disabled()

    release_sync.set()
    await playwright.async_api.expect(refresh_button).to_be_enabled()
    await playwright.async_api.expect(refresh_button).to_be_focused()
    await page.unroute("**/api/sync", delay_sync)


@pytest.mark.asyncio(loop_scope="session")
async def test_manual_sync_git_failure_shows_check_and_retry_actions(
    browser_harness: _BrowserHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「今すぐ同期」が一般的なGitコマンドの失敗で終わっても、ローカルの一覧を保ち、確認と再試行の操作を表示する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator('.entry-select[data-key="awi.md"]').wait_for(state="visible")

    def fail_sync() -> bool:
        raise subprocess.CalledProcessError(1, ["git", "pull"])

    monkeypatch.setattr(harness.operations, "sync", fail_sync)
    await page.get_by_role("button", name="今すぐ同期", exact=True).click()

    message = page.get_by_role("alert").filter(has_text="ローカル内容を表示中です")
    await playwright.async_api.expect(message).to_be_visible()
    await playwright.async_api.expect(message).to_contain_text("Git同期に失敗しました。ローカル内容を表示中です。")
    await playwright.async_api.expect(message).to_contain_text("次の操作: ")
    await playwright.async_api.expect(message).to_contain_text(str(harness.root.resolve()))
    await playwright.async_api.expect(message).to_contain_text("status`を実行して保存リポジトリの状態を確認し")
    await playwright.async_api.expect(message).to_contain_text("「今すぐ同期」で再試行する")
    await playwright.async_api.expect(page.locator('.entry-select[data-key="awi.md"]')).to_be_visible()


@pytest.mark.asyncio(loop_scope="session")
async def test_invalid_state_warning_shows_saved_path_and_fix_action(browser_harness: _BrowserHarness) -> None:
    """不正なstateで一覧から除外した項目の警告は、年月の保存先とstateの修正操作を表示する。"""
    harness = browser_harness
    path = _entry_path(harness.root, "bad-state.md")
    path.write_text("---\nstate: bogus\ntype: awi\ntarget_repo: example/repo\n---\n\n本文\n", encoding="utf-8")
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")

    # 既存fixtureの`invalid.md`と合わせて2件を、折り畳んだ見出しの件数で示す。
    summary = page.get_by_role("alert").locator("summary")
    await playwright.async_api.expect(summary).to_contain_text("一覧から除外したファイルの警告: 2件")
    warning = page.locator("#list-warning > div").filter(has_text="bad-state.md")
    await playwright.async_api.expect(warning).to_be_hidden()
    await summary.click()
    await playwright.async_api.expect(warning).to_be_visible()
    await playwright.async_api.expect(warning).to_contain_text(str(path), use_inner_text=True)
    await playwright.async_api.expect(warning).to_contain_text("stateが不正", use_inner_text=True)
    await playwright.async_api.expect(warning).to_contain_text("次の操作: ", use_inner_text=True)
    await playwright.async_api.expect(warning).to_contain_text("のいずれかへ修正する", use_inner_text=True)
    await playwright.async_api.expect(page.locator('.entry-select[data-key="bad-state.md"]')).to_have_count(0)

    # 更新通知による一覧の再描画でも、開いた状態と全文の表示を保つ。
    async with page.expect_response(lambda response: "/api/entries?" in response.url):
        harness.current_state.publish()
    await playwright.async_api.expect(page.locator("#list-warning-details")).to_have_attribute("open", "")
    await playwright.async_api.expect(warning).to_be_visible()
    await summary.click()
    await playwright.async_api.expect(warning).to_be_hidden()
    async with page.expect_response(lambda response: "/api/entries?" in response.url):
        harness.current_state.publish()
    await playwright.async_api.expect(page.locator("#list-warning-details")).not_to_have_attribute("open", "")
    await playwright.async_api.expect(summary).to_contain_text("2件")


@pytest.mark.asyncio(loop_scope="session")
async def test_background_sync_failure_offers_retry_in_browser(browser_harness: _BrowserHarness) -> None:
    """定期同期の失敗を通知し、次の成功時に案内を閉じる。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator('.entry-select[data-key="awi.md"]').wait_for(state="visible")
    harness.current_state.publish("sync-error")
    status = page.locator("#connection-status")
    await playwright.async_api.expect(status).to_contain_text("今すぐ同期")
    await playwright.async_api.expect(status).to_be_visible()
    harness.current_state.publish("sync-ok")
    await playwright.async_api.expect(status).to_be_hidden()


@pytest.mark.asyncio(loop_scope="session")
async def test_work_item_warnings_identify_storage_state_and_file(screen_harness: _ScreenHarness) -> None:
    """読取不能ファイルの警告をファイル名と保存元で識別する。"""
    harness = screen_harness
    page = harness.page
    notes_root = harness.root.parent
    (_entry_path(notes_root, "invalid-additional.md")).write_bytes(b"\xff")
    await page.goto(harness.base_url + "/?status=all")
    await _open_filters(page)
    await page.locator("#state-filter").select_option("all")
    warnings = page.locator("#list-warning")
    await playwright.async_api.expect(warnings).to_contain_text("invalid.md")
    await playwright.async_api.expect(warnings).to_contain_text("invalid-additional.md")
    await playwright.async_api.expect(warnings).to_contain_text(f"WI / browser-test / {notes_root}")


@pytest.mark.asyncio(loop_scope="session")
async def test_work_item_list_failure_names_server_and_operation(screen_harness: _ScreenHarness) -> None:
    """API取得失敗の理由を保ち、通知自身が保存先と一覧操作を示す。"""
    harness = screen_harness
    page = harness.page
    await page.route("**/api/entries?*", lambda route: route.fulfill(status=503, json={"error": "同じ取得失敗"}))
    await page.goto(harness.base_url + "/")
    notice = page.locator("#operation-notice")
    await playwright.async_api.expect(notice).to_contain_text(f"WI / browser-test / {harness.root.parent} / 一覧取得")
    await playwright.async_api.expect(notice).to_contain_text("同じ取得失敗")

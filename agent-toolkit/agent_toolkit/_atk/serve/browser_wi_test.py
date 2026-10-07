"""`atk serve`のワークアイテム画面の実ブラウザー統合テスト。"""

import asyncio
import dataclasses
import datetime
import functools
import re
import threading
import urllib.parse
from typing import Any

import playwright.async_api
import pytest

from agent_toolkit._atk import git_sync

# pytestがテストの引数名で参照するfixtureを、このモジュールへ登録する。
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _BROWSER_TEST_ENV,
    _LONG_UNKNOWN_FRONTMATTER_KEY,
    _browser_fixture,
    _browser_harness_fixture,
    _browser_tests_enabled,
    _BrowserHarness,
    _hold_route,
    _open_filters,
    _open_question,
    _screen_harness_fixture,
    _ScreenHarness,
    _shift_click_default_prevented,
)

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        not _browser_tests_enabled(),
        reason=f"{_BROWSER_TEST_ENV}=1の場合のみ実行する",
    ),
]
_SAVE_RELEASE_DELAY_SEC = 1.0


def _assert_list_request_without_fallback(request_urls: list[str], base_url: str, expected_path: str) -> None:
    """期待した条件の一覧要求が送られ、補助検索の要求が送られていないことを判定する。

    要求一覧の完全一致は使わない。画面はエンドユーザーの操作と独立に、外部変更時の再読込で
    `/api/entries?type=uwi&status=all&answered=all`と現在の条件の一覧要求を任意の時点で追加送信するためである。
    再読込の契機はSSE接続の確立、`window`の`focus`、`visibilitychange`、SSEの`changed`、
    およびエンドユーザーの操作中に保留した再読込の操作終了後の実行である。
    条件付きの一覧要求は常に`/api/entries?type=`で始まり、条件を外した補助検索の要求だけが
    `/api/entries?q=`で始まるため、後者の不在で補助検索の混入を判定できる。
    """
    assert f"{base_url}{expected_path}" in request_urls
    fallback_prefix = f"{base_url}/api/entries?q="
    assert not [url for url in request_urls if url.startswith(fallback_prefix)]


_POST_APPROVAL_UWI = (
    "---\ntype: uwi\ntarget_repo: example/repo\nquestion_type: choice\nchoices: その対応で問題無い, 問題がある\n---\n\n"
    "## 質問\n\nこの対応で進めた。問題無いか？\n\n## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n"
)
_FREE_FORM_UWI = (
    "---\ntype: uwi\ntarget_repo: example/repo\nquestion_type: free-form\n---\n\n"
    "## 質問\n\n方針はどうするか？\n\n## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n"
)
_AGENT_AWI = "---\ntype: awi\ntarget_repo: example/repo\nsource: session-review\n---\n\n変更操作の対象\n"
_SELF_WRITE_WARNINGS = ("外部で項目が更新されました", "削除されたため表示できません")


@dataclasses.dataclass(frozen=True)
class _DetailMutationCase:
    """詳細ダイアログから送信する変更操作1件の手順と期待結果。"""

    state: str
    content: str
    mode_button: str | None
    field: tuple[str, str] | None
    submit: str
    route: str
    method: str
    message: str
    final_state: str | None


_DETAIL_MUTATION_CASES = {
    "answer-auto-adopt": _DetailMutationCase(
        "inbox",
        _POST_APPROVAL_UWI,
        "#answer-button",
        ("#answer-input", "その対応で問題無い"),
        "#save-answer-button",
        "**/api/entries/answer",
        "POST",
        "へ回答しました。",
        "adopted",
    ),
    "answer": _DetailMutationCase(
        "inbox",
        _FREE_FORM_UWI,
        "#answer-button",
        ("#answer-input", "自由記述の回答"),
        "#save-answer-button",
        "**/api/entries/answer",
        "POST",
        "へ回答しました。",
        "inbox",
    ),
    "save": _DetailMutationCase(
        "inbox",
        _AGENT_AWI,
        "#edit-button",
        ("#edit-content", _AGENT_AWI + "\n保存追記\n"),
        "#save-entry-button",
        "**/api/entries/inbox/target.md",
        "PUT",
        "を保存しました。",
        "inbox",
    ),
    "user-comment": _DetailMutationCase(
        "inbox",
        _AGENT_AWI,
        "#user-comment-button",
        ("#user-comment-input", "ユーザーのコメント"),
        "#save-user-comment-button",
        "**/api/entries/user-comment",
        "POST",
        "のユーザーコメントを保存しました。",
        "inbox",
    ),
    "adopt": _DetailMutationCase(
        "inbox",
        _AGENT_AWI,
        "#adopt-button",
        None,
        "#confirm-adopt-button",
        "**/api/entries/adopt",
        "POST",
        "を採用しました。",
        "adopted",
    ),
    "reject": _DetailMutationCase(
        "inbox",
        _AGENT_AWI,
        "#reject-button",
        None,
        "#confirm-reject-button",
        "**/api/entries/reject",
        "POST",
        "を却下しました。",
        "rejected",
    ),
    "hold": _DetailMutationCase(
        "inbox",
        _AGENT_AWI,
        None,
        None,
        "#hold-button",
        "**/api/entries/hold",
        "POST",
        "を保留しました。",
        "hold",
    ),
    "unhold": _DetailMutationCase(
        "hold",
        _AGENT_AWI,
        None,
        None,
        "#unhold-button",
        "**/api/entries/unhold",
        "POST",
        "を保留解除しました。",
        "inbox",
    ),
    "return-to-inbox": _DetailMutationCase(
        "adopted",
        _AGENT_AWI,
        None,
        None,
        "#return-to-inbox-button",
        "**/api/entries/return-to-inbox",
        "POST",
        "をinboxへ戻すしました。",
        "inbox",
    ),
    "delete": _DetailMutationCase(
        "inbox",
        _AGENT_AWI,
        "#delete-button",
        None,
        "#delete-submit-button",
        "**/api/entries/remove",
        "POST",
        "を削除しました。",
        None,
    ),
}


async def _record_self_write_warnings(page: playwright.async_api.Page) -> None:
    """詳細と通知の表示へ、自分の書込みを外部更新とみなした表示が一度でも現れたかを記録する。"""
    await page.evaluate(
        """warnings => {
          window.__selfWriteWarnings = [];
          const check = () => {
            for (const id of ['detail-alert', 'detail-status', 'operation-notice-message', 'delete-alert']) {
              const text = document.getElementById(id)?.textContent || '';
              if (warnings.some(warning => text.includes(warning))) window.__selfWriteWarnings.push(text);
            }
          };
          new MutationObserver(check).observe(document.body, {subtree: true, childList: true, characterData: true});
        }""",
        list(_SELF_WRITE_WARNINGS),
    )


async def _release_save_after_delay(release: threading.Event) -> None:
    """一定時間の経過後に保存処理を解放する。

    保存が即座に完了する場合、前段の通知が残存したまま待機が成立する欠陥があっても、
    本文の読み取りは保存の完了後に到達し、テストは通過する。解放を遅延させると、
    保存の完了を待たない待機条件は実行速度によらず失敗として現れる。
    """
    await asyncio.sleep(_SAVE_RELEASE_DELAY_SEC)
    release.set()


async def _wait_and_close_operation_notice(page: playwright.async_api.Page, text: str) -> None:
    """操作の通知が表示されるまで待ち、成立した通知を閉じる。

    通知は自動で消えないため、閉じずに次の待機へ進むと残存した通知で待機が即座に成立する。
    待機のたびに閉じることで、次の待機がその操作で新たに表示された通知だけで成立する。
    """
    await page.get_by_role("status").filter(has_text=text).wait_for(state="visible")
    await page.locator("#operation-notice-close-button").click()
    await playwright.async_api.expect(page.locator("#operation-notice")).to_be_hidden()


@pytest.mark.asyncio
async def test_responsive_layout_dialog_scroll_and_markdown(browser_harness: _BrowserHarness) -> None:
    """代表3画面幅で横overflow、固定領域、タッチ寸法、Markdown表示を検証する。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await _open_filters(page)

    for width, height, columns_visible in [(390, 844, False), (768, 1024, False), (1280, 800, True)]:
        await page.set_viewport_size({"width": width, "height": height})
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert await page.locator(".entry-columns").is_visible() is columns_visible
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
        if width == 390:
            header_box = await page.locator("#screen-wi .app-header").bounding_box()
            assert header_box is not None
            assert header_box["height"] < 150
        await page.keyboard.press("Escape")
        await playwright.async_api.expect(row).to_be_focused()
        await row.click()
        await playwright.async_api.expect(dialog).to_be_visible()
        assert await body.evaluate("element => element.scrollTop") == 0
        await page.keyboard.press("Escape")
        await playwright.async_api.expect(row).to_be_focused()


@pytest.mark.asyncio
async def test_mobile_wi_list_starts_with_compact_two_row_entries(browser_harness: _BrowserHarness) -> None:
    """390px幅では一覧を先に示し、各項目を2段で描画する。"""
    page = browser_harness.page
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.goto(browser_harness.base_url + "/")
    row = page.locator("#entry-list .entry-row").first
    await row.wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")

    assert not await page.locator(".filters details").evaluate("element => element.open")
    cells = row.locator(".entry-cell")
    pseudo_display = await cells.evaluate_all(
        "elements => elements.map(element => getComputedStyle(element, '::before').display)"
    )
    assert pseudo_display == ["none"] * await cells.count()
    summary_box = await row.locator(".summary-cell").bounding_box()
    status_box = await row.locator(".status-cell").bounding_box()
    row_box = await row.bounding_box()
    assert summary_box is not None
    assert status_box is not None
    assert row_box is not None
    assert summary_box["y"] < status_box["y"]
    assert row_box["height"] <= 96
    assert row_box["y"] >= 0
    assert row_box["y"] + row_box["height"] <= 844


@pytest.mark.asyncio
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

    await page.route("**/api/entries?*", fail_first_list_request)
    await page.locator("#refresh-button").click()
    await playwright.async_api.expect(error_message).to_have_text("一覧取得失敗")
    await playwright.async_api.expect(error_region).to_be_visible()
    await page.unroute("**/api/entries?*", fail_first_list_request)
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

    await page.route("**/api/entries?*", fail_second_list_request)
    await page.locator("#refresh-button").click()
    await playwright.async_api.expect(error_message).to_have_text("後続のエラー")
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
    await page.unroute("**/api/entries?*", fail_second_list_request)


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_long_unknown_metadata_key_wraps_at_narrow_viewport(
    browser_harness: _BrowserHarness,
) -> None:
    """狭幅画面で500文字の未知キーを折り返し、横overflowを生じさせない。"""
    page = browser_harness.page
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await _open_question(page)

    dialog = page.get_by_role("dialog", name="詳細")
    long_key_term = dialog.locator("#detail-metadata dt").filter(has_text=_LONG_UNKNOWN_FRONTMATTER_KEY)
    await playwright.async_api.expect(long_key_term).to_have_count(1)
    # 詳細の再読込でdtが置換されるため、表示済み要素の解決と寸法取得を同じ描画状態で行う。
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


@pytest.mark.asyncio
async def test_accessible_workflows_filters_warnings_and_sse_status(browser_harness: _BrowserHarness) -> None:
    """回答・削除フォーカス、条件依存、警告、エンドユーザー起点だけの件数通知を検証する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    assert await page.locator(".filters details").evaluate("element => element.open")
    await _open_filters(page)

    warning = page.get_by_role("alert").filter(has_text="invalid.md")
    await warning.wait_for(state="visible")
    assert "UTF-8として読み取れません" in await warning.inner_text()
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
    await dialog.get_by_role("button", name="回答", exact=True).click()
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
    await playwright.async_api.expect(page.locator("#answer-filter")).to_be_disabled()
    assert await page.locator("#answer-filter").input_value() == "all"
    assert await page.locator("#source-filter option").all_text_contents() == ["all", "human", "agent"]
    async with page.expect_response(
        lambda response: response.url.endswith(
            "/api/entries?type=awi&status=active&answered=all&period=2w&source_kind=agent&page=1"
        )
    ):
        await page.locator("#source-filter").select_option("agent")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(2)
    await playwright.async_api.expect(awi_row).to_be_visible()
    await playwright.async_api.expect(empty_row).to_be_visible()
    async with page.expect_response(
        lambda response: response.url.endswith(
            "/api/entries?type=awi&status=active&answered=all&period=2w&source_kind=human&page=1"
        )
    ):
        await page.locator("#source-filter").select_option("human")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(0)
    await page.locator("#clear-filters-button").click()

    await page.locator("#target-filter").select_option("example/repo")
    await page.locator("#state-filter").select_option("adopted")
    await playwright.async_api.expect(page.locator("#target-filter")).to_have_value("")
    assert await page.locator("#target-filter option").all_text_contents() == ["all", "adopted/repo"]
    await page.locator("#entry-list .entry-select").filter(has_text="adopted.md").wait_for(state="visible")

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
                "/api/entries?type=all&status=active&answered=all&period=2w&q=%E7%B7%A8%E9%9B%86%E5%AF%BE%E8%B1%A1&page=1"
            )
        )
    ):
        await page.locator("#search-input").fill("編集対象")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(1)
    await playwright.async_api.expect(awi_row).to_be_visible()
    await playwright.async_api.expect(page.locator("#result-status")).to_have_text("1件を表示")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await playwright.async_api.expect(page.locator("#connection-status")).to_be_hidden()
    (harness.root / "inbox" / "sse.md").write_text(
        "---\ntype: awi\ntarget_repo: sse/repo\nsource: browser\n---\n\n編集対象の外部追加\n",
        encoding="utf-8",
    )
    harness.current_state.publish()
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(2)
    await page.locator("#entry-list .entry-select").filter(has_text="sse.md").wait_for(state="visible")
    await playwright.async_api.expect(page.locator('#target-filter option[value="sse/repo"]')).to_have_count(1)
    await playwright.async_api.expect(page.locator("#result-status")).to_have_text("1件を表示")


@pytest.mark.asyncio
async def test_one_choice_uwi_can_be_answered_then_adopted(browser_harness: _BrowserHarness) -> None:
    """回答後は詳細を閉じ、再び開いて採用できる。"""
    harness = browser_harness
    filename = "one-choice.md"
    (harness.root / "inbox" / filename).write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\nquestion_type: choice\n"
        "choices: 問題がある（是正内容をこの欄へ書いてください）\n---\n\n"
        "## 質問\n\n問題はありますか？\n\n## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    harness.operations.enable_file_mutations()
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator(f'.entry-select[data-key="inbox/{filename}"]').click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="回答", exact=True).click()
    await detail.get_by_role("button", name="問題がある（是正内容をこの欄へ書いてください）").click()
    await detail.locator("#answer-input").fill("問題がある。是正内容を確認しました。")
    await detail.get_by_role("button", name="回答を保存").click()
    await playwright.async_api.expect(detail).to_be_hidden()
    await page.get_by_role("status").filter(has_text="回答しました").wait_for(state="visible")
    await page.locator(f'.entry-select[data-key="inbox/{filename}"]').click()
    await playwright.async_api.expect(detail.locator("#detail-metadata")).to_contain_text("answered")
    await playwright.async_api.expect(detail.locator("#detail-metadata")).to_contain_text("yes")
    await playwright.async_api.expect(detail.locator("#decision-panel")).to_be_hidden()
    await detail.get_by_role("button", name="採用", exact=True).click()
    note = detail.get_by_label("メモ（任意）")
    await playwright.async_api.expect(note).to_be_focused()
    await playwright.async_api.expect(detail.get_by_role("button", name="保留", exact=True)).to_be_hidden()
    await note.fill("回答どおり採用する")
    await detail.get_by_role("button", name="採用を確定").click()
    await page.get_by_role("status").filter(has_text="採用しました").wait_for(state="visible")
    assert harness.operations.last_transition == ("adopt", [filename], "回答どおり採用する")
    assert (harness.root / "adopted" / filename).exists()
    assert not (harness.root / "inbox" / filename).exists()


@pytest.mark.asyncio
async def test_search_fallback_shows_limited_terminal_matches_and_keeps_filters(
    browser_harness: _BrowserHarness,
) -> None:
    """初期状態の条件で終端状態を検索し、少数結果だけを補助表示して条件を維持する。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await _open_filters(page)
    notice = page.locator("#list-fallback-notice")
    expected_notice = (
        "状態などの条件では一致しなかったため、検索欄の条件だけで見つかった項目を表示しています。"
        "フィルターの選択値は変更していません。"
    )

    async with page.expect_response(
        lambda response: response.url.endswith("/api/entries?q=%E6%8E%A1%E7%94%A8%E6%B8%88%E3%81%BF&page=1")
    ):
        await page.locator("#search-input").fill("採用済み")
    await page.locator('.entry-select[data-key="adopted/adopted.md"]').wait_for(state="visible")
    await playwright.async_api.expect(notice).to_have_text(expected_notice)
    await playwright.async_api.expect(page.locator("#state-filter")).to_have_value("active")
    await playwright.async_api.expect(page.locator("#kind-filter")).to_have_value("all")
    await playwright.async_api.expect(page.locator("#answer-filter")).to_have_value("all")

    async with page.expect_response(
        lambda response: response.url.endswith("/api/entries?q=%E4%B8%8D%E6%8E%A1%E7%94%A8&page=1")
    ):
        await page.locator("#search-input").fill("不採用")
    await page.locator('.entry-select[data-key="rejected/rejected.md"]').wait_for(state="visible")
    await playwright.async_api.expect(notice).to_be_visible()
    await playwright.async_api.expect(page.locator("#state-filter")).to_have_value("active")

    async with page.expect_response(lambda response: response.url.endswith("/api/entries?q=many-terminal&page=1")):
        await page.locator("#search-input").fill("many-terminal")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(0)
    await playwright.async_api.expect(notice).to_be_hidden()
    await playwright.async_api.expect(page.locator("#state-filter")).to_have_value("active")

    request_urls: list[str] = []
    page.on("request", lambda request: request_urls.append(request.url) if "/api/entries?" in request.url else None)
    await page.locator("#search-input").fill("")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(4)
    await page.wait_for_timeout(100)
    _assert_list_request_without_fallback(
        request_urls, browser_harness.base_url, "/api/entries?type=all&status=active&answered=all&period=2w&page=1"
    )

    request_urls.clear()
    async with page.expect_response(
        lambda response: response.url.endswith(
            "/api/entries?type=all&status=active&answered=all&period=2w&q=%E7%B7%A8%E9%9B%86%E5%AF%BE%E8%B1%A1&page=1"
        )
    ):
        await page.locator("#search-input").fill("編集対象")
    await page.locator('.entry-select[data-key="inbox/awi.md"]').wait_for(state="visible")
    await playwright.async_api.expect(notice).to_be_hidden()
    await playwright.async_api.expect(page.locator("#state-filter")).to_have_value("active")
    await page.wait_for_timeout(100)
    _assert_list_request_without_fallback(
        request_urls,
        browser_harness.base_url,
        "/api/entries?type=all&status=active&answered=all&period=2w&q=%E7%B7%A8%E9%9B%86%E5%AF%BE%E8%B1%A1&page=1",
    )

    await page.locator("#kind-filter").select_option("all")
    await page.locator("#state-filter").select_option("all")
    await page.locator("#period-filter").select_option("all")
    await page.locator("#answer-filter").select_option("all")
    await page.locator("#target-filter").select_option("")
    await page.locator("#source-filter").select_option("")
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    request_urls.clear()
    async with page.expect_response(
        lambda response: response.url.endswith(
            "/api/entries?type=all&status=all&answered=all&period=all&q=all-filters-only&page=1"
        )
    ):
        await page.locator("#search-input").fill("all-filters-only")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(0)
    await playwright.async_api.expect(notice).to_be_hidden()
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    _assert_list_request_without_fallback(
        request_urls,
        browser_harness.base_url,
        "/api/entries?type=all&status=all&answered=all&period=all&q=all-filters-only&page=1",
    )


@pytest.mark.asyncio
async def test_period_filter_limits_work_items_to_recent_by_default(browser_harness: _BrowserHarness) -> None:
    """期間の初期値は直近2週間で、期間を広げると古いWIが現れ、条件をクリアすると直近2週間へ戻る。

    初期値で期間を限定しないと古いWIが一覧を埋め、期間を広げる操作が無いと古いWIへ到達できない。
    """
    harness = browser_harness
    page = harness.page
    now = datetime.datetime.now()
    names = {days: f"{now - datetime.timedelta(days=days):%Y%m%d-%H%M%S}-001.md" for days in (1, 20, 40)}
    for name in names.values():
        (harness.root / "inbox" / name).write_text(
            f"---\ntype: awi\ntarget_repo: example/repo\nsource: browser\n---\n\n期間の確認 {name}\n",
            encoding="utf-8",
        )
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await _open_filters(page)
    period = page.locator("#period-filter")
    rows = page.locator("#entry-list .entry-select")
    await playwright.async_api.expect(period).to_have_value("2w")
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
    await playwright.async_api.expect(period).to_have_value("2w")
    await playwright.async_api.expect(rows.filter(has_text=names[20])).to_have_count(0)
    await playwright.async_api.expect(rows.filter(has_text=names[1])).to_have_count(1)
    await playwright.async_api.expect(page.locator("#entry-period")).to_have_text("直近2週間に作成")


@pytest.mark.asyncio
async def test_empty_list_limited_by_period_offers_all_periods(browser_harness: _BrowserHarness) -> None:
    """期間の内側に対応中の項目が無い空状態から、全期間の一覧へ1操作で進め、狭い幅でも期間の限定を確認できる。

    空状態に期間を広げる操作が無いと、古い対応中の項目を探すエンドユーザーがフィルター欄を探して開く必要がある。
    """
    harness = browser_harness
    page = harness.page
    await page.set_viewport_size({"width": 320, "height": 800})
    old_name = f"{datetime.datetime.now() - datetime.timedelta(days=40):%Y%m%d-%H%M%S}-001.md"
    (harness.root / "inbox" / old_name).write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: browser\n---\n\n古い対応中の項目\n", encoding="utf-8"
    )

    async def empty_recent(route: playwright.async_api.Route) -> None:
        await route.fulfill(
            json={"entries": [], "warnings": [], "pagination": {"page": 1, "page_size": 100, "page_count": 1, "total_count": 0}}
        )

    await page.route("**/api/entries?*period=2w*", empty_recent)
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#empty-state-message")).to_have_text(
        "直近2週間に作成された対応中の項目はありません。"
    )
    await playwright.async_api.expect(page.locator("#entry-period")).to_be_visible()
    await playwright.async_api.expect(page.locator("#entry-period")).to_have_text("直近2週間に作成")
    await page.get_by_role("button", name="全期間を表示").click()

    await playwright.async_api.expect(page.locator("#entry-list .entry-select").filter(has_text=old_name)).to_have_count(1)
    await playwright.async_api.expect(page.locator("#period-filter")).to_have_value("all")
    await playwright.async_api.expect(page.locator("#entry-period")).to_be_hidden()


@pytest.mark.asyncio
async def test_answer_change_terminal_read_only_and_identifier_surfaces(browser_harness: _BrowserHarness) -> None:
    """既存回答の変更、終端状態の編集制限、削除および識別子表示を検証する。"""
    harness = browser_harness
    page = harness.page
    question_path = harness.root / "inbox" / "question.md"
    question_path.write_text(question_path.read_text(encoding="utf-8") + "既存回答\n", encoding="utf-8")
    await page.goto(harness.base_url + "/")
    await _open_filters(page)

    question_row = page.locator('.entry-select[data-key="inbox/question.md"]')
    await question_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("uwi / inbox")
    await detail.get_by_role("button", name="回答を変更", exact=True).click()
    await playwright.async_api.expect(detail.locator("#answer-input")).to_have_value("既存回答")
    await page.keyboard.press("Escape")

    await page.locator("#state-filter").select_option("all")
    await page.locator('.entry-select[data-key="adopted/adopted.md"]').click()
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("awi / adopted")
    await playwright.async_api.expect(detail.locator("#readonly-notice")).to_be_visible()
    await playwright.async_api.expect(detail.locator("#readonly-notice")).to_have_text("この項目は編集と回答の対象外です。")
    await playwright.async_api.expect(detail.locator("#edit-button")).to_be_hidden()
    await playwright.async_api.expect(detail.locator("#answer-button")).to_be_hidden()
    await playwright.async_api.expect(detail.locator("#delete-button")).to_be_visible()
    await playwright.async_api.expect(detail.locator("#hold-button")).to_be_visible()
    await playwright.async_api.expect(detail.locator("#return-to-inbox-button")).to_be_visible()


@pytest.mark.asyncio
async def test_hold_and_rejected_details_offer_recovery_operations(browser_harness: _BrowserHarness) -> None:
    """保留中の全操作と、不採用項目のinbox復帰操作を実ブラウザーで表示する。"""
    harness = browser_harness
    page = harness.page
    hold = harness.root / "hold"
    hold.mkdir()
    (hold / "held-awi.md").write_text(
        "---\ntype: awi\ntarget_repo: held/repo\nsource: session-review\n---\n\n保留中の本文\n",
        encoding="utf-8",
    )
    (hold / "held-question.md").write_text(
        "---\ntype: uwi\ntarget_repo: held/repo\nquestion_type: free-form\n---\n\n"
        "## 質問\n\n保留中の質問\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    await page.goto(browser_harness.base_url + "/")
    await _open_filters(page)
    await page.locator("#state-filter").select_option("all")
    detail = page.get_by_role("dialog", name="詳細")

    await page.locator('.entry-select[data-key="hold/held-awi.md"]').click()
    for button_name in ("編集", "採用", "却下", "保留を解除", "削除"):
        await playwright.async_api.expect(detail.get_by_role("button", name=button_name, exact=True)).to_be_visible()
    await playwright.async_api.expect(detail.locator("#user-comment-button")).to_be_visible()
    await page.keyboard.press("Escape")

    await page.locator('.entry-select[data-key="hold/held-question.md"]').click()
    await playwright.async_api.expect(detail.get_by_role("button", name="回答", exact=True)).to_be_visible()
    await playwright.async_api.expect(detail.get_by_role("button", name="採用", exact=True)).to_be_visible()
    await playwright.async_api.expect(detail.locator("#reject-button")).to_be_hidden()
    await page.keyboard.press("Escape")

    await page.locator('.entry-select[data-key="rejected/rejected.md"]').click()
    await playwright.async_api.expect(detail.locator("#readonly-notice")).to_be_hidden()
    await playwright.async_api.expect(detail.get_by_role("button", name="受信へ戻す", exact=True)).to_be_visible()
    await playwright.async_api.expect(detail.locator("#hold-button")).to_be_visible()


@pytest.mark.asyncio
async def test_terminal_work_items_reopen_and_hold_through_browser(browser_harness: _BrowserHarness) -> None:
    """両終端状態のWIを画面から再開・保留・削除し、結果まで確認する。"""
    harness = browser_harness
    harness.operations.persist_mutations = True
    page = harness.page
    delete_paths = {}
    for state in ("adopted", "rejected"):
        path = harness.root / state / f"delete-{state}.md"
        path.write_text(f"---\ntype: awi\ntarget_repo: example/repo\n---\n\n{state}の削除対象\n", encoding="utf-8")
        delete_paths[state] = path
    await page.goto(harness.base_url + "/")
    await _open_filters(page)
    await page.locator("#state-filter").select_option("all")

    await page.locator('.entry-select[data-key="adopted/adopted.md"]').click()
    await page.locator("#hold-button").click()
    await playwright.async_api.expect(page.get_by_role("dialog", name="詳細")).to_be_hidden()
    await page.locator('.entry-select[data-key="hold/adopted.md"]').wait_for(state="visible")

    await page.locator('.entry-select[data-key="rejected/rejected.md"]').click()
    await page.locator("#return-to-inbox-button").click()
    await playwright.async_api.expect(page.get_by_role("dialog", name="詳細")).to_be_hidden()
    await page.locator('.entry-select[data-key="inbox/rejected.md"]').wait_for(state="visible")

    detail = page.get_by_role("dialog", name="詳細")
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    for state, path in delete_paths.items():
        row = page.locator(f'.entry-select[data-key="{state}/{path.name}"]')
        await row.click()
        await detail.get_by_role("button", name="削除", exact=True).click()
        await playwright.async_api.expect(delete_dialog.locator("#delete-state")).to_have_text(f"awi / {state}")
        async with page.expect_request("**/api/entries/remove") as request_info:
            await delete_dialog.get_by_role("button", name="削除する").click()
        payload = (await request_info.value).post_data_json
        assert isinstance(payload, dict)
        assert payload["state"] == state
        await delete_dialog.wait_for(state="hidden")
        await playwright.async_api.expect(row).to_have_count(0)
        assert not path.exists()


@pytest.mark.asyncio
async def test_browser_notification_uses_filename_registration_identity(browser_harness: _BrowserHarness) -> None:
    """初回と既知UWIの属性変化を通知せず、新規未回答UWIだけを通知する。"""
    harness = browser_harness
    page = harness.page
    await page.add_init_script(
        """
window.__notificationCalls = [];
class TestNotification {
  static permission = 'default';
  static async requestPermission() { TestNotification.permission = 'granted'; return 'granted'; }
  constructor(title, options) { window.__notificationCalls.push({title, body: options.body}); }
}
Object.defineProperty(window, 'Notification', {configurable: true, value: TestNotification});
"""
    )
    async with page.expect_response(lambda response: response.url.endswith("/api/entries?type=uwi&status=all&answered=all")):
        await page.goto(harness.base_url + "/")
    assert await page.evaluate("window.__notificationCalls.length") == 0
    notification_button = page.get_by_role("button", name="通知を有効化")
    await notification_button.click()
    await playwright.async_api.expect(notification_button).to_be_hidden()

    async def publish_and_wait() -> None:
        async with page.expect_response(
            lambda response: response.url.endswith("/api/entries?type=uwi&status=all&answered=all")
        ):
            harness.current_state.publish()

    marker = "<!-- ユーザーはこの行以降に回答を追記する -->"
    new_path = harness.root / "inbox" / "new-question.md"
    new_unanswered = f"---\ntype: uwi\ntarget_repo: new/repo\n---\n\n## 質問\n\n新規\n\n## 回答\n\n{marker}\n"
    new_path.write_text(new_unanswered, encoding="utf-8")
    await publish_and_wait()
    await page.wait_for_function("window.__notificationCalls.length === 1")
    assert await page.evaluate("window.__notificationCalls") == [{"title": "新規未回答UWI", "body": "new-question.md"}]

    question_path = harness.root / "inbox" / "question.md"
    original_question = question_path.read_text(encoding="utf-8")
    question_path.write_text(original_question + "回答済み\n", encoding="utf-8")
    await publish_and_wait()
    question_path.write_text(original_question, encoding="utf-8")
    await publish_and_wait()
    question_path.write_text(original_question.replace("example/repo", "changed/repo"), encoding="utf-8")
    await publish_and_wait()

    processing_path = harness.root / "processing" / "question.md"
    processing_path.parent.mkdir(exist_ok=True)
    question_path.rename(processing_path)
    await publish_and_wait()
    processing_path.rename(question_path)
    await publish_and_wait()

    new_path.unlink()
    await publish_and_wait()
    new_path.write_text(new_unanswered, encoding="utf-8")
    await publish_and_wait()
    await page.wait_for_timeout(100)
    assert await page.evaluate("window.__notificationCalls.length") == 1


@pytest.mark.asyncio
async def test_pending_edit_and_closed_delete_route_results(browser_harness: _BrowserHarness) -> None:
    """未解決更新の二重送信、入力固定、処理中閉鎖、完了結果の配送先を検証する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")

    awi_row = page.locator('#entry-list .entry-select[data-kind="awi"]').filter(has_text="awi.md")
    await awi_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="編集", exact=True).click()
    edit_input = detail.locator("#edit-content")
    await edit_input.fill((await edit_input.input_value()) + "\n追記")
    harness.operations.arm_edit_delay()
    await page.evaluate(
        "document.getElementById('save-entry-button').click(); document.getElementById('save-entry-button').click()"
    )
    assert await asyncio.to_thread(harness.operations.edit_started.wait, 5)
    await playwright.async_api.expect(edit_input).to_be_disabled()
    await playwright.async_api.expect(detail.locator("#save-entry-button")).to_be_disabled()
    await playwright.async_api.expect(detail.get_by_role("button", name="閉じる")).to_be_enabled()
    assert await detail.locator("#detail-shell").get_attribute("aria-busy") == "true"
    assert harness.operations.edit_calls == 1
    await detail.get_by_role("button", name="閉じる").click()
    harness.operations.edit_release.set()
    await page.get_by_role("status").filter(has_text="保存しました").wait_for(state="visible")

    await awi_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="削除").click()
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    harness.operations.arm_remove_delay()
    await delete_dialog.get_by_role("button", name="削除する").click()
    assert await asyncio.to_thread(harness.operations.remove_started.wait, 5)
    await playwright.async_api.expect(delete_dialog.get_by_role("button", name="閉じる")).to_be_enabled()
    await delete_dialog.get_by_role("button", name="閉じる").click()
    assert await detail.is_visible()
    harness.operations.remove_release.set()
    await page.get_by_role("status").filter(has_text="削除しました").wait_for(state="visible")
    assert harness.operations.remove_calls == 1


@pytest.mark.asyncio
async def test_edit_success_does_not_depend_on_auxiliary_detail_get(
    browser_harness: _BrowserHarness,
) -> None:
    """本文保存の成功を後続の詳細取得失敗へ変換しない。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    awi_row = page.locator('.entry-select[data-key="inbox/awi.md"]')
    await awi_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="編集", exact=True).click()
    edit_input = detail.locator("#edit-content")
    await edit_input.fill((await edit_input.input_value()) + "\n保存追記")
    request_methods: list[str] = []

    async def fail_detail_get(route: playwright.async_api.Route) -> None:
        request_methods.append(route.request.method)
        if route.request.method == "GET":
            await route.fulfill(status=500, json={"error": "詳細を再取得できませんでした。"})
            return
        await route.continue_()

    await page.route("**/api/entries/inbox/awi.md", fail_detail_get)
    await detail.get_by_role("button", name="保存", exact=True).click()
    await page.get_by_role("status").filter(has_text="保存しました").wait_for(state="visible")
    await detail.wait_for(state="hidden")
    assert request_methods == ["PUT"]
    await page.unroute("**/api/entries/inbox/awi.md", fail_detail_get)


@pytest.mark.asyncio
async def test_sse_refreshes_open_detail_preserves_input_and_closes_missing_entry(
    browser_harness: _BrowserHarness,
) -> None:
    """SSE更新時の詳細再取得、入力保持、消失時の閉鎖と復帰先を検証する。"""
    harness = browser_harness
    page = harness.page
    awi_path = harness.root / "inbox" / "awi.md"
    await page.goto(harness.base_url + "/")
    awi_row = page.locator('#entry-list .entry-select[data-kind="awi"]').filter(has_text="awi.md")
    await awi_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.wait_for(state="visible")

    awi_path.write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: browser\n---\n\n外部更新後の本文\n",
        encoding="utf-8",
    )
    harness.current_state.publish()
    await playwright.async_api.expect(detail.locator("#detail-content")).to_contain_text("外部更新後の本文")

    await detail.get_by_role("button", name="編集", exact=True).click()
    edit_input = detail.locator("#edit-content")
    await edit_input.fill("ユーザーの未保存本文")
    awi_path.write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: browser\n---\n\n編集中の外部更新\n",
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


@pytest.mark.asyncio
async def test_detail_focus_falls_back_after_answer_filter_and_delete(
    browser_harness: _BrowserHarness,
) -> None:
    """回答後の詳細を閉じたときや削除・0件化後に操作可能な一覧要素へ戻る。"""
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
    await detail.get_by_role("button", name="回答", exact=True).click()
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


@pytest.mark.asyncio
async def test_sse_reconciliation_preserves_identity_and_owned_dialogs(
    browser_harness: _BrowserHarness,
) -> None:
    """同名項目、状態移動、親子ダイアログ消失を実ブラウザーで調停する。"""
    harness = browser_harness
    page = harness.page
    processing = harness.root / "processing"
    processing.mkdir(exist_ok=True)
    inbox_same = harness.root / "inbox" / "same.md"
    processing_same = processing / "same.md"
    moving = harness.root / "inbox" / "moving.md"
    inbox_same.write_text("---\ntype: awi\n---\n\n未処理の同名本文\n", encoding="utf-8")
    processing_same.write_text("---\ntype: awi\n---\n\n処理中の同名本文\n", encoding="utf-8")
    moving.write_text("---\ntype: awi\n---\n\n移動対象\n", encoding="utf-8")
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await playwright.async_api.expect(page.locator("#connection-status")).to_be_hidden()

    processing_row = page.locator('.entry-select[data-key="processing/same.md"]')
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
        lambda request: request.method == "PUT" and request.url.endswith("/api/entries/processing/same.md")
    ):
        await detail.get_by_role("button", name="保存", exact=True).click()
    await page.get_by_role("status").filter(has_text="保存しました").wait_for(state="visible")
    await detail.wait_for(state="hidden")

    await processing_row.click()
    await detail.wait_for(state="visible")
    adopted_same = harness.root / "adopted" / "same.md"
    processing_same.rename(adopted_same)
    harness.current_state.publish()
    await detail.wait_for(state="hidden")
    await page.get_by_role("alert").filter(has_text="移動先を一意に特定できません").wait_for(state="visible")

    moving_row = page.locator('.entry-select[data-key="inbox/moving.md"]')
    await moving_row.click()
    await detail.get_by_role("button", name="削除").click()
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    moved = processing / "moving.md"
    moving.rename(moved)
    harness.current_state.publish()
    await delete_dialog.wait_for(state="hidden")
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("awi / processing")
    await playwright.async_api.expect(detail.locator("#detail-dialog-body")).to_be_focused()

    await detail.get_by_role("button", name="削除").click()
    await playwright.async_api.expect(delete_dialog.locator("#force-delete-row")).to_be_visible()
    moved.unlink()
    harness.current_state.publish()
    await delete_dialog.wait_for(state="hidden")
    await detail.wait_for(state="hidden")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select").first).to_be_focused()


@pytest.mark.asyncio
@pytest.mark.parametrize("case_name", list(_DETAIL_MUTATION_CASES))
async def test_detail_mutation_self_write_does_not_warn(browser_harness: _BrowserHarness, case_name: str) -> None:
    """変更操作の応答より先に自分の書込みの更新通知が届いても、警告せずに詳細を閉じて成功を通知する。

    送信中の更新通知を外部更新として扱うと、成功した操作に「外部で項目が更新されました」が出る。
    状態が変わる操作（自動採用される回答、採用、保留、削除など）では詳細の表示も書き換わり、詳細が閉じずに
    成功の通知が背面へ出るか、自分が削除した項目を削除済みとして通知する。
    """
    case = _DETAIL_MUTATION_CASES[case_name]
    harness = browser_harness
    page = harness.page
    harness.operations.enable_file_mutations()
    for directory in ("hold", "processing"):
        (harness.root / directory).mkdir(exist_ok=True)
    (harness.root / case.state / "target.md").write_text(case.content, encoding="utf-8")
    key = f"{case.state}/target.md"
    await page.goto(f"{harness.base_url}/?state={case.state}&filename=target.md")
    detail = page.get_by_role("dialog", name="詳細")
    await detail.wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await _record_self_write_warnings(page)
    if case.mode_button is not None:
        await page.locator(case.mode_button).click()
    if case.field is not None:
        await page.locator(case.field[0]).fill(case.field[1])
    fetched = asyncio.Event()
    release = asyncio.Event()

    async def delay_response(route: playwright.async_api.Route) -> None:
        if route.request.method != case.method:
            await route.continue_()
            return
        response = await route.fetch()
        fetched.set()
        await release.wait()
        await route.fulfill(response=response)

    await page.route(case.route, delay_response)
    await page.locator(case.submit).click()
    await asyncio.wait_for(fetched.wait(), timeout=5)
    # 更新通知が届き、画面が一覧の再取得を終えて詳細の再読込へ進むまで応答を返さない。
    async with page.expect_response(lambda response: "/api/entries?type=" in response.url):
        harness.current_state.publish()
    await page.wait_for_timeout(300)
    release.set()

    await detail.wait_for(state="hidden")
    await playwright.async_api.expect(page.locator("#operation-notice-message")).to_have_text(f"{key}{case.message}")
    assert await page.evaluate("window.__selfWriteWarnings") == []
    if case.final_state is None:
        assert not list(harness.root.glob("*/target.md"))
    else:
        assert (harness.root / case.final_state / "target.md").is_file()
    if case_name == "answer-auto-adopt":
        assert "その対応で問題無い" in (harness.root / "adopted" / "target.md").read_text(encoding="utf-8")
        await _open_filters(page)
        await page.locator("#state-filter").select_option("all")
        await page.locator('.entry-select[data-key="adopted/target.md"]').wait_for(state="visible")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode_button", "field", "submit", "failure"),
    [
        ("#answer-button", "#answer-input", "#save-answer-button", "へ回答できませんでした。"),
        ("#edit-button", "#edit-content", "#save-entry-button", "を保存できませんでした。"),
    ],
    ids=["answer", "save"],
)
async def test_detail_mutation_conflict_keeps_input(
    browser_harness: _BrowserHarness, mode_button: str, field: str, submit: str, failure: str
) -> None:
    """送信中に他プロセスが本文を変えてサーバーが409を返した場合は、入力を残して失敗と回復の手順を示す。

    保留した詳細の再読込は失敗時に実行するが、その警告で失敗の表示を上書きしない。
    """
    harness = browser_harness
    page = harness.page
    harness.operations.enable_file_mutations()
    path = harness.root / "inbox" / "target.md"
    path.write_text(_FREE_FORM_UWI, encoding="utf-8")
    await page.goto(f"{harness.base_url}/?state=inbox&filename=target.md")
    detail = page.get_by_role("dialog", name="詳細")
    await detail.wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await page.locator(mode_button).click()
    user_input = _FREE_FORM_UWI + "\n入力の追記\n" if field == "#edit-content" else "保持する回答"
    await page.locator(field).fill(user_input)

    async def change_before_send(route: playwright.async_api.Route) -> None:
        if route.request.method not in {"POST", "PUT"}:
            await route.continue_()
            return
        path.write_text(_FREE_FORM_UWI.replace("方針はどうするか？", "他プロセスが変えた質問"), encoding="utf-8")
        harness.current_state.publish()
        await page.wait_for_timeout(300)
        await route.continue_()

    await page.route("**/api/entries/answer", change_before_send)
    await page.route("**/api/entries/inbox/target.md", change_before_send)
    await page.locator(submit).click()

    alert = detail.locator("#detail-alert")
    await playwright.async_api.expect(alert).to_contain_text(f"inbox/target.md{failure}")
    await playwright.async_api.expect(alert).to_contain_text(
        "inbox/target.mdは外部で更新されました。入力を保持しています。詳細を閉じて開き直してから保存してください。"
    )
    assert await page.locator(field).input_value() == user_input


@pytest.mark.asyncio
async def test_needs_verify_badge_replaces_inbox(browser_harness: _BrowserHarness) -> None:
    """反映後の観測だけが残るinboxのawiは、一覧・詳細・削除確認でinboxの代わりにneeds-verifyを示す。

    保存状態だけでバッジを決めると、観測待ちの項目が未着手の項目と同じinboxに見え、一覧で見分けられない。
    色はprocessingと同じにし、状態フィルターと保存先は保存状態のinboxのまま扱う。
    """
    harness = browser_harness
    page = harness.page
    (harness.root / "inbox" / "verify.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: browser\n---\n\n観測待ちの本文\n\n"
        "## 反映後の観測の再開記録\n\n- 再開区分: 反映後の観測だけが残る\n- 計画: 計画なし\n",
        encoding="utf-8",
    )
    (harness.root / "processing").mkdir(exist_ok=True)
    (harness.root / "processing" / "working.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: browser\n---\n\n処理中の本文\n", encoding="utf-8"
    )
    await page.goto(harness.base_url + "/")
    # 接続の確立時に一覧を再描画するため、確立を待ってから行の要素を読む。
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    row = page.locator('.entry-select[data-key="inbox/verify.md"]')
    badge = row.locator(".state-badge")
    await playwright.async_api.expect(badge).to_have_text("needs-verify")
    assert "inbox" not in await row.locator(".status-cell").inner_text()
    label = await row.get_attribute("aria-label")
    assert label is not None
    assert "needs-verify" in label.split("、")
    assert "inbox" not in label.split("、")
    processing_badge = page.locator('.entry-select[data-key="processing/working.md"] .state-badge')
    style = "element => [getComputedStyle(element).color, getComputedStyle(element).backgroundColor]"
    assert await badge.evaluate(style) == await processing_badge.evaluate(style)

    await row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("awi / needs-verify")
    await detail.locator("#delete-button").click()
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    await playwright.async_api.expect(delete_dialog.locator("#delete-state")).to_have_text("awi / needs-verify")
    await delete_dialog.locator("#delete-close-button").click()
    await detail.locator("#detail-close-button").click()

    await _open_filters(page)
    await page.locator("#state-filter").select_option("inbox")
    await playwright.async_api.expect(page.locator('.entry-select[data-key="inbox/verify.md"]')).to_be_visible()
    assert (harness.root / "inbox" / "verify.md").is_file()


@pytest.mark.asyncio
async def test_delete_and_sse_completion_orders_close_owned_dialogs_once(
    browser_harness: _BrowserHarness,
) -> None:
    """削除応答とSSEの到着順にかかわらず、親子を閉じて一覧へ戻す。

    応答より先に届いた更新通知は自分の削除によるものを含むため、応答まで詳細の再読込を保留し、
    応答の後に親子を閉じて削除の成功を通知する。
    """
    harness = browser_harness
    page = harness.page
    for filename in ("sse-first.md", "response-first.md"):
        (harness.root / "inbox" / filename).write_text(
            "---\ntype: awi\n---\n\n削除順序の検証\n",
            encoding="utf-8",
        )
    harness.operations.enable_file_mutations()
    await page.goto(harness.base_url + "/")

    detail = page.get_by_role("dialog", name="詳細")
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    sse_first = page.locator('.entry-select[data-key="inbox/sse-first.md"]')
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

    response_first = page.locator('.entry-select[data-key="inbox/response-first.md"]')
    await response_first.click()
    await detail.get_by_role("button", name="削除").click()
    await delete_dialog.get_by_role("button", name="削除する").click()
    await delete_dialog.wait_for(state="hidden")
    await detail.wait_for(state="hidden")
    harness.current_state.publish()
    await playwright.async_api.expect(page.locator("#entry-list .entry-select").first).to_be_focused()


@pytest.mark.asyncio
async def test_user_filter_announcement_survives_same_state_sse_repo_request(
    browser_harness: _BrowserHarness,
) -> None:
    """同一状態の後発SSE候補要求後も、エンドユーザーの件数通知を完了する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#result-status")).to_have_text("")
    await playwright.async_api.expect(page.locator('#target-filter option[value="example/repo"]')).to_have_count(1)
    await _open_filters(page)
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()
    request_count = 0

    async def delay_first_repo_request(route: playwright.async_api.Route) -> None:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            first_started.set()
            await release_first.wait()
        else:
            second_started.set()
        await route.continue_()

    await page.route("**/api/repos?status=active", delay_first_repo_request)
    await page.locator("#result-status").evaluate("element => { element.textContent = '変更前の通知'; }")
    await page.locator("#kind-filter").evaluate("element => { element.value = 'awi'; }")
    # 状態フィルター変更時の処理を起動し、対象リポジトリの再取得を伴う一覧の更新を実行する。
    await page.locator("#state-filter").dispatch_event("change")
    await asyncio.wait_for(first_started.wait(), timeout=5)
    try:
        harness.current_state.publish()
        await playwright.async_api.expect(page.locator("#entry-list .entry-select[data-kind=awi]")).to_have_count(2)
    finally:
        release_first.set()
    await playwright.async_api.expect(page.locator("#result-status")).to_have_text("2件を表示")
    await asyncio.wait_for(second_started.wait(), timeout=5)
    assert request_count == 2


@pytest.mark.asyncio
async def test_answer_and_delete_target_the_visible_state(browser_harness: _BrowserHarness) -> None:
    """同名項目が両状態にあっても、回答と削除は表示中の複合キーへ作用する。"""
    harness = browser_harness
    page = harness.page
    processing = harness.root / "processing"
    processing.mkdir(exist_ok=True)
    marker = "<!-- ユーザーはこの行以降に回答を追記する -->"
    uwi_content = (
        "---\ntype: uwi\ntarget_repo: example/repo\nquestion_type: free-form\n---\n\n"
        f"## 質問\n\n状態を指定しますか？\n\n## 回答\n\n{marker}\n"
    )
    awi_content = "---\ntype: awi\ntarget_repo: example/repo\n---\n\n状態付き削除\n"
    for state_name in ("inbox", "processing"):
        (harness.root / state_name / "answer-same.md").write_text(uwi_content, encoding="utf-8")
        (harness.root / state_name / "remove-same.md").write_text(awi_content, encoding="utf-8")
    harness.operations.enable_file_mutations()
    await page.goto(harness.base_url + "/")

    answer_row = page.locator('.entry-select[data-key="inbox/answer-same.md"]')
    await answer_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="回答", exact=True).click()
    await detail.locator("#answer-input").fill("未処理側だけへの回答")
    async with page.expect_request("**/api/entries/answer") as answer_request_info:
        await detail.get_by_role("button", name="回答を保存").click()
    answer_request = await answer_request_info.value
    answer_payload = answer_request.post_data_json
    assert isinstance(answer_payload, dict)
    assert answer_payload["state"] == "inbox"
    await page.get_by_role("status").filter(has_text="回答しました").wait_for(state="visible")
    assert (harness.root / "inbox/answer-same.md").read_text(encoding="utf-8").endswith("未処理側だけへの回答\n")
    assert (processing / "answer-same.md").read_text(encoding="utf-8") == uwi_content

    await page.keyboard.press("Escape")
    remove_row = page.locator('.entry-select[data-key="inbox/remove-same.md"]')
    await remove_row.click()
    await detail.get_by_role("button", name="削除").click()
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    async with page.expect_request("**/api/entries/remove") as remove_request_info:
        await delete_dialog.get_by_role("button", name="削除する").click()
    remove_request = await remove_request_info.value
    remove_payload = remove_request.post_data_json
    assert isinstance(remove_payload, dict)
    assert remove_payload["state"] == "inbox"
    assert remove_payload["expected_content"] == awi_content
    await delete_dialog.wait_for(state="hidden")
    assert not (harness.root / "inbox/remove-same.md").exists()
    assert (processing / "remove-same.md").read_text(encoding="utf-8") == awi_content


@pytest.mark.asyncio
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
        row_key: str,
        mutation_pattern: str,
        submit_name: str,
        input_selector: str,
        updated_content: str,
    ) -> None:
        await page.locator(f'.entry-select[data-key="{row_key}"]').click()
        mode_name = "回答" if input_selector == "#answer-input" else "編集"
        await detail.get_by_role("button", name=mode_name, exact=True).click()
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
        state_name, filename = row_key.split("/", maxsplit=1)
        (harness.root / state_name / filename).write_text(updated_content, encoding="utf-8")
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
        "inbox/awi.md",
        "**/api/entries/inbox/awi.md",
        "保存",
        "#edit-content",
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n保存中の外部更新\n",
    )
    await exercise(
        "inbox/question.md",
        "**/api/entries/answer",
        "回答を保存",
        "#answer-input",
        "---\ntype: uwi\ntarget_repo: example/repo\nquestion_type: free-form\n---\n\n"
        "## 質問\n\n回答中の外部更新ですか？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n",
    )


@pytest.mark.asyncio
async def test_answer_rebase_in_progress_shows_recovery(
    browser_harness: _BrowserHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rebaseの中間状態が残る間の回答は理由と解消操作を示して入力を残し、解消後の再実行で保存できる。

    拒否を`edit_conflict`と同じ応答にすると、画面が詳細の開き直しを促し、解消操作へたどり着けない。
    """
    harness = browser_harness
    page = harness.page
    harness.operations.enable_file_mutations()
    rebasing = [True]
    answer_uwi = harness.operations.answer_uwi

    def answer_unless_rebasing(*args: Any, **kwargs: Any) -> bool:
        if rebasing[0]:
            raise git_sync.RebaseInProgressError("rebase中のため新しい更新を開始できない")
        return answer_uwi(*args, **kwargs)

    monkeypatch.setattr(harness.operations, "answer_uwi", answer_unless_rebasing)
    await page.goto(harness.base_url + "/")
    detail = page.get_by_role("dialog", name="詳細")
    await page.locator('.entry-select[data-key="inbox/question.md"]').click()
    await detail.get_by_role("button", name="回答", exact=True).click()
    field = detail.locator("#answer-input")
    await field.fill("解消後に保存する回答")

    await detail.get_by_role("button", name="回答を保存", exact=True).click()

    alert = detail.get_by_role("alert").filter(has_text="rebase中")
    await alert.wait_for(state="visible")
    alert_text = await alert.inner_text()
    assert "git rebase --continue" in alert_text
    assert "git rebase --abort" in alert_text
    assert "開き直して" not in alert_text
    assert await field.input_value() == "解消後に保存する回答"

    rebasing[0] = False
    await detail.get_by_role("button", name="回答を保存", exact=True).click()
    await page.get_by_role("status").filter(has_text="回答しました").wait_for(state="visible")
    assert "解消後に保存する回答" in (harness.root / "inbox" / "question.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_delete_confirmation_closes_on_detail_failure_and_content_conflict(
    browser_harness: _BrowserHarness,
) -> None:
    """一覧後の詳細失敗と確認後の内容競合では、古い削除確認を閉じる。"""
    harness = browser_harness
    page = harness.page
    harness.operations.enable_file_mutations()
    failure_path = harness.root / "inbox/dialog-failure.md"
    conflict_path = harness.root / "inbox/delete-conflict.md"
    original = "---\ntype: awi\ntarget_repo: example/old\n---\n\n変更前の要約\n"
    failure_path.write_text(original, encoding="utf-8")
    conflict_path.write_text(original, encoding="utf-8")
    await page.goto(harness.base_url + "/")
    detail = page.get_by_role("dialog", name="詳細")
    delete_dialog = page.get_by_role("dialog", name="削除の確認")

    await page.locator('.entry-select[data-key="inbox/dialog-failure.md"]').click()
    await detail.get_by_role("button", name="削除").click()

    async def fail_detail(route: playwright.async_api.Route) -> None:
        await route.fulfill(
            status=500,
            content_type="application/json",
            body='{"error":"詳細取得に失敗しました"}',
        )

    await page.route("**/api/entries/inbox/dialog-failure.md", fail_detail)
    failure_path.write_text(
        "---\ntype: awi\ntarget_repo: example/new\n---\n\n変更後の要約\n",
        encoding="utf-8",
    )
    harness.current_state.publish()
    await delete_dialog.wait_for(state="hidden")
    await detail.get_by_role("alert").filter(has_text="詳細取得に失敗しました").wait_for(state="visible")
    assert "削除操作をやり直してください" in await detail.locator("#detail-alert").inner_text()
    await playwright.async_api.expect(detail.locator("#detail-dialog-body")).to_be_focused()
    await page.unroute("**/api/entries/inbox/dialog-failure.md", fail_detail)
    await page.keyboard.press("Escape")

    await page.locator('.entry-select[data-key="inbox/delete-conflict.md"]').click()
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


@pytest.mark.asyncio
async def test_create_dialog_supports_batch_import_and_omitted_target_repo(
    browser_harness: _BrowserHarness,
) -> None:
    """一括登録種別の入力切替と取り込み、frontmatter指定による対象リポジトリ省略投入を検証する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")

    await page.get_by_role("button", name="新規追加").click()
    create_dialog = page.get_by_role("dialog", name="新規追加")
    await create_dialog.wait_for(state="visible")
    await create_dialog.locator("#create-kind").select_option("batch")
    await playwright.async_api.expect(create_dialog.locator("#create-repo-fields")).to_be_hidden()
    await playwright.async_api.expect(create_dialog.locator("#create-content-label")).to_have_text("show形式テキスト（必須）")
    batch_text = (
        "# awi\n## target_repo: batch/repo\n"
        "### imported.md [inbox]\n---\ntarget_repo: batch/repo\ntype: awi\n---\n\n一括取り込みの本文\n\n"
    )
    await create_dialog.locator("#create-content").fill(batch_text)
    async with page.expect_request("**/api/entries/batch") as batch_request_info:
        await create_dialog.get_by_role("button", name="追加").click()
    batch_request = await batch_request_info.value
    assert batch_request.post_data_json == {"text": batch_text}
    await create_dialog.wait_for(state="hidden")
    await page.get_by_role("status").filter(has_text="1件を取り込みました").wait_for(state="visible")
    await page.locator('.entry-select[data-key="inbox/imported.md"]').wait_for(state="visible")
    assert (harness.root / "inbox" / "imported.md").read_text(encoding="utf-8") == (
        "---\ntarget_repo: batch/repo\ntype: awi\n---\n\n一括取り込みの本文\n"
    )

    await page.get_by_role("button", name="新規追加").click()
    await create_dialog.wait_for(state="visible")
    await playwright.async_api.expect(create_dialog.locator("#create-repo-fields")).to_be_visible()
    await playwright.async_api.expect(create_dialog.locator("#create-target")).to_have_value("")
    await create_dialog.locator("#create-content").fill("---\ntarget_repo: frontmatter/repo\n---\n\nfrontmatter指定の本文")
    async with page.expect_request(lambda request: request.url.endswith("/api/entries") and request.method == "POST"):
        await create_dialog.get_by_role("button", name="追加").click()
    await create_dialog.wait_for(state="hidden")
    assert harness.operations.add_calls[-1]["target_repo"] is None
    detail = page.get_by_role("dialog", name="詳細")
    await playwright.async_api.expect(detail).to_be_hidden()
    await page.locator("#entry-list .entry-select").filter(has_text="frontmatter指定の本文").wait_for(state="visible")


@pytest.mark.asyncio
async def test_create_dialog_auto_switches_show_format_to_batch(browser_harness: _BrowserHarness) -> None:
    """種別uwiのままshow形式の本文を送ると一括登録として取り込み、使わなかった入力欄を通知する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")

    await page.get_by_role("button", name="新規追加").click()
    create_dialog = page.get_by_role("dialog", name="新規追加")
    await create_dialog.wait_for(state="visible")
    await create_dialog.locator("#create-kind").select_option("uwi")
    # UWIの回答形式ははい／いいえと選択肢形式だけを選べる。初期値は選択肢の入力が要らないはい／いいえとする。
    question_type = create_dialog.locator("#create-question-type")
    await playwright.async_api.expect(question_type).to_have_value("yes-no")
    assert await question_type.locator("option").evaluate_all("options => options.map(option => option.value)") == [
        "yes-no",
        "choice",
    ]
    await question_type.select_option("choice")
    await playwright.async_api.expect(create_dialog.locator("#choice-fields")).to_be_visible()
    await question_type.select_option("yes-no")
    await create_dialog.locator("#create-target").fill("ignored/repo")
    show_text = (
        "## target_repo: batch/repo\n"
        "### auto-imported.md [inbox]\n---\ntarget_repo: batch/repo\ntype: awi\n---\n\n自動切替の本文  \n"
    )
    await create_dialog.locator("#create-content").fill(show_text)
    async with page.expect_request(
        lambda request: request.url.endswith("/api/entries") and request.method == "POST"
    ) as request_info:
        await create_dialog.get_by_role("button", name="追加").click()
    request = await request_info.value
    request_body = request.post_data_json
    assert isinstance(request_body, dict)
    assert request_body["raw_text"] == show_text
    await create_dialog.wait_for(state="hidden")
    notice = page.get_by_role("status").filter(has_text="一括登録として取り込みました")
    await notice.wait_for(state="visible")
    # 使わなかった入力欄はユーザーが入力したtarget-repoだけであり、初期値へ戻した回答形式は数えない。
    # 列挙の末尾まで一致を求め、回答形式などの余分な入力欄が後ろへ連結された通知を検出する。
    await playwright.async_api.expect(page.locator("#operation-notice-message")).to_have_text(
        re.compile(r"1件を取り込みました。.*使わなかった入力欄: target-repo$")
    )
    await page.locator('.entry-select[data-key="inbox/auto-imported.md"]').wait_for(state="visible")
    assert harness.operations.batch_calls[-1] == show_text
    assert (harness.root / "inbox" / "auto-imported.md").read_text(encoding="utf-8") == (
        "---\ntarget_repo: batch/repo\ntype: awi\n---\n\n自動切替の本文  \n"
    )


@pytest.mark.asyncio
async def test_create_failure_is_visible_inside_open_dialog(browser_harness: _BrowserHarness) -> None:
    """新規追加の失敗は開いたダイアログ内へ表示し、ページ通知を表示しない。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")

    async def fail_create(route: playwright.async_api.Route) -> None:
        await route.fulfill(status=500, json={"error": "追加処理に失敗しました"})

    await page.route("**/api/entries", fail_create)
    await page.get_by_role("button", name="新規追加").click()
    create_dialog = page.get_by_role("dialog", name="新規追加")
    await create_dialog.locator("#create-content").fill("新規本文")
    await create_dialog.locator("#create-target").fill("example/repo")
    await create_dialog.get_by_role("button", name="追加").click()

    await playwright.async_api.expect(create_dialog.locator("#create-alert")).to_contain_text("追加処理に失敗しました")
    await playwright.async_api.expect(create_dialog.locator("#create-alert")).to_be_visible()
    await playwright.async_api.expect(page.locator("#operation-notice")).to_be_hidden()
    await page.unroute("**/api/entries", fail_create)


@pytest.mark.asyncio
async def test_save_failure_message_stays_at_scrolled_dialog_top(browser_harness: _BrowserHarness) -> None:
    """本文末尾までスクロールした保存失敗でも結果をダイアログ本文の上端へ留める。"""
    harness = browser_harness
    path = harness.root / "inbox" / "long-entry.md"
    path.write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n" + "長い本文\n\n" * 120,
        encoding="utf-8",
    )
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator('.entry-select[data-key="inbox/long-entry.md"]').click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="編集", exact=True).click()
    body = detail.locator(".dialog-body")
    await body.evaluate("element => { element.scrollTop = element.scrollHeight; }")

    async def fail_save(route: playwright.async_api.Route) -> None:
        await route.fulfill(status=500, json={"error": "保存処理に失敗しました"})

    await page.route("**/api/entries/inbox/long-entry.md", fail_save)
    await detail.get_by_role("button", name="保存", exact=True).click()
    alert = detail.locator("#detail-alert")
    await playwright.async_api.expect(alert).to_contain_text("保存処理に失敗しました")
    await playwright.async_api.expect(detail.locator("#edit-content")).to_be_focused()
    metrics = await body.evaluate(
        """element => {
          const bodyRect = element.getBoundingClientRect();
          const alertRect = document.getElementById('detail-alert').getBoundingClientRect();
          return {
            bodyTop: bodyRect.top,
            bodyBottom: bodyRect.bottom,
            alertTop: alertRect.top,
            alertBottom: alertRect.bottom,
            paddingTop: Number.parseFloat(getComputedStyle(element).paddingTop)
          };
        }"""
    )
    assert metrics["bodyTop"] <= metrics["alertTop"] <= metrics["bodyTop"] + metrics["paddingTop"] + 1
    assert metrics["alertBottom"] <= metrics["bodyBottom"]
    await playwright.async_api.expect(page.locator("#operation-notice")).to_be_hidden()
    await page.unroute("**/api/entries/inbox/long-entry.md", fail_save)


@pytest.mark.asyncio
async def test_user_comment_ui_empty_save_removes_section(browser_harness: _BrowserHarness) -> None:
    """コメント欄を空にして保存すると節が消え、節が無い状態の空保存も成功として伝える。"""
    harness = browser_harness
    page = harness.page
    path = harness.root / "inbox" / "awi.md"
    kept = "---\ntype: awi\ntarget_repo: example/repo\nsource: session-review\n---\n\n通常本文\n"
    path.write_text(kept + "\n## ユーザーコメント\n\n削除対象のコメント\n", encoding="utf-8")
    await page.goto(harness.base_url + "/")
    detail = page.get_by_role("dialog", name="詳細")

    await page.locator('.entry-select[data-key="inbox/awi.md"]').click()
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

    await page.locator('.entry-select[data-key="inbox/awi.md"]').click()
    await detail.wait_for(state="visible")
    await playwright.async_api.expect(detail).not_to_contain_text("削除対象のコメント")
    await detail.get_by_role("button", name="ユーザーコメント", exact=True).click()
    await playwright.async_api.expect(comment_input).to_have_value("")
    await detail.get_by_role("button", name="コメントを保存").click()
    await _wait_and_close_operation_notice(page, "ユーザーコメントが無いため、変更していません")
    assert path.read_text(encoding="utf-8") == kept


@pytest.mark.asyncio
async def test_user_comment_ui_appends_replaces_and_recovers_from_external_updates(
    browser_harness: _BrowserHarness,
) -> None:
    """エージェント由来UIの追記・置換・pending・SSE・競合復旧を実ブラウザーで検証する。"""
    harness = browser_harness
    page = harness.page
    path = harness.root / "inbox" / "awi.md"
    original = "---\ntype: awi\ntarget_repo: example/repo\nsource: session-review\n---\n\n通常本文\n"
    path.write_text(original, encoding="utf-8")
    await page.goto(harness.base_url + "/")
    detail = page.get_by_role("dialog", name="詳細")

    await page.locator('.entry-select[data-key="inbox/empty.md"]').click()
    await playwright.async_api.expect(detail).to_be_visible()
    await playwright.async_api.expect(detail.locator("#user-comment-button")).to_be_visible()
    await page.keyboard.press("Escape")
    await playwright.async_api.expect(detail).to_be_hidden()

    await page.locator('.entry-select[data-key="inbox/awi.md"]').click()
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

    await page.locator('.entry-select[data-key="inbox/awi.md"]').click()
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
    release_task = asyncio.create_task(_release_save_after_delay(harness.operations.user_comment_release))
    await _wait_and_close_operation_notice(page, "ユーザーコメントを保存しました")
    await release_task
    await playwright.async_api.expect(detail).to_be_hidden()
    saved = path.read_text(encoding="utf-8")
    assert "置換後のコメント" in saved
    assert "最初のコメント" not in saved

    await page.locator('.entry-select[data-key="inbox/awi.md"]').click()
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
    release_task = asyncio.create_task(_release_save_after_delay(harness.operations.user_comment_release))
    await _wait_and_close_operation_notice(page, "ユーザーコメントを保存しました")
    await release_task
    await playwright.async_api.expect(detail).to_be_hidden()
    assert "競合後も保持する入力" in path.read_text(encoding="utf-8")
    assert harness.operations.user_comment_calls == 4


@pytest.mark.asyncio
async def test_user_comment_ui_keeps_input_when_sse_moves_entry_to_processing(
    browser_harness: _BrowserHarness,
) -> None:
    """processingへのSSE移動後も入力へ到達でき、保存だけを無効にする。"""
    harness = browser_harness
    page = harness.page
    path = harness.root / "inbox" / "awi.md"
    path.write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: session-review\n---\n\n通常本文\n",
        encoding="utf-8",
    )
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    detail = page.get_by_role("dialog", name="詳細")
    await page.locator('.entry-select[data-key="inbox/awi.md"]').click()
    await detail.get_by_role("button", name="ユーザーコメント", exact=True).click()
    comment_input = detail.locator("#user-comment-input")
    await comment_input.fill("processing移動後も保持する入力")

    processing = harness.root / "processing"
    processing.mkdir(exist_ok=True)
    path.replace(processing / path.name)
    harness.current_state.publish()

    await detail.get_by_role("alert").filter(has_text="processingへ移動したため").wait_for(state="visible")
    await playwright.async_api.expect(detail).to_be_visible()
    await playwright.async_api.expect(comment_input).to_have_value("processing移動後も保持する入力")
    await playwright.async_api.expect(comment_input).to_be_focused()
    await playwright.async_api.expect(detail.locator("#save-user-comment-button")).to_be_disabled()


@pytest.mark.asyncio
@pytest.mark.parametrize("external_change", ["delete", "move"])
async def test_selecting_deleted_entry_refreshes_list_without_error(
    browser_harness: _BrowserHarness,
    external_change: str,
) -> None:
    """一覧表示後に外部で削除・移動された行を選んでも、エラーを表示せず最新状態を示して一覧を更新する。"""
    harness = browser_harness
    page = harness.page
    path = harness.root / "inbox" / "stale.md"
    path.write_text("---\ntype: awi\ntarget_repo: example/repo\n---\n\n外部で変わる本文\n", encoding="utf-8")
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    stale_row = page.locator('.entry-select[data-key="inbox/stale.md"]')
    await stale_row.wait_for(state="visible")

    # SSEの変更通知を発行せず、一覧が古いままの状態で行を選ぶ。
    if external_change == "delete":
        path.unlink()
    else:
        processing = harness.root / "processing"
        processing.mkdir(exist_ok=True)
        path.replace(processing / path.name)
    await stale_row.click()

    notice = page.locator("#operation-notice")
    detail = page.get_by_role("dialog", name="詳細")
    if external_change == "delete":
        await playwright.async_api.expect(page.locator("#operation-notice-message")).to_have_text(
            "stale.mdは削除されたため表示できません。一覧を更新しました。"
        )
        await playwright.async_api.expect(notice).to_have_attribute("data-error", "false")
        await playwright.async_api.expect(detail).to_be_hidden()
        await playwright.async_api.expect(page.locator('.entry-select[data-key$="/stale.md"]')).to_have_count(0)
    else:
        await playwright.async_api.expect(detail).to_be_visible()
        await playwright.async_api.expect(detail.locator("#detail-state")).to_have_attribute("data-state", "processing")
        await playwright.async_api.expect(notice).to_be_hidden()
        await playwright.async_api.expect(page.locator('.entry-select[data-key="inbox/stale.md"]')).to_have_count(0)


@pytest.mark.asyncio
async def test_work_item_rows_keep_fixed_columns_and_equal_heights(screen_harness: _ScreenHarness) -> None:
    """最長の状態表示を含む一覧でも固定列を折り返さず、全行を同じ高さで描画する。"""
    processing = screen_harness.plan_path.parent.parent / "processing"
    processing.mkdir()
    # 期間の初期値（直近2週間）の内側に入るよう、作成日時を現在に近づける。
    (processing / f"{datetime.datetime.now():%Y%m%d}-123456-001.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/long-repository\nsource: human\n"
        "plan_file: /tmp/plan.md\n---\n\n長い要約を持つ確認事項 "
        + "要約" * 80
        + "\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    page = screen_harness.page
    await page.set_viewport_size({"width": 1600, "height": 800})
    await page.goto(screen_harness.base_url + "/")
    rows = page.locator("#entry-list .entry-row")
    await playwright.async_api.expect(rows).to_have_count(5)

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


@pytest.mark.asyncio
async def test_entry_copy_button_copies_filename_and_summary_without_selecting(
    screen_harness: _ScreenHarness,
) -> None:
    """一覧のコピー操作はファイル名と要約を写し、詳細選択を発生させない。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    row = page.locator("#entry-list .entry-row").first
    await row.locator(".entry-copy").wait_for(state="visible")
    filename = await row.locator(".filename-cell").inner_text()
    summary = await row.locator(".summary-cell").inner_text()

    await row.locator(".entry-copy").click()

    assert await page.evaluate("() => navigator.clipboard.readText()") == f"{filename} {summary}"
    await playwright.async_api.expect(row.locator(".entry-copy")).to_have_text("コピーしました")
    await playwright.async_api.expect(page.locator("#result-status")).to_contain_text("コピーしました")
    await playwright.async_api.expect(row.locator(".entry-copy")).to_have_text("コピー", timeout=4000)
    assert await page.locator("#detail-dialog").evaluate("element => element.open") is False


@pytest.mark.asyncio
async def test_entry_copy_label_survives_list_reload(screen_harness: _ScreenHarness) -> None:
    """コピー表示の期間中に一覧の再読込が完了しても、同じ項目のボタンは表示を保ち、期間の終わりに戻る。

    再読込はエンドユーザーの操作と無関係な契機（ウィンドウのfocus、SSEの変更通知）でも起きるため、
    一覧取得の応答を保留してクリックを再描画より先に起こし、再描画後の新しいボタンの表示を確かめる。
    """
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    row = page.locator("#entry-list .entry-row").first
    await row.locator(".entry-copy").wait_for(state="visible")
    release = asyncio.Event()

    async def hold_list_request(route: playwright.async_api.Route) -> None:
        await release.wait()
        await route.continue_()

    await page.route("**/api/entries?*", hold_list_request)
    await row.locator(".entry-copy").evaluate("element => { element.dataset.beforeReload = 'true'; }")
    await page.evaluate("() => window.dispatchEvent(new Event('focus'))")
    await row.locator(".entry-copy").click()
    await playwright.async_api.expect(row.locator(".entry-copy")).to_have_text("コピーしました")

    release.set()
    await playwright.async_api.expect(page.locator("#entry-list .entry-copy[data-before-reload]")).to_have_count(0)
    await playwright.async_api.expect(row.locator(".entry-copy")).to_have_text("コピーしました")
    await playwright.async_api.expect(row.locator(".entry-copy")).to_have_text("コピー", timeout=4000)
    await page.unroute("**/api/entries?*", hold_list_request)


@pytest.mark.asyncio
async def test_work_item_filename_link_supports_get_and_shift_click(screen_harness: _ScreenHarness) -> None:
    """WI名は詳細を復元するGET URLを持ち、Shiftクリックをブラウザーへ委ねる。"""
    harness = screen_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    link = page.locator('.entry-select[data-key="inbox/awi.md"]')
    await link.wait_for(state="visible")
    href = await link.get_attribute("href")
    assert href == "/?state=inbox&filename=awi.md"

    await link.click()
    await page.get_by_role("dialog", name="詳細").wait_for(state="visible")
    assert urllib.parse.urlsplit(page.url).query == "state=inbox&filename=awi.md"

    await page.go_back()
    await playwright.async_api.expect(page.get_by_role("dialog", name="詳細")).to_be_hidden()
    await page.go_forward()
    await page.get_by_role("dialog", name="詳細").wait_for(state="visible")
    assert await page.locator("#detail-filename").inner_text() == "awi.md"
    await page.keyboard.press("Escape")

    assert not await _shift_click_default_prevented(link)


@pytest.mark.asyncio
async def test_initial_sync_shows_work_item_loading(browser_harness: _BrowserHarness) -> None:
    """初回表示はGit同期を待たず、一覧取得中だけナビゲーションへ表示する。"""
    page = browser_harness.page
    entries_started = asyncio.Event()
    release_entries = asyncio.Event()
    sync_calls: list[str] = []

    async def track_sync(route: playwright.async_api.Route) -> None:
        sync_calls.append(route.request.url)
        await route.continue_()

    await page.route("**/api/sync", track_sync)
    await page.route("**/api/entries?*", functools.partial(_hold_route, started=entries_started, release=release_entries))
    try:
        await page.goto(browser_harness.base_url + "/")
        await asyncio.wait_for(entries_started.wait(), timeout=5)
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
    finally:
        release_entries.set()
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    assert not sync_calls


@pytest.mark.asyncio
async def test_work_item_focus_recovers_change_without_sse_event(browser_harness: _BrowserHarness) -> None:
    """SSE通知を受信できなかった場合も、フォーカス復帰時に一覧を再取得する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator('.entry-select[data-key="inbox/awi.md"]').wait_for(state="visible")
    (harness.root / "inbox" / "missed.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n切断中の変更\n", encoding="utf-8"
    )
    await page.evaluate("window.dispatchEvent(new Event('focus'))")
    await page.locator('.entry-select[data-key="inbox/missed.md"]').wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#connection-status")).to_be_hidden()


@pytest.mark.asyncio
async def test_background_sync_failure_offers_retry_in_browser(browser_harness: _BrowserHarness) -> None:
    """定期同期の失敗を通知し、次の成功時に案内を閉じる。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator('.entry-select[data-key="inbox/awi.md"]').wait_for(state="visible")
    harness.current_state.publish("sync-error")
    status = page.locator("#connection-status")
    await playwright.async_api.expect(status).to_contain_text("今すぐ同期")
    await playwright.async_api.expect(status).to_be_visible()
    harness.current_state.publish("sync-ok")
    await playwright.async_api.expect(status).to_be_hidden()


@pytest.mark.asyncio
async def test_external_work_item_refresh_keeps_loading_hidden(browser_harness: _BrowserHarness) -> None:
    """SSEによる背景更新は既存一覧を保ち、読み込み表示を点滅させない。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await page.evaluate(
        """() => {
          window.loadingShown = 0;
          const indicator = document.getElementById('loading-indicator');
          new MutationObserver(() => { if (!indicator.hidden) window.loadingShown += 1; })
            .observe(indicator, {attributes: true, attributeFilter: ['hidden']});
        }"""
    )
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()

    async def delay_background_entries(route: playwright.async_api.Route) -> None:
        if "status=active" in route.request.url:
            fetch_started.set()
            await release_fetch.wait()
        await route.continue_()

    await page.route("**/api/entries?*", delay_background_entries)
    (harness.root / "inbox" / "background.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n背景更新\n",
        encoding="utf-8",
    )
    harness.current_state.publish()
    try:
        await asyncio.wait_for(fetch_started.wait(), timeout=5)
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
        await playwright.async_api.expect(page.locator('.entry-select[data-key="inbox/awi.md"]')).to_be_visible()
    finally:
        release_fetch.set()
    await page.locator('.entry-select[data-key="inbox/background.md"]').wait_for(state="visible")
    assert await page.evaluate("window.loadingShown") == 0


@pytest.mark.asyncio
async def test_manual_sync_refreshes_work_items_without_page_reload(browser_harness: _BrowserHarness) -> None:
    """同期後の一覧取得で新着がページ再読込なしに現れる。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    navigation_count = await page.evaluate("performance.getEntriesByType('navigation').length")
    (browser_harness.root / "inbox" / "new-item.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: browser\n---\n\n新着の本文\n",
        encoding="utf-8",
    )

    await page.locator("#refresh-button").click()

    await page.locator('.entry-select[data-key="inbox/new-item.md"]').wait_for(state="visible")
    assert await page.evaluate("performance.getEntriesByType('navigation').length") == navigation_count


@pytest.mark.asyncio
async def test_manual_sync_stays_busy_until_entries_render(browser_harness: _BrowserHarness) -> None:
    """一覧の新着が描画されるまで同期ボタンと結果表示を完了させない。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    (browser_harness.root / "inbox" / "delayed-sync.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n遅延した同期結果\n",
        encoding="utf-8",
    )
    entries_started = asyncio.Event()
    release_entries = asyncio.Event()

    await page.route("**/api/entries?*", functools.partial(_hold_route, started=entries_started, release=release_entries))
    try:
        await page.locator("#refresh-button").click()
        await asyncio.wait_for(entries_started.wait(), timeout=5)
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
        await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "true")
        await playwright.async_api.expect(page.locator("#refresh-button")).to_be_disabled()
        await playwright.async_api.expect(page.locator("#operation-notice")).to_be_hidden()
        await playwright.async_api.expect(page.locator('.entry-select[data-key="inbox/delayed-sync.md"]')).to_have_count(0)
    finally:
        release_entries.set()
    await playwright.async_api.expect(page.locator('.entry-select[data-key="inbox/delayed-sync.md"]')).to_be_visible()
    await playwright.async_api.expect(page.locator("#operation-notice")).to_be_hidden()
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "false")
    await playwright.async_api.expect(page.locator("#refresh-button")).to_be_enabled()


@pytest.mark.asyncio
async def test_clear_filters_stays_busy_through_repos_and_entries(browser_harness: _BrowserHarness) -> None:
    """条件クリア後の候補取得と一覧取得の両方を処理中として表示する。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await _open_filters(page)
    repos_started = asyncio.Event()
    release_repos = asyncio.Event()
    entries_started = asyncio.Event()
    release_entries = asyncio.Event()

    await page.route("**/api/repos?*", functools.partial(_hold_route, started=repos_started, release=release_repos))
    await page.route("**/api/entries?*", functools.partial(_hold_route, started=entries_started, release=release_entries))
    try:
        await page.locator("#clear-filters-button").click()
        await asyncio.wait_for(repos_started.wait(), timeout=5)
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
        await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "true")
        release_repos.set()
        await asyncio.wait_for(entries_started.wait(), timeout=5)
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
        await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "true")
    finally:
        release_repos.set()
        release_entries.set()
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "false")


@pytest.mark.asyncio
async def test_search_stays_busy_during_debounce_and_entries(browser_harness: _BrowserHarness) -> None:
    """検索入力直後から遅延取得の描画完了まで処理中を保つ。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    entries_started = asyncio.Event()
    release_entries = asyncio.Event()

    await page.route("**/api/entries?*", functools.partial(_hold_route, started=entries_started, release=release_entries))
    try:
        await page.locator("#search-input").fill("awi")
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
        await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "true")
        await asyncio.wait_for(entries_started.wait(), timeout=5)
        browser_harness.current_state.publish()
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
        await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "true")
    finally:
        release_entries.set()
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "false")


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_changed_detail_inputs_confirm_discard_and_unchanged_input_closes(browser_harness: _BrowserHarness) -> None:
    """変更済みの編集・回答・コメント入力を閉じる際は破棄確認を表示し、取消時に入力を保持する。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    detail = page.get_by_role("dialog", name="詳細")

    await page.locator('.entry-select[data-key="inbox/awi.md"]').click()
    await detail.get_by_role("button", name="編集", exact=True).click()
    await page.keyboard.press("Escape")
    await playwright.async_api.expect(detail).to_be_hidden()

    for key, mode, field_id in (
        ("inbox/awi.md", "編集", "edit-content"),
        ("inbox/question.md", "回答", "answer-input"),
        ("inbox/awi.md", "ユーザーコメント", "user-comment-input"),
    ):
        await page.locator(f'.entry-select[data-key="{key}"]').click()
        await detail.get_by_role("button", name=mode, exact=True).click()
        field = detail.locator(f"#{field_id}")
        await field.fill((await field.input_value()) + "変更")
        async with page.expect_event("dialog") as confirmation:
            press = asyncio.create_task(page.keyboard.press("Escape"))
        prompt = await confirmation.value
        assert prompt.message == "変更した入力を破棄しますか？"
        await prompt.dismiss()
        await press
        await playwright.async_api.expect(detail).to_be_visible()
        assert (await field.input_value()).endswith("変更")
        async with page.expect_event("dialog") as confirmation:
            click = asyncio.create_task(detail.locator("#detail-close-button").click())
        await (await confirmation.value).accept()
        await click
        await playwright.async_api.expect(detail).to_be_hidden()

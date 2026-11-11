"""serveのbrowser_wiに関するtimingsの試験。"""

import json

import playwright.async_api
import pytest

from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _browser_harness_fixture,
    _BrowserHarness,
    _observe_screen_requests,
    _screen_harness_fixture,
    _wait_screen_requests_consumed,
)
from agent_toolkit._testing.serve_browser_wi_support import _POST_APPROVAL_UWI, _entry_path

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
async def test_answer_save_records_whole_operation_linked_to_requests(browser_harness: _BrowserHarness) -> None:
    """回答の保存は、押下から詳細を閉じた後の一覧の再描画までを1件の操作として記録し、操作内の取得と識別子で対応付ける。

    取得単位の記録だけでは、保存後の表示更新までに掛かった時間を追跡できない。
    """
    harness = browser_harness
    page = harness.page
    harness.operations.enable_file_mutations()
    await page.add_init_script("Object.defineProperty(navigator, 'sendBeacon', {value: undefined})")
    # 記録は件数・定期送信・`pagehide`のいずれかで分割して送られるため、全ての送信を集めて検査する。
    sent: list[dict] = []
    page.on(
        "request",
        lambda request: (
            sent.extend(json.loads(request.post_data or "{}").get("records", []))
            if request.url.endswith("/api/timings")
            else None
        ),
    )
    path = _entry_path(harness.root, "target.md")
    path.write_text(_POST_APPROVAL_UWI, encoding="utf-8")
    await page.goto(f"{harness.base_url}/?filename=target.md")
    detail = page.get_by_role("dialog", name="詳細")
    await detail.wait_for(state="visible")
    footer = detail.locator("#detail-footer")
    await footer.locator("#answer-choices button").first.click()
    await footer.locator("#save-answer-button").click()
    await detail.wait_for(state="hidden")
    async with page.expect_request(
        lambda request: request.url.endswith("/api/timings") and '"mutation:answer"' in (request.post_data or ""),
        timeout=30000,
    ):
        await page.evaluate("() => window.dispatchEvent(new Event('pagehide'))")
    records = list(sent)
    operation = next(record for record in records if record.get("operation") == "mutation:answer")
    assert (operation["kind"], operation["outcome"], operation["trigger"]) == ("operation", "ok", "click")
    linked = [
        record for record in records if record.get("operation_id") == operation["operation_id"] and record is not operation
    ]
    routes = {record.get("route") for record in linked}
    assert {"/api/entries/answer", "/api/entries"} <= routes
    assert all(isinstance(record.get("request_id"), str) and record["request_id"] for record in linked)
    saved = next(record for record in linked if record.get("route") == "/api/entries/answer")
    assert operation["total_ms"] >= saved["fetch_ms"]


@pytest.mark.asyncio(loop_scope="session")
async def test_wi_screen_sends_operation_timings_with_request_ids(browser_harness: _BrowserHarness) -> None:
    """一覧の取得と描画の所要を、サーバーの記録と同じ要求IDで送る。URLのqueryは送らない。"""
    page = browser_harness.page
    # `sendBeacon`のBlob本文はPlaywrightの要求から読めないため、同じ本文を`fetch`で送らせて検査する。
    await page.add_init_script("Object.defineProperty(navigator, 'sendBeacon', {value: undefined})")
    await page.goto(browser_harness.base_url + "/")
    # 一覧の記録は描画完了の次のフレームで積まれ、`pagehide`の送信に間に合わない場合は定期送信で届く。
    # 一覧の記録を含む送信を待ち、`pagehide`と定期送信のどちらで届いても同じ内容を検査する。
    async with page.expect_request(
        lambda request: request.url.endswith("/api/timings") and '"/api/entries"' in (request.post_data or ""),
        timeout=30000,
    ) as request_info:
        await page.evaluate("() => window.dispatchEvent(new Event('pagehide'))")
    request = await request_info.value
    body = request.post_data or ""
    records = json.loads(body)["records"]
    listed = [record for record in records if record.get("route") == "/api/entries"]
    assert listed
    assert all(isinstance(record.get("request_id"), str) and record["request_id"] for record in listed)
    assert all(record["fetch_ms"] >= 0 and record["render_ms"] >= 0 for record in listed)
    assert all("?" not in record.get("route", "") for record in records)


@pytest.mark.asyncio(loop_scope="session")
async def test_initial_load_fetches_list_once_after_subscription(browser_harness: _BrowserHarness) -> None:
    """購読の登録後に初期取得するため、最初の接続では一覧を読み直さず、登録後の変更は通知で反映する。"""
    harness = browser_harness
    page = harness.page
    await _observe_screen_requests(page)
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await _wait_screen_requests_consumed(page, ("/api/events",))
    # 新着通知の基準を取る`refreshKnownEntries`の取得（page指定なし）は数えず、一覧の取得だけを数える。
    list_fetches = await page.evaluate(
        "Object.entries(window.__screenRequests.completedByUrl)"
        ".filter(([url]) => new URL(url).pathname === '/api/entries' && new URL(url).searchParams.has('page'))"
        ".reduce((total, [, count]) => total + count, 0)"
    )
    assert list_fetches == 1
    _entry_path(harness.root, "after-subscription.md").write_text(
        "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\nsource: browser\n---\n\n購読後に追加した項目\n",
        encoding="utf-8",
    )
    harness.current_state.publish()
    await page.locator("#entry-list .entry-select").filter(has_text="after-subscription.md").wait_for(state="visible")

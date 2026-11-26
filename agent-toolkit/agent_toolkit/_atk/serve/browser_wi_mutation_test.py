import asyncio
from typing import Any

import playwright.async_api
import pytest

from agent_toolkit._atk import git_sync
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _browser_harness_fixture,
    _BrowserHarness,
    _open_filters,
    _screen_harness_fixture,
)
from agent_toolkit._atk.wi import entries as wi_entries
from agent_toolkit._testing.serve_browser_wi_support import (
    _DETAIL_MUTATION_CASES,
    _FREE_FORM_UWI,
    _assert_detail_notice_and_controls_reachable,
    _entry_path,
    _record_self_write_warnings,
    _saved_mutation_commit_failure,
    _state_text,
    _wait_for_detail_reload_deferred,
)

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.cpu_limit(
    1.5,
    reason=(
        "実ファイル監視による再取得の前後で、保存済み・未反映と復旧手順を保つ。実ブラウザーの観測とtra"
        "ce回収が必要（CPU最大実測0.509秒）。"
    ),
)
@pytest.mark.parametrize("timing", ["natural", "during"])
@pytest.mark.parametrize(
    "operation", ["save", "answer", "user-comment", "important", "adopt", "hold", "delete", "create", "batch"]
)
@pytest.mark.asyncio(loop_scope="session")
async def test_saved_mutation_commit_failure_with_watcher(
    browser_harness: _BrowserHarness,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    operation: str,
    timing: str,
) -> None:
    await _saved_mutation_commit_failure(browser_harness, monkeypatch, request, operation, timing)


@pytest.mark.cpu_limit(
    1.75,
    reason=(
        "400%拡大相当の高さでも保存後の通知全文と操作を重ねず、スクロールとTabで到達できる。実ブラ"
        "ウザーの観測とtrace回収が必要（CPU最大実測0.717秒）。"
    ),
)
@pytest.mark.parametrize("operation", ["save", "important", "adopt", "hold"])
@pytest.mark.asyncio(loop_scope="session")
async def test_low_height_saved_notice_and_controls_are_reachable(
    browser_harness: _BrowserHarness,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    operation: str,
) -> None:
    await browser_harness.page.set_viewport_size({"width": 320, "height": 225})
    await _saved_mutation_commit_failure(browser_harness, monkeypatch, request, operation, "natural", low_height=True)
    page = browser_harness.page
    for width, height in [(320, 480), (320, 800), (1280, 900)]:
        await page.set_viewport_size({"width": width, "height": height})
        if height == 480:
            await _assert_detail_notice_and_controls_reachable(
                page, browser_harness.root / f"{operation}-320x480-notice-end.png"
            )
        else:
            assert await page.locator("#detail-shell").evaluate("e => getComputedStyle(e).display") == "grid"
            assert await page.locator("#detail-alert").evaluate("e => getComputedStyle(e).position") == "sticky"
        await page.locator("#detail-dialog").evaluate("e => { e.scrollTop = 0; }")
        await page.screenshot(path=str(browser_harness.root / f"{operation}-{width}x{height}.png"))


@pytest.mark.asyncio(loop_scope="session")
async def test_pending_edit_and_closed_delete_route_results(browser_harness: _BrowserHarness) -> None:
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


@pytest.mark.asyncio(loop_scope="session")
async def test_edit_success_does_not_depend_on_auxiliary_detail_get(
    browser_harness: _BrowserHarness,
) -> None:
    """本文保存の成功を後続の詳細取得失敗へ変換しない。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    awi_row = page.locator('.entry-select[data-key="awi.md"]')
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

    await page.route("**/api/entries/awi.md", fail_detail_get)
    await detail.get_by_role("button", name="保存", exact=True).click()
    await page.get_by_role("status").filter(has_text="保存しました").wait_for(state="visible")
    await detail.wait_for(state="hidden")
    assert request_methods == ["PUT"]
    await page.unroute("**/api/entries/awi.md", fail_detail_get)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("case_name", list(_DETAIL_MUTATION_CASES))
async def test_detail_mutation_self_write_does_not_warn(browser_harness: _BrowserHarness, case_name: str) -> None:
    case = _DETAIL_MUTATION_CASES[case_name]
    harness = browser_harness
    page = harness.page
    (_entry_path(harness.root, "target.md")).write_text(_state_text(case.content, case.state), encoding="utf-8")
    key = "target.md"
    await page.goto(f"{harness.base_url}/?filename=target.md")
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

    try:
        async with page.expect_response(lambda response: "/api/entries?type=" in response.url):
            harness.current_state.publish()
        await _wait_for_detail_reload_deferred(page)
    finally:
        release.set()

    await detail.wait_for(state="hidden")
    await playwright.async_api.expect(page.locator("#operation-notice-message")).to_have_text(f"{key}{case.message}")
    assert await page.evaluate("window.__selfWriteWarnings") == []
    if case.final_state is None:
        assert not list(harness.root.rglob("target.md"))
    else:
        saved_path = _entry_path(harness.root, "target.md")
        assert wi_entries.entry_state_of(saved_path) == case.final_state
        saved = saved_path.read_text(encoding="utf-8")
        if case_name == "save":
            assert saved == case.content + "\n保存追記\n"
        elif case_name in {"answer", "answer-auto-adopt"}:
            assert case.field is not None
            answer = saved.split("<!-- ユーザーはこの行以降に回答を追記する -->\n", 1)[1]
            answer = answer.split("\n## 処理結果\n", 1)[0]
            assert answer.strip() == case.field[1]
            assert "## 質問" in saved
        elif case_name == "user-comment":
            assert saved == case.content + "\n## ユーザーコメント\n\nユーザーのコメント\n"
        else:
            assert "変更操作の対象" in saved
    if case_name == "answer-auto-adopt":
        assert "その対応で問題無い" in _entry_path(harness.root, "target.md").read_text(encoding="utf-8")
        await _open_filters(page)
        await page.locator("#state-filter").select_option("all")
        await page.locator('.entry-select[data-key="target.md"]').wait_for(state="visible")


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    ("mode_button", "field", "submit", "failure"),
    [
        (None, "#answer-input", "#save-answer-button", "へ回答できませんでした。"),
        ("#edit-button", "#edit-content", "#save-entry-button", "を保存できませんでした。"),
    ],
    ids=["answer", "save"],
)
async def test_detail_mutation_conflict_keeps_input(
    browser_harness: _BrowserHarness, mode_button: str | None, field: str, submit: str, failure: str
) -> None:
    """送信中に他プロセスが本文を変えてサーバーが409を返した場合は、入力を残して失敗と回復の手順を示す。

    保留した詳細の再読込は失敗時に実行するが、その警告で失敗の表示を上書きしない。
    """
    await _detail_conflict(browser_harness, mode_button, field, submit, failure)


async def _detail_conflict(harness: _BrowserHarness, mode_button: str | None, field: str, submit: str, failure: str) -> None:
    page = harness.page
    path = _entry_path(harness.root, "target.md")
    path.write_text(_FREE_FORM_UWI, encoding="utf-8")
    await page.goto(f"{harness.base_url}/?filename=target.md")
    detail = page.get_by_role("dialog", name="詳細")
    await detail.wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    if mode_button is not None:
        await page.locator(mode_button).click()
    user_input = _FREE_FORM_UWI + "\n入力の追記\n" if field == "#edit-content" else "保持する回答"
    await page.locator(field).fill(user_input)
    request_observed = asyncio.Event()
    request_release = asyncio.Event()

    async def change_before_send(route: playwright.async_api.Route) -> None:
        if route.request.method not in {"POST", "PUT"}:
            await route.continue_()
            return
        path.write_text(_FREE_FORM_UWI.replace("方針はどうするか？", "他プロセスが変えた質問"), encoding="utf-8")
        harness.current_state.publish()
        request_observed.set()
        try:
            await request_release.wait()
        finally:
            await route.continue_()

    await page.route("**/api/entries/answer", change_before_send)
    await page.route("**/api/entries/target.md", change_before_send)
    await page.locator(submit).click()
    await asyncio.wait_for(request_observed.wait(), timeout=5)
    try:
        await _wait_for_detail_reload_deferred(page)
    finally:
        request_release.set()

    alert = detail.locator("#detail-alert")
    await playwright.async_api.expect(alert).to_contain_text(f"target.md{failure}")
    await playwright.async_api.expect(alert).to_contain_text("閉じる前にコピーして退避してください。")
    await playwright.async_api.expect(alert).to_contain_text("最新の内容へ必要な変更だけを反映")
    assert await page.locator(field).input_value() == user_input
    assert path.read_text(encoding="utf-8") == _FREE_FORM_UWI.replace("方針はどうするか？", "他プロセスが変えた質問")


@pytest.mark.parametrize("width,height", [(1280, 900), (390, 844), (320, 480)])
@pytest.mark.asyncio(loop_scope="session")
async def test_edit_conflict_recovery_preserves_saved_input_and_external_change(
    browser_harness: _BrowserHarness, width: int, height: int
) -> None:
    harness = browser_harness
    page = harness.page
    await page.set_viewport_size({"width": width, "height": height})
    await _detail_conflict(harness, "#edit-button", "#edit-content", "#save-entry-button", "を保存できませんでした。")
    detail = page.get_by_role("dialog", name="詳細")
    field = detail.locator("#edit-content")

    await page.evaluate(
        """document.addEventListener('copy', event => {
          const field = document.activeElement;
          window.__recoveryCopy = field.value.slice(field.selectionStart, field.selectionEnd);
          event.preventDefault();
        }, {once: true});"""
    )
    await field.press("Control+A")
    await field.press("Control+C")
    copied = await page.evaluate("window.__recoveryCopy")
    assert copied == _FREE_FORM_UWI + "\n入力の追記\n"
    await page.unroute("**/api/entries/target.md")
    page.once("dialog", lambda dialog: dialog.accept())
    await detail.get_by_role("button", name="閉じる", exact=True).click()
    await playwright.async_api.expect(detail).to_be_hidden()
    await page.locator('.entry-select[data-key="target.md"]').click()
    await detail.get_by_role("button", name="編集", exact=True).click()
    latest = await field.input_value()
    assert "他プロセスが変えた質問" in latest
    assert "入力の追記" not in latest
    delta = copied[copied.index("入力の追記") :]
    await field.fill(latest + "\n" + delta)
    await detail.get_by_role("button", name="保存", exact=True).click()
    await page.get_by_role("status").filter(has_text="保存しました").wait_for(state="visible")
    saved = _entry_path(harness.root, "target.md").read_text(encoding="utf-8")
    assert "他プロセスが変えた質問" in saved
    assert saved.endswith(delta)


@pytest.mark.asyncio(loop_scope="session")
async def test_answer_and_delete_target_the_visible_state(browser_harness: _BrowserHarness) -> None:
    harness = browser_harness
    page = harness.page
    marker = "<!-- ユーザーはこの行以降に回答を追記する -->"
    uwi_content = (
        "---\ntype: uwi\nstate: inbox\ntarget_repo: example/repo\nquestion_type: free-form\n---\n\n"
        f"## 質問\n\n状態を指定しますか？\n\n## 回答\n\n{marker}\n"
    )
    awi_content = "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\n---\n\n状態付き削除\n"
    for state_name in ("inbox", "processing"):
        prefix = "processing-" if state_name == "processing" else ""
        _entry_path(harness.root, prefix + "answer-same.md").write_text(_state_text(uwi_content, state_name), encoding="utf-8")
        _entry_path(harness.root, prefix + "remove-same.md").write_text(_state_text(awi_content, state_name), encoding="utf-8")
    await page.goto(harness.base_url + "/")

    answer_row = page.locator('.entry-select[data-key="answer-same.md"]')
    await answer_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    answer = detail.get_by_role("textbox", name="UWIへの回答（必須）")
    await playwright.async_api.expect(answer).to_have_attribute("placeholder", "回答を入力")
    await answer.fill("未処理側だけへの回答")
    async with page.expect_request("**/api/entries/answer") as answer_request_info:
        await detail.get_by_role("button", name="回答を保存").click()
    answer_request = await answer_request_info.value
    answer_payload = answer_request.post_data_json
    assert isinstance(answer_payload, dict)
    assert answer_payload["state"] == "inbox"
    await page.get_by_role("status").filter(has_text="回答しました").wait_for(state="visible")
    assert _entry_path(harness.root, "answer-same.md").read_text(encoding="utf-8").endswith("未処理側だけへの回答\n")
    assert _entry_path(harness.root, "processing-answer-same.md").read_text(encoding="utf-8") == _state_text(
        uwi_content, "processing"
    )

    await page.keyboard.press("Escape")
    remove_row = page.locator('.entry-select[data-key="remove-same.md"]')
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
    assert not _entry_path(harness.root, "remove-same.md").exists()
    assert _entry_path(harness.root, "processing-remove-same.md").read_text(encoding="utf-8") == _state_text(
        awi_content, "processing"
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_answer_rebase_in_progress_shows_recovery(
    browser_harness: _BrowserHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = browser_harness
    page = harness.page
    rebasing = [True]
    answer_uwi = harness.operations.answer_uwi

    def answer_unless_rebasing(*args: Any, **kwargs: Any) -> bool:
        if rebasing[0]:
            raise git_sync.RebaseInProgressError("rebase中のため新しい更新を開始できない")
        return answer_uwi(*args, **kwargs)

    monkeypatch.setattr(harness.operations, "answer_uwi", answer_unless_rebasing)
    await page.goto(harness.base_url + "/")
    detail = page.get_by_role("dialog", name="詳細")
    await page.locator('.entry-select[data-key="question.md"]').click()
    field = detail.locator("#answer-input")
    await field.fill("解消後に保存する回答")

    await detail.get_by_role("button", name="回答を保存", exact=True).click()

    alert = detail.get_by_role("alert").filter(has_text="rebase中")
    await alert.wait_for(state="visible")
    alert_text = await alert.inner_text()
    assert "git -c submodule.recurse=false rebase --continue" in alert_text
    assert "git -c submodule.recurse=false rebase --abort" in alert_text
    assert "開き直して" not in alert_text
    assert await field.input_value() == "解消後に保存する回答"

    rebasing[0] = False
    await detail.get_by_role("button", name="回答を保存", exact=True).click()
    await page.get_by_role("status").filter(has_text="回答しました").wait_for(state="visible")
    assert "解消後に保存する回答" in _entry_path(harness.root, "question.md").read_text(encoding="utf-8")


@pytest.mark.asyncio(loop_scope="session")
async def test_save_failure_message_stays_at_scrolled_dialog_top(browser_harness: _BrowserHarness) -> None:
    """本文末尾までスクロールした保存失敗でも結果をダイアログ本文の上端へ留める。"""
    harness = browser_harness
    path = _entry_path(harness.root, "long-entry.md")
    path.write_text(
        "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\n---\n\n" + "長い本文\n\n" * 120,
        encoding="utf-8",
    )
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator('.entry-select[data-key="long-entry.md"]').click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="編集", exact=True).click()
    body = detail.locator(".dialog-body")
    await body.evaluate("element => { element.scrollTop = element.scrollHeight; }")

    async def fail_save(route: playwright.async_api.Route) -> None:
        await route.fulfill(status=500, json={"error": "保存処理に失敗しました"})

    await page.route("**/api/entries/long-entry.md", fail_save)
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
    await page.unroute("**/api/entries/long-entry.md", fail_save)

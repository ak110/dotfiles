import asyncio
import functools
import urllib.parse

import playwright.async_api
import pytest

from agent_toolkit._atk.serve import wi_operations
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _browser_harness_fixture,
    _BrowserHarness,
    _open_filters,
    _screen_harness_fixture,
)
from agent_toolkit._atk.wi import entries as wi_entries
from agent_toolkit._testing.serve_browser_wi_support import _POST_APPROVAL_UWI, _entry_path, _set_entry_state
from agent_toolkit._testing.serve_support import _disable_wi_git

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
async def test_legacy_confirmation_has_no_button_or_api(
    browser_harness: _BrowserHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_wi_git(monkeypatch)
    path = _entry_path(browser_harness.root, "legacy.md")
    content = "---\ntype: awi\nstate: inbox\nconfirmation: required\n---\n\n本文\n"
    path.write_text(content, encoding="utf-8")
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/?filename=" + path.name)
    detail = page.get_by_role("dialog", name="詳細", exact=True)
    await playwright.async_api.expect(detail).to_be_visible()
    await playwright.async_api.expect(page.locator("#confirmation-button")).to_have_count(0)
    await playwright.async_api.expect(page.locator(".confirmation-badge")).to_have_count(0)
    response = await page.request.post(
        browser_harness.base_url + "/api/entries/confirmation",
        data={"filename": path.name, "confirmed": True, "expected_content": content},
    )

    assert response.status == 405
    assert path.read_text(encoding="utf-8") == content


@pytest.mark.asyncio(loop_scope="session")
async def test_effect_check_uses_generic_metadata_display(browser_harness: _BrowserHarness) -> None:
    path = _entry_path(browser_harness.root, "effect.md")
    path.write_text(
        "---\ntype: awi\nstate: adopted\neffect_check: confirmed\nconfirmation: required\n---\n\n本文\n",
        encoding="utf-8",
    )
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/?filename=" + path.name)
    dialog = page.get_by_role("dialog", name="詳細", exact=True)
    await playwright.async_api.expect(dialog).to_be_visible()
    await playwright.async_api.expect(dialog.locator("#detail-metadata dt").filter(has_text="effect_check")).to_have_count(1)
    await playwright.async_api.expect(dialog.locator("#detail-metadata dd").filter(has_text="confirmed")).to_have_count(1)
    await playwright.async_api.expect(dialog.locator("#detail-metadata dt").filter(has_text="confirmation")).to_have_count(1)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    ("choice", "addition", "saved_state"),
    [
        ("その対応で問題無い", "", "adopted"),
        ("問題がある", "\n是正してください。", "inbox"),
        ("その対応で問題無い", "\n追加の確認があります。", "inbox"),
    ],
)
async def test_post_approval_fixed_choices_show_saved_state(
    browser_harness: _BrowserHarness, choice: str, addition: str, saved_state: str
) -> None:
    harness = browser_harness
    filename = "post-approval.md"
    (_entry_path(harness.root, filename)).write_text(
        "---\ntype: uwi\nstate: inbox\ntarget_repo: example/repo\nquestion_type: post-approval\n---\n\n"
        "## 質問\n\n実施した対応で問題ありませんか？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator(f'.entry-select[data-key="{filename}"]').click()
    detail = page.get_by_role("dialog", name="詳細")
    await playwright.async_api.expect(detail.locator("#detail-content")).not_to_contain_text(
        "ユーザーはこの行以降に回答を追記する"
    )
    await playwright.async_api.expect(detail.get_by_role("button", name="問題がある", exact=True)).to_be_visible()
    await detail.get_by_role("button", name=choice, exact=True).click()
    await playwright.async_api.expect(detail.locator("#answer-input")).to_have_value(choice)
    if addition:
        await detail.locator("#answer-input").fill(choice + addition)
    await detail.get_by_role("button", name="回答を保存").click()
    await playwright.async_api.expect(detail).to_be_hidden()
    message = "採用済み（adopted）へ移しました。" if saved_state == "adopted" else "へ回答しました。"
    await playwright.async_api.expect(page.get_by_role("status").filter(has_text=message)).to_be_visible()
    assert wi_entries.entry_state_of(_entry_path(harness.root, filename)) == saved_state
    saved = (_entry_path(harness.root, filename)).read_text(encoding="utf-8")
    assert "<!-- ユーザーはこの行以降に回答を追記する -->" in saved
    assert choice + addition in saved
    if saved_state == "inbox":
        await page.locator(f'.entry-select[data-key="{filename}"]').click()
        await playwright.async_api.expect(detail.locator("#detail-metadata")).to_contain_text("answered")
        await playwright.async_api.expect(detail.locator("#detail-metadata")).to_contain_text("yes")
        await playwright.async_api.expect(detail.locator("#detail-content")).to_contain_text(choice + addition)
        await playwright.async_api.expect(detail.locator("#detail-content")).not_to_contain_text(
            "ユーザーはこの行以降に回答を追記する"
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_one_choice_uwi_can_be_answered_then_adopted(browser_harness: _BrowserHarness) -> None:
    harness = browser_harness
    filename = "one-choice.md"
    (_entry_path(harness.root, filename)).write_text(
        "---\ntype: uwi\nstate: inbox\ntarget_repo: example/repo\nquestion_type: choice\n"
        "choices: 問題がある（是正内容をこの欄へ書いてください）\n---\n\n"
        "## 質問\n\n問題はありますか？\n\n## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator(f'.entry-select[data-key="{filename}"]').click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="問題がある（是正内容をこの欄へ書いてください）").click()
    await detail.locator("#answer-input").fill("問題がある。是正内容を確認しました。")
    await detail.get_by_role("button", name="回答を保存").click()
    await playwright.async_api.expect(detail).to_be_hidden()
    await page.get_by_role("status").filter(has_text="回答しました").wait_for(state="visible")
    await page.locator(f'.entry-select[data-key="{filename}"]').click()
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
    assert wi_entries.entry_state_of(_entry_path(harness.root, filename)) == "adopted"


@pytest.mark.asyncio(loop_scope="session")
async def test_answer_change_terminal_edit_and_identifier_surfaces(browser_harness: _BrowserHarness) -> None:
    harness = browser_harness
    page = harness.page
    question_path = _entry_path(harness.root, "question.md")
    question_path.write_text(question_path.read_text(encoding="utf-8") + "既存回答\n", encoding="utf-8")
    await page.goto(harness.base_url + "/")
    await _open_filters(page)

    question_row = page.locator('.entry-select[data-key="question.md"]')
    await question_row.click()
    detail = page.get_by_role("dialog", name="詳細")

    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("uwi / answered")
    await playwright.async_api.expect(detail.locator("#answer-input")).to_have_value("既存回答")
    await page.keyboard.press("Escape")

    await page.locator("#state-filter").select_option("all")
    await page.locator('.entry-select[data-key="adopted.md"]').click()
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("awi / adopted")
    await playwright.async_api.expect(detail.locator("#readonly-notice")).to_be_hidden()
    await detail.locator("#edit-button").click()
    editor = detail.locator("#edit-content")
    original = await editor.input_value()
    await editor.fill(original + "\n終了後の追記\n")
    await detail.locator("#save-entry-button").click()
    await playwright.async_api.expect(detail).to_be_hidden()
    await page.locator('.entry-select[data-key="adopted.md"]').click()
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("awi / adopted")
    assert "終了後の追記" in _entry_path(harness.root, "adopted.md").read_text(encoding="utf-8")
    await playwright.async_api.expect(detail.locator("#answer-panel")).to_be_hidden()
    await playwright.async_api.expect(detail.locator("#delete-button")).to_be_visible()
    await playwright.async_api.expect(detail.locator("#hold-button")).to_be_visible()
    await playwright.async_api.expect(detail.locator("#return-to-inbox-button")).to_be_visible()


@pytest.mark.asyncio(loop_scope="session")
async def test_hold_and_rejected_details_offer_recovery_operations(browser_harness: _BrowserHarness) -> None:
    harness = browser_harness
    page = harness.page
    _entry_path(harness.root, "held-awi.md").write_text(
        "---\ntype: awi\nstate: hold\ntarget_repo: held/repo\nsource: session-review\n---\n\n保留中の本文\n",
        encoding="utf-8",
    )
    _entry_path(harness.root, "held-question.md").write_text(
        "---\ntype: uwi\nstate: hold\ntarget_repo: held/repo\nquestion_type: free-form\n---\n\n"
        "## 質問\n\n保留中の質問\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    await page.goto(browser_harness.base_url + "/")
    await _open_filters(page)
    await page.locator("#state-filter").select_option("all")
    detail = page.get_by_role("dialog", name="詳細")

    await page.locator('.entry-select[data-key="held-awi.md"]').click()
    for button_name in ("編集", "採用", "却下", "保留を解除", "削除"):
        await playwright.async_api.expect(detail.get_by_role("button", name=button_name, exact=True)).to_be_visible()
    await playwright.async_api.expect(detail.locator("#user-comment-button")).to_be_visible()
    await page.keyboard.press("Escape")

    await page.locator('.entry-select[data-key="held-question.md"]').click()
    await playwright.async_api.expect(detail.locator("#answer-panel")).to_be_visible()
    await playwright.async_api.expect(detail.get_by_role("button", name="採用", exact=True)).to_be_visible()
    await playwright.async_api.expect(detail.locator("#reject-button")).to_be_hidden()
    await page.keyboard.press("Escape")

    await page.locator('.entry-select[data-key="rejected.md"]').click()
    await playwright.async_api.expect(detail.locator("#readonly-notice")).to_be_hidden()
    await playwright.async_api.expect(detail.get_by_role("button", name="受信へ戻す", exact=True)).to_be_visible()
    await playwright.async_api.expect(detail.locator("#hold-button")).to_be_visible()


@pytest.mark.asyncio(loop_scope="session")
async def test_terminal_work_items_reopen_and_hold_through_browser(browser_harness: _BrowserHarness) -> None:
    harness = browser_harness
    page = harness.page
    delete_paths = {}
    for state in ("adopted", "rejected"):
        path = _entry_path(harness.root, f"delete-{state}.md")
        path.write_text(
            f"---\ntype: awi\nstate: {state}\ntarget_repo: example/repo\n---\n\n{state}の削除対象\n", encoding="utf-8"
        )
        delete_paths[state] = path
    await page.goto(harness.base_url + "/")
    await _open_filters(page)
    await page.locator("#state-filter").select_option("all")

    await page.locator('.entry-select[data-key="adopted.md"]').click()
    await page.locator("#hold-button").click()
    await playwright.async_api.expect(page.get_by_role("dialog", name="詳細")).to_be_hidden()
    await page.locator('.entry-select[data-key="adopted.md"]').wait_for(state="visible")

    await page.locator('.entry-select[data-key="rejected.md"]').click()
    await page.locator("#return-to-inbox-button").click()
    await playwright.async_api.expect(page.get_by_role("dialog", name="詳細")).to_be_hidden()
    await page.locator('.entry-select[data-key="rejected.md"]').wait_for(state="visible")

    detail = page.get_by_role("dialog", name="詳細")
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    for state, path in delete_paths.items():
        row = page.locator(f'.entry-select[data-key="{path.name}"]')
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


@pytest.mark.cpu_limit(
    1.75,
    reason=(
        "初回と既知項目の属性変化を通知せず、新規の未回答UWIを通知する。実ブラウザーの観測とtrace"
        "回収が必要（CPU最大実測0.704秒）。"
    ),
)
@pytest.mark.asyncio(loop_scope="session")
async def test_browser_notification_uses_filename_registration_identity(browser_harness: _BrowserHarness) -> None:
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
    async with page.expect_response(lambda response: response.url.endswith("/api/entries?type=all&status=active&answered=all")):
        await page.goto(harness.base_url + "/")
    assert await page.evaluate("window.__notificationCalls.length") == 0
    notification_button = page.get_by_role("button", name="通知を有効化")
    await notification_button.click()
    await playwright.async_api.expect(notification_button).to_be_hidden()

    async def publish_and_wait() -> None:
        async with page.expect_response(
            lambda response: response.url.endswith("/api/entries?type=all&status=active&answered=all")
        ):
            harness.current_state.publish()

    control_number = 0

    async def observe_no_extra_notification() -> None:
        nonlocal control_number
        control_number += 1

        control = _entry_path(harness.root, f"notification-control-{control_number}.md")
        control.write_text(new_unanswered, encoding="utf-8")
        await publish_and_wait()
        await page.wait_for_function("window.__notificationCalls.length === 2")
        assert await page.evaluate("window.__notificationCalls.pop().body") == control.name
        await page.wait_for_timeout(100)
        assert await page.evaluate("window.__notificationCalls.length") == 1
        control.unlink()
        await publish_and_wait()

    marker = "<!-- ユーザーはこの行以降に回答を追記する -->"
    new_path = _entry_path(harness.root, "new-question.md")
    new_unanswered = f"---\ntype: uwi\nstate: inbox\ntarget_repo: new/repo\n---\n\n## 質問\n\n新規\n\n## 回答\n\n{marker}\n"
    new_path.write_text(new_unanswered, encoding="utf-8")
    await publish_and_wait()
    await page.wait_for_function("window.__notificationCalls.length === 1")
    assert await page.evaluate("window.__notificationCalls") == [{"title": "新規未回答UWI", "body": "new-question.md"}]

    question_path = _entry_path(harness.root, "question.md")
    original_question = question_path.read_text(encoding="utf-8")
    question_path.write_text(original_question + "回答済み\n", encoding="utf-8")
    await publish_and_wait()
    question_path.write_text(original_question, encoding="utf-8")
    await publish_and_wait()
    question_path.write_text(original_question.replace("example/repo", "changed/repo"), encoding="utf-8")
    await publish_and_wait()

    _set_entry_state(question_path, "processing")
    await publish_and_wait()
    _set_entry_state(question_path, "inbox")
    await publish_and_wait()

    new_path.unlink()
    await publish_and_wait()
    new_path.write_text(new_unanswered, encoding="utf-8")
    await publish_and_wait()
    await observe_no_extra_notification()

    awi_path = _entry_path(harness.root, "new-awi.md")
    required_awi = "---\ntype: awi\nstate: inbox\ntarget_repo: new/repo\nconfirmation: required\n---\n\n# 要確認\n"
    awi_path.write_text(required_awi, encoding="utf-8")
    await publish_and_wait()
    await observe_no_extra_notification()
    awi_path.write_text(required_awi.replace("confirmation: required", "confirmation: confirmed"), encoding="utf-8")
    await publish_and_wait()
    await observe_no_extra_notification()


@pytest.mark.asyncio(loop_scope="session")
async def test_changed_detail_inputs_confirm_discard_and_unchanged_input_closes(browser_harness: _BrowserHarness) -> None:
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    detail = page.get_by_role("dialog", name="詳細")

    await page.locator('.entry-select[data-key="awi.md"]').click()
    await detail.get_by_role("button", name="編集", exact=True).click()
    await page.keyboard.press("Escape")
    await playwright.async_api.expect(detail).to_be_hidden()

    for key, mode, field_id in (
        ("awi.md", "編集", "edit-content"),
        ("question.md", "回答", "answer-input"),
        ("awi.md", "ユーザーコメント", "user-comment-input"),
    ):
        await page.locator(f'.entry-select[data-key="{key}"]').click()
        if mode != "回答":
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


@pytest.mark.asyncio(loop_scope="session")
async def test_work_item_important_conflict_preserves_latest_content(
    browser_harness: _BrowserHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_wi_git(monkeypatch)
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/?filename=awi.md")
    detail = page.get_by_role("dialog", name="詳細", exact=True)
    await playwright.async_api.expect(detail).to_be_visible()
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")

    async def update_before_important(route: playwright.async_api.Route) -> None:
        paths = list(harness.root.rglob("awi.md"))
        assert len(paths) == 1
        paths[0].write_text(paths[0].read_text(encoding="utf-8") + "\n外部から追記\n", encoding="utf-8")
        await route.continue_()

    await page.route("**/api/entries/important", update_before_important)
    await page.get_by_role("button", name="重要設定", exact=True).click()
    await playwright.async_api.expect(detail.get_by_role("alert")).to_contain_text("重要設定できませんでした")
    await playwright.async_api.expect(detail.get_by_role("alert")).to_contain_text(
        "未保存の入力があれば、閉じる前にコピーして退避してください。"
    )
    await playwright.async_api.expect(detail.get_by_role("alert")).to_contain_text(
        "詳細を開き直し、最新の内容へ必要な変更だけを反映して保存してください。"
    )
    await playwright.async_api.expect(page.get_by_role("button", name="重要設定", exact=True)).to_be_disabled()
    saved = next(harness.root.rglob("awi.md")).read_text(encoding="utf-8")
    assert saved.endswith("外部から追記\n")
    assert "important:" not in saved


@pytest.mark.asyncio(loop_scope="session")
async def test_work_item_url_and_selection_survive_state_change(
    browser_harness: _BrowserHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _disable_wi_git(monkeypatch)
    harness = browser_harness
    monkeypatch.setattr(
        harness.operations, "transition", functools.partial(wi_operations.Operations.transition, harness.operations)
    )
    page = harness.page
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    row = page.locator('.entry-select[data-key="awi.md"]')
    await row.click()
    detail = page.get_by_role("dialog", name="詳細", exact=True)
    await playwright.async_api.expect(detail).to_be_visible()
    stable_url = page.url
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(stable_url).query) == {"filename": ["awi.md"]}
    response = await page.request.post(harness.base_url + "/api/entries/hold", data={"filenames": ["awi.md"]})
    assert response.ok
    assert wi_entries.entry_state_of(_entry_path(harness.root, "awi.md")) == "hold"
    harness.current_state.publish()
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("awi / hold")
    assert page.url == stable_url
    await playwright.async_api.expect(row).to_have_attribute("aria-current", "true")
    await page.reload()
    await playwright.async_api.expect(detail.locator("#detail-filename")).to_have_text("awi.md")
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("awi / hold")
    assert page.url == stable_url


@pytest.mark.asyncio(loop_scope="session")
async def test_uwi_detail_offers_answer_and_other_operations_at_once(browser_harness: _BrowserHarness) -> None:
    """回答できるUWIの詳細を開いた時点で、選択肢・1行の回答欄・保存と他の操作を同時に表示し、2操作で回答を保存する。

    回答の前にモードを切り替える操作が要ると、回答のたびに余計なクリックが増える。
    """
    harness = browser_harness
    page = harness.page
    await page.set_viewport_size({"width": 1600, "height": 900})
    path = _entry_path(harness.root, "target.md")
    path.write_text(_POST_APPROVAL_UWI, encoding="utf-8")
    await page.goto(f"{harness.base_url}/?filename=target.md")
    detail = page.get_by_role("dialog", name="詳細")
    await detail.wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")

    footer = detail.locator("#detail-footer")
    for selector in ("#answer-input", "#save-answer-button", "#adopt-button", "#edit-button", "#delete-button"):
        await playwright.async_api.expect(footer.locator(selector)).to_be_visible()
    choices = footer.locator("#answer-choices button")
    assert await choices.count() >= 1
    input_box = await footer.locator("#answer-input").bounding_box()
    line_height = await page.locator("#answer-input").evaluate("element => parseFloat(getComputedStyle(element).lineHeight)")
    assert input_box is not None
    assert input_box["height"] < line_height * 2.5

    choice = (await choices.first.text_content() or "").strip()
    await choices.first.click()
    await footer.locator("#save-answer-button").click()
    await detail.wait_for(state="hidden")
    assert choice in path.read_text(encoding="utf-8")


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("width", [1280, 360])
async def test_wi_references_open_nested_readonly_dialogs(browser_harness: _BrowserHarness, width: int) -> None:
    harness = browser_harness
    for filename, body in (
        (
            "parent.md",
            "depends_on: [child.md, 20261011-203736-1001.md]\n---\n\n"
            "`child.md`、旧式 WI.mdを確認、AGENTS.md、parent.md\n\n" + "親の本文\n\n" * 24,
        ),
        ("child.md", "---\n\n```text\n20261011-203736-1000.md\n```\n"),
        ("20261011-203736-1000.md", "---\n\n孫の本文\n"),
        ("旧式 WI.md", "---\n\n参照先\n"),
    ):
        _entry_path(harness.root, filename).write_text("---\ntype: awi\nstate: inbox\n" + body, encoding="utf-8")
    page = harness.page
    await page.set_viewport_size({"width": width, "height": 900})
    await page.goto(harness.base_url + "/?filename=parent.md")
    detail = page.locator("#detail-dialog")
    await playwright.async_api.expect(detail.locator("#detail-content a")).to_have_count(2)
    links = detail.locator("#detail-metadata").get_by_role("link")
    await playwright.async_api.expect(links).to_have_count(2)
    await playwright.async_api.expect(detail.locator("#detail-metadata")).not_to_contain_text('["child.md"')
    await links.last.click()
    missing = page.get_by_role("dialog", name="詳細（1段目）")
    await playwright.async_api.expect(missing).to_contain_text("WIが見つかりません。")
    await page.keyboard.press("Escape")
    await links.first.click()
    child = page.get_by_role("dialog", name="詳細（1段目）")
    await playwright.async_api.expect(child.get_by_role("button")).to_have_count(2)
    await playwright.async_api.expect(child.get_by_role("heading", name="本文", exact=True)).to_be_visible()
    preview_metadata = child.locator(".detail-metadata")
    await playwright.async_api.expect(preview_metadata).to_have_css("display", "grid")
    preview_values = await preview_metadata.text_content()
    parent_rect = await detail.bounding_box()
    child_rect = await child.bounding_box()
    assert parent_rect is not None and child_rect is not None
    assert child_rect["x"] + child_rect["width"] == pytest.approx(parent_rect["x"] + parent_rect["width"], abs=1)
    assert child_rect["y"] + child_rect["height"] == pytest.approx(parent_rect["y"] + parent_rect["height"], abs=1)
    if width == 360:
        assert (child_rect["x"], child_rect["y"]) == pytest.approx((parent_rect["x"], parent_rect["y"]), abs=1)
    else:
        assert child_rect["x"] > parent_rect["x"]
        assert child_rect["y"] > parent_rect["y"]
    grandchild_link = child.get_by_role("link", name="20261011-203736-1000.md")
    await grandchild_link.click()
    grandchild = page.get_by_role("dialog", name="詳細（2段目）")
    await playwright.async_api.expect(grandchild).to_contain_text("孫の本文")
    await page.keyboard.press("Escape")
    await playwright.async_api.expect(grandchild).to_have_count(0)
    await playwright.async_api.expect(grandchild_link).to_be_focused()
    await child.get_by_role("button", name="このWIを開く").click()
    await playwright.async_api.expect(detail.locator("#detail-filename")).to_have_text("child.md")
    assert await detail.locator("#detail-metadata").text_content() == preview_values


@pytest.mark.asyncio(loop_scope="session")
async def test_wi_detail_keeps_reading_space_and_discloses_secondary_actions(browser_harness: _BrowserHarness) -> None:
    page = browser_harness.page
    await page.set_viewport_size({"width": 360, "height": 900})
    await page.goto(browser_harness.base_url + "/?filename=awi.md")
    detail = page.locator("#detail-dialog")
    footer = detail.locator("#detail-footer")
    disclosure = footer.locator("summary", has_text="その他の操作")
    delete = footer.locator("#delete-button")
    await playwright.async_api.expect(disclosure).to_be_visible()
    await playwright.async_api.expect(footer.locator("#adopt-button")).to_be_visible()
    await playwright.async_api.expect(footer.locator("#edit-button")).to_be_visible()
    await playwright.async_api.expect(delete).to_be_hidden()
    bounds = await footer.bounding_box()
    assert bounds is not None and bounds["height"] <= 100
    await playwright.async_api.expect(detail.get_by_text("編集対象の本文", exact=True)).to_be_in_viewport()

    await disclosure.focus()
    await disclosure.press("Enter")
    await delete.click()
    await page.get_by_role("dialog", name="削除の確認").get_by_role("button", name="閉じる").click()
    await playwright.async_api.expect(delete).to_be_focused()
    await disclosure.press("Enter")
    await playwright.async_api.expect(delete).to_be_hidden()

    await page.set_viewport_size({"width": 1280, "height": 900})
    await playwright.async_api.expect(disclosure).to_be_hidden()
    await playwright.async_api.expect(delete).to_be_visible()


@pytest.mark.parametrize(("dimension", "boundary"), [("width", "33rem"), ("height", "21rem")])
@pytest.mark.asyncio(loop_scope="session")
async def test_wi_reference_stops_offset_at_readable_boundary(
    browser_harness: _BrowserHarness, dimension: str, boundary: str
) -> None:
    harness = browser_harness
    for name, text in (
        ("parent.md", "`legacy child.md`\n"),
        ("legacy child.md", "`grandchild.md`\n"),
        ("grandchild.md", "境界の確認\n"),
    ):
        _entry_path(harness.root, name).write_text("---\ntype: awi\nstate: inbox\n---\n\n" + text, encoding="utf-8")
    page = harness.page
    await page.set_viewport_size({"width": 1280, "height": 900})
    await page.goto(harness.base_url + "/?filename=parent.md")
    parent = page.locator("#detail-dialog")
    await playwright.async_api.expect(parent).to_be_visible()
    await parent.evaluate(
        """(element, {dimension, boundary}) => {
           element.style.width = "50rem";
           element.style.height = "30rem";
           element.style[dimension] = boundary;
        }""",
        {"dimension": dimension, "boundary": boundary},
    )
    opened_from = parent.get_by_role("link", name="legacy child.md")
    await opened_from.click()
    child = page.get_by_role("dialog", name="詳細（1段目）")
    parent_bounds, child_bounds = await parent.bounding_box(), await child.bounding_box()
    assert parent_bounds is not None and child_bounds is not None
    assert child_bounds["x"] > parent_bounds["x"]
    assert child_bounds["y"] > parent_bounds["y"]
    next_link = child.get_by_role("link", name="grandchild.md")
    await next_link.click()
    last = page.get_by_role("dialog", name="詳細（2段目）")
    assert await last.bounding_box() == child_bounds
    await page.keyboard.press("Escape")
    await playwright.async_api.expect(next_link).to_be_focused()
    await page.keyboard.press("Escape")
    await playwright.async_api.expect(opened_from).to_be_focused()

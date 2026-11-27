import datetime
import functools
import re

import playwright.async_api
import pytest

from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _browser_harness_fixture,
    _BrowserHarness,
    _open_filters,
    _screen_harness_fixture,
)
from agent_toolkit._atk.wi import readiness
from agent_toolkit._testing.serve_browser_wi_support import _entry_path, _state_text

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
async def test_blocked_badge_tracks_unanswered_uwi_dependency(browser_harness: _BrowserHarness) -> None:
    harness = browser_harness
    page = harness.page
    marker = "<!-- ユーザーはこの行以降に回答を追記する -->"
    uwi_path = _entry_path(harness.root, "wait-question.md")
    unanswered = f"---\ntype: uwi\nstate: inbox\ntarget_repo: example/repo\n---\n\n## 質問\n\n確認\n\n## 回答\n\n{marker}\n"
    uwi_path.write_text(unanswered, encoding="utf-8")
    _entry_path(harness.root, "waiting-awi.md").write_text(
        "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\ndepends_on:\n  - wait-question.md\n---\n\n# 回答待ち\n",
        encoding="utf-8",
    )
    _entry_path(harness.root, "wait-prerequisite.md").write_text(
        "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\n---\n\n# 前提\n", encoding="utf-8"
    )
    _entry_path(harness.root, "wait-another-awi.md").write_text(
        "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\ndepends_on: [wait-prerequisite.md]\n---\n\n# 依存あり\n",
        encoding="utf-8",
    )
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    row = page.locator('.entry-select[data-key="waiting-awi.md"]')
    awi_row = page.locator('.entry-select[data-key="wait-another-awi.md"]')
    await playwright.async_api.expect(row.locator(".blocked-badge")).to_have_text("回答待ち")
    await playwright.async_api.expect(row).to_have_accessible_name(re.compile("回答待ち"))
    await playwright.async_api.expect(awi_row.locator(".blocked-badge")).to_have_text("依存先の処理待ち")
    await playwright.async_api.expect(awi_row).to_have_accessible_name(re.compile("依存先の処理待ち"))
    await playwright.async_api.expect(row.locator(".state-badge")).to_have_text("受信")
    question_badges = page.locator('.entry-select[data-key="wait-question.md"] .blocked-badge')
    await playwright.async_api.expect(question_badges).to_have_count(0)

    uwi_path.write_text(unanswered + "回答済み\n", encoding="utf-8")
    harness.current_state.publish()
    await playwright.async_api.expect(row.locator(".blocked-badge")).to_have_text("依存先の処理待ち")
    uwi_path.write_text(unanswered.replace("state: inbox", "state: adopted") + "回答済み\n", encoding="utf-8")
    prerequisite = _entry_path(harness.root, "wait-prerequisite.md")
    prerequisite.write_text(
        prerequisite.read_text(encoding="utf-8").replace("state: inbox", "state: adopted"), encoding="utf-8"
    )
    harness.current_state.publish()
    await playwright.async_api.expect(awi_row.locator(".blocked-badge")).to_have_count(0)
    await playwright.async_api.expect(row.locator(".blocked-badge")).to_have_count(0)
    await playwright.async_api.expect(row.locator(".state-badge")).to_have_text("受信")


@pytest.mark.asyncio(loop_scope="session")
async def test_needs_verify_badge_replaces_inbox(browser_harness: _BrowserHarness) -> None:
    harness = browser_harness
    page = harness.page
    (_entry_path(harness.root, "verify.md")).write_text(
        "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\nsource: browser\n---\n\n観測待ちの本文\n\n"
        "## 反映後の観測の再開記録\n\n- 再開区分: 反映後の観測だけが残る\n- 計画: 計画なし\n",
        encoding="utf-8",
    )
    (_entry_path(harness.root, "working.md")).write_text(
        "---\ntype: awi\nstate: processing\ntarget_repo: example/repo\nsource: browser\n---\n\n処理中の本文\n", encoding="utf-8"
    )
    await page.goto(harness.base_url + "/")

    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    row = page.locator('.entry-select[data-key="verify.md"]')
    badge = row.locator(".state-badge")
    await playwright.async_api.expect(badge).to_have_text("確認待ち")
    assert "受信" not in await row.locator(".status-cell").inner_text()
    label = await row.get_attribute("aria-label")
    assert label is not None
    assert "確認待ち" in label.split("、")
    assert "受信" not in label.split("、")
    processing_badge = page.locator('.entry-select[data-key="working.md"] .state-badge')
    style = "element => [getComputedStyle(element).color, getComputedStyle(element).backgroundColor]"
    assert await badge.evaluate(style) == await processing_badge.evaluate(style)

    await row.click()
    detail = page.locator("#detail-dialog")
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("確認待ち")
    await detail.locator("#delete-button").click()
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    await playwright.async_api.expect(delete_dialog.locator("#delete-state")).to_have_text("確認待ち")
    await delete_dialog.locator("#delete-close-button").click()
    await detail.locator("#detail-close-button").click()

    await _open_filters(page)
    await page.locator("#state-filter").select_option("inbox")
    await playwright.async_api.expect(page.locator('.entry-select[data-key="verify.md"]')).to_be_visible()
    assert (_entry_path(harness.root, "verify.md")).is_file()


@pytest.mark.asyncio(loop_scope="session")
async def test_answered_badge_replaces_inbox_for_answered_uwi(browser_harness: _BrowserHarness) -> None:
    harness = browser_harness
    page = harness.page
    answered_text = "---\ntype: uwi\nstate: inbox\ntarget_repo: example/repo\n---\n\n## 質問\n\n確認\n\n## 回答\n\n回答済み\n"
    (_entry_path(harness.root, "answered.md")).write_text(answered_text, encoding="utf-8")
    (_entry_path(harness.root, "unanswered.md")).write_text(
        "---\ntype: uwi\nstate: inbox\ntarget_repo: example/repo\n---\n\n## 質問\n\n確認\n\n## 回答\n\n<!-- 記入欄 -->\n",
        encoding="utf-8",
    )
    (_entry_path(harness.root, "taken.md")).write_text(_state_text(answered_text, "processing"), encoding="utf-8")
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    row = page.locator('.entry-select[data-key="answered.md"]')
    badge = row.locator(".state-badge")
    await playwright.async_api.expect(badge).to_have_text("回答済み")
    label = await row.get_attribute("aria-label")
    assert label is not None
    assert "回答済み" in label.split("、")
    assert "受信" not in label.split("、")
    unanswered_badge = page.locator('.entry-select[data-key="unanswered.md"] .state-badge')
    await playwright.async_api.expect(unanswered_badge).to_have_text("受信")
    await playwright.async_api.expect(page.locator('.entry-select[data-key="taken.md"] .state-badge')).to_have_text("処理中")
    style = "element => [getComputedStyle(element).color, getComputedStyle(element).backgroundColor]"
    assert await badge.evaluate(style) == await unanswered_badge.evaluate(style)

    await row.click()
    detail = page.locator("#detail-dialog")
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("回答済み")
    await detail.locator("#detail-more summary").click()
    await detail.locator("#delete-button").click()
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    await playwright.async_api.expect(delete_dialog.locator("#delete-state")).to_have_text("回答済み")
    await delete_dialog.locator("#delete-close-button").click()
    await detail.locator("#detail-close-button").click()

    await _open_filters(page)
    await page.locator("#state-filter").select_option("inbox")
    await playwright.async_api.expect(page.locator('.entry-select[data-key="answered.md"]')).to_be_visible()


@pytest.mark.asyncio(loop_scope="session")
async def test_cooldown_expiry_updates_visible_states(browser_harness: _BrowserHarness) -> None:
    """AWIは期限だけの経過で一覧・読み上げ・開いた詳細と削除確認の表示を戻し、UWIは期限によらず保存状態を示す。

    冷却期限を適用するのはAWIだけである。UWIを冷却中と示すと、ユーザーの判断を待つ項目を時間経過で解消する待機として案内する。
    """
    harness = browser_harness
    page = harness.page
    await page.clock.install(time=datetime.datetime(2099, 1, 2, tzinfo=datetime.UTC))
    deadline = "2099-01-02T00:00:10+00:00"
    record = "\n## 反映後の観測の再開記録\n\n- 再開区分: 反映後の観測だけが残る\n- 計画: 計画なし\n"
    for filename, kind, extra in [("verify.md", "awi", record), ("plain.md", "awi", ""), ("question.md", "uwi", "")]:
        (_entry_path(harness.root, filename)).write_text(
            f"---\ntype: {kind}\nstate: inbox\ncooldown_until: '{deadline}'\n---\n\n# 冷却\n" + extra, encoding="utf-8"
        )
    (_entry_path(harness.root, "normal.md")).write_text("---\ntype: awi\nstate: inbox\n---\n\n通常\n", encoding="utf-8")
    (_entry_path(harness.root, "unreadable.md")).write_bytes(b"\xff")
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    warning = page.locator("#list-warning")
    await playwright.async_api.expect(warning).to_contain_text("unreadable.md")
    warning_text = await warning.text_content()
    assert warning_text is not None
    style = "element => [getComputedStyle(element).color, getComputedStyle(element).backgroundColor]"
    normal = page.locator('.entry-select[data-key="normal.md"] .state-badge')
    for filename in ["verify.md", "plain.md"]:
        row = page.locator(f'.entry-select[data-key="{filename}"]')
        badge = row.locator(".state-badge")
        await playwright.async_api.expect(badge).to_have_text("再開待ち")
        assert await badge.count() == 1
        assert await badge.evaluate(style) == await normal.evaluate(style)
        await playwright.async_api.expect(badge).to_have_attribute("title", "解除日時: 2099/01/02 09:00 JST")
        assert "再開待ち" in (await row.get_attribute("aria-label") or "").split("、")
        await playwright.async_api.expect(row).to_have_attribute("aria-label", re.compile("解除日時: 2099/01/02 09:00 JST"))
    question = page.locator('.entry-select[data-key="question.md"]')
    await playwright.async_api.expect(question.locator(".state-badge")).to_have_text("受信")
    assert await question.locator(".state-badge").get_attribute("title") is None
    question_label = await question.get_attribute("aria-label") or ""
    assert "再開待ち" not in question_label.split("、")
    assert "解除日時:" not in question_label
    await page.set_viewport_size({"width": 320, "height": 720})

    assert await question.locator(".status-cell").evaluate("""status => {
        const row = status.closest('.entry-select').getBoundingClientRect();
        return [...status.children].every(child => {
            const bounds = child.getBoundingClientRect();
            return bounds.left >= row.left && bounds.right <= row.right &&
                child.scrollWidth <= child.clientWidth;
        });
    }""")
    await question.focus()
    await page.keyboard.press("Enter")
    detail = page.locator("#detail-dialog")
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("受信")
    # 保存済みの期限は詳細のメタデータに保存値のまま残り、解除日時としては示さない。
    await playwright.async_api.expect(detail.locator("#detail-metadata")).to_contain_text(deadline)
    await playwright.async_api.expect(detail.locator("#detail-metadata")).not_to_contain_text("2099/01/02 09:00 JST")
    await detail.locator("#detail-more > summary").click()
    await detail.locator("#delete-button").click()
    deletion = page.get_by_role("dialog", name="削除の確認")
    await playwright.async_api.expect(deletion.locator("#delete-state")).to_have_text("受信")
    await deletion.locator("#delete-close-button").click()
    await detail.locator("#detail-close-button").click()
    await page.locator('.entry-select[data-key="verify.md"]').click()
    detail = page.locator("#detail-dialog")
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("再開待ち")
    await playwright.async_api.expect(detail.locator("#detail-metadata")).to_contain_text("2099/01/02 09:00 JST")
    await detail.locator("#detail-more > summary").click()
    await detail.locator("#delete-button").click()
    deletion = page.get_by_role("dialog", name="削除の確認")
    await playwright.async_api.expect(deletion.locator("#delete-state")).to_have_text("再開待ち")
    await page.clock.run_for(11000)
    await playwright.async_api.expect(warning).to_have_text(warning_text)
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("確認待ち")
    await playwright.async_api.expect(deletion.locator("#delete-state")).to_have_text("確認待ち")
    for filename, expected in [("verify.md", "確認待ち"), ("plain.md", "受信"), ("question.md", "受信")]:
        row = page.locator(f'.entry-select[data-key="{filename}"]')
        await playwright.async_api.expect(row.locator(".state-badge")).to_have_text(expected)
        assert "再開待ち" not in (await row.get_attribute("aria-label") or "").split("、")
        assert await row.locator(".state-badge").get_attribute("title") is None
        assert "解除日時:" not in (await row.get_attribute("aria-label") or "")


@pytest.mark.asyncio(loop_scope="session")
async def test_cooldown_expiry_updates_remaining_blocked_reasons(
    browser_harness: _BrowserHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = browser_harness
    page = harness.page
    now = datetime.datetime(2099, 1, 2, tzinfo=datetime.UTC)
    until = now + datetime.timedelta(seconds=20)
    calculate = readiness.calculate_readiness
    await page.clock.install(time=now)
    monkeypatch.setattr(readiness, "calculate_readiness", functools.partial(calculate, now=now))
    prefix = "---\nstate: inbox\ntarget_repo: github.com/example/repo\n"
    _entry_path(harness.root, "prerequisite.md").write_text(prefix + "type: awi\n---\n\n前提\n", encoding="utf-8")
    _entry_path(harness.root, "question.md").write_text(prefix + "type: uwi\n---\n\n## 回答\n", encoding="utf-8")
    examples = {
        "free.md": ("", ""),
        "awi.md": ("depends_on: [prerequisite.md]\n", "依存先の処理待ち"),
        "uwi.md": ("depends_on: [question.md]\n", "回答待ち"),
        "invalid.md": ("depends_on: invalid\n", "依存先の指定が不正"),
    }
    for name, (dependency, _) in examples.items():
        _entry_path(harness.root, name).write_text(
            prefix + f"type: awi\ncooldown_until: '{until.isoformat()}'\n" + dependency + "---\n\n# 再開待ち\n",
            encoding="utf-8",
        )
    await page.goto(harness.base_url + "/")
    focused = page.locator('.entry-select[data-key="awi.md"]')
    await playwright.async_api.expect(focused.locator(".state-badge")).to_have_text("再開待ち")
    await focused.focus()
    monkeypatch.setattr(readiness, "calculate_readiness", functools.partial(calculate, now=until))
    await page.clock.run_for(21000)
    for name, (_, reason) in examples.items():
        row = page.locator(f'.entry-select[data-key="{name}"]')
        blocked = row.locator(".blocked-badge")
        await playwright.async_api.expect(row.locator(".state-badge")).to_have_text("受信", timeout=10000)
        if reason:
            await playwright.async_api.expect(blocked).to_have_text(reason)
            await playwright.async_api.expect(row).to_have_accessible_name(re.compile(reason))
        else:
            await playwright.async_api.expect(blocked).to_have_count(0)
    await playwright.async_api.expect(focused).to_be_focused()


@pytest.mark.asyncio(loop_scope="session")
async def test_cooldown_resyncs_after_changes_and_visibility(browser_harness: _BrowserHarness) -> None:
    """期限変更・解除・復帰時の再取得でも保存状態によるフィルターと表示を保つ。"""
    harness = browser_harness
    page = harness.page
    await page.clock.install(time=datetime.datetime(2099, 1, 2, tzinfo=datetime.UTC))
    future = "2099-01-03T00:00:00+00:00"
    for state_name in ["inbox", "hold", "processing", "adopted", "rejected"]:
        (_entry_path(harness.root, f"{state_name}.md")).write_text(
            f"---\ntype: awi\nstate: {state_name}\ncooldown_until: '{future}'\n---\n\n# 件名\n", encoding="utf-8"
        )
    for name, deadline in [
        ("now", "2099-01-02T00:00:00Z"),
        ("past", "2020-01-02T00:00:00Z"),
        ("naive", "2099-01-03T00:00:00"),
        ("invalid", "bad"),
        ("empty", ""),
    ]:
        (_entry_path(harness.root, f"{name}.md")).write_text(
            f"---\ntype: awi\nstate: inbox\ncooldown_until: '{deadline}'\n---\n\n# 件名\n", encoding="utf-8"
        )
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await _open_filters(page)
    await page.locator("#state-filter").select_option("all")
    for state_name in ["hold", "processing", "adopted", "rejected"]:
        await playwright.async_api.expect(page.locator(f'.entry-select[data-key="{state_name}.md"] .state-badge')).to_have_text(
            {"hold": "保留", "processing": "処理中", "adopted": "採用", "rejected": "却下"}[state_name]
        )
    for name in ["now", "past", "naive", "invalid", "empty"]:
        await playwright.async_api.expect(page.locator(f'.entry-select[data-key="{name}.md"] .state-badge')).to_have_text(
            "受信"
        )
    await page.locator("#state-filter").select_option("inbox")
    badge = page.locator('.entry-select[data-key="inbox.md"] .state-badge')
    await playwright.async_api.expect(badge).to_have_text("再開待ち")
    target = _entry_path(harness.root, "inbox.md")
    target.write_text("---\ntype: awi\nstate: inbox\n---\n\n# 件名\n", encoding="utf-8")
    await page.evaluate("window.dispatchEvent(new Event('focus'))")
    await playwright.async_api.expect(badge).to_have_text("受信")
    target.write_text(f"---\ntype: awi\nstate: inbox\ncooldown_until: '{future}'\n---\n\n# 件名\n", encoding="utf-8")
    await page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    await playwright.async_api.expect(badge).to_have_text("再開待ち")
    row_count = await page.locator("#entry-list .entry-select").count()
    # 非表示中のタイマー停止を再現し、時刻だけを期限後へ進めて復帰する。
    await page.evaluate("Object.defineProperty(document, 'visibilityState', {value: 'hidden', configurable: true})")
    await page.clock.set_system_time(datetime.datetime(2099, 1, 4, tzinfo=datetime.UTC))
    await page.evaluate("""() => {
        Object.defineProperty(document, 'visibilityState', {value: 'visible', configurable: true});
        document.dispatchEvent(new Event('visibilitychange'));
    }""")
    await playwright.async_api.expect(badge).to_have_text("受信")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(row_count)
    assert target.is_file()

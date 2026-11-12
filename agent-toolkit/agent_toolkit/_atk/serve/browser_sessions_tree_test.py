"""serveのbrowser_sessionsに関するtreeの試験。"""

from pathlib import Path

import playwright.async_api
import pytest

from agent_toolkit._atk.serve import sessions as serve_sessions
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_app,
    _browser_fixture,
    _isolate_creation_time_index,
    _screen_harness_fixture,
    _ScreenHarness,
    _serve,
    _write_entries,
)
from agent_toolkit._testing import session_tree
from agent_toolkit._testing.serve_browser_sessions_support import (  # noqa: F401  # pylint: disable=unused-import
    _LEGACY_REMOTE_HOST,
    _NEW_REMOTE_HOST,
    _remote_sessions_harness_fixture,
    _RemoteSessionsHarness,
)

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
async def test_subagent_records_open_from_the_parent_detail(screen_harness: _ScreenHarness) -> None:
    """親セッションの詳細からサブエージェント記録を開いて委譲元へ戻り、記録本体が無い項目は選択できない表示とする。"""
    harness = screen_harness
    await harness.page.goto(harness.base_url + "/sessions")
    await harness.page.locator('#sessions .session-item[data-engine="claude"]').click()

    items = harness.page.locator(".subagent-item")
    await items.first.wait_for(state="visible")
    assert await items.count() == 2
    assert "所在の調査" in await items.nth(0).inner_text()
    assert "設計の検討" in await items.nth(1).inner_text()
    # 記録本体が残っていない項目は開けないため選択できない。
    assert not await items.nth(0).is_disabled()
    assert await items.nth(1).is_disabled()
    # 起動の深さを字下げで表す。
    offsets = [
        await items.nth(index).evaluate("(element) => parseFloat(getComputedStyle(element).paddingLeft)") for index in (0, 1)
    ]
    assert offsets[1] > offsets[0]

    # 委譲元の記録は左ペインの一覧から選び直せるが、サブエージェントの記録は一覧に現れないため戻る操作を置く。
    assert await harness.page.locator("#detail .detail-back").count() == 0
    await items.nth(0).click()
    await harness.page.locator("#detail .event").first.wait_for(state="visible")
    assert "サブエージェントの発話" in await harness.page.locator("#detail").inner_text()
    assert await harness.page.locator("#detail .detail-back").inner_text() == "委譲元の記録へ戻る"

    await harness.page.locator("#detail .detail-back").click()
    await harness.page.locator("#detail .kind-thinking").wait_for(state="visible")
    assert "Claudeの発話" in await harness.page.locator("#detail").inner_text()
    assert await harness.page.locator("#detail .detail-back").count() == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_remote_subagents_are_shown_or_reported_as_unavailable(
    remote_sessions_harness: _RemoteSessionsHarness,
) -> None:
    """リモートホストのセッションでもサブエージェントを表示し、一覧を返さないホストでは取得不能を示す。"""
    harness = remote_sessions_harness
    await harness.page.goto(harness.base_url + "/sessions")
    await harness.page.locator("#sessions .session-item").nth(1).wait_for(state="visible")

    await harness.page.locator(f'#sessions .session-item[data-host="{_NEW_REMOTE_HOST}"]').click()
    await harness.page.locator(".subagent-item").first.wait_for(state="visible")
    assert "リモートの調査" in await harness.page.locator("#detail").inner_text()
    assert await harness.page.locator("#detail .unavailable").count() == 0

    # 一覧を返さない版のヘルパーが動くホストでは、サブエージェントが無い場合と区別して取得不能を示す。
    await harness.page.locator(f'#sessions .session-item[data-host="{_LEGACY_REMOTE_HOST}"]').click()
    await harness.page.locator("#detail .unavailable").first.wait_for(state="visible")
    assert "サブエージェントの一覧を取得できません" in await harness.page.locator("#detail").inner_text()
    assert await harness.page.locator(".subagent-item").count() == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_session_tree_expands_one_branch_and_retains_keyboard_focus(screen_harness: _ScreenHarness) -> None:
    """親の展開操作で子だけを表示し、子の選択後もフォーカスを維持する。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    rows = page.locator("#sessions .session-tree-row")
    await playwright.async_api.expect(rows).to_have_count(2)
    parent = rows.filter(has=page.locator('.session-item[data-engine="claude"]'))
    toggle = parent.locator(".session-tree-toggle")
    await toggle.focus()
    await page.keyboard.press("Enter")
    await playwright.async_api.expect(toggle).to_have_attribute("aria-expanded", "true")
    await playwright.async_api.expect(rows).to_have_count(3)
    child = rows.filter(has=page.locator('.session-item[data-path$="agent-first.jsonl"]'))
    await playwright.async_api.expect(child).to_have_attribute("aria-level", "2")
    child_button = child.locator(".session-item")
    await child_button.focus()
    await page.keyboard.press("Enter")
    await playwright.async_api.expect(child_button).to_be_focused()
    await playwright.async_api.expect(child_button).to_have_attribute("aria-current", "true")
    await toggle.focus()
    await page.keyboard.press("Enter")
    await playwright.async_api.expect(toggle).to_have_attribute("aria-expanded", "false")
    await playwright.async_api.expect(rows).to_have_count(2)


@pytest.mark.asyncio(loop_scope="session")
async def test_session_tree_places_children_of_every_launch_source_under_parents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    browser: playwright.async_api.Browser,
) -> None:
    """全ての起動経路の子が、親の展開で`aria-level`2として現れ、第1階層に現れない。

    テストコードの記録は実行系の異なる親子、Codexの親thread、登録簿の委譲元だけで親が決まる委譲先、
    Claude Codeのサブエージェント、件数上限で外れた親とその子を持つ。
    画面が子の実行系を条件にして親を検索すると、実行系の異なる子が第1階層へ並ぶ。
    """
    _isolate_creation_time_index(tmp_path, monkeypatch)
    _write_entries(tmp_path)
    state_dir = tmp_path / "state" / "agent-toolkit"
    tree = session_tree.write_session_tree(tmp_path / "claude", tmp_path / "codex", state_dir)
    monkeypatch.setattr(serve_sessions, "MAX_LIST_ENTRIES", session_tree.TOTAL_ENTRIES - 1)
    plans_root = tmp_path / "plans"
    plans_root.mkdir()
    app = _browser_app(
        tmp_path,
        plans_root,
        state_dir=state_dir,
    )
    async with _serve(app, browser) as (_context, page, port):
        await page.goto(f"http://127.0.0.1:{port}/sessions")
        rows = page.locator("#sessions .session-tree-row")
        parents = set(tree.expected_parents.values())
        await playwright.async_api.expect(rows).to_have_count(session_tree.TOTAL_ENTRIES - len(tree.expected_parents))
        top_level = page.locator('#sessions .session-tree-row[aria-level="1"] .session-item')
        top_paths = [await item.get_attribute("data-path") for item in await top_level.all()]
        assert set(top_paths) == parents
        for parent_path in sorted(parents):
            parent_row = rows.filter(has=page.locator(f'.session-item[data-path="{parent_path}"]'))
            await parent_row.locator(".session-tree-toggle").click()
        await playwright.async_api.expect(rows).to_have_count(session_tree.TOTAL_ENTRIES)
        for child_path in tree.expected_parents:
            child_row = rows.filter(has=page.locator(f'.session-item[data-path="{child_path}"]'))
            await playwright.async_api.expect(child_row).to_have_attribute("aria-level", "2")


@pytest.mark.asyncio(loop_scope="session")
async def test_session_tree_aligns_rows_without_children(screen_harness: _ScreenHarness) -> None:
    """子の無い行のマーカーは操作できず、Tab移動の対象にしない。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    rows = page.locator("#sessions .session-tree-row")
    await playwright.async_api.expect(rows).to_have_count(2)
    parent = rows.filter(has=page.locator(".session-tree-toggle[aria-expanded]"))
    leaf = rows.filter(has=page.locator(".session-tree-leaf"))
    await playwright.async_api.expect(parent).to_have_count(1)
    await playwright.async_api.expect(leaf).to_have_count(1)
    marker = leaf.locator(".session-tree-leaf")
    await playwright.async_api.expect(marker).to_have_text("−")
    await playwright.async_api.expect(marker).to_have_attribute("aria-hidden", "true")
    assert await marker.evaluate("(element) => element.tagName") == "SPAN"
    assert await leaf.locator("button.session-tree-toggle").count() == 0
    # Tab移動は子のある行の開閉ボタンと項目にだけ止まり、子の無い行の「−」には止まらない。
    await leaf.locator(".session-item").focus()
    await page.keyboard.press("Shift+Tab")
    focused_class = await page.evaluate("() => document.activeElement.className")
    assert "session-tree-leaf" not in focused_class

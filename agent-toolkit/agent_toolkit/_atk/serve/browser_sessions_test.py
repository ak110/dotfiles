"""serveのbrowser_sessionsに関するdetailの試験。"""

import asyncio
import functools
import json

import playwright.async_api
import pytest

from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _hold_route,
    _isolate_creation_time_index,
    _screen_harness_fixture,
    _ScreenHarness,
)
from agent_toolkit._testing.serve_browser_sessions_support import _remote_sessions_harness_fixture  # noqa: F401  # pylint: disable=unused-import

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("pending", ["list", "detail"])
async def test_session_drawer_keeps_user_open_during_initial_fetch(screen_harness: _ScreenHarness, pending: str) -> None:
    """一覧取得中とURLの詳細復元中の開閉操作を、初期化の完了後にも保つ。"""
    page = screen_harness.page
    await page.set_viewport_size({"width": 320, "height": 720})
    await page.goto(screen_harness.base_url + "/sessions")
    item = page.locator("#sessions .session-item").first
    await item.wait_for(state="attached")
    query = await item.evaluate("element => new URLSearchParams(element.dataset).toString()")
    started = asyncio.Event()
    release = asyncio.Event()
    pattern = "**/api/sessions/list" if pending == "list" else "**/api/sessions/detail?*"
    handler = functools.partial(_hold_route, started=started, release=release)
    await page.route(pattern, handler)
    try:
        await page.goto(screen_harness.base_url + "/sessions?" + query, wait_until="domcontentloaded")
        await asyncio.wait_for(started.wait(), timeout=10)
        menu = page.locator("#sessions-menu-btn")
        await menu.click()
        await playwright.async_api.expect(menu).to_have_attribute("aria-expanded", "true")
        # 購読は一覧より先に始まるため、初期化の完了は復元した詳細で観測する。
        release.set()
        await page.locator("#detail .event").first.wait_for(state="visible")
        await playwright.async_api.expect(menu).to_have_attribute("aria-expanded", "true")
        assert not await page.locator("#sessions-app > aside").evaluate("element => element.inert")
    finally:
        release.set()
        await page.unroute(pattern, handler)


@pytest.mark.asyncio(loop_scope="session")
async def test_session_list_omits_message_preview(screen_harness: _ScreenHarness) -> None:
    """セッション一覧には発話内容のプレビューを表示しない。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    first_item = page.locator("#sessions .session-item").first
    await first_item.wait_for(state="visible")

    child_classes = await first_item.locator(":scope > *").evaluate_all(
        "elements => elements.map(element => element.className)"
    )
    assert child_classes == ["session-cwd pane-item-title", "session-meta pane-item-meta"]


@pytest.mark.asyncio(loop_scope="session")
async def test_session_previous_and_next_buttons_navigate_list(screen_harness: _ScreenHarness) -> None:
    """セッション詳細の前後ボタンで一覧の隣接項目へ移動する。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    items = page.locator("#sessions .session-item")
    await playwright.async_api.expect(items).to_have_count(2)
    names = await items.all_inner_texts()
    await items.first.click()
    await page.locator("#sessions-next-btn").click()
    assert await page.locator('#sessions .session-item[aria-current="true"]').inner_text() == names[1]
    await page.locator("#sessions-prev-btn").click()
    assert await page.locator('#sessions .session-item[aria-current="true"]').inner_text() == names[0]


@pytest.mark.asyncio(loop_scope="session")
async def test_session_screen_lists_and_renders_both_engines(screen_harness: _ScreenHarness) -> None:
    """左ペインを内容と非表示の識別子で限定し、右ペインへ発話とツール入力を表示する。"""
    harness = screen_harness
    await harness.page.goto(harness.base_url + "/sessions")

    items = harness.page.locator("#sessions .session-item")
    await items.nth(1).wait_for(state="visible")
    assert await items.count() == 2
    listing = await harness.page.locator("#sessions").inner_text()
    assert "browser-test" in listing
    assert "/home/aki/proj" in listing
    assert "Claudeの発話" not in listing
    assert "11111111-2222-3333-4444-555555555555" not in listing
    # 実行系による限定の操作、実行系のバッジ、ホストの接続状態および件数の表示は画面へ現れない。
    for selector in ("#sessions .engine-badge", ".engine-filter", "#host-status", "#list-status"):
        assert await harness.page.locator(selector).count() == 0, selector

    # 文字列による限定。
    await harness.page.locator("#sessions-filter").fill("aaaaaaaa")
    await harness.page.wait_for_function("document.querySelectorAll('#sessions .session-item').length === 1")
    assert await harness.page.locator('#sessions .session-item[data-engine="codex"]').count() == 1

    await harness.page.locator("#sessions .session-item").first.click()
    await harness.page.locator("#detail .event").first.wait_for(state="visible")
    assert "Codexの発話" in await harness.page.locator("#detail").inner_text()
    developer = harness.page.locator("#detail .kind-developer")
    compacted = harness.page.locator("#detail .kind-compact_boundary")
    assert "開発者" in await developer.locator("summary").inner_text()
    assert "コンテキスト圧縮" in await compacted.locator("summary").inner_text()
    developer_background = await developer.locator(".event-kind").evaluate(
        "element => getComputedStyle(element).backgroundColor"
    )
    default_background = await harness.page.evaluate(
        """() => {
            const event = document.createElement("div");
            event.className = "event";
            const kind = document.createElement("span");
            kind.className = "event-kind";
            event.append(kind);
            document.body.append(event);
            const color = getComputedStyle(kind).backgroundColor;
            event.remove();
            return color;
        }"""
    )
    existing_backgrounds = []
    for kind in ("user", "assistant", "thinking", "tool_call", "tool_result", "compact_boundary"):
        existing_backgrounds.append(
            await harness.page.locator(f"#detail .kind-{kind} .event-kind").first.evaluate(
                "element => getComputedStyle(element).backgroundColor"
            )
        )
    assert developer_background != default_background
    assert developer_background not in existing_backgrounds
    assert await developer.evaluate("element => element.open")
    assert not await compacted.evaluate("element => element.open")
    # 名前の記録を持たないCodexの記録はエンジン名だけを見出しへ表示し、ホスト名と作業ディレクトリを表示しない。
    assert await harness.page.locator("#detail-title").inner_text() == "Codex"
    shell_summary = harness.page.locator("#detail .kind-tool_call", has_text="shell").locator("summary")
    assert "pwd" in await shell_summary.inner_text()
    broken_summary = harness.page.locator("#detail .kind-tool_call", has_text="broken").locator("summary")
    assert "invalid-json" not in await broken_summary.inner_text()

    await harness.page.locator("#sessions-filter").fill("Claudeの発話")
    await harness.page.locator('#sessions .session-item[data-engine="claude"]').wait_for(state="visible")
    assert await harness.page.locator('#sessions .session-item[data-engine="claude"]').count() == 1

    await harness.page.locator("#sessions-filter").fill("/home/aki/proj")
    await harness.page.wait_for_function("document.querySelectorAll('#sessions .session-item').length === 1")
    assert await harness.page.locator('#sessions .session-item[data-engine="claude"]').count() == 1

    await harness.page.locator("#sessions-filter").fill("browser-test")
    await harness.page.wait_for_function("document.querySelectorAll('#sessions .session-item').length === 3")

    await harness.page.locator("#sessions-filter").fill("")
    await harness.page.wait_for_function("document.querySelectorAll('#sessions .session-item').length === 2")
    await harness.page.set_viewport_size({"width": 390, "height": 844})
    await harness.page.locator("#sessions-menu-btn").click()
    await harness.page.locator('#sessions .session-item[data-engine="claude"]').click()
    await harness.page.locator("#detail .kind-thinking").wait_for(state="visible")
    tool_call = harness.page.locator("#detail .kind-tool_call").first
    assert "ツール呼び出し" in await tool_call.locator(".event-kind").inner_text()
    header_metrics = await tool_call.locator("summary").evaluate(
        """element => {
          const lineCount = child => {
            const range = document.createRange();
            range.selectNodeContents(child);
            return range.getClientRects().length;
          };
          const kind = element.querySelector('.event-kind');
          const time = element.querySelector('.event-time');
          const name = element.querySelector(':scope > span:not([class])');
          const input = element.querySelector('.event-input-summary');
          const inputStyle = getComputedStyle(input);
          return {
            kindLines: lineCount(kind),
            timeLines: lineCount(time),
            name: name.textContent,
            inputOverflow: input.scrollWidth > input.clientWidth,
            inputTextOverflow: inputStyle.textOverflow,
            inputWhiteSpace: inputStyle.whiteSpace,
          };
        }"""
    )
    assert header_metrics == {
        "kindLines": 1,
        "timeLines": 1,
        "name": "Bash",
        "inputOverflow": True,
        "inputTextOverflow": "ellipsis",
        "inputWhiteSpace": "nowrap",
    }
    # 本文のある思考は発言と同じく初期状態で開き、ツール呼び出しは畳む。
    assert await harness.page.locator("#detail .kind-thinking").evaluate("element => element.open")
    detail_text = await harness.page.locator("#detail").inner_text()
    assert "Claudeの発話" in detail_text
    assert "Claudeの思考" in detail_text
    assert "Bash" in detail_text
    tool_summaries = await harness.page.locator("#detail .kind-tool_call summary").all_inner_texts()
    assert any("Bash" in summary and "ls" in summary and "pwd" not in summary for summary in tool_summaries)
    assert any("Read" in summary and "/tmp/input.md" in summary for summary in tool_summaries)
    assert any("Search" in summary and "needle" in summary for summary in tool_summaries)
    assert "入力: 12" in await harness.page.locator("#detail > .secondary-text").first.inner_text()
    # 破損した行は該当セッションの警告として示し、他の発話を失わせない。
    assert "解析できない行が1件あります" in detail_text


@pytest.mark.asyncio(loop_scope="session")
async def test_session_details_use_exclusive_default_closed_sections(screen_harness: _ScreenHarness) -> None:
    """ツール呼び出しなどの畳む項目は初期状態では閉じ、同時に1項目だけを展開する。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator('#sessions .session-item[data-engine="claude"]').click()
    sections = page.locator('#detail details[data-exclusive-event="true"]')
    await sections.nth(1).wait_for(state="visible")

    assert not await sections.nth(0).evaluate("element => element.open")
    assert not await sections.nth(1).evaluate("element => element.open")
    await sections.nth(0).locator("summary").click()
    assert await sections.nth(0).evaluate("element => element.open")
    await sections.nth(1).locator("summary").click()
    await playwright.async_api.expect(sections.nth(0)).not_to_have_attribute("open", "")
    await playwright.async_api.expect(sections.nth(1)).to_have_attribute("open", "")


@pytest.mark.asyncio(loop_scope="session")
async def test_runtime_inserted_accordion(screen_harness: _ScreenHarness) -> None:
    """挿入本文だけを閉じ、一覧の検索値と通常発話を保つ。"""
    records_root = screen_harness.root.parent
    claude_path = records_root / "claude" / "projects" / "-home-aki-proj" / "11111111-2222-3333-4444-555555555555.jsonl"
    original_claude = claude_path.read_text(encoding="utf-8")
    claude_injected = [
        {"type": "user", "isMeta": True, "message": {"content": "構造標識の自動本文"}},
        {"type": "user", "message": {"content": [{"type": "text", "text": "Base directory for this skill: /plugin"}]}},
    ]
    claude_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in claude_injected) + original_claude,
        encoding="utf-8",
    )
    codex_path = next((records_root / "codex" / "sessions").rglob("rollout-*.jsonl"))
    original_codex = codex_path.read_text(encoding="utf-8").splitlines(keepends=True)
    codex_injected = {
        "type": "response_item",
        "payload": {"type": "message", "role": "developer", "content": [{"text": "You are Codex, an agent based on GPT-6."}]},
    }
    codex_path.write_text(
        original_codex[0] + json.dumps(codex_injected, ensure_ascii=False) + "\n" + "".join(original_codex[1:]),
        encoding="utf-8",
    )

    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    summaries = await page.evaluate("async () => (await (await fetch('/api/sessions/list')).json()).sessions")
    assert {item["engine"]: item["first_user_message"] for item in summaries} == {
        "claude": "Claudeの発話",
        "codex": "Codexの発話",
    }

    await page.locator('#sessions .session-item[data-engine="claude"]').click()
    injected = page.locator("#detail .kind-injected")
    await injected.nth(1).wait_for(state="visible")
    assert await injected.count() == 2
    assert not await injected.nth(0).evaluate("element => element.open")
    assert await page.locator("#detail .kind-user").first.evaluate("element => element.open")
    assert "自動挿入" in await injected.nth(0).locator("summary").inner_text()
    await injected.nth(0).locator("summary").click()
    assert "構造標識の自動本文" in await injected.nth(0).locator("pre").inner_text()
    await injected.nth(1).locator("summary").click()
    await playwright.async_api.expect(injected.nth(0)).not_to_have_attribute("open", "")
    await playwright.async_api.expect(injected.nth(1)).to_have_attribute("open", "")

    await page.locator('#sessions .session-item[data-engine="codex"]').click()
    codex_injected_section = page.locator("#detail .kind-injected")
    await codex_injected_section.wait_for(state="visible")
    assert not await codex_injected_section.evaluate("element => element.open")
    assert await page.locator("#detail .kind-developer").evaluate("element => element.open")


@pytest.mark.asyncio(loop_scope="session")
async def test_session_details_open_developer_by_default(screen_harness: _ScreenHarness) -> None:
    """ユーザー、アシスタントおよび開発者の本文を初期状態で開く。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator('#sessions .session-item[data-engine="codex"]').click()

    for kind in ("user", "assistant", "developer"):
        section = page.locator(f"#detail .kind-{kind}").first
        await section.wait_for(state="visible")
        assert await section.evaluate("element => element.open")


@pytest.mark.asyncio(loop_scope="session")
async def test_session_detail_toolbar_scrolls_with_content(screen_harness: _ScreenHarness) -> None:
    """狭い画面では右ペイン全体がスクロールし、タイトルとトークン表示も本文とともに移動する。"""
    page = screen_harness.page
    await page.set_viewport_size({"width": 900, "height": 360})
    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator('#sessions .session-item[data-engine="claude"]').click()
    await page.locator("#detail .event").first.wait_for(state="visible")
    await page.locator("#detail").evaluate(
        "element => { const spacer = document.createElement('div'); spacer.style.height = '600px'; element.append(spacer); }"
    )
    toolbar = page.locator("#screen-sessions main .toolbar")
    before = await toolbar.bounding_box()
    assert before is not None

    scroll_top = await page.locator("#screen-sessions main").evaluate(
        "element => { element.scrollTop = 120; return element.scrollTop; }"
    )
    after = await toolbar.bounding_box()

    assert scroll_top > 0
    assert after is not None
    assert after["y"] < before["y"]

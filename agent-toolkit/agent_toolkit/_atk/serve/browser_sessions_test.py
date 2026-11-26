import asyncio
import functools
import json
import os
from pathlib import Path
from urllib.parse import urlencode

import playwright.async_api
import pytest

from agent_toolkit._atk import run_skill_execution
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _hold_route,
    _isolate_creation_time_index,
    _screen_harness_fixture,
    _ScreenHarness,
)
from agent_toolkit._common import automated_prompt, skill_modes
from agent_toolkit._common.message_format import auto_message
from agent_toolkit._testing.serve_browser_sessions_support import _remote_sessions_harness_fixture  # noqa: F401  # pylint: disable=unused-import

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("pending", ["list", "detail"])
async def test_session_drawer_keeps_user_open_during_initial_fetch(screen_harness: _ScreenHarness, pending: str) -> None:
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

        release.set()
        await page.locator("#detail .event").first.wait_for(state="visible")
        await playwright.async_api.expect(menu).to_have_attribute("aria-expanded", "true")
        assert not await page.locator("#sessions-app > aside").evaluate("element => element.inert")
    finally:
        release.set()
        await page.unroute(pattern, handler)


@pytest.mark.asyncio(loop_scope="session")
async def test_session_list_omits_message_preview(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    first_item = page.locator("#sessions .session-item").first
    await first_item.wait_for(state="visible")

    listing = await page.locator("#sessions").inner_text()
    assert "browser-test" in listing
    assert "Claudeの発話" not in listing
    assert "Codexの発話" not in listing
    await first_item.click()
    await playwright.async_api.expect(page.locator("#detail")).to_contain_text("の発話")


@pytest.mark.asyncio(loop_scope="session")
async def test_session_previous_and_next_buttons_navigate_list(screen_harness: _ScreenHarness) -> None:
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
async def test_session_details_open_the_initial_work_but_not_empty_developer_or_followup(
    screen_harness: _ScreenHarness,
) -> None:
    (codex_record,) = (screen_harness.root.parent / "codex").rglob("rollout-*.jsonl")
    resources = auto_message("リソース案内", source="agent-toolkit", kind="plugin-resources")
    delivery = auto_message("最初の作業依頼", source="agents-server", kind="delivery")
    followup = auto_message("後からの追送", source="agents-server", kind="delivery")
    entries = [
        {"type": "session_meta", "payload": {"cwd": "/work"}},
        {"type": "response_item", "payload": {"type": "additional_tools", "role": "developer", "tools": []}},
        *[
            {
                "type": "response_item",
                "payload": {"type": "message", "role": role, "content": [{"type": "input_text", "text": text}]},
            }
            for role, text in (
                ("developer", " "),
                ("developer", "通常の開発者入力"),
                ("user", f"{resources}\n\n{delivery}"),
                ("user", followup),
            )
        ],
    ]
    codex_record.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator('#sessions .session-item[data-engine="codex"]').click()
    expect = playwright.async_api.expect
    await expect(page.locator("#detail .event")).to_have_count(4)
    await expect(page.locator("#detail .kind-developer[open]")).to_contain_text("通常の開発者入力")
    await expect(page.locator("#detail .kind-developer:not([open])")).to_have_count(1)
    await expect(page.locator("#detail .kind-injected[open]")).to_contain_text("最初の作業依頼")
    await expect(page.locator("#detail .kind-injected:not([open])")).to_have_count(1)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("engine", ["claude", "codex"])
async def test_session_details_open_run_skill_goal(screen_harness: _ScreenHarness, engine: str) -> None:
    goal = automated_prompt.wrap(
        run_skill_execution.build_goal(skill_modes.PROCESS_WI, "single-lane"),
        source=automated_prompt.SOURCE_RUN_SKILL,
        kind=automated_prompt.KIND_GOAL,
    )
    followup = auto_message("後からの追送", source="agents-server", kind="delivery")
    root = screen_harness.root.parent
    if engine == "claude":
        (record,) = (root / "claude" / "projects").glob("*/*.jsonl")
        entries = [
            {"type": "user", "cwd": "/home/aki/proj", "message": {"role": "user", "content": text}} for text in (goal, followup)
        ]
    else:
        (record,) = (root / "codex").rglob("rollout-*.jsonl")
        entries = [
            {"type": "session_meta", "payload": {"cwd": "/work"}},
            *[
                {
                    "type": "response_item",
                    "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
                }
                for text in (goal, followup)
            ],
        ]
    record.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator(f'#sessions .session-item[data-engine="{engine}"]').first.click()
    expect = playwright.async_api.expect
    await expect(page.locator("#detail .kind-injected[open]")).to_contain_text("工程を完遂する")
    await expect(page.locator("#detail .kind-injected:not([open])")).to_have_count(1)
    if directory := os.environ.get("ATK_SERVE_SCREENSHOT_DIR"):
        await page.screenshot(path=Path(directory) / f"007-{engine}.png")


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "両engineの保存会話を実ブラウザーで一覧から表示する代表。"
        "単独起動では共有Chromium準備と回収も同じ試験に計上する（Linux CPU実測0.811秒）。"
    ),
)
async def test_session_screen_lists_and_renders_both_engines(screen_harness: _ScreenHarness) -> None:
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

    for selector in ("#sessions .engine-badge", ".engine-filter", "#host-status", "#list-status"):
        assert await harness.page.locator(selector).count() == 0, selector

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

    assert await harness.page.locator("#detail .kind-thinking").evaluate("element => element.open")
    detail_text = await harness.page.locator("#detail").inner_text()
    assert "Claudeの発話" in detail_text
    assert "Claudeの思考" in detail_text
    assert "Bash" in detail_text
    tool_summaries = await harness.page.locator("#detail .kind-tool_call summary").all_inner_texts()
    assert any("Bash" in summary and "ls" in summary and "pwd" not in summary for summary in tool_summaries)
    assert any("Read" in summary and "/tmp/input.md" in summary for summary in tool_summaries)
    assert any("Search" in summary and "needle" in summary for summary in tool_summaries)
    assert "入力 12" in await harness.page.locator("#detail > .secondary-text").first.inner_text()

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


@pytest.mark.cpu_limit(
    1.5,
    reason=("挿入本文だけを閉じ、一覧の検索値と通常発話を保つ。実ブラウザーの観測とtrace回収が必要（CPU最大実測0.612秒）。"),
)
@pytest.mark.asyncio(loop_scope="session")
async def test_runtime_inserted_accordion(screen_harness: _ScreenHarness) -> None:
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
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator('#sessions .session-item[data-engine="codex"]').click()

    for kind in ("user", "assistant", "developer"):
        section = page.locator(f"#detail .kind-{kind}").first
        await section.wait_for(state="visible")
        assert await section.evaluate("element => element.open")


@pytest.mark.asyncio(loop_scope="session")
async def test_session_detail_toolbar_scrolls_with_content(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    await page.set_viewport_size({"width": 900, "height": 360})
    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator('#sessions .session-item[data-engine="claude"]').click()
    await page.locator("#detail .event").first.wait_for(state="visible")
    await page.locator("#detail").evaluate(
        "element => { const spacer = document.createElement('div'); spacer.style.height = '600px'; element.append(spacer); }"
    )
    toolbar = page.locator("#detail-title")
    reading_toolbar = page.locator("#session-reading")
    before = await toolbar.bounding_box()
    assert before is not None

    scroll_top = await page.locator("#screen-sessions main").evaluate(
        "element => { element.scrollTop = 120; return element.scrollTop; }"
    )
    after = await toolbar.bounding_box()

    assert scroll_top > 0
    assert after is not None
    assert after["y"] < before["y"]
    await playwright.async_api.expect(reading_toolbar.get_by_label("記録内検索")).to_be_in_viewport()
    await reading_toolbar.get_by_label("記録内検索").fill("確認")


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.cpu_limit(3.0, reason="Chromiumで4000件以上の表示・検索・リンク復元を操作する")
async def test_long_session_can_search_jump_and_link_without_initially_rendering_all(screen_harness: _ScreenHarness) -> None:
    (record,) = (screen_harness.root.parent / "claude" / "projects").glob("*/*.jsonl")
    entries = [
        {
            "type": "user" if index in (0, 4050) else "assistant",
            "cwd": "/home/aki/proj",
            "message": {"content": "目印の検索語" if index == 4050 else f"記録の発話 {index}"},
        }
        for index in range(4051)
    ]
    record.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator('#sessions .session-item[data-engine="claude"]').click()
    expect = playwright.async_api.expect
    events = page.locator("#detail > .event")
    await expect(events.first).to_be_visible()
    assert await events.count() < len(entries)
    await page.get_by_role("button", name="次のユーザー発話").click()
    await expect(events.last).to_contain_text("目印の検索語")
    await page.get_by_role("button", name="先頭へ", exact=True).click()
    assert await page.locator("#sessions-app main").evaluate("main => main.scrollTop") == 0
    await page.get_by_role("button", name="末尾へ", exact=True).click()
    await expect(events.last).to_be_in_viewport()
    await page.get_by_label("記録内検索").fill("目印の検索語")
    await expect(page.locator("#session-search-count")).to_have_text("1 / 1件")
    await expect(events.last).to_have_class("event kind-user event-search-match")
    link = await events.last.get_by_role("link", name="この発話へのリンク").get_attribute("href")
    assert link is not None
    await page.goto(link)
    await expect(page.locator("#detail .event-linked")).to_contain_text("目印の検索語")
    await page.get_by_label("会話だけ").check()
    assert "view=conversation" in page.url
    if directory := os.environ.get("ATK_SERVE_SCREENSHOT_DIR"):
        await page.screenshot(path=Path(directory) / "002-conversation-search.png")


@pytest.mark.asyncio(loop_scope="session")
async def test_sessions_and_agents_share_wi_reference_dialog(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    filename = "question.md"
    (record,) = (screen_harness.root.parent / "claude" / "projects").glob("*/*.jsonl")
    records = [
        {"type": "assistant", "cwd": "/home/aki/proj", "message": {"content": f"Markdownで `{filename}` を確認"}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": f"等幅で {filename} を確認"}]}},
    ]
    record.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in records), encoding="utf-8")
    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator('#sessions .session-item[data-engine="claude"]').click()
    await page.locator("#detail .kind-tool_result summary").click()
    links = page.locator("#detail").get_by_role("link", name=filename)
    expect = playwright.async_api.expect
    await expect(links).to_have_count(2)
    for link in await links.all():
        await link.click()
        await expect(page.get_by_role("dialog", name="参照先のワークアイテム").first).to_contain_text(filename)
        await page.keyboard.press("Escape")

    async def listing(route):
        await route.fulfill(json={"roots": [{"id": "fixture", "name": filename, "children": []}]})

    await page.route("**/api/agents/list?*", listing)
    await page.goto(screen_harness.base_url + "/agents")
    await page.locator("#screen-agents").get_by_role("link", name=filename).click()
    await expect(page.get_by_role("dialog", name="参照先のワークアイテム").first).to_contain_text(filename)


@pytest.mark.asyncio(loop_scope="session")
async def test_session_links_distinguish_blocks_on_the_same_record_line(screen_harness: _ScreenHarness) -> None:
    (record,) = (screen_harness.root.parent / "claude" / "projects").glob("*/*.jsonl")
    entry = {
        "type": "assistant",
        "cwd": "/home/aki/proj",
        "message": {"content": [{"type": "text", "text": "最初のブロック"}, {"type": "text", "text": "二番目のブロック"}]},
    }
    record.write_text("not-json\n" + json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
    page = screen_harness.page
    query = urlencode({"host": "browser-test", "engine": "claude", "path": record.as_posix()})
    await page.goto(screen_harness.base_url + "/sessions?" + query)
    entries = page.locator("#detail [data-record-line='2']")
    expect = playwright.async_api.expect
    await expect(entries).to_have_count(2)
    link = await entries.last.get_by_role("link", name="この発話へのリンク").get_attribute("href")
    assert link is not None and "ordinal=1" in link
    await page.goto(link)
    await expect(page.locator("#detail .event-linked")).to_have_count(1)
    await expect(page.locator("#detail .event-linked")).to_contain_text("二番目のブロック")
    await page.goto(link.replace("&ordinal=1", ""))
    await expect(page.locator("#detail .event-linked")).to_have_count(2)


@pytest.mark.asyncio(loop_scope="session")
async def test_session_line_link_survives_record_notification_during_open(screen_harness: _ScreenHarness) -> None:
    (record,) = (screen_harness.root.parent / "claude" / "projects").glob("*/*.jsonl")
    paragraph = {"type": "user", "cwd": "/home/aki/proj", "message": {"content": "長い会話の発話"}}
    linked = {"type": "assistant", "message": {"content": [{"type": "text", "text": text} for text in ("先行", "後続")]}}
    entries = [*([paragraph] * 24), linked, *([paragraph] * 24)]
    record.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")
    page = screen_harness.page
    await page.add_init_script("""
        const NativeEventSource = window.EventSource;
        window.EventSource = class extends NativeEventSource {
          constructor(url) {
            super(url);
            if (!new URL(url, location.href).pathname.endsWith('/api/sessions/events')) return;
            this.close();
            window.testSessionEvents = this;
            queueMicrotask(() => this.dispatchEvent(new Event('subscribed')));
          }
          get readyState() {
            return this === window.testSessionEvents ? NativeEventSource.OPEN : super.readyState;
          }
        };
    """)
    open_started, release_open = asyncio.Event(), asyncio.Event()
    detail_pattern = "**/api/sessions/detail?*"
    requested = 0

    async def hold_detail(route: playwright.async_api.Route) -> None:
        nonlocal requested
        index = requested
        requested += 1
        title, cost = (("遅い初回", 0.11), ("通知による初回表示", 0.22), ("再取得済み", 0.33))[index]
        if index == 0:
            open_started.set()
            await release_open.wait()
        response = await route.fetch()
        await route.fulfill(
            response=response,
            json={**await response.json(), "session_name": title, "cost_pending": False, "cost": {"known_usd": cost}},
        )

    await page.route(detail_pattern, hold_detail)
    selection = {"host": "browser-test", "engine": "claude", "path": record.as_posix()}
    expect = playwright.async_api.expect
    markers = page.locator("#detail .event-linked")
    title = page.locator("#detail-title")
    usage = page.locator("#detail-usage")
    main = page.locator("#screen-sessions main")

    async def notify_record_change() -> None:
        await page.evaluate(
            "selection => window.testSessionEvents.dispatchEvent(new MessageEvent('message', {"
            "data: JSON.stringify({...selection, type: 'record'})}))",
            selection,
        )

    try:
        await page.goto(
            screen_harness.base_url + "/sessions?" + urlencode({**selection, "line": 25}), wait_until="domcontentloaded"
        )
        await asyncio.wait_for(open_started.wait(), timeout=10)
        await notify_record_change()
        assert requested == 1
        release_open.set()
        await expect(title).to_contain_text("通知による初回表示")
        await expect(usage).to_contain_text("$0.22")
        await expect(markers).to_have_count(2)
        await expect(markers.first).to_be_in_viewport()
        assert await main.evaluate(
            "element => 0 < element.scrollTop && element.scrollTop + element.clientHeight < element.scrollHeight - 1"
        )

        await main.evaluate("element => { element.scrollTop = 0; }")
        await notify_record_change()
        await expect(title).to_contain_text("再取得済み")
        await expect(usage).to_contain_text("$0.33")
        await expect(markers).to_have_count(2)
        assert await main.evaluate("element => element.scrollTop") == 0
    finally:
        release_open.set()
        await page.unroute(detail_pattern, hold_detail)


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("ordinal", [None, 1])
async def test_session_line_link_tracks_record_position_after_repair(
    screen_harness: _ScreenHarness, ordinal: int | None
) -> None:
    (record,) = (screen_harness.root.parent / "claude" / "projects").glob("*/*.jsonl")

    def message(text: str) -> str:
        return json.dumps({"type": "user", "cwd": "/home/aki/proj", "message": {"content": text}})

    linked = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": text} for text in ("先行", "後続")]},
        }
    )
    filler = [message("長い会話の発話")] * 22

    def replace_record(earlier: str, target: str) -> None:
        replacement = record.with_suffix(".replacement")
        lines = [message("開始"), earlier, *filler, target, *filler, ""]
        replacement.write_text("\n".join(lines), encoding="utf-8")
        replacement.replace(record)

    replace_record("not-json", linked)
    params: dict[str, str | int] = {"host": "browser-test", "engine": "claude", "path": record.as_posix(), "line": 25}
    if ordinal is not None:
        params["ordinal"] = ordinal
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions?" + urlencode(params))
    markers = page.locator("#detail .event-linked")
    main = page.locator("#screen-sessions main")
    expect = playwright.async_api.expect
    expected = [["25", "0"], ["25", "1"]] if ordinal is None else [["25", "1"]]
    positions = "nodes => nodes.map(node => [node.dataset.recordLine, node.dataset.recordOrdinal])"
    await expect(markers).to_have_count(len(expected))
    assert await markers.evaluate_all(positions) == expected
    await main.evaluate("element => { element.scrollTop = 0; }")

    replace_record(message("修復した発話"), linked)
    await expect(page.locator("#detail [data-record-line='2']")).to_contain_text("修復した発話", timeout=10000)
    assert await markers.evaluate_all(positions) == expected
    assert await main.evaluate("element => element.scrollTop") == 0

    replace_record(message("修復した発話"), "not-json")
    await expect(page.locator("#detail [data-record-line='25']")).to_have_count(0, timeout=10000)
    await expect(markers).to_have_count(0)


@pytest.mark.asyncio(loop_scope="session")
async def test_session_report_markdown_and_result_follow_the_view_mode(screen_harness: _ScreenHarness) -> None:
    (record,) = (screen_harness.root.parent / "claude" / "projects").glob("*/*.jsonl")
    entries = [
        {"type": "user", "cwd": "/home/aki/proj", "message": {"content": "依頼"}},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "report",
                        "name": "mcp__plugin_agent-toolkit_agents_server__send_to_user",
                        "input": {"message": "**報告の本文**"},
                    }
                ]
            },
        },
        {
            "type": "user",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "report", "content": '{"ok": true}'}]},
        },
    ]
    record.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    page = screen_harness.page
    query = urlencode({"host": "browser-test", "engine": "claude", "path": record.as_posix()})
    await page.goto(screen_harness.base_url + "/sessions?" + query)
    report = page.locator("#detail .kind-user_report")
    expect = playwright.async_api.expect
    await expect(report.locator("strong")).to_have_text("報告の本文")
    await expect(report.locator(".event-kind").first).to_contain_text("成功")
    result = report.locator(".kind-tool_result")
    await result.locator("summary").click()
    await expect(result.locator("pre")).to_be_visible()
    if directory := os.environ.get("ATK_SERVE_SCREENSHOT_DIR"):
        await page.screenshot(path=Path(directory) / "002-user-report.png")
    await page.get_by_label("会話だけ").check()
    await expect(report.locator("strong")).to_have_text("報告の本文")
    await expect(result).to_have_count(0)


@pytest.mark.asyncio(loop_scope="session")
async def test_full_text_search_keeps_matches_and_only_retries_temporary_records(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    records: list[list[str]] = []
    queries: list[dict] = []

    async def list_sessions(route: playwright.async_api.Route) -> None:
        response = await route.fetch()
        payload = await response.json()
        if not records:
            records.extend([[entry["host"], entry["engine"], entry["path"]] for entry in payload["sessions"]])
        await route.fulfill(response=response)

    async def search(route: playwright.async_api.Route) -> None:
        payload = route.request.post_data_json
        assert isinstance(payload, dict)
        queries.append(payload)
        if len(queries) == 1:
            await route.fulfill(
                json={
                    "matches": records[:1],
                    "incomplete": True,
                    "unavailable": [records[0][0]],
                    "retry": {records[1][0]: [records[1][2]]},
                }
            )
        else:
            await route.fulfill(json={"matches": records[1:], "incomplete": False, "unavailable": [], "retry": {}})

    await page.route("**/api/sessions/list", list_sessions)
    await page.route("**/api/sessions/search", search)
    await page.goto(screen_harness.base_url + "/sessions")
    expect = playwright.async_api.expect
    items = page.locator("#sessions .session-item")
    await expect(items).to_have_count(2)
    await page.locator("#sessions-filter").fill("fixture-search-unsupplied")
    await expect(page.locator("#sessions-search-status")).to_contain_text("検索できない記録があります。")
    for path in (records[0][2], records[1][2]):
        await expect(page.locator(f'#sessions .session-item[data-path="{path}"]')).to_be_visible()
    assert len(queries) == 2
    assert queries[1]["retry"] == {records[1][0]: [records[1][2]]}
    await expect(page.locator("#update-status")).to_be_hidden()


@pytest.mark.asyncio(loop_scope="session")
async def test_full_text_search_abandons_retry_when_screen_changes(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    attempts = 0
    noticed = asyncio.Event()

    async def search(route: playwright.async_api.Route) -> None:
        nonlocal attempts
        attempts += 1
        await route.fulfill(json={"matches": [], "incomplete": True, "retry": {"browser-test": None}})
        noticed.set()

    await page.route("**/api/sessions/search", search)
    await page.goto(screen_harness.base_url + "/sessions")
    await page.clock.install()
    await page.locator("#sessions-filter").fill("fixture-search-unsupplied")
    await asyncio.wait_for(noticed.wait(), 5)
    await page.get_by_role("navigation", name="画面").get_by_role("link", name="計画ファイル").click()
    await page.clock.fast_forward(31_000)
    assert attempts == 1

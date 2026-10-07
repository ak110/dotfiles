"""`atk serve`のセッション画面の実ブラウザー統合テスト。"""

import asyncio
import base64
import contextlib
import dataclasses
import json
import re
import shutil
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import playwright.async_api
import pytest
import pytest_asyncio

from agent_toolkit._atk.serve import sessions as serve_sessions

# pytestがテストの引数名で参照するfixtureを、このモジュールへ登録する。
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _BROWSER_TEST_ENV,
    _browser_app,
    _browser_fixture,
    _browser_tests_enabled,
    _isolate_creation_time_index,
    _screen_harness_fixture,
    _ScreenHarness,
    _serve,
    _valid_diagram_markdown,
    _write_entries,
)
from agent_toolkit._testing import session_tree

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        not _browser_tests_enabled(),
        reason=f"{_BROWSER_TEST_ENV}=1の場合のみ実行する",
    ),
]


@dataclasses.dataclass
class _RemoteSessionsHarness:
    page: playwright.async_api.Page
    context: playwright.async_api.BrowserContext
    base_url: str


_NEW_REMOTE_HOST = "new-host"
_LEGACY_REMOTE_HOST = "legacy-host"
_REMOTE_RECORD_PATH = "/home/remote/.claude/projects/-home-remote-proj/33333333-4444-5555-6666-777777777777.jsonl"


async def _remote_sessions_runner(host: str, op: str, _args: list[str]) -> str:
    """リモートヘルパーの応答を模す。旧版のホストは`read`応答へサブエージェント一覧を含めない。

    `read`の対象は1件だけであり、渡されたパスを見分ける必要が無いため引数を使わない。
    """
    if op == "list":
        return json.dumps(
            {
                "ok": True,
                "host": host,
                "entries": [
                    {
                        "engine": "claude",
                        "cwd": "/home/remote/proj",
                        "first_user_message": "リモートの最初の発話",
                        "session_id": f"{host}-session",
                        "path": _REMOTE_RECORD_PATH,
                        "updated_at": 1_800_000_000,
                        "size": 120,
                    }
                ],
            },
            ensure_ascii=False,
        )
    text = (
        json.dumps(
            {"type": "user", "timestamp": "2026-09-01T00:00:00Z", "message": {"content": f"{host}の発話"}},
            ensure_ascii=False,
        )
        + "\n"
    )
    payload: dict[str, Any] = {"ok": True, "data": base64.b64encode(text.encode("utf-8")).decode("ascii")}
    if host == _NEW_REMOTE_HOST:
        payload["subagents"] = [
            {
                "agent_id": "agent-remote",
                "agent_type": "Explore",
                "description": "リモートの調査",
                "spawn_depth": 1,
                "parent_agent_id": None,
                "model": "opus",
                "path": None,
            }
        ]
    return json.dumps(payload, ensure_ascii=False)


@pytest_asyncio.fixture(name="remote_sessions_harness")
async def _remote_sessions_harness_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    browser: playwright.async_api.Browser,
) -> AsyncGenerator[_RemoteSessionsHarness]:
    """サブエージェント一覧を返すリモートホストと、返さない旧版のリモートホストを登録した画面を用意する。"""
    _isolate_creation_time_index(tmp_path, monkeypatch)
    _write_entries(tmp_path)
    plans_root = tmp_path / "plans"
    plans_root.mkdir()
    (plans_root / "plan.md").write_text(_valid_diagram_markdown("初回"), encoding="utf-8")
    # 常駐接続は実際のsshを起動するため開始させず、単発SSHの差し替えだけでリモートの応答を与える。
    monkeypatch.setattr(serve_sessions, "start_remote_clients", lambda context: None)
    app = _browser_app(
        tmp_path,
        plans_root,
        remote_hosts=[_NEW_REMOTE_HOST, _LEGACY_REMOTE_HOST],
        ssh_runner=_remote_sessions_runner,
    )
    async with _serve(app, browser) as (context, page, port):
        yield _RemoteSessionsHarness(page=page, context=context, base_url=f"http://127.0.0.1:{port}")


_SESSION_MARKDOWN = (
    "## 調査結果\n\n- 1つ目の項目\n- 2つ目の項目\n\n1. 手順\n\n| 列A | 列B |\n| --- | --- |\n| 値1 | 値2 |\n\n"
    "```python\nprint('コード')\n```\n\n`inline`のコード\n"
)
_MARKDOWN_REMOTE_HOST = "markdown-host"
_MARKDOWN_REMOTE_PATH = "/home/remote/.claude/projects/-home-remote-md/44444444-5555-6666-7777-888888888888.jsonl"


def _session_lines(*records: dict[str, Any]) -> str:
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def _assistant_record(text: str) -> dict[str, Any]:
    return {"type": "assistant", "timestamp": "2026-09-30T00:00:01Z", "message": {"content": [{"type": "text", "text": text}]}}


def _write_local_session(tmp_path: Path, name: str, *records: dict[str, Any]) -> str:
    """ローカルのClaude Codeの記録1件を、開始日時が既存の記録より新しい形で作成する。"""
    path = tmp_path / "claude" / "projects" / "-home-aki-md" / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    first = {"type": "user", "timestamp": "2026-09-30T00:00:00Z", "cwd": "/home/aki/md", "message": {"content": "整形の確認"}}
    path.write_text(_session_lines(first, *records), encoding="utf-8")
    return str(path)


async def _markdown_remote_runner(host: str, op: str, _args: list[str]) -> str:
    """整形の対象となるアシスタントの発言を含む記録を返す、リモートヘルパーの偽の応答。"""
    if op == "list":
        entry = {
            "engine": "claude",
            "cwd": "/home/remote/md",
            "first_user_message": "リモートの整形",
            "session_id": "remote-md",
            "path": _MARKDOWN_REMOTE_PATH,
            "updated_at": 1_800_000_000,
            "size": 200,
        }
        return json.dumps({"ok": True, "host": host, "entries": [entry]}, ensure_ascii=False)
    text = _session_lines(
        {"type": "user", "timestamp": "2026-09-01T00:00:00Z", "message": {"content": "リモートの整形"}},
        _assistant_record(_SESSION_MARKDOWN),
    )
    return json.dumps({"ok": True, "data": base64.b64encode(text.encode("utf-8")).decode("ascii"), "subagents": []})


@contextlib.asynccontextmanager
async def _markdown_sessions_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: playwright.async_api.Browser
) -> AsyncGenerator[tuple[playwright.async_api.Page, str]]:
    """ローカルの記録と、偽の応答を返すリモートホストを登録したセッション画面を開く。"""
    _isolate_creation_time_index(tmp_path, monkeypatch)
    _write_entries(tmp_path)
    plans_root = tmp_path / "plans"
    plans_root.mkdir()
    monkeypatch.setattr(serve_sessions, "start_remote_clients", lambda context: None)
    app = _browser_app(tmp_path, plans_root, remote_hosts=[_MARKDOWN_REMOTE_HOST], ssh_runner=_markdown_remote_runner)
    async with _serve(app, browser) as (_context, page, port):
        yield page, f"http://127.0.0.1:{port}"


async def _open_session(page: playwright.async_api.Page, base_url: str, host: str, path: str) -> None:
    await page.goto(base_url + "/sessions")
    await page.locator(f'#sessions .session-item[data-host="{host}"][data-path="{path}"]').click()
    await page.locator("#detail .event.kind-assistant").first.wait_for(state="visible")


def _append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """JSON Linesの記録へ行を追記する。記録が無ければ作成する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.writelines(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def _session_list_requests(harness: _ScreenHarness) -> int:
    return sum(1 for url in harness.requests if "/api/sessions/list" in url)


# 右ペインを末尾までスクロールする。
_SCROLL_DETAIL_TO_END_JS = (
    "() => { const main = document.querySelector('#screen-sessions main'); main.scrollTop = main.scrollHeight; }"
)


# 右ペインのスクロール位置から末尾までの距離（px）を返す。
_DETAIL_DISTANCE_FROM_END_JS = (
    "() => { const main = document.querySelector('#screen-sessions main');"
    " return main.scrollHeight - main.scrollTop - main.clientHeight; }"
)


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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
    assert "/home/aki/other" in await harness.page.locator("#detail-title").inner_text()
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
    # 思考とツール呼び出しは初期状態では畳み、要求された時だけ本文を展開する。
    await harness.page.locator("#detail .kind-thinking summary").click()
    detail_text = await harness.page.locator("#detail").inner_text()
    assert "Claudeの発話" in detail_text
    assert "Claudeの思考" in detail_text
    assert "Bash" in detail_text
    tool_summaries = await harness.page.locator("#detail .kind-tool_call summary").all_inner_texts()
    assert any("Bash" in summary and "ls" in summary and "pwd" not in summary for summary in tool_summaries)
    assert any("Read" in summary and "/tmp/input.md" in summary for summary in tool_summaries)
    assert any("Search" in summary and "needle" in summary for summary in tool_summaries)
    assert "入力: 12" in await harness.page.locator("#detail-usage").inner_text()
    # 破損した行は該当セッションの警告として示し、他の発話を失わせない。
    assert "解析できない行が1件あります" in detail_text


@pytest.mark.asyncio
async def test_session_details_use_exclusive_default_closed_sections(screen_harness: _ScreenHarness) -> None:
    """思考とツール呼び出しは初期状態では畳み、同時に1項目だけを展開する。"""
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


@pytest.mark.asyncio
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
        "payload": {"type": "message", "role": "developer", "content": [{"text": "<permissions instructions>自動指示"}]},
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


@pytest.mark.asyncio
async def test_session_details_open_developer_by_default(screen_harness: _ScreenHarness) -> None:
    """ユーザー、アシスタントおよび開発者の本文を初期状態で開く。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator('#sessions .session-item[data-engine="codex"]').click()

    for kind in ("user", "assistant", "developer"):
        section = page.locator(f"#detail .kind-{kind}").first
        await section.wait_for(state="visible")
        assert await section.evaluate("element => element.open")


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_session_assistant_markdown_rendered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: playwright.async_api.Browser
) -> None:
    """ローカルとリモートの記録で、アシスタントの発言の見出し・箇条書き・表・コードを要素として表示する。

    `<pre>`のまま表示すると、Markdownの記号が並んだ本文を読み手が頭の中で組み立てる必要がある。
    リモートの記録はヘルパーが生データだけを返し、整形はサーバー側で行う。
    """
    path = _write_local_session(tmp_path, "markdown", _assistant_record(_SESSION_MARKDOWN))
    async with _markdown_sessions_page(tmp_path, monkeypatch, browser) as (page, base_url):
        for host, record_path in (("browser-test", path), (_MARKDOWN_REMOTE_HOST, _MARKDOWN_REMOTE_PATH)):
            await _open_session(page, base_url, host, record_path)
            body = page.locator("#detail .event.kind-assistant .event-markdown")
            await playwright.async_api.expect(body.locator("h2")).to_have_text("調査結果")
            assert await body.locator("ul > li").count() == 2
            assert await body.locator("ol > li").count() == 1
            assert await body.locator("table th").all_inner_texts() == ["列A", "列B"]
            await playwright.async_api.expect(body.locator("pre code")).to_have_text("print('コード')")
            await playwright.async_api.expect(body.locator(":not(pre) > code")).to_have_text("inline")
            text = await body.inner_text()
            assert "## " not in text
            assert "| ---" not in text
            assert await page.locator("#detail .event.kind-assistant > pre").count() == 0


@pytest.mark.asyncio
async def test_session_non_assistant_events_stay_preformatted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: playwright.async_api.Browser
) -> None:
    """アシスタント以外の発言・挿入本文・思考・ツール・圧縮のイベントは、記号をそのまま`<pre>`で表示する。"""
    path = _write_local_session(
        tmp_path,
        "preformatted",
        {"type": "user", "isMeta": True, "timestamp": "2026-09-30T00:00:01Z", "message": {"content": "## 挿入本文"}},
        {
            "type": "assistant",
            "timestamp": "2026-09-30T00:00:02Z",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": "## 思考"},
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "echo '## ツール'"}},
                    {"type": "text", "text": "## 発言"},
                ]
            },
        },
        {
            "type": "user",
            "timestamp": "2026-09-30T00:00:03Z",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "## 結果"}]},
        },
        {"type": "user", "timestamp": "2026-09-30T00:00:04Z", "message": {"content": "## ユーザー"}},
        {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-09-30T00:00:05Z", "content": "## 圧縮"},
    )
    async with _markdown_sessions_page(tmp_path, monkeypatch, browser) as (page, base_url):
        await _open_session(page, base_url, "browser-test", path)
        for kind in ("user", "injected", "thinking", "tool_call", "tool_result", "compact_boundary"):
            events = page.locator(f"#detail .event.kind-{kind}")
            assert await events.count() >= 1, kind
            assert await events.locator(".event-markdown").count() == 0, kind
            assert await events.locator(":scope > pre").count() == await events.count(), kind
        await playwright.async_api.expect(page.locator("#detail .event.kind-user > pre").last).to_have_text("## ユーザー")


@pytest.mark.asyncio
async def test_session_assistant_markdown_escapes_raw_html(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: playwright.async_api.Browser
) -> None:
    """アシスタントの発言の生HTMLは文字として表示し、スクリプトを実行せず、`javascript:`をリンクにしない。"""
    hostile = (
        '<script>window.__sessionXss = 1</script>\n\n<img src=x onerror="window.__sessionXss = 2">\n\n'
        "[リンク](javascript:window.__sessionXss=3)\n"
    )
    path = _write_local_session(tmp_path, "hostile", _assistant_record(hostile))
    async with _markdown_sessions_page(tmp_path, monkeypatch, browser) as (page, base_url):
        await _open_session(page, base_url, "browser-test", path)
        body = page.locator("#detail .event.kind-assistant .event-markdown")
        await playwright.async_api.expect(body).to_contain_text("<script>window.__sessionXss = 1</script>")
        assert await body.locator("script, img").count() == 0
        assert await body.locator('a[href^="javascript:"]').count() == 0
        await page.wait_for_timeout(200)
        assert await page.evaluate("window.__sessionXss") is None


@pytest.mark.asyncio
async def test_session_assistant_markdown_scrolls_wide_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: playwright.async_api.Browser
) -> None:
    """長いコード行と幅の広い表は本文の中で横スクロールし、右ペインの横幅を超えない。"""
    columns = 24
    wide = (
        "```text\n"
        + "x" * 400
        + "\n```\n\n"
        + "| "
        + " | ".join(f"列{index}見出しの長い名前" for index in range(columns))
        + " |\n"
        + "|"
        + " --- |" * columns
        + "\n"
        + "| "
        + " | ".join(f"値{index}" for index in range(columns))
        + " |\n"
    )
    path = _write_local_session(tmp_path, "wide", _assistant_record(wide))
    async with _markdown_sessions_page(tmp_path, monkeypatch, browser) as (page, base_url):
        await page.set_viewport_size({"width": 1100, "height": 800})
        await _open_session(page, base_url, "browser-test", path)
        body = page.locator("#detail .event.kind-assistant .event-markdown")
        overflow = "element => [element.scrollWidth, element.clientWidth]"
        for selector in ("pre", "table"):
            scroll_width, client_width = await body.locator(selector).evaluate(overflow)
            assert scroll_width > client_width, selector
        detail_scroll, detail_client = await page.locator("#detail").evaluate(overflow)
        assert detail_scroll <= detail_client
        pane = await page.locator("#detail").evaluate("element => element.getBoundingClientRect().right")
        block = await page.locator("#detail .event.kind-assistant").evaluate("element => element.getBoundingClientRect().right")
        assert block <= pane


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_session_tree_places_children_of_every_launch_source_under_parents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    browser: playwright.async_api.Browser,
) -> None:
    """全ての起動経路の子が、親の展開で`aria-level`2として現れ、第1階層に現れない。

    テストコードの記録は実行系の異なる親子、Codexの親thread、登録簿の委譲元だけで親が決まる委譲先、
    Claude Codeのサブエージェント、件数上限で外れた親とその子を持つ。
    画面が親を子の実行系で引くと、実行系の異なる子が第1階層へ並ぶ。
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


@pytest.mark.asyncio
async def test_session_tree_aligns_rows_without_children(screen_harness: _ScreenHarness) -> None:
    """子の無い行も開閉欄の幅を持ち、操作できない「−」を表示して項目の開始位置を子のある行と揃える。"""
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
    # 展開済みの開閉ボタンの「−」と、子の無い行の「−」を表示から見分けられる。
    marker_opacity = await marker.evaluate("(element) => getComputedStyle(element).opacity")
    parent_toggle = parent.locator(".session-tree-toggle")
    await parent_toggle.click()
    await playwright.async_api.expect(parent_toggle).to_have_text("−")
    toggle_opacity = await parent_toggle.evaluate("(element) => getComputedStyle(element).opacity")
    assert float(marker_opacity) < float(toggle_opacity)
    await parent_toggle.click()
    assert await leaf.locator("button.session-tree-toggle").count() == 0
    parent_box = await parent.locator(".session-item").bounding_box()
    leaf_box = await leaf.locator(".session-item").bounding_box()
    assert parent_box is not None
    assert leaf_box is not None
    assert parent_box["x"] == leaf_box["x"]
    # Tab移動は子のある行の開閉ボタンと項目にだけ止まり、子の無い行の「−」には止まらない。
    await leaf.locator(".session-item").focus()
    await page.keyboard.press("Shift+Tab")
    focused_class = await page.evaluate("() => document.activeElement.className")
    assert "session-tree-leaf" not in focused_class


@pytest.mark.asyncio
async def test_session_list_updates_when_records_are_added(screen_harness: _ScreenHarness, tmp_path: Path) -> None:
    """画面を開いたまま保存された新しい記録が、再読み込みせずに左ペインへ現れる。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    items = page.locator("#sessions .session-item")
    await playwright.async_api.expect(items).to_have_count(2)

    _append_jsonl(
        tmp_path / "claude" / "projects" / "-home-aki-added" / "added-claude.jsonl",
        [{"type": "user", "timestamp": "2026-09-03T00:00:00Z", "cwd": "/home/aki/added-claude", "message": {"content": "a"}}],
    )
    _append_jsonl(
        tmp_path / "codex" / "sessions" / "2026" / "09" / "03" / "rollout-2026-09-03T00-00-00-added.jsonl",
        [
            {"type": "session_meta", "payload": {"cwd": "/home/aki/added-codex", "timestamp": "2026-09-03T00:00:01Z"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": "b"}]}},
        ],
    )

    await playwright.async_api.expect(items).to_have_count(4, timeout=5000)
    listing = page.locator("#sessions")
    await playwright.async_api.expect(listing).to_contain_text("/home/aki/added-claude")
    await playwright.async_api.expect(listing).to_contain_text("/home/aki/added-codex")


@pytest.mark.asyncio
async def test_session_list_refreshes_are_not_issued_concurrently(screen_harness: _ScreenHarness, tmp_path: Path) -> None:
    """一覧の取得中に`refresh`通知が続いても、一覧の要求を同時に2件以上発行せず、完了後に1回だけ取り直す。

    通知ごとに要求を重ねると、サーバーで同じ走査が重なって他の画面の応答まで遅れる。
    """
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    items = page.locator("#sessions .session-item")
    await playwright.async_api.expect(items).to_have_count(2)
    release = asyncio.Event()
    entered = asyncio.Event()
    concurrency = {"current": 0, "max": 0, "total": 0}

    async def hold_list(route: playwright.async_api.Route) -> None:
        concurrency["current"] += 1
        concurrency["total"] += 1
        concurrency["max"] = max(concurrency["max"], concurrency["current"])
        entered.set()
        try:
            await release.wait()
            await route.continue_()
        finally:
            concurrency["current"] -= 1

    await page.route("**/api/sessions/list", hold_list)
    try:
        for index in range(3):
            _append_jsonl(
                tmp_path / "claude" / "projects" / "-home-aki-burst" / f"burst-{index}.jsonl",
                [{"type": "user", "timestamp": f"2026-09-05T00:00:0{index}Z", "message": {"content": f"通知{index}"}}],
            )
            if index == 0:
                await asyncio.wait_for(entered.wait(), timeout=5)
            else:
                # 記録の変更監視は0.5秒の時間窓で通知をまとめるため、窓を越えて別々の`refresh`を届ける。
                await asyncio.sleep(1.0)
        assert concurrency["total"] == 1
        release.set()
        await playwright.async_api.expect(items).to_have_count(5, timeout=5000)
    finally:
        release.set()
        await page.unroute("**/api/sessions/list", hold_list)
    assert concurrency["max"] == 1
    # 取得中に届いた通知は完了後の1回の取り直しへまとまる。負荷で変更監視が3件の作成を1回の通知へ
    # まとめた場合は取り直しが生じないため、上限だけを判定する。
    assert concurrency["total"] <= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("opened_from", ["list", "subagent"])
async def test_session_detail_follows_appended_events_and_keeps_view_state(
    screen_harness: _ScreenHarness,
    tmp_path: Path,
    opened_from: str,
) -> None:
    """右ペインは末尾まで読み進めるたびに続きを自動で描画して記録の最後まで到達し、追記で表示状態を保つ。

    末尾より上を読んでいる間の追記では、開閉とスクロール位置を保ち、通知を表示せず、一覧も再取得しない。
    その後に末尾まで読み進めると追記したイベントへ到達する。サブエージェントの記録も同じ右ペインの描画で開く。
    続きの描画に操作が要ると、記録の後半と追記へ到達するたびにボタンを探す手間が生じる。
    """
    page = screen_harness.page
    await page.set_viewport_size({"width": 1280, "height": 600})
    parent = tmp_path / "claude" / "projects" / "-home-aki-long" / "long.jsonl"
    records: list[dict[str, Any]] = [
        {"type": "user", "timestamp": "2026-09-04T00:00:00Z", "cwd": "/home/aki/long", "message": {"content": "開始"}}
    ]
    for index in range(230):
        records.append(
            {
                "type": "assistant",
                "timestamp": "2026-09-04T00:00:01Z",
                "message": {"content": [{"type": "thinking", "thinking": f"思考{index}"}]},
            }
        )
    if opened_from == "list":
        path = parent
        _append_jsonl(path, records)
    else:
        _append_jsonl(parent, records[:1])
        subagents = parent.with_suffix("") / "subagents"
        subagents.mkdir(parents=True, exist_ok=True)
        (subagents / "agent-long.meta.json").write_text(
            json.dumps({"agentType": "Explore", "description": "長い調査", "spawnDepth": 1}, ensure_ascii=False),
            encoding="utf-8",
        )
        path = subagents / "agent-long.jsonl"
        _append_jsonl(path, records)
    # 記録の作成による一覧の再取得の通知は、画面を開く前に配信を終えさせ、追記の検証へ混ぜない。
    await asyncio.sleep(1.5)
    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator("#sessions .session-item", has_text="/home/aki/long").click()
    if opened_from == "subagent":
        await page.locator(".subagent-item", has_text="長い調査").click()
    events = page.locator("#detail details.event")
    await playwright.async_api.expect(events).to_have_count(100)
    await playwright.async_api.expect(page.get_by_role("button", name="さらに100件表示")).to_have_count(0)
    for expected in (200, 231):
        await page.evaluate(_SCROLL_DETAIL_TO_END_JS)
        await playwright.async_api.expect(events).to_have_count(expected)
    # 最初から展開されるユーザー発話を閉じ、折りたたまれた思考を1件開く。
    await events.nth(0).locator("summary").click()
    await events.nth(120).locator("summary").click()
    await playwright.async_api.expect(events.nth(120)).to_have_attribute("open", "")
    scroll_top = await page.evaluate(
        "() => { const main = document.querySelector('#screen-sessions main'); main.scrollTop = 1500; return main.scrollTop; }"
    )
    assert scroll_top > 0
    list_requests = _session_list_requests(screen_harness)

    _append_jsonl(
        path,
        [
            {
                "type": "assistant",
                "timestamp": "2026-09-04T00:00:02Z",
                "message": {"content": [{"type": "text", "text": "追記されたイベント"}]},
            }
        ],
    )

    await playwright.async_api.expect(events).to_have_count(232, timeout=5000)
    assert not await events.nth(0).evaluate("element => element.open")
    assert await events.nth(120).evaluate("element => element.open")
    assert await page.evaluate("() => document.querySelector('#screen-sessions main').scrollTop") == scroll_top
    await playwright.async_api.expect(page.get_by_role("button", name=re.compile("新しいイベント"))).to_have_count(0)
    await asyncio.sleep(1.0)
    assert _session_list_requests(screen_harness) == list_requests

    await page.evaluate(_SCROLL_DETAIL_TO_END_JS)
    await playwright.async_api.expect(page.locator("#detail").get_by_text("追記されたイベント")).to_be_in_viewport()


@pytest.mark.asyncio
async def test_session_detail_follows_the_tail_while_reading_the_latest_events(
    screen_harness: _ScreenHarness,
    tmp_path: Path,
) -> None:
    """末尾を読んでいる間の追記では通知を表示せず、追記した末尾へ追従する。"""
    page = screen_harness.page
    await page.set_viewport_size({"width": 1280, "height": 600})
    path = tmp_path / "claude" / "projects" / "-home-aki-tail" / "tail.jsonl"
    records: list[dict[str, Any]] = [
        {"type": "user", "timestamp": "2026-09-04T00:00:00Z", "cwd": "/home/aki/tail", "message": {"content": "開始"}}
    ]
    records += [
        {"type": "assistant", "timestamp": "2026-09-04T00:00:01Z", "message": {"content": f"応答{index}"}}
        for index in range(40)
    ]
    _append_jsonl(path, records)
    await asyncio.sleep(1.5)
    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator("#sessions .session-item", has_text="/home/aki/tail").click()
    events = page.locator("#detail details.event")
    await playwright.async_api.expect(events).to_have_count(41)
    await page.evaluate(
        "() => { const main = document.querySelector('#screen-sessions main'); main.scrollTop = main.scrollHeight; }"
    )

    _append_jsonl(path, [{"type": "assistant", "timestamp": "2026-09-04T00:00:02Z", "message": {"content": "末尾の追記"}}])

    await playwright.async_api.expect(events).to_have_count(42, timeout=5000)
    await page.wait_for_function(f"({_DETAIL_DISTANCE_FROM_END_JS})() <= 1")
    await playwright.async_api.expect(page.get_by_role("button", name=re.compile("新しいイベント"))).to_have_count(0)


@pytest.mark.asyncio
async def test_session_empty_state_distinguishes_records_without_user_messages(
    screen_harness: _ScreenHarness,
    tmp_path: Path,
) -> None:
    """発話を含む記録が1件も無いときは、記録の不在ではなく発話を含む記録が無いことと、自動で加わることを示す。"""
    shutil.rmtree(tmp_path / "claude")
    shutil.rmtree(tmp_path / "codex")
    _append_jsonl(tmp_path / "claude" / "projects" / "-home-aki-silent" / "silent.jsonl", [{"type": "permission-mode"}])
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")

    empty = page.locator("#sessions-empty")
    await playwright.async_api.expect(empty).to_be_visible()
    await playwright.async_api.expect(empty).to_contain_text("ユーザーの発話を含むセッション記録はまだありません")
    await playwright.async_api.expect(empty).to_contain_text("自動で一覧へ加わります")


@pytest.mark.asyncio
async def test_session_without_user_message_appears_after_first_message(
    screen_harness: _ScreenHarness,
    tmp_path: Path,
) -> None:
    """ユーザー発話を持たない記録は一覧に現れず、発話が追記されると再読み込みせずに現れる。"""
    silent_claude = tmp_path / "claude" / "projects" / "-home-aki-silent" / "silent.jsonl"
    _append_jsonl(silent_claude, [{"type": "permission-mode", "cwd": "/home/aki/silent-claude"}])
    silent_codex = tmp_path / "codex" / "sessions" / "2026" / "09" / "05" / "rollout-2026-09-05T00-00-00-silent.jsonl"
    _append_jsonl(silent_codex, [{"type": "session_meta", "payload": {"cwd": "/home/aki/silent-codex"}}])
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    items = page.locator("#sessions .session-item")
    await playwright.async_api.expect(items).to_have_count(2)
    listing = page.locator("#sessions")
    await playwright.async_api.expect(listing).not_to_contain_text("/home/aki/silent")

    _append_jsonl(silent_claude, [{"type": "user", "timestamp": "2026-09-05T00:00:00Z", "message": {"content": "今から"}}])
    _append_jsonl(silent_codex, [{"type": "response_item", "payload": {"type": "message", "role": "user", "content": []}}])

    await playwright.async_api.expect(items).to_have_count(4, timeout=5000)
    await playwright.async_api.expect(listing).to_contain_text("/home/aki/silent-claude")
    await playwright.async_api.expect(listing).to_contain_text("/home/aki/silent-codex")

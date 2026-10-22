"""推定費用の親子の表示範囲、料金表の基準日と単価不明を実ブラウザーで検証する。"""

import json
from pathlib import Path
from typing import Any

import playwright.async_api
import pytest

from agent_toolkit._atk.serve import sessions as serve_sessions
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_app,
    _browser_fixture,
    _isolate_creation_time_index,
    _observe_screen_requests,
    _screen_harness_fixture,
    _ScreenHarness,
    _serve,
    _valid_diagram_markdown,
    _wait_screen_requests_consumed,
    _write_entries,
)
from agent_toolkit._common.model_pricing import PRICE_DATE

pytestmark = [pytest.mark.browser]


def _priced_records(root: Path) -> None:
    """親の算出額はnative子を含み、別プロセスのCodexだけを追加する入力を用意する。"""
    parent = next((root / "claude" / "projects").glob("*/*.jsonl"))
    child = parent.with_suffix("") / "subagents" / "agent-first.jsonl"
    delegate = next((root / "codex" / "sessions").rglob("*.jsonl"))
    identity = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    records: list[tuple[Path, list[dict[str, Any]]]] = [
        (
            parent,
            [
                {"type": "user", "timestamp": "2026-09-01T00:00:00Z", "message": {"role": "user", "content": "費用の親"}},
                {
                    "type": "assistant",
                    "timestamp": "2026-09-01T00:00:01Z",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "launch",
                                "name": "mcp__agents_server__start",
                                "input": {"mode": "explore"},
                            }
                        ],
                    },
                },
                {
                    "type": "user",
                    "timestamp": "2026-09-01T00:00:02Z",
                    "message": {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "launch", "content": "完了"}],
                    },
                    "toolUseResult": {"session_id": identity},
                },
                {"type": "cost-state", "timestamp": "2026-09-01T00:00:05Z", "totalCostUSD": 3},
            ],
        ),
        (
            child,
            [
                {"type": "user", "timestamp": "2026-09-01T00:00:01Z", "message": {"role": "user", "content": "費用の子"}},
                {
                    "type": "assistant",
                    "timestamp": "2026-09-01T00:00:03Z",
                    "message": {
                        "id": "child",
                        "role": "assistant",
                        "model": "claude-opus-5-5",
                        "usage": {"input_tokens": 1000, "output_tokens": 100},
                        "content": [],
                    },
                },
            ],
        ),
        (
            delegate,
            [
                {"type": "session_meta", "timestamp": "2026-09-01T00:00:01Z", "payload": {"id": identity}},
                {"type": "turn_context", "timestamp": "2026-09-01T00:00:01Z", "payload": {"model": "gpt-6-sol"}},
                {
                    "type": "response_item",
                    "timestamp": "2026-09-01T00:00:01Z",
                    "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "費用の委譲先"}]},
                },
                {
                    "type": "event_msg",
                    "timestamp": "2026-09-01T00:00:04Z",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {"input_tokens": 100, "output_tokens": 50},
                            "total_token_usage": {"total_tokens": 150},
                        },
                    },
                },
            ],
        ),
    ]
    for path, entries in records:
        path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("width", [320, 1280])
async def test_parent_child_and_delegate_cost_scope(screen_harness: _ScreenHarness, width: int) -> None:
    """親は子孫込み、本文から開く子と一覧から開く委譲先は自身の額を表示する。"""
    harness = screen_harness
    # 更新通知を明示的に送る本試験ではwatchを止め、準備時の変更通知との混入を防ぐ。
    await serve_sessions.stop_local_watch_async(harness.sessions_context)
    _priced_records(harness.root.parent)
    page = harness.page
    await _observe_screen_requests(page)
    await page.set_viewport_size({"width": width, "height": 900})
    await page.goto(harness.base_url + "/sessions")
    if width == 320:
        await page.locator("#sessions-menu-btn").click()
    await page.locator('#sessions .session-item[data-engine="claude"]').first.click()
    await playwright.async_api.expect(page.locator("#detail-usage")).to_contain_text("$3.0007 USD")
    await playwright.async_api.expect(page.locator("#detail")).to_contain_text(f"料金表基準日: {PRICE_DATE}")
    await playwright.async_api.expect(page.locator("#detail")).to_contain_text("子孫を含む")
    await page.locator("#detail .subagent-item").first.click()
    await playwright.async_api.expect(page.locator("#detail-usage")).to_contain_text("$0.006 USD")
    await playwright.async_api.expect(page.locator("#detail")).to_contain_text("この記録")
    await page.locator("#detail .detail-back").click()
    await playwright.async_api.expect(page.locator("#detail-usage")).to_contain_text("$3.0007 USD")
    await _wait_screen_requests_consumed(page, ("/api/sessions/events",))
    listings = await page.evaluate("window.__screenRequests.completed['/api/sessions/list']")
    delegate = next((harness.root.parent / "codex" / "sessions").rglob("*.jsonl"))
    with delegate.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "event_msg",
                    "timestamp": "2026-09-01T00:00:06Z",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {"input_tokens": 100, "output_tokens": 50},
                            "total_token_usage": {"total_tokens": 300},
                        },
                    },
                }
            )
            + "\n"
        )
    await serve_sessions.deliver_record(harness.sessions_context.state, "browser-test", "codex", str(delegate))
    await playwright.async_api.expect(page.locator("#detail-usage")).to_contain_text("$3.0014 USD")
    assert await page.evaluate("window.__screenRequests.completed['/api/sessions/list']") == listings
    if width == 320:
        await page.locator("#sessions-menu-btn").click()
    await page.locator("#sessions .session-tree-toggle").first.click()
    await page.locator('#sessions .session-item[data-engine="codex"]').click()
    await playwright.async_api.expect(page.locator("#detail-usage")).to_contain_text("$0.0014 USD")
    await playwright.async_api.expect(page.locator("#detail")).to_contain_text("この記録")
    assert not await page.evaluate("document.documentElement.scrollWidth > window.innerWidth")


@pytest.mark.asyncio(loop_scope="session")
async def test_unknown_price_is_not_shown_as_zero(screen_harness: _ScreenHarness) -> None:
    """未知モデルの応答を無料として表示せず、単価不明の件数を示す。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator('#sessions .session-item[data-engine="claude"]').first.click()
    await playwright.async_api.expect(page.locator("#detail-usage")).to_contain_text("単価不明 1件")
    assert "$0" not in await page.locator("#detail-usage").inner_text()

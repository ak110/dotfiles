"""serveのbrowser_sessionsに関するupdatesの試験。"""

import asyncio
import json
import re
import shutil
from pathlib import Path
from typing import Any

import playwright.async_api
import pytest

from agent_toolkit._atk.serve import sessions as serve_sessions
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _isolate_creation_time_index,
    _observe_screen_requests,
    _screen_harness_fixture,
    _ScreenHarness,
)
from agent_toolkit._testing.serve_browser_sessions_support import (  # noqa: F401  # pylint: disable=unused-import
    _DETAIL_DISTANCE_FROM_END_JS,
    _SCROLL_DETAIL_TO_END_JS,
    _append_jsonl,
    _remote_sessions_harness_fixture,
    _session_list_requests,
    _wait_record_notification,
)

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
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


@pytest.mark.asyncio(loop_scope="session")
async def test_session_operations_record_open_and_notified_reload(screen_harness: _ScreenHarness, tmp_path: Path) -> None:
    """記録の選択から詳細の描画まで、`refresh`通知から一覧と詳細の再描画までを、それぞれ1件の操作として記録する。"""
    page = screen_harness.page
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
    await page.goto(screen_harness.base_url + "/sessions")
    items = page.locator("#sessions .session-item")
    await playwright.async_api.expect(items).to_have_count(2)
    _append_jsonl(
        tmp_path / "claude" / "projects" / "-home-aki-timed" / "timed.jsonl",
        [{"type": "user", "timestamp": "2026-09-04T00:00:00Z", "cwd": "/home/aki/timed", "message": {"content": "a"}}],
    )
    await playwright.async_api.expect(items).to_have_count(3, timeout=5000)
    async with page.expect_request(
        lambda request: request.url.endswith("/api/timings") and '"external-reload"' in (request.post_data or ""),
        timeout=30000,
    ):
        await page.evaluate("() => window.dispatchEvent(new Event('pagehide'))")
    records = [record for record in sent if record.get("screen") == "sessions"]

    def linked_routes(operation: dict) -> set[str]:
        return {
            record.get("route", "")
            for record in records
            if record.get("operation_id") == operation["operation_id"] and record is not operation
        }

    opened = next(record for record in records if record.get("operation") == "open-session")
    assert (opened["kind"], opened["outcome"]) == ("operation", "ok")
    assert "/api/sessions/detail" in linked_routes(opened)
    reloaded = next(
        record for record in records if record.get("operation") == "external-reload" and record.get("trigger") == "sse"
    )
    assert "/api/sessions/list" in linked_routes(reloaded)


@pytest.mark.asyncio(loop_scope="session")
async def test_session_list_refreshes_are_not_issued_concurrently(screen_harness: _ScreenHarness, tmp_path: Path) -> None:
    """一覧の取得中に`refresh`通知が続いても、一覧の要求を同時に2件以上発行せず、完了後に1回だけ取り直す。

    通知ごとに要求を重ねると、サーバーで同じ走査が重なって他の画面の応答まで遅れる。
    背景の再走査が完了後に送る通知も取得中に受信させ、走査の所要時間による追加取得を混ぜない。
    """
    page = screen_harness.page
    await page.add_init_script("""
        window.testSessionRefreshCount = 0;
        const OriginalEventSource = window.EventSource;
        window.EventSource = class extends OriginalEventSource {
          constructor(...args) {
            super(...args);
            this.addEventListener("message", event => {
              if (JSON.parse(event.data).type === "refresh") window.testSessionRefreshCount++;
            });
          }
        };
    """)
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
            received = await page.evaluate("window.testSessionRefreshCount")
            _append_jsonl(
                tmp_path / "claude" / "projects" / "-home-aki-burst" / f"burst-{index}.jsonl",
                [{"type": "user", "timestamp": f"2026-09-05T00:00:0{index}Z", "message": {"content": f"通知{index}"}}],
            )
            # 各作成の通知をブラウザーで受信してから次を作り、変更監視の時間窓によるまとめを避ける。
            await page.wait_for_function("count => window.testSessionRefreshCount > count", arg=received)
            if index == 0:
                await asyncio.wait_for(entered.wait(), timeout=5)
        received = await page.evaluate("window.testSessionRefreshCount")
        # 画面の取得要求を保留したまま一覧APIを直接呼び、再走査の完了と通知の受信を両方待つ。
        response = await page.request.get(screen_harness.base_url + "/api/sessions/list")
        assert response.ok
        async with asyncio.timeout(10):
            while screen_harness.sessions_context.state.local_rescan_running:
                await asyncio.sleep(0.01)
        await page.wait_for_function("count => window.testSessionRefreshCount > count", arg=received)
        assert concurrency["total"] == 1
        release.set()
        await playwright.async_api.expect(items).to_have_count(5, timeout=5000)
    finally:
        release.set()
        await page.unroute("**/api/sessions/list", hold_list)
    assert concurrency["max"] == 1
    # 作成と走査完了の通知は全て保留中に届いており、取り直し1回へまとまる。
    assert concurrency["total"] <= 2


@pytest.mark.asyncio(loop_scope="session")
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
    queue = await serve_sessions.subscribe(screen_harness.sessions_context.state)
    page = screen_harness.page
    await _observe_screen_requests(page)
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
    await _wait_record_notification(screen_harness, queue, path)
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
    # 最初から展開されるユーザー発話と、本文があるため展開される思考を1件閉じる。
    await events.nth(0).locator("summary").click()
    await events.nth(120).locator("summary").click()
    await playwright.async_api.expect(events.nth(120)).not_to_have_attribute("open", "")
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
    assert not await events.nth(120).evaluate("element => element.open")
    assert await page.evaluate("() => document.querySelector('#screen-sessions main').scrollTop") == scroll_top
    await playwright.async_api.expect(page.get_by_role("button", name=re.compile("新しいイベント"))).to_have_count(0)
    await page.wait_for_function("window.__screenRequests.pending === 0")
    assert _session_list_requests(screen_harness) == list_requests

    await page.evaluate(_SCROLL_DETAIL_TO_END_JS)
    await playwright.async_api.expect(page.locator("#detail").get_by_text("追記されたイベント")).to_be_in_viewport()


@pytest.mark.asyncio(loop_scope="session")
async def test_session_detail_follows_the_tail_while_reading_the_latest_events(
    screen_harness: _ScreenHarness,
    tmp_path: Path,
) -> None:
    """末尾を読んでいる間の追記では通知を表示せず、追記した末尾へ追従する。"""
    queue = await serve_sessions.subscribe(screen_harness.sessions_context.state)
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
    await _wait_record_notification(screen_harness, queue, path)
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


@pytest.mark.asyncio(loop_scope="session")
async def test_session_empty_state_distinguishes_records_without_user_messages(
    screen_harness: _ScreenHarness,
    tmp_path: Path,
) -> None:
    """発話を含む記録が1件も無いときは、発話を含む記録が無いことと、自動で加わることを示す。"""
    shutil.rmtree(tmp_path / "claude")
    shutil.rmtree(tmp_path / "codex")
    _append_jsonl(tmp_path / "claude" / "projects" / "-home-aki-silent" / "silent.jsonl", [{"type": "permission-mode"}])
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")

    empty = page.locator("#sessions-empty")
    await playwright.async_api.expect(empty).to_be_visible()
    await playwright.async_api.expect(empty).to_contain_text("ユーザーの発話を含むセッション記録はまだありません")
    await playwright.async_api.expect(empty).to_contain_text("自動で一覧へ加わります")


@pytest.mark.asyncio(loop_scope="session")
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


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("failure", ["list", "detail", "disappeared"])
async def test_session_failures_preserve_host_engine_and_record(screen_harness: _ScreenHarness, failure: str) -> None:
    """一覧の取得失敗と、選択した記録の取得失敗・消失を、実ブラウザーの画面操作で起こし、区別できる表示を確かめる。"""
    harness = screen_harness
    page = harness.page
    if failure == "list":
        await page.route("**/api/sessions/list", lambda route: route.fulfill(status=503, json={"error": "同じ失敗"}))
    elif failure == "detail":
        await page.route("**/api/sessions/detail?*", lambda route: route.fulfill(status=503, json={"error": "同じ失敗"}))
    await page.goto(harness.base_url + "/sessions")
    if failure == "list":
        alert = page.locator("#sessions-list-error")
        await playwright.async_api.expect(alert).to_contain_text("browser-test / 一覧取得")
        return
    item = page.locator('#sessions .session-item[data-engine="claude"]').first
    path = await item.get_attribute("data-path")
    assert path is not None
    await item.click()
    if failure == "disappeared":
        await page.locator("#detail .event").first.wait_for(state="visible")
        await page.route(
            "**/api/sessions/list", lambda route: route.fulfill(json={"sessions": [], "warnings": [], "roots": []})
        )
        await page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    alert = page.locator("#detail")
    await playwright.async_api.expect(alert).to_contain_text("セッション / browser-test / Claude")
    await playwright.async_api.expect(alert).to_contain_text(path)
    await playwright.async_api.expect(alert).to_contain_text("見つかりません" if failure == "disappeared" else "取得できません")

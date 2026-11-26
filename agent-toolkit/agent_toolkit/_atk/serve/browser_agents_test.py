import asyncio
import datetime
import json
import os
import pathlib
import re
import threading

import playwright.async_api
import pytest

from agent_toolkit import atk
from agent_toolkit._atk import usage_refresh
from agent_toolkit._atk.serve import sessions_local, sessions_remote
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_app,
    _browser_fixture,
    _screen_harness_fixture,
    _ScreenHarness,
    _serve,
    _write_session_records,
)
from agent_toolkit._common import session_activity, session_registry, state_paths
from agent_toolkit._testing.agents_server_commands_support import _write_completed_child_result, _write_root_sessions

pytestmark = [pytest.mark.browser]


@pytest.fixture(autouse=True)
def isolate_background_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    async def skip_resident_connection(_client: sessions_remote.RemoteSessionClient) -> None:
        pass

    monkeypatch.setattr(sessions_remote.RemoteSessionClient, "run", skip_resident_connection)
    monkeypatch.setattr(usage_refresh, "refresh_for_display", lambda *, force=False: {})


@pytest.mark.cpu_limit(
    0.65,
    reason="ホストの失敗通知からキーボードで移動し、独立して更新される記録への遷移まで実ブラウザーで確認する",
)
@pytest.mark.asyncio(loop_scope="session")
async def test_agent_record_link_and_independent_remote_failure(
    browser: playwright.async_api.Browser, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = "11111111-2222-3333-4444-555555555555"
    exited = threading.Event()

    def agent_listing(_terminated: bool) -> list[dict[str, object]]:
        return [
            {
                "id": session_id,
                "status": "終了" if exited.is_set() else "稼働中",
                "engine": "claude",
                "children": [{"id": "not-recorded", "tree_label": "└─ not-recorded", "engine": "agy", "status": "completed"}],
            }
        ]

    async def offline(*_args: object) -> str:
        raise OSError("接続できません")

    monkeypatch.setattr(sessions_local, "MAX_LIST_ENTRIES", 0)
    _write_session_records(tmp_path)
    plans = tmp_path / "plans"
    plans.mkdir()
    app = _browser_app(tmp_path, plans, remote_hosts=("old-host",), ssh_runner=offline, agent_listing=agent_listing)
    async with _serve(app, browser) as (_context, page, port):
        await page.goto(f"http://127.0.0.1:{port}/agents")
        panel = page.locator("#agents-hosts")
        banner = page.locator("#update-status")
        await playwright.async_api.expect(banner).to_contain_text("old-hostのエージェント一覧を取得できません。")
        await playwright.async_api.expect(page.locator("#screen-agents > header + #update-status")).to_be_visible()
        await playwright.async_api.expect(banner).not_to_contain_text("前回取得")
        await playwright.async_api.expect(banner.get_by_role("button", name="再取得")).to_be_visible()
        failed_host = page.locator("#agents-attention").get_by_role("button", name="old-host", exact=True)
        await failed_host.press("Enter")
        await playwright.async_api.expect(panel.get_by_role("heading", name="old-host", exact=True)).to_be_focused()
        await playwright.async_api.expect(panel.get_by_text("一覧は未取得です。")).to_be_visible()
        unsupported = panel.locator('[data-agent-id="not-recorded"]')
        await playwright.async_api.expect(unsupported).to_contain_text("セッション画面で扱いません")
        await playwright.async_api.expect(unsupported.get_by_text("記録を開けない", exact=True)).to_have_count(0)
        await playwright.async_api.expect(page.locator("#agents-attention button:visible")).to_have_count(1)
        await playwright.async_api.expect(panel).not_to_contain_text(re.compile(r"[\u2500-\u257f]"))
        record = panel.get_by_role("link", name=session_id, exact=True)
        await record.focus()
        exited.set()
        await playwright.async_api.expect(panel.get_by_text("終了", exact=True)).to_be_visible()
        await playwright.async_api.expect(record).to_be_focused()
        await record.press("Enter")
        await playwright.async_api.expect(page.locator("#screen-sessions")).to_be_visible()
        await playwright.async_api.expect(page.get_by_text("Claudeの発話", exact=True).first).to_be_visible()


@pytest.mark.asyncio(loop_scope="session")
async def test_agent_remote_record_lookup_failure_preserves_agent_rows(
    browser: playwright.async_api.Browser, tmp_path: pathlib.Path
) -> None:
    async def remote(_host: str, op: str, _args: list[str]) -> str:
        if op != "agents-list":
            return "{}"
        return json.dumps(
            {
                "roots": [{"id": "remote-agent", "status": "稼働中", "engine": "codex", "children": []}],
                "records": None,
            }
        )

    plans = tmp_path / "plans"
    plans.mkdir()
    app = _browser_app(tmp_path, plans, remote_hosts=("remote-host",), ssh_runner=remote)
    async with _serve(app, browser) as (_context, page, port):
        await page.goto(f"http://127.0.0.1:{port}/agents")
        row = page.locator('[data-agent-id="remote-agent"]')
        await playwright.async_api.expect(row).to_contain_text("稼働中")
        await playwright.async_api.expect(row).to_contain_text("記録の所在を確認できません")
        await playwright.async_api.expect(page.locator("#agents-attention li:visible")).to_contain_text("記録を開けない 1件")
        await playwright.async_api.expect(row.get_by_role("link")).to_have_count(0)


@pytest.mark.cpu_limit(1.5, reason="活動記録を読み、実ブラウザーで端末相当の一覧を確認する。")
@pytest.mark.asyncio(loop_scope="session")
async def test_agent_activity_records_drive_root_states(
    browser: playwright.async_api.Browser, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    identity = session_registry.ProcessIdentity.of(os.getpid())
    assert identity is not None
    dead = session_registry.ProcessIdentity(2147483647, 1)
    started = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=10)).isoformat()
    for identifier, process in (("live", identity), ("alone", identity), ("finished", dead), ("broken", identity)):
        session_activity.record_activity(
            {"session_id": identifier, "hook_event_name": "SessionStart"},
            process=session_activity.HostProcess("codex", process),
            state_root=tmp_path,
        )
    (session_activity.activity_directory(tmp_path) / "broken.json").write_text("{", encoding="utf-8")
    for identifier in ("live", "finished", "missing", "broken"):
        _write_root_sessions(
            tmp_path,
            identifier,
            [
                {
                    "session_id": f"{identifier}-child",
                    "status": "completed" if identifier == "finished" else "running",
                    "result_available": identifier == "finished",
                    "label": "子の表示名",
                    "engine": "codex",
                    "model": "sol",
                    "effort": "low",
                    "last_action": "作業を検査",
                    "started_at": started,
                    "context_tokens": 2000,
                    "context_window": 8000,
                }
            ],
        )
    _write_root_sessions(
        tmp_path,
        "live",
        [
            {
                "session_id": "live-grandchild",
                "status": "running",
                "label": "再試行中の委譲先",
                "engine": "claude",
                "context_tokens": 92500,
                "context_window": 840000,
                "api_error": {"type": "overloaded", "first_at": started, "http_status": 529, "count": 3},
            }
        ],
        host_session_id="live-child",
    )
    _write_completed_child_result(tmp_path, "finished", "finished-child")
    plans = tmp_path / "plans"
    plans.mkdir()
    app = _browser_app(tmp_path, plans, agent_listing=atk.serve_agent_listing)
    async with _serve(app, browser) as (_context, page, port):
        await page.goto(f"http://127.0.0.1:{port}/agents")
        panel = page.locator("#agents-hosts")
        await playwright.async_api.expect(panel).to_contain_text("子の表示名")
        await playwright.async_api.expect(panel).not_to_contain_text(re.compile(r"[\u2500-\u257f]"))
        await playwright.async_api.expect(panel).not_to_contain_text("経過時間は不明")
        await playwright.async_api.expect(panel).not_to_contain_text("行動・文脈使用量は不明")
        for identifier, expected in (
            ("live", "稼働中"),
            ("alone", "稼働中"),
            ("finished", "終了"),
            ("missing", "不明"),
            ("broken", "不明"),
        ):
            root = page.locator(f'li[data-agent-id="{identifier}"]')
            await playwright.async_api.expect(root.locator(":scope > .agent-row > .agent-status .agent-value")).to_have_text(
                expected
            )
        child = page.locator('[data-agent-id="live"] [data-agent-id="live-child"]')
        await playwright.async_api.expect(child).to_contain_text("codex:sol/low")
        await playwright.async_api.expect(child).to_contain_text("作業を検査")
        await playwright.async_api.expect(child).to_contain_text("子の表示名")
        grandchild = child.locator(':scope > ul > li[data-agent-id="live-grandchild"]')
        await playwright.async_api.expect(grandchild.locator(".agent-action")).to_have_text(
            "API再試行 overloaded · HTTP 529 · 3回"
        )
        await playwright.async_api.expect(page.locator("#agents-attention")).to_contain_text("再試行 1件")
        await playwright.async_api.expect(page.locator("#agents-attention")).to_contain_text("状態不明 2件")
        await playwright.async_api.expect(panel.get_by_text("完了", exact=True)).to_be_visible()
        action = grandchild.locator("summary")
        await action.press("Space")
        await playwright.async_api.expect(grandchild.locator("details[open] > p")).to_have_text(
            "API再試行 overloaded · HTTP 529 · 3回"
        )
        await playwright.async_api.expect(child.locator(":scope > .agent-row .agent-usage .agent-value")).to_have_text(
            "2.0k/25%"
        )
        await playwright.async_api.expect(grandchild.locator(".agent-usage .agent-value")).to_have_text("92.5k/11%")
        for width in (1280, 360):
            await page.set_viewport_size({"width": width, "height": 900})
            parent_boxes, child_boxes = await panel.evaluate("""(panel) =>
                ["live-child", "live-grandchild"].map(id => ["status", "elapsed", "usage"].map(column => {
                  const cell = panel.querySelector(`[data-agent-id="${id}"] > .agent-row .agent-${column}`);
                  const {x, width} = cell.getBoundingClientRect();
                  return [x, width];
                }))""")
            for parent_box, child_box in zip(parent_boxes, child_boxes, strict=True):
                assert parent_box[1] > 0 and child_box[1] > 0
                assert parent_box == pytest.approx(child_box)
            await playwright.async_api.expect(grandchild.locator(".agent-elapsed")).to_have_css(
                "font-variant-numeric", "tabular-nums"
            )
            await grandchild.locator(".agent-status").scroll_into_view_if_needed()
            await playwright.async_api.expect(grandchild.locator(".agent-status")).to_be_in_viewport()


@pytest.mark.asyncio(loop_scope="session")
async def test_agent_filter_abandons_old_failure(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    first = asyncio.Event()
    release = asyncio.Event()

    async def serve_list(route: playwright.async_api.Route) -> None:
        if "terminated=0" in route.request.url:
            first.set()
            await release.wait()
            await route.fulfill(status=503)
        else:
            await route.fulfill(json={"roots": []})

    await page.route("**/api/agents/list?*", serve_list)
    try:
        await page.goto(screen_harness.base_url + "/agents")
        await asyncio.wait_for(first.wait(), 5)
        async with page.expect_response(lambda response: "terminated=1" in response.url):
            await page.locator("#agents-terminated").check()
        await playwright.async_api.expect(page.locator("#update-status")).to_be_hidden()
    finally:
        release.set()


@pytest.mark.asyncio(loop_scope="session")
async def test_agent_progress_and_last_success_survive_a_read_failure(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    release = asyncio.Event()
    roots: list[dict[str, object]] | None = None

    async def agents(route: playwright.async_api.Route) -> None:
        await release.wait()
        payload = {"error": "一覧を返せません"} if roots is None else {"roots": roots}
        await route.fulfill(json=payload)

    await page.route("**/api/agents/list?*", agents)
    expect = playwright.async_api.expect
    await page.clock.install()
    panel = page.locator("#agents-hosts")
    banner = page.locator("#update-status")
    progress = panel.get_by_role("progressbar", name=re.compile("エージェント一覧を取得中"))
    try:
        await page.goto(screen_harness.base_url + "/agents")
        await expect(progress).to_be_visible()
        await expect(panel.get_by_text("読み込み中", exact=True)).to_have_count(0)
    finally:
        release.set()
    await expect(progress).to_have_count(0)
    await expect(panel.get_by_text("一覧は未取得です。")).to_be_visible()
    await expect(banner).not_to_contain_text("前回取得")

    roots = [{"id": "fixture-root", "status": "running", "engine": "codex", "children": []}]
    await banner.get_by_role("button", name="再取得").click()
    row = panel.locator('[data-agent-id="fixture-root"]')
    await expect(row).to_contain_text("稼働中")
    await expect(banner).to_be_hidden()

    roots = None
    await page.clock.fast_forward(2100)
    await expect(banner).to_contain_text("前回取得")
    await expect(row).to_contain_text("稼働中")
    await expect(panel.get_by_text("一覧は未取得です。")).to_have_count(0)

    roots = [{"id": "fixture-root", "status": "completed", "engine": "codex", "children": []}]
    await banner.get_by_role("button", name="再取得").click()
    await expect(row).to_contain_text("完了")
    await expect(banner).to_be_hidden()

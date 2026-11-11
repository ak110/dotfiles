"""`atk serve`の計画ファイル画面の実ブラウザー統合テスト（選択の復元と複数rootの扱い）。"""

import asyncio
import dataclasses
import os
import urllib.parse
from collections.abc import AsyncGenerator
from pathlib import Path

import playwright.async_api
import pytest
import pytest_asyncio

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config
from agent_toolkit._atk.serve import plans as serve_plans
from agent_toolkit._atk.serve import sessions as serve_sessions
from agent_toolkit._atk.serve import state as serve_state

# pytestがテストの引数名で参照するfixtureを、このモジュールへ登録する。
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _BrowserOperations,
    _hold_route,
    _isolate_creation_time_index,
    _observe_screen_requests,
    _screen_harness_fixture,
    _ScreenHarness,
    _serve,
    _shift_click_default_prevented,
    _valid_diagram_markdown,
    _write_entries,
)

pytestmark = [
    pytest.mark.browser,
]


@dataclasses.dataclass
class _MultiRootHarness:
    page: playwright.async_api.Page
    context: playwright.async_api.BrowserContext
    new_plan: Path
    legacy_plan: Path
    plans_state: serve_plans.BroadcastState
    base_url: str


@pytest_asyncio.fixture(loop_scope="session", name="multi_root_harness")
async def _multi_root_harness_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    browser: playwright.async_api.Browser,
) -> AsyncGenerator[_MultiRootHarness]:
    """同じ相対パスのファイルを持つ2つのrootを登録したアプリを用意する。"""
    _isolate_creation_time_index(tmp_path, monkeypatch)
    _write_entries(tmp_path)
    new_root = tmp_path / "new"
    legacy_root = tmp_path / "legacy"
    new_root.mkdir()
    legacy_root.mkdir()
    new_plan = new_root / "same.md"
    legacy_plan = legacy_root / "same.md"
    new_plan.write_text("# 新root\n\nnew needle\n", encoding="utf-8")
    legacy_plan.write_text("# 旧root\n\nold needle\n", encoding="utf-8")
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        serve_state.ServeState(tmp_path),
        operations=_BrowserOperations(tmp_path),
        plans_context=serve_plans.create_context(
            hostname="browser-multi",
            roots=(
                serve_plans.RootSpec(serve_plans.NEW_SOURCE_ID, new_root, serve_plans.NEW_PORTABLE_ROOT),
                serve_plans.RootSpec(serve_plans.LEGACY_SOURCE_ID, legacy_root, serve_plans.LEGACY_PORTABLE_ROOT),
            ),
        ),
        sessions_context=serve_sessions.create_context(
            hostname="browser-multi",
            claude_home=tmp_path / "claude",
            codex_home=tmp_path / "codex",
        ),
    )
    plans_state: serve_plans.BroadcastState = app.config["PLANS_CONTEXT"].state
    async with _serve(app, browser) as (context, page, port):
        yield _MultiRootHarness(
            page=page,
            context=context,
            new_plan=new_plan,
            legacy_plan=legacy_plan,
            plans_state=plans_state,
            base_url=f"http://127.0.0.1:{port}",
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_multiple_roots_keep_selection_search_update_and_copy_portable_path(
    multi_root_harness: _MultiRootHarness,
) -> None:
    """同一相対パスのroot別選択・検索・更新通知・可搬パスコピーを検証する。"""
    harness = multi_root_harness
    await harness.page.goto(harness.base_url + "/plans")
    items = harness.page.locator("#files .file")
    await items.nth(1).wait_for(state="visible")
    assert await items.count() == 2
    assert await items.locator(".name").all_inner_texts() == ["same.md", "same.md"]

    expected_paths = {
        "新root": f"{serve_plans.NEW_PORTABLE_ROOT}/same.md",
        "旧root": f"{serve_plans.LEGACY_PORTABLE_ROOT}/same.md",
    }
    headings: set[str] = set()
    headings_by_index: list[str] = []
    for index in range(2):
        previous_heading = await harness.page.locator("#preview h1").inner_text() if index else None
        await items.nth(index).click()
        heading = harness.page.locator("#preview h1")
        if previous_heading is None:
            await heading.wait_for(state="visible")
        else:
            await harness.page.wait_for_function(
                "(previous) => document.querySelector('#preview h1')?.innerText !== previous",
                arg=previous_heading,
            )
        current_heading = await heading.inner_text()
        headings.add(current_heading)
        headings_by_index.append(current_heading)
        await harness.page.get_by_role("button", name="計画ファイルのパスをコピー").click()
        assert await harness.page.evaluate("navigator.clipboard.readText()") == expected_paths[current_heading]
    assert headings == {"新root", "旧root"}

    await harness.page.locator("#plans-filter").fill("needle")
    await harness.page.locator("#files .file").nth(1).wait_for(state="visible")
    assert await harness.page.locator("#files .file").count() == 2

    legacy_index = headings_by_index.index("旧root")
    await harness.page.locator("#files .file").nth(legacy_index).click()
    await harness.page.wait_for_function(
        "(expected) => document.querySelector('#preview h1')?.innerText === expected",
        arg="旧root",
    )

    harness.legacy_plan.write_text("# 旧root更新\n\nold needle\n", encoding="utf-8")
    await serve_plans.schedule_broadcast(harness.plans_state)
    await harness.page.locator("#preview h1", has_text="旧root更新").wait_for(state="visible")


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("select_during_initial_fetch", [False, True])
async def test_initial_plan_restore_preserves_earlier_selection(
    screen_harness: _ScreenHarness, select_during_initial_fetch: bool
) -> None:
    """初期取得中に始めた閲覧を保持し、操作が無い場合はURLの計画を復元する。"""
    harness = screen_harness
    page = harness.page
    await _observe_screen_requests(page)
    (harness.root / "second.md").write_text("# 後の計画\n\n" + "本文\n\n" * 200, encoding="utf-8")
    await page.route("**/api/plans/events", lambda route: route.abort())
    started = asyncio.Event()
    release = asyncio.Event()
    fulfilled = asyncio.Event()

    async def hold_initial_list(route: playwright.async_api.Route) -> None:
        response = await route.fetch()
        started.set()
        await release.wait()
        await route.fulfill(response=response)
        fulfilled.set()

    await page.route("**/api/plans/files", hold_initial_list, times=1)
    initial_path = "plan.md" if select_during_initial_fetch else "second.md"
    try:
        await page.goto(
            harness.base_url + "/plans?host=browser-test&path=" + initial_path,
            wait_until="domcontentloaded",
        )
        await asyncio.wait_for(started.wait(), timeout=10)
        if select_during_initial_fetch:
            await page.evaluate("window.dispatchEvent(new Event('focus'))")
            await page.locator("#files").get_by_text("second.md", exact=True).click()
            await playwright.async_api.expect(page.locator("#preview h1")).to_have_text("後の計画")
            await playwright.async_api.expect(page.locator("#preview")).to_have_attribute("aria-busy", "false")
            await page.locator("#screen-plans main").evaluate("element => { element.scrollTop = 240; }")
            assert await page.locator("#screen-plans main").evaluate("element => element.scrollTop") == 240
        release.set()
        await asyncio.wait_for(fulfilled.wait(), timeout=10)
        await page.wait_for_function("window.__screenRequests.pending === 0")
        await page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        await playwright.async_api.expect(page.locator("#preview h1")).to_have_text("後の計画")
        await playwright.async_api.expect(page.locator('#files .file[aria-current="true"]')).to_contain_text("second.md")
        if select_during_initial_fetch:
            assert await page.locator("#screen-plans main").evaluate("element => element.scrollTop") == 240
            await page.go_back()
            await playwright.async_api.expect(page.locator("#preview h1")).to_have_text("初回")
            await playwright.async_api.expect(page.locator('#files .file[aria-current="true"]')).to_contain_text("plan.md")
    finally:
        release.set()


@pytest.mark.asyncio(loop_scope="session")
async def test_later_file_selection_wins_when_first_response_arrives_last(
    screen_harness: _ScreenHarness,
) -> None:
    """先に選択したファイルの応答が後から届いても、後の選択の表示を保つ。"""
    harness = screen_harness
    await harness.page.goto(harness.base_url + "/plans")
    await playwright.async_api.expect(harness.page.locator("#preview h1")).to_have_text("初回")
    await playwright.async_api.expect(harness.page.locator("#preview")).to_have_attribute("aria-busy", "false")
    (harness.root / "first.md").write_text("# 先の選択\n\n先の内容\n", encoding="utf-8")
    (harness.root / "second.md").write_text("# 後の選択\n\n後の内容\n", encoding="utf-8")
    await serve_plans.schedule_broadcast(harness.plans_state)
    first_item = harness.page.locator("#files").get_by_text("first.md", exact=True)
    second_item = harness.page.locator("#files").get_by_text("second.md", exact=True)
    await first_item.wait_for(state="visible")
    await second_item.wait_for(state="visible")

    first_requested = asyncio.Event()
    release_first = asyncio.Event()
    first_fulfilled = asyncio.Event()

    async def delay_first_response(
        route: playwright.async_api.Route,
        request: playwright.async_api.Request,
    ) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.url).query)
        if query.get("path") != ["first.md"]:
            await route.continue_()
            return
        response = await route.fetch()
        first_requested.set()
        await release_first.wait()
        await route.fulfill(response=response)
        first_fulfilled.set()

    await harness.page.route("**/api/plans/file?*", delay_first_response)
    await first_item.click()
    await asyncio.wait_for(first_requested.wait(), timeout=5)
    await second_item.click()
    await harness.page.get_by_role("heading", name="後の選択").wait_for(state="visible")
    release_first.set()
    await asyncio.wait_for(first_fulfilled.wait(), timeout=5)
    await harness.page.evaluate(
        "() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
    )
    await harness.page.wait_for_function("document.title === '計画ファイル - atk serve'")

    assert await harness.page.get_by_role("heading", name="後の選択").is_visible()
    assert not await harness.page.get_by_role("heading", name="先の選択").is_visible()


@pytest.mark.asyncio(loop_scope="session")
async def test_selection_state_survives_preview_resync(screen_harness: _ScreenHarness) -> None:
    """再同期が先行しても、選択状態とツールバーの操作可否を保つ。"""
    harness = screen_harness
    await harness.page.goto(harness.base_url + "/plans")
    await playwright.async_api.expect(harness.page.locator("#preview h1")).to_have_text("初回")
    await playwright.async_api.expect(harness.page.locator("#preview")).to_have_attribute("aria-busy", "false")
    second_path = harness.root / "second.md"
    second_path.write_text("# 選択直後\n\n更新前の内容\n", encoding="utf-8")
    await serve_plans.schedule_broadcast(harness.plans_state)
    second_item = harness.page.locator("#files").get_by_text("second.md", exact=True)
    await second_item.wait_for(state="visible")

    first_requested = asyncio.Event()
    release_first = asyncio.Event()
    first_fulfilled = asyncio.Event()
    second_request_count = 0

    async def delay_first_response(
        route: playwright.async_api.Route,
        request: playwright.async_api.Request,
    ) -> None:
        nonlocal second_request_count
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.url).query)
        if query.get("path") != ["second.md"]:
            await route.continue_()
            return
        second_request_count += 1
        if second_request_count > 1:
            await route.continue_()
            return
        response = await route.fetch()
        first_requested.set()
        await release_first.wait()
        await route.fulfill(response=response)
        first_fulfilled.set()

    await harness.page.route("**/api/plans/file?*", delay_first_response)
    await second_item.click()
    await asyncio.wait_for(first_requested.wait(), timeout=5)
    second_path.write_text("# 再同期後\n\n更新後の内容\n", encoding="utf-8")
    stat = second_path.stat()
    os.utime(second_path, (stat.st_atime, stat.st_mtime + 1))
    # ウィンドウのフォーカス復帰時と同じ処理で強制再同期を起動する。
    await harness.page.evaluate("() => window.dispatchEvent(new Event('focus'))")
    await harness.page.get_by_role("heading", name="再同期後").wait_for(state="visible")
    release_first.set()
    await asyncio.wait_for(first_fulfilled.wait(), timeout=5)
    await harness.page.evaluate(
        "() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
    )

    assert await harness.page.title() == "計画ファイル - atk serve"
    assert not await harness.page.locator("#copy-btn").is_disabled()
    assert not await harness.page.locator("#copy-path-btn").is_disabled()
    assert await harness.page.locator("#meta-mobile .meta-path").text_content() == "second.md"
    prev_disabled = await harness.page.locator("#prev-btn").is_disabled()
    next_disabled = await harness.page.locator("#next-btn").is_disabled()
    assert not (prev_disabled and next_disabled)


@pytest.mark.asyncio(loop_scope="session")
async def test_root_warnings_distinguish_hosts_and_sources(multi_root_harness: _MultiRootHarness) -> None:
    """同じ理由でもホストと保存元で区別し、内部のsource IDを表示しない。"""
    harness = multi_root_harness
    page = harness.page
    roots = {
        "browser-multi": {
            serve_plans.NEW_SOURCE_ID: {"portable_root": "~/new"},
            serve_plans.LEGACY_SOURCE_ID: {"portable_root": "~/legacy"},
        },
        "remote-host": {serve_plans.NEW_SOURCE_ID: {"portable_root": "~/new"}},
    }
    statuses = {
        host: {source: {"status": "warning", "message": "同じ取得失敗"} for source in sources}
        for host, sources in roots.items()
    }
    await page.route("**/api/plans/root-info", lambda route: route.fulfill(json=roots))
    await page.route("**/api/plans/root-status", lambda route: route.fulfill(json=statuses))
    await page.goto(harness.base_url + "/plans")
    warnings = page.locator("#root-warnings")
    await playwright.async_api.expect(warnings).to_contain_text("remote-host / ~/new")
    await playwright.async_api.expect(warnings).to_contain_text("browser-multi / ~/new")
    await playwright.async_api.expect(warnings).to_contain_text("browser-multi / ~/legacy")
    assert (await warnings.inner_text()).count("同じ取得失敗") == 3
    assert serve_plans.NEW_SOURCE_ID not in await warnings.inner_text()

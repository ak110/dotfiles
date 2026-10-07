"""`atk serve`の計画ファイル画面の実ブラウザー統合テスト。"""

import asyncio
import dataclasses
import functools
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
    _BROWSER_TEST_ENV,
    _browser_fixture,
    _browser_tests_enabled,
    _BrowserOperations,
    _hold_route,
    _isolate_creation_time_index,
    _screen_harness_fixture,
    _ScreenHarness,
    _serve,
    _shift_click_default_prevented,
    _valid_diagram_markdown,
    _write_entries,
)

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        not _browser_tests_enabled(),
        reason=f"{_BROWSER_TEST_ENV}=1の場合のみ実行する",
    ),
]
_MERMAID_CDN_URL = "https://cdn.jsdelivr.net/npm/mermaid@12.0.0/dist/mermaid.min.js"


@dataclasses.dataclass
class _MultiRootHarness:
    page: playwright.async_api.Page
    context: playwright.async_api.BrowserContext
    new_plan: Path
    legacy_plan: Path
    plans_state: serve_plans.BroadcastState
    base_url: str


@pytest_asyncio.fixture(name="multi_root_harness")
async def _multi_root_harness_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    browser: playwright.async_api.Browser,
) -> AsyncGenerator[_MultiRootHarness]:
    """同じ相対パスのファイルを持つ2rootを登録したアプリを用意する。"""
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


@pytest.mark.asyncio
async def test_plan_filename_link_supports_get_and_shift_click(screen_harness: _ScreenHarness) -> None:
    """計画名はプレビューを復元するGET URLを持ち、Shiftクリックをブラウザーへ委ねる。"""
    harness = screen_harness
    page = harness.page
    await page.goto(harness.base_url + "/plans")
    link = page.locator("#files .file").first
    await link.wait_for(state="visible")
    href = await link.get_attribute("href")
    assert href is not None
    parsed = urllib.parse.urlsplit(href)
    query = urllib.parse.parse_qs(parsed.query)
    assert parsed.path == "/plans"
    assert query["host"] == ["browser-test"]
    assert query["path"] == ["plan.md"]

    assert not await _shift_click_default_prevented(link)


@pytest.mark.asyncio
@pytest.mark.parametrize("delay_auto_opened_body", [False, True])
async def test_selecting_deleted_plan_refreshes_list(screen_harness: _ScreenHarness, delay_auto_opened_body: bool) -> None:
    """一覧表示後に削除された計画ファイルを選ぶと、再試行を求めず移動または削除済みを示して一覧を更新する。

    広い画面は一覧の表示後に先頭の計画を自動で開き、本文を取得する。
    この取得が削除より後にサーバーへ届くと404から一覧が取り直され、選ぶ項目がDOMから外れる。
    負荷の高いCIで起きるこの到着順を、本文の応答の遅延で再現しても成功することを確かめる。
    """
    harness = screen_harness
    page = harness.page
    # 更新通知の購読を止め、削除後も一覧が古いままの状態で項目を選ぶ。
    await page.route("**/api/plans/events", lambda route: route.abort())
    body_delivered = asyncio.Event()
    if delay_auto_opened_body:

        async def delay_body(route: playwright.async_api.Route) -> None:
            # 負荷の高い環境での到着の遅れを模すため、自動で開く本文の要求をサーバーへ送る前に待つ。
            await asyncio.sleep(0.3)
            response = await route.fetch()
            await route.fulfill(response=response)
            body_delivered.set()

        await page.route("**/api/plans/file?*", delay_body, times=1)
    await page.goto(harness.base_url + "/plans")
    item = page.locator("#files .file", has_text="plan.md")
    await item.wait_for(state="visible")
    # 自動で開いた先頭の計画の本文表示を待ち、本文の取得と削除を競合させない。
    await page.locator("#preview h1", has_text="初回").wait_for(state="visible")

    harness.plan_path.unlink()
    if delay_auto_opened_body:
        # 遅れた本文の応答が画面へ届いてから項目を選び、削除と本文の取得が重なる順序を固定する。
        await asyncio.wait_for(body_delivered.wait(), timeout=5)
    await item.click()

    preview = page.locator("#preview")
    await playwright.async_api.expect(preview.get_by_role("status")).to_have_text(
        "選択した計画ファイルは移動または削除されたため表示できません。一覧を更新しました。"
    )
    await playwright.async_api.expect(preview.get_by_role("button", name="再読み込み")).to_have_count(0)
    await playwright.async_api.expect(page.locator("#files .file")).to_have_count(0)
    await playwright.async_api.expect(page.locator("#copy-btn")).to_be_disabled()


@pytest.mark.asyncio
async def test_deleted_plan_status_remains_visible_while_choosing_next_on_mobile(
    screen_harness: _ScreenHarness, tmp_path: Path
) -> None:
    """狭幅で失効後に一覧を開いても理由を読み、別の計画へ進める。"""
    harness = screen_harness
    page = harness.page
    next_plan = harness.root / "next-plan.md"
    next_plan.write_text("# 次の計画\n", encoding="utf-8")
    await page.set_viewport_size({"width": 320, "height": 720})
    await page.route("**/api/plans/events", lambda route: route.abort())
    await page.goto(harness.base_url + "/plans")
    await page.locator("#plans-menu-btn").click()
    first = page.locator('#files .file[href*="path=plan.md"]')
    await first.wait_for(state="visible")

    harness.plan_path.unlink()
    await first.click()
    message = "選択した計画ファイルは移動または削除されたため表示できません。一覧を更新しました。"
    await playwright.async_api.expect(page.locator("#preview").get_by_role("status")).to_have_text(message)
    await page.locator("#plans-menu-btn").click()

    sidebar_status = page.locator("#plans-selection-status")
    opening_bounds = await sidebar_status.bounding_box()
    assert opening_bounds is not None and opening_bounds["x"] >= 0
    await playwright.async_api.expect(sidebar_status).to_have_text(message)
    await playwright.async_api.expect(sidebar_status).to_be_visible()
    await page.wait_for_function("""() => {
      const bounds = document.getElementById('plans-selection-status').getBoundingClientRect();
      return bounds.x >= 0 && bounds.right <= innerWidth;
    }""")
    bounds = await sidebar_status.bounding_box()
    assert bounds is not None and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 320
    assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 720
    await page.screenshot(path=str(tmp_path / "missing-selection-320-after.png"))

    await page.locator("#files .file", has_text="next-plan.md").click()
    await playwright.async_api.expect(page.locator("#preview h1")).to_have_text("次の計画")
    await playwright.async_api.expect(sidebar_status).to_be_hidden()


@pytest.mark.asyncio
async def test_mobile_plan_drawer_keeps_user_open_during_initial_fetch(screen_harness: _ScreenHarness) -> None:
    """初期取得中に開いた一覧を維持し、選択後と未操作での閉状態も確認する。"""
    page = screen_harness.page
    await page.set_viewport_size({"width": 320, "height": 720})
    started = asyncio.Event()
    release = asyncio.Event()
    route_handler = functools.partial(_hold_route, started=started, release=release)
    await page.route("**/api/plans/files", route_handler)
    try:
        await page.goto(screen_harness.base_url + "/plans", wait_until="domcontentloaded")
        await asyncio.wait_for(started.wait(), timeout=10)
        screen = page.locator("#screen-plans")
        menu = page.locator("#plans-menu-btn")
        sidebar = page.locator("#plans-app > aside")
        assert not await screen.evaluate("element => element.classList.contains('drawer-open')")
        await playwright.async_api.expect(menu).to_have_attribute("aria-expanded", "false")
        await playwright.async_api.expect(sidebar).to_have_attribute("inert", "")

        await menu.click()
        assert await screen.evaluate("element => element.classList.contains('drawer-open')")
        await playwright.async_api.expect(menu).to_have_attribute("aria-expanded", "true")
        assert not await sidebar.evaluate("element => element.inert")
        release.set()
        item = page.locator("#files .file").first
        await item.wait_for(state="visible")
        await page.evaluate("() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        assert await screen.evaluate("element => element.classList.contains('drawer-open')")
        await playwright.async_api.expect(menu).to_have_attribute("aria-expanded", "true")
        assert not await sidebar.evaluate("element => element.inert")
        await item.click()
        await playwright.async_api.expect(menu).to_have_attribute("aria-expanded", "false")
        await playwright.async_api.expect(sidebar).to_have_attribute("inert", "")
    finally:
        release.set()
        await page.unroute("**/api/plans/files", route_handler)

    await page.reload()
    await page.locator("#files .file").first.wait_for(state="attached")
    await playwright.async_api.expect(page.locator("#plans-menu-btn")).to_have_attribute("aria-expanded", "false")
    await playwright.async_api.expect(page.locator("#plans-app > aside")).to_have_attribute("inert", "")


@pytest.mark.asyncio
async def test_mobile_plan_copy_buttons_stay_on_one_line(screen_harness: _ScreenHarness) -> None:
    """390px幅の計画画面で本文とパスのコピー操作を1行の高さに保つ。"""
    page = screen_harness.page
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.goto(screen_harness.base_url + "/plans")
    await page.locator("#files .file").first.wait_for(state="visible")
    await page.locator("#plans-menu-btn").click()
    await playwright.async_api.expect(page.locator("#plans-menu-btn")).to_have_attribute("aria-expanded", "true")
    await page.locator("#files .file").first.click()
    await page.locator("#preview h1").wait_for(state="visible")

    for selector in ("#copy-btn", "#copy-path-btn"):
        button = page.locator(selector)
        line_metrics = await button.evaluate(
            """element => {
              const style = getComputedStyle(element);
              return {
                contentHeight: element.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom),
                lineHeight: parseFloat(style.lineHeight),
              };
            }"""
        )
        assert line_metrics["contentHeight"] <= line_metrics["lineHeight"] + 1
        assert await button.locator(".short-label").evaluate("element => element.getClientRects().length") == 1


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_plan_copy_buttons_restore_labels_after_repeated_clicks(screen_harness: _ScreenHarness) -> None:
    """結果表示中に再度コピーしても両ボタンの幅別操作名を復元する。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/plans")
    await page.locator("#preview h1").wait_for(state="visible")
    await page.evaluate(
        """() => {
          const nativeSetTimeout = window.setTimeout.bind(window);
          window.copyLabelTimers = [];
          window.setTimeout = (callback, delay, ...args) => {
            if (delay !== 2000) return nativeSetTimeout(callback, delay, ...args);
            window.copyLabelTimers.push(() => callback(...args));
            return window.copyLabelTimers.length;
          };
        }"""
    )

    for selector, wide, short in (
        ("#copy-btn", "Markdownをコピー", "本文"),
        ("#copy-path-btn", "計画ファイルのパスをコピー", "パス"),
    ):
        button = page.locator(selector)
        await button.click()
        await playwright.async_api.expect(button.locator(".short-label")).to_have_text("完了")
        await page.wait_for_function("window.copyLabelTimers.length === 1")
        await button.click()
        await page.wait_for_function("window.copyLabelTimers.length === 2")
        await page.evaluate("() => window.copyLabelTimers.splice(0).forEach(callback => callback())")
        assert await button.locator(".wide-label").inner_text() == wide
        assert await button.locator(".short-label").inner_text() == short


@pytest.mark.asyncio
async def test_diagrams_render_and_refresh_safely(screen_harness: _ScreenHarness) -> None:
    """MermaidとSVGを描画し、更新後も能動的な内容を実行させず、古いblob URLを解放する。"""
    harness = screen_harness
    await harness.page.goto(harness.base_url + "/plans")

    mermaid = harness.page.locator("#preview .diagram-mermaid svg")
    svg_image = harness.page.locator("#preview .diagram-svg img")
    await mermaid.wait_for(state="visible")
    await svg_image.wait_for(state="visible")
    assert await svg_image.evaluate("(image) => image.complete && image.naturalWidth > 0")

    initial_blob_url = await svg_image.get_attribute("src")
    assert initial_blob_url is not None
    mermaid_responses = [status for url, status in harness.responses if url == _MERMAID_CDN_URL]
    assert mermaid_responses == [200]
    assert not any("/chunks/" in url or url.endswith(".mjs") for url in harness.requests)

    malicious_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40" '
        "onload=\"fetch('/svg-onload-ran')\">"
        "<script>fetch('/svg-script-ran')</script>"
        '<image href="/svg-external-resource.png" width="10" height="10"/>'
        '<rect width="120" height="40" fill="green"/>'
        "</svg>"
    )
    await harness.notify_file_update(
        f"# 更新1\n\n本文-更新1\n\n```mermaid\ngraph TD\n  C[更新1] --> D[完了]\n```\n\n```svg\n{malicious_svg}\n```\n"
    )
    await harness.page.locator("#preview h1", has_text="更新1").wait_for()
    await harness.page.locator("#preview .diagram-mermaid svg").wait_for(state="visible")
    updated_image = harness.page.locator("#preview .diagram-svg img")
    await updated_image.wait_for(state="visible")
    assert await updated_image.evaluate("(image) => image.complete && image.naturalWidth > 0")
    assert not any(
        marker in url
        for url in harness.requests
        for marker in ("svg-onload-ran", "svg-script-ran", "svg-external-resource.png")
    )
    assert not await harness.page.evaluate("(url) => fetch(url).then(() => true, () => false)", initial_blob_url)

    first_updated_blob_url = await updated_image.get_attribute("src")
    assert first_updated_blob_url is not None
    await harness.notify_file_update(_valid_diagram_markdown("更新2"))
    await harness.page.locator("#preview h1", has_text="更新2").wait_for()
    await harness.page.locator("#preview .diagram-mermaid svg").wait_for(state="visible")
    current_image = harness.page.locator("#preview .diagram-svg img")
    await current_image.wait_for(state="visible")
    assert await harness.page.locator("#preview h1").all_inner_texts() == ["更新2"]
    assert not await harness.page.evaluate(
        "(url) => fetch(url).then(() => true, () => false)",
        first_updated_blob_url,
    )
    current_blob_url = await current_image.get_attribute("src")
    assert current_blob_url is not None
    assert await harness.page.evaluate("(url) => fetch(url).then(() => true, () => false)", current_blob_url)


@pytest.mark.asyncio
async def test_same_mtime_rewrites_refresh_plan_preview(screen_harness: _ScreenHarness) -> None:
    """更新時刻を変えない連続書き換えでも、SSE通知後に新しい本文と図を表示する。"""
    harness = screen_harness
    await harness.page.goto(harness.base_url + "/plans")
    await harness.page.locator("#preview h1", has_text="初回").wait_for()
    await harness.page.locator("#preview .diagram-mermaid svg").wait_for(state="visible")
    fixed_stat = harness.plan_path.stat()

    async def rewrite_keeping_mtime(markdown: str) -> None:
        # 同じ時刻刻み内の連続書き換えを、実行速度に依存せず再現するため更新時刻を初回の値へ戻す。
        harness.plan_path.write_text(markdown, encoding="utf-8")
        os.utime(harness.plan_path, ns=(fixed_stat.st_atime_ns, fixed_stat.st_mtime_ns))
        assert harness.plan_path.stat().st_mtime_ns == fixed_stat.st_mtime_ns
        await serve_plans.schedule_broadcast(harness.plans_state)

    await rewrite_keeping_mtime(_valid_diagram_markdown("更新1"))
    await harness.page.locator("#preview h1", has_text="更新1").wait_for()
    first_image = harness.page.locator("#preview .diagram-svg img")
    await first_image.wait_for(state="visible")
    first_blob_url = await first_image.get_attribute("src")
    assert first_blob_url is not None

    await rewrite_keeping_mtime(_valid_diagram_markdown("更新2"))
    await harness.page.locator("#preview h1", has_text="更新2").wait_for()
    assert await harness.page.locator("#preview h1").all_inner_texts() == ["更新2"]
    await playwright.async_api.expect(harness.page.locator("#preview .diagram-mermaid svg")).to_contain_text("更新2")
    current_image = harness.page.locator("#preview .diagram-svg img")
    await current_image.wait_for(state="visible")
    assert await current_image.evaluate("(image) => image.complete && image.naturalWidth > 0")
    assert "更新2" in (await harness.page.locator("#preview .diagram-svg .diagram-source pre").text_content() or "")
    current_blob_url = await current_image.get_attribute("src")
    assert current_blob_url is not None
    assert current_blob_url != first_blob_url
    assert not await harness.page.evaluate("(url) => fetch(url).then(() => true, () => false)", first_blob_url)
    assert await harness.page.evaluate("(url) => fetch(url).then(() => true, () => false)", current_blob_url)


@pytest.mark.asyncio
async def test_review_table_uses_available_width_and_markdown_keeps_width_limit(
    screen_harness: _ScreenHarness,
) -> None:
    """レビュー指摘管理表は全幅を使い、Markdown本文は読みやすさのため幅上限を保つ。"""
    harness = screen_harness
    review_row = '"1"\t"implementation-review"\t"a.py:1"\t"指摘"\t"要"\t"対応済み"\t""\n'
    (harness.root / "plan.exec-review.tsv").write_text(review_row, encoding="utf-8")
    await harness.page.set_viewport_size({"width": 1600, "height": 900})
    await harness.page.goto(harness.base_url + "/plans")

    normal_widths = await harness.page.locator("#preview").evaluate(
        """preview => {
            const main = preview.closest("main");
            const rect = preview.getBoundingClientRect();
            const mainRect = main.getBoundingClientRect();
            return {
                preview: rect.width,
                leftMargin: rect.left - mainRect.left,
                rightMargin: mainRect.right - rect.right,
            };
        }""",
    )
    assert normal_widths["preview"] <= 860
    assert normal_widths["leftMargin"] == pytest.approx(normal_widths["rightMargin"], abs=1)

    await harness.page.locator('a[data-plan-path="plan.exec-review.tsv"]').click()
    await harness.page.get_by_role("columnheader", name="ラウンド").wait_for(state="visible")
    review_widths = await harness.page.locator("#preview").evaluate(
        """preview => ({
            preview: preview.getBoundingClientRect().width,
            main: preview.closest("main").clientWidth,
        })""",
    )
    assert review_widths["preview"] == pytest.approx(review_widths["main"], abs=1)


@pytest.mark.asyncio
async def test_mermaid_strict_security_blocks_active_content(screen_harness: _ScreenHarness) -> None:
    """Mermaid図のクリック定義と埋め込みHTMLから、能動的な内容を実行させない。"""
    harness = screen_harness
    await harness.page.goto(harness.base_url + "/plans")
    await harness.page.locator("#preview .diagram-mermaid svg").wait_for(state="visible")
    await harness.page.evaluate(
        """() => {
            window.__dangerousCallCount = 0;
            window.someFunction = () => { window.__dangerousCallCount++; };
        }""",
    )

    await harness.notify_file_update(
        "# Mermaidセキュリティ\n\n"
        "```mermaid\n"
        "graph TD\n"
        "  A[\"<img src='data:image/png;base64,invalid' "
        "onerror=&quot;fetch('/mermaid-onerror-ran')&quot;>\"]\n"
        '  B["リンク"]\n'
        "  C[\"<script>fetch('/mermaid-script-ran')</script>\"]\n"
        "  A --> B --> C\n"
        "  click A call someFunction()\n"
        '  click B href "/mermaid-navigation-ran"\n'
        "```\n"
    )

    await harness.page.get_by_role("heading", name="Mermaidセキュリティ").wait_for(state="visible")
    mermaid = harness.page.locator("#preview .diagram-mermaid svg")
    await mermaid.wait_for(state="visible")
    nodes = mermaid.locator("g.node")
    assert await nodes.count() == 3
    await nodes.nth(0).click()
    await nodes.nth(1).click()
    await harness.page.wait_for_timeout(100)

    assert harness.page.url == harness.base_url + "/plans"
    assert await harness.page.evaluate("window.__dangerousCallCount") == 0
    assert not any(
        marker in url
        for url in harness.requests
        for marker in (
            "mermaid-navigation-ran",
            "mermaid-onerror-ran",
            "mermaid-script-ran",
        )
    )


@pytest.mark.asyncio
async def test_later_file_selection_wins_when_first_response_arrives_last(
    screen_harness: _ScreenHarness,
) -> None:
    """先に選択したファイルの応答が後から届いても、後の選択の表示を保つ。"""
    harness = screen_harness
    await harness.page.goto(harness.base_url + "/plans")
    await harness.page.locator("#preview .diagram-mermaid svg").wait_for(state="visible")
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


@pytest.mark.asyncio
async def test_selection_state_survives_preview_resync(screen_harness: _ScreenHarness) -> None:
    """再同期が先行しても、選択状態とツールバーの操作可否を保つ。"""
    harness = screen_harness
    await harness.page.goto(harness.base_url + "/plans")
    await harness.page.locator("#preview .diagram-mermaid svg").wait_for(state="visible")
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


@pytest.mark.asyncio
async def test_mermaid_error_stays_near_source_and_keeps_preview(screen_harness: _ScreenHarness) -> None:
    """Mermaidの構文エラーは図の位置へ表示し、本文と他の図の描画を保つ。"""
    harness = screen_harness
    await harness.page.goto(harness.base_url + "/plans")
    await harness.page.locator("#preview .diagram-mermaid svg").wait_for(state="visible")

    await harness.notify_file_update(
        "# 構文エラー\n\n残る本文\n\n"
        "```mermaid\ngraph TD\n  A[未完了 -->\n```\n\n"
        '```svg\n<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>\n```\n'
    )

    error = harness.page.locator("#preview .diagram-mermaid .diagram-error")
    await error.wait_for(state="visible")
    assert "Mermaid図を描画できませんでした" in await error.inner_text()
    assert await harness.page.locator("#preview .diagram-mermaid details").is_visible()
    source = await harness.page.locator("#preview .diagram-mermaid details pre").text_content()
    assert source is not None
    assert "graph TD" in source
    assert "残る本文" in await harness.page.locator("#preview").inner_text()
    assert await harness.page.locator("#preview .diagram-svg img").is_visible()


@pytest.mark.asyncio
async def test_mermaid_cdn_failure_stays_near_source_and_keeps_preview(screen_harness: _ScreenHarness) -> None:
    """CDNからMermaidを取得できない場合も図の位置へエラーを表示し、本文を保つ。"""
    harness = screen_harness
    await harness.page.route(_MERMAID_CDN_URL, lambda route: route.abort())

    await harness.page.goto(harness.base_url + "/plans")

    error = harness.page.locator("#preview .diagram-mermaid .diagram-error")
    await error.wait_for(state="visible")
    assert "Mermaidの読み込みに失敗しました" in await error.inner_text()
    assert await harness.page.locator("#preview .diagram-mermaid details").is_visible()
    assert "本文-初回" in await harness.page.locator("#preview").inner_text()
    assert await harness.page.locator("#preview .diagram-svg img").is_visible()


@pytest.mark.asyncio
async def test_forwarded_prefix_uses_cdn_for_mermaid(screen_harness: _ScreenHarness) -> None:
    """ベースパス配下でもMermaidは同じCDNから読み込む。"""
    harness = screen_harness
    route_pattern = harness.base_url + "/**"

    async def add_forwarded_prefix(route: playwright.async_api.Route) -> None:
        await route.continue_(headers={**route.request.headers, "X-Forwarded-Prefix": "/atk"})

    await harness.page.route(route_pattern, add_forwarded_prefix)
    try:
        response = await harness.page.goto(harness.base_url + "/atk/plans")

        assert response is not None
        assert response.status == 200
        await harness.page.locator("#preview .diagram-mermaid svg").wait_for(state="visible")
        svg_image = harness.page.locator("#preview .diagram-svg img")
        await svg_image.wait_for(state="visible")
        assert await svg_image.evaluate("(image) => image.complete && image.naturalWidth > 0")
        assert _MERMAID_CDN_URL in harness.requests
    finally:
        await harness.page.unroute(route_pattern, add_forwarded_prefix)

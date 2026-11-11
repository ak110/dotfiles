"""`atk serve`の計画ファイル画面の実ブラウザー統合テスト（一覧・本文・複写・失敗の表示）。"""

import asyncio
import functools
import json
import os
import urllib.parse
from pathlib import Path

import playwright.async_api
import pytest

from agent_toolkit._atk.serve import plans as serve_plans

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


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("width", [320, 1280])
@pytest.mark.parametrize("failure", [False, True])
async def test_plan_copy_feedback_preserves_toolbar_positions(
    screen_harness: _ScreenHarness, width: int, failure: bool
) -> None:
    """本文・パスのコピー成功と失敗の表示で、ボタン幅と隣接操作の位置が変わらない。"""
    page = screen_harness.page
    await page.set_viewport_size({"width": width, "height": 800})
    await page.goto(screen_harness.base_url + "/plans?host=browser-test&path=plan.md")
    await page.locator("#preview h1").wait_for(state="visible")
    if failure:
        await page.evaluate(
            "() => { navigator.clipboard.writeText = async () => { throw new Error('clipboard unavailable'); }; }"
        )
    buttons = page.locator("#screen-plans main > .toolbar button")
    before = await buttons.evaluate_all("elements => elements.map(element => element.getBoundingClientRect().toJSON())")
    for selector in ("#copy-btn", "#copy-path-btn"):
        button = page.locator(selector)
        await button.click()
        await playwright.async_api.expect(button.locator(".short-label")).to_have_text("失敗" if failure else "完了")
        after = await buttons.evaluate_all("elements => elements.map(element => element.getBoundingClientRect().toJSON())")
        assert after == before
        assert await button.evaluate("element => element.scrollWidth <= element.clientWidth")
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.asyncio(loop_scope="session")
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


@pytest.mark.asyncio(loop_scope="session")
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
    release_body = asyncio.Event()
    if delay_auto_opened_body:

        async def delay_body(route: playwright.async_api.Route) -> None:
            await asyncio.wait_for(release_body.wait(), timeout=5)
            response = await route.fetch()
            await route.fulfill(response=response)
            body_delivered.set()

        await page.route("**/api/plans/file?*", delay_body, times=1)
    await page.goto(harness.base_url + "/plans")
    item = page.locator("#files .file", has_text="plan.md")
    await item.wait_for(state="visible")
    release_body.set()
    # 自動で開いた先頭の計画の本文表示を待ち、本文の取得と削除を競合させない。
    await page.locator("#preview h1", has_text="初回").wait_for(state="visible")

    harness.plan_path.unlink()
    if delay_auto_opened_body:
        # 遅れた本文の応答が画面へ届いてから項目を選び、削除と本文の取得が重なる順序を固定する。
        await asyncio.wait_for(body_delivered.wait(), timeout=5)
    await item.click()

    preview = page.locator("#preview")
    await playwright.async_api.expect(preview.get_by_role("status")).to_contain_text(
        "選択した計画ファイルは移動または削除されたため表示できません。一覧を更新しました。"
    )
    await playwright.async_api.expect(preview.get_by_role("status")).to_contain_text("計画 / browser-test /")
    await playwright.async_api.expect(preview.get_by_role("status")).to_contain_text("plan.md")
    await playwright.async_api.expect(preview.get_by_role("button", name="再読み込み")).to_have_count(0)
    await playwright.async_api.expect(page.locator("#files .file")).to_have_count(0)
    await playwright.async_api.expect(page.locator("#copy-btn")).to_be_disabled()


@pytest.mark.asyncio(loop_scope="session")
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
    await playwright.async_api.expect(page.locator("#preview").get_by_role("status")).to_contain_text(message)
    await page.locator("#plans-menu-btn").click()

    sidebar_status = page.locator("#plans-selection-status")
    opening_bounds = await sidebar_status.bounding_box()
    assert opening_bounds is not None and opening_bounds["x"] >= 0
    await playwright.async_api.expect(sidebar_status).to_contain_text(message)
    await playwright.async_api.expect(sidebar_status).to_contain_text("browser-test")
    await playwright.async_api.expect(sidebar_status).to_contain_text("plan.md")
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


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("pending", ["list", "detail"])
async def test_mobile_plan_drawer_keeps_user_open_during_initial_fetch(screen_harness: _ScreenHarness, pending: str) -> None:
    """狭い画面で一覧の初期取得中に開いた一覧を取得後も開いたまま保ち、項目の選択後と操作しない再読み込み後は閉じていることも確かめる。"""
    page = screen_harness.page
    await page.set_viewport_size({"width": 320, "height": 720})
    started = asyncio.Event()
    release = asyncio.Event()
    route_handler = functools.partial(_hold_route, started=started, release=release)
    pattern = "**/api/plans/files" if pending == "list" else "**/api/plans/file?*"
    await page.route(pattern, route_handler)
    try:
        await page.goto(screen_harness.base_url + "/plans?host=browser-test&path=plan.md", wait_until="domcontentloaded")
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
        async with page.expect_response("**/api/plans/file?*"):
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
        await page.unroute(pattern, route_handler)

    await page.reload()
    await page.locator("#files .file").first.wait_for(state="attached")
    await playwright.async_api.expect(page.locator("#plans-menu-btn")).to_have_attribute("aria-expanded", "false")
    await playwright.async_api.expect(page.locator("#plans-app > aside")).to_have_attribute("inert", "")


@pytest.mark.asyncio(loop_scope="session")
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


@pytest.mark.asyncio(loop_scope="session")
async def test_plan_copy_buttons_restore_labels_after_repeated_clicks(screen_harness: _ScreenHarness) -> None:
    """コピー結果の表示中に再度コピーしても、両ボタンの画面幅ごとの操作名を元に戻す。"""
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


@pytest.mark.asyncio(loop_scope="session")
async def test_same_mtime_rewrites_refresh_plan_preview(screen_harness: _ScreenHarness) -> None:
    """更新時刻を変えない連続書き換えでも、SSE通知後に新しい本文と図を表示する。"""
    harness = screen_harness
    await harness.page.goto(harness.base_url + "/plans")
    await harness.page.locator("#preview h1", has_text="初回").wait_for()
    await harness.page.locator("#preview .diagram-mermaid svg").wait_for(state="visible")
    fixed_stat = harness.plan_path.stat()

    async def rewrite_keeping_mtime(markdown: str) -> None:
        # 同じ時刻刻み内の連続書き換えを、実行速度によらず再現するため更新時刻を初回の値へ戻す。
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


@pytest.mark.asyncio(loop_scope="session")
async def test_plan_operations_record_open_and_notified_reload(screen_harness: _ScreenHarness) -> None:
    """選択から図の描画まで、変更通知から一覧とプレビューの再描画までを、それぞれ1件の操作として取得と対応付けて記録する。

    取得単位の記録は本文の受信直後で終わり、図の非同期描画や通知後の再取得全体の所要を追跡できない。
    """
    harness = screen_harness
    page = harness.page
    await page.add_init_script("Object.defineProperty(navigator, 'sendBeacon', {value: undefined})")
    # 記録は件数・定期送信・`pagehide`のいずれかで分割して送られるため、全ての送信を集めて検査する。
    sent: list[dict] = []
    reload_sent = asyncio.Event()

    def record_timings(request: playwright.async_api.Request) -> None:
        if request.url.endswith("/api/timings"):
            records = json.loads(request.post_data or "{}").get("records", [])
            sent.extend(records)
            if any(record.get("operation") == "external-reload" for record in records):
                reload_sent.set()

    page.on("request", record_timings)
    await page.goto(harness.base_url + "/plans")
    await page.locator("#preview .diagram-mermaid svg").wait_for(state="visible")
    # 初期表示の選択は接続確立時の再同期に置き換わることがあるため、表示の確定後に別の計画を選び直して測る。
    second = harness.plan_path.with_name("second-plan.md")
    second.write_text(_valid_diagram_markdown("二件目"), encoding="utf-8")
    await serve_plans.schedule_broadcast(harness.plans_state)
    await page.locator("#files .file", has_text="second-plan.md").click()
    await page.locator("#preview .diagram-mermaid svg", has_text="二件目").wait_for(state="visible")
    second.write_text(_valid_diagram_markdown("通知後"), encoding="utf-8")
    await serve_plans.schedule_broadcast(harness.plans_state)
    await page.locator("#preview h1", has_text="通知後").wait_for()
    await page.locator("#preview .diagram-mermaid svg", has_text="通知後").wait_for(state="visible")
    await page.evaluate("() => window.dispatchEvent(new Event('pagehide'))")
    await asyncio.wait_for(reload_sent.wait(), timeout=30)
    records = list(sent)

    def linked_routes(operation: dict) -> set[str]:
        return {
            record.get("route", "")
            for record in records
            if record.get("operation_id") == operation["operation_id"] and record is not operation
        }

    plan_records = [record for record in records if record.get("screen") == "plans"]
    opened = next(record for record in plan_records if record.get("operation") == "open-file" and record.get("outcome") == "ok")
    assert opened["kind"] == "operation"
    assert "/api/plans/file" in linked_routes(opened)
    reloaded = next(
        record for record in plan_records if record.get("operation") == "external-reload" and record.get("trigger") == "sse"
    )
    assert {"/api/plans/files", "/api/plans/file"} <= linked_routes(reloaded)
    assert reloaded["total_ms"] >= 0


@pytest.mark.asyncio(loop_scope="session")
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


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("failure", ["body", "clipboard", "svg"])
async def test_plan_failures_keep_selected_source_and_browser_owner(screen_harness: _ScreenHarness, failure: str) -> None:
    """選択した計画の取得の失敗とブラウザー側の処理の失敗を区別して表示し、表示中の本文を保つ。"""
    harness = screen_harness
    page = harness.page
    if failure == "body":
        await page.route("**/api/plans/file?*", lambda route: route.fulfill(status=503, body="同じ取得失敗"))
    elif failure == "clipboard":
        # コピーの失敗だけを確かめるため、共有の入力にある図を除き、外部CDNの読込を待たずに描画を終える。
        harness.plan_path.write_text("# 初回\n\nコピーの失敗の検証\n", encoding="utf-8")
        await page.add_init_script(
            "Object.defineProperty(navigator, 'clipboard', {value: {writeText: async () => {throw new Error('書込拒否')}}})"
        )
    else:
        harness.plan_path.write_text("# 図の失敗\n\n```svg\n<svg>broken\n```\n", encoding="utf-8")
    await page.goto(harness.base_url + "/plans")
    if failure == "clipboard":
        await page.locator("#preview h1").wait_for(state="visible")
        # 見出しは図の描画より先に現れる。初期描画の完了後にコピーして失敗表示を検証する。
        await playwright.async_api.expect(page.locator("#preview")).to_have_attribute("aria-busy", "false")
        await page.locator("#copy-btn").click()
    alert = page.locator("#preview [role=alert]" if failure != "svg" else "#preview .diagram-error")
    await playwright.async_api.expect(alert).to_contain_text("plan.md")
    await playwright.async_api.expect(alert).to_contain_text("browser-test")
    await playwright.async_api.expect(alert).to_contain_text(str(harness.root))
    await playwright.async_api.expect(alert).to_contain_text("ブラウザー" if failure != "body" else "計画ファイルの本文")
    if failure == "clipboard":
        await playwright.async_api.expect(alert).to_contain_text("書込拒否")
        await playwright.async_api.expect(page.locator("#preview h1")).to_have_text("初回")


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("failure", ["list", "search"])
async def test_plan_collection_failures_show_operation(screen_harness: _ScreenHarness, failure: str) -> None:
    """計画ファイルの一覧と検索の取得に失敗した場合は、エラー表示にserveのホストと、失敗した操作または検索条件を示す。"""
    harness = screen_harness
    page = harness.page
    route = "**/api/plans/files" if failure == "list" else "**/api/plans/search?*"
    await page.route(route, lambda request: request.fulfill(status=503, body="取得失敗"))
    await page.goto(harness.base_url + "/plans")
    if failure == "search":
        await page.locator("#plans-filter").fill("本文条件")
    alert = page.locator("#plans-list-error" if failure == "list" else "#search-status")
    await playwright.async_api.expect(alert).to_contain_text("browser-test")
    await playwright.async_api.expect(alert).to_contain_text("一覧取得" if failure == "list" else "本文条件")

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
from agent_toolkit._atk.serve import sessions as serve_sessions

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
from agent_toolkit._testing import plan_fixture
from agent_toolkit._testing.serve_browser_wi_support import _entry_path

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
    await page.route("https://cdn.jsdelivr.net/**", lambda route: route.abort())
    await page.set_viewport_size({"width": width, "height": 800})
    await page.goto(screen_harness.base_url + "/plans?host=browser-test&path=plan.md")
    await page.locator("#preview h1").wait_for(state="visible")
    if failure:
        await page.evaluate(
            "() => { navigator.clipboard.writeText = async () => { throw new Error('clipboard unavailable'); }; }"
        )
    buttons = page.locator("#screen-plans .detail-toolbar button")
    used_buttons = await buttons.evaluate_all("elements => elements.map(element => element.getBoundingClientRect().toJSON())")
    for selector, label in (("#copy-btn", "本文"), ("#copy-path-btn", "パス")):
        button = page.locator(selector)
        await button.click()
        if failure:
            await playwright.async_api.expect(page.locator("#preview .copy-error")).to_contain_text("コピーに失敗")
        else:
            await playwright.async_api.expect(page.locator("#plan-copy-status")).to_have_text(f"{label}をコピーしました")
        after = await buttons.evaluate_all("elements => elements.map(element => element.getBoundingClientRect().toJSON())")
        assert [rect for rect, before in zip(after, used_buttons, strict=True) if before["width"]] == [
            rect for rect in used_buttons if rect["width"]
        ]
        assert await button.evaluate("element => element.scrollWidth <= element.clientWidth")
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.asyncio(loop_scope="session")
async def test_plan_filename_link_supports_get_and_shift_click(screen_harness: _ScreenHarness) -> None:
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
async def test_plan_list_shows_the_distinguishing_end_of_long_filenames(screen_harness: _ScreenHarness) -> None:
    harness = screen_harness
    prefix = "serve-implementation-plan-with-a-common-prefix-"
    for end in ("settings.md", "sessions.md"):
        (harness.root / (prefix + end)).write_text("# 計画\n", encoding="utf-8")
    page = harness.page
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.goto(harness.base_url + "/plans")
    for end in ("settings.md", "sessions.md"):
        link = page.get_by_role("link", name=prefix + end, exact=False)
        name = link.locator(".name")
        await playwright.async_api.expect(name).to_have_text(prefix + end)
        assert await name.evaluate(
            """name => {
                const [leading, trailing] = name.children;
                return leading.scrollWidth > leading.clientWidth
                    && trailing.getBoundingClientRect().right <= name.getBoundingClientRect().right;
            }"""
        )


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
    harness = screen_harness
    page = harness.page
    next_plan = harness.root / "next-plan.md"
    next_plan.write_text("# 次の計画\n", encoding="utf-8")
    await page.set_viewport_size({"width": 320, "height": 720})
    await page.route("**/api/plans/events", lambda route: route.abort())
    await page.goto(harness.base_url + "/plans")
    first = page.locator('#files .file[href*="path=plan.md"]')
    await first.wait_for(state="visible")

    harness.plan_path.unlink()
    await first.click()
    message = "選択した計画ファイルは移動または削除されたため表示できません。一覧を更新しました。"
    await playwright.async_api.expect(page.locator("#plans-sidebar")).to_be_visible()
    await playwright.async_api.expect(page.locator("#plans-app main")).to_be_hidden()

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
async def test_mobile_plan_keeps_list_visible_after_return_during_fetch(screen_harness: _ScreenHarness, pending: str) -> None:
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
        expect = playwright.async_api.expect
        sidebar = page.locator("#plans-sidebar")
        preview = page.locator("#preview")
        await expect(sidebar).to_be_hidden()
        await page.locator("#plans-menu-btn").click()
        await expect(sidebar).to_be_visible()
        await expect(page.locator("#plans-app main")).to_be_hidden()
        if pending == "list":
            await expect(page.locator("#plans-list-loading")).to_be_visible()
            await expect(page.locator("#plans-filter")).to_be_focused()
        release.set()
        await expect(preview.locator("h1")).to_have_text("初回")
        item = sidebar.locator(".file").first
        await expect(item).to_be_visible()
        await expect(page.locator("#plans-app main")).to_be_hidden()
        await item.click()
        await expect(sidebar).to_be_hidden()
        await expect(page.locator("#plan-title")).to_be_focused()
        await expect(preview.locator("h1")).to_be_visible()
    finally:
        release.set()
        await page.unroute(pattern, route_handler)

    await page.reload()
    await page.locator("#files .file").first.wait_for(state="attached")
    await playwright.async_api.expect(page.locator("#plans-sidebar")).to_be_hidden()
    await playwright.async_api.expect(page.locator("#preview h1")).to_be_visible()


@pytest.mark.asyncio(loop_scope="session")
async def test_mobile_plan_copy_buttons_stay_on_one_line(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    await page.route("https://cdn.jsdelivr.net/**", lambda route: route.abort())
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.goto(screen_harness.base_url + "/plans")
    await page.locator("#files .file").first.wait_for(state="visible")
    await page.locator("#files .file").first.click()
    await page.locator("#preview h1").wait_for(state="visible")

    body = page.get_by_role("button", name="本文をコピー", exact=True)
    path = page.get_by_role("button", name="ファイルのパスをコピー", exact=True)
    for button in (body, path):
        await playwright.async_api.expect(button.locator("svg")).to_be_visible()
    assert await body.evaluate("button => button.offsetTop") == await path.evaluate("button => button.offsetTop")


@pytest.mark.asyncio(loop_scope="session")
async def test_same_mtime_rewrites_refresh_plan_preview(screen_harness: _ScreenHarness) -> None:
    harness = screen_harness
    harness.plan_path.write_text(_valid_diagram_markdown("初回"), encoding="utf-8")
    await harness.page.goto(harness.base_url + "/plans")
    await harness.page.locator("#preview h1", has_text="初回").wait_for()
    await harness.page.locator("#preview .diagram-mermaid svg").wait_for(state="visible")
    fixed_stat = harness.plan_path.stat()

    async def rewrite_keeping_mtime(markdown: str) -> None:

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


class TestPlanOperationTimings:
    @pytest.fixture(autouse=True)
    def without_session_record_watch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def skip(_context: serve_sessions.SessionsContext) -> None:
            pass

        monkeypatch.setattr(serve_sessions, "start_local_watch_async", skip)

    @pytest.mark.asyncio(loop_scope="session")
    async def test_plan_operations_record_open_and_notified_reload(self, screen_harness: _ScreenHarness) -> None:
        """選択から図の描画まで、変更通知から一覧とプレビューの再描画までを、それぞれ1件の操作として取得と対応付けて記録する。

        取得単位の記録は本文の受信直後で終わり、図の非同期描画や通知後の再取得全体の所要を追跡できない。
        """
        harness = screen_harness
        page = harness.page
        harness.plan_path.write_text(_valid_diagram_markdown("初回"), encoding="utf-8")
        sent: list[dict] = []
        timings_changed = asyncio.Event()
        body_responses: list[playwright.async_api.Response] = []

        def record_timings(request: playwright.async_api.Request) -> None:
            if request.method == "POST" and request.url.endswith("/api/timings"):
                sent.extend(json.loads(request.post_data or "{}").get("records", []))
                timings_changed.set()

        def record_body_response(response: playwright.async_api.Response) -> None:
            url = urllib.parse.urlsplit(response.url)
            query = urllib.parse.parse_qs(url.query)
            if (
                response.ok
                and url.path == "/api/plans/file"
                and query.get("host") == ["browser-test"]
                and query.get("path") == [harness.plan_path.name]
            ):
                body_responses.append(response)

        async def wait_for_operation(operation: str, trigger: str, routes: set[str], request_ids: set[str]) -> None:
            async with asyncio.timeout(30):
                while True:
                    timings_changed.clear()
                    for record in sent:
                        if (
                            record.get("screen") != "plans"
                            or record.get("kind") != "operation"
                            or record.get("operation") != operation
                            or record.get("trigger") != trigger
                            or record.get("outcome") != "ok"
                        ):
                            continue
                        linked = [
                            item
                            for item in sent
                            if item.get("screen") == "plans"
                            and item.get("operation_id") == record["operation_id"]
                            and "route" in item
                        ]
                        if {item["route"] for item in linked} == routes and (
                            not request_ids or any(item.get("request_id") in request_ids for item in linked)
                        ):
                            assert record["total_ms"] >= 0
                            return
                    await timings_changed.wait()

        async def body_request_ids(heading: str) -> set[str]:
            request_ids = set()
            for response in list(body_responses):
                if f">{heading}</h1>" in await response.text():
                    request_id = await response.request.header_value("X-Atk-Request-Id")
                    assert request_id
                    request_ids.add(request_id)
            assert request_ids, heading
            return request_ids

        page.on("request", record_timings)
        page.on("response", record_body_response)
        await page.set_viewport_size({"width": 320, "height": 900})
        await page.goto(harness.base_url + "/plans")

        # 未選択時の一覧更新を先に記録し、同名の操作があっても本文更新を取り違えないことを確かめる。 pyfltr: allow-comment
        await harness.notify_file_update(_valid_diagram_markdown("選択前"))
        await wait_for_operation("external-reload", "sse", {"/api/plans/files"}, set())
        await page.locator("#files .file", has_text=harness.plan_path.name).click()
        await page.locator("#preview .diagram-mermaid svg", has_text="選択前").wait_for(state="visible")
        opened_request_ids = await body_request_ids("選択前")

        await harness.notify_file_update(_valid_diagram_markdown("通知後"))
        await page.locator("#preview h1", has_text="通知後").wait_for()
        await page.locator("#preview .diagram-mermaid svg", has_text="通知後").wait_for(state="visible")
        await wait_for_operation(
            "external-reload", "sse", {"/api/plans/files", "/api/plans/file"}, await body_request_ids("通知後")
        )
        await wait_for_operation("open-file", "click", {"/api/plans/file"}, opened_request_ids)


@pytest.mark.asyncio(loop_scope="session")
async def test_review_table_uses_available_width_and_markdown_keeps_width_limit(
    screen_harness: _ScreenHarness,
) -> None:
    harness = screen_harness
    review_row = '"1"\t"implementation-review"\t"a.py:1"\t"指摘"\t"要"\t"対応済み"\t""\n'
    (harness.root / "plan.exec-review.tsv").write_text(
        "".join(review_row.replace("a.py:1", f"src/long-component/review/actions.py:{n}") for n in range(1, 24)),
        encoding="utf-8",
    )
    await harness.page.set_viewport_size({"width": 1280, "height": 900})
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
    review = harness.page.get_by_role("region", name="レビュー指摘一覧")
    headers = review.get_by_role("columnheader")
    expect = playwright.async_api.expect
    await expect(headers.nth(0)).to_have_text("指摘内容")
    await expect(headers.nth(1)).to_have_text("対応内容")
    widths = await review.evaluate(
        """region => {
            const cells = [...region.querySelectorAll("th")];
            return {full: region.scrollWidth, visible: region.clientWidth,
                    columns: cells.map(cell => cell.getBoundingClientRect().width)};
        }""",
    )
    assert widths["full"] == widths["visible"]
    assert min(widths["columns"][:2]) > max(widths["columns"][2:])
    await expect(headers.last).to_have_text("対応不要理由")
    await expect(headers.last).to_be_in_viewport(ratio=0.99)
    await review.focus()
    await harness.page.keyboard.press("End")
    await expect(headers.nth(0)).to_be_in_viewport()
    await expect(headers.nth(1)).to_be_in_viewport()
    await harness.page.wait_for_function(
        """() => {
            const region = document.querySelector(".review-scroll");
            return region.scrollTop > 0 && region.scrollHeight - region.clientHeight - region.scrollTop <= 1;
        }"""
    )
    await harness.page.get_by_role("button", name="先頭へ").click()
    await harness.page.wait_for_function("document.querySelector('.review-scroll').scrollTop === 0")


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("failure", ["body", "clipboard", "svg"])
async def test_plan_failures_keep_selected_source_and_browser_owner(screen_harness: _ScreenHarness, failure: str) -> None:
    """選択した計画の取得の失敗とブラウザー側の処理の失敗を区別して表示し、表示中の本文を保つ。"""
    harness = screen_harness
    page = harness.page
    await page.route("https://cdn.jsdelivr.net/**", lambda route: route.abort())
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
    if failure == "body":
        notice = page.locator("#update-status")
        await playwright.async_api.expect(notice).to_contain_text("計画の本文を取得できません。")
        await playwright.async_api.expect(notice.get_by_role("button")).to_have_count(1)
        return
    alert = page.locator("#preview [role=alert]" if failure != "svg" else "#preview .diagram-error")
    await playwright.async_api.expect(alert).to_contain_text("plan.md")
    await playwright.async_api.expect(alert).to_contain_text("browser-test")
    await playwright.async_api.expect(alert).to_contain_text(str(harness.root))
    await playwright.async_api.expect(alert).to_contain_text("ブラウザー" if failure != "body" else "計画ファイルの本文")
    if failure == "clipboard":
        await playwright.async_api.expect(alert).to_contain_text("書込拒否")
        await playwright.async_api.expect(page.locator("#preview h1")).to_have_text("初回")
        await playwright.async_api.expect(
            page.locator("#preview").get_by_text("コピーの失敗の検証", exact=True)
        ).to_be_visible()


@pytest.mark.cpu_limit(
    1.5,
    reason=(
        "計画ファイルの一覧と検索の取得に失敗した場合は、エラー表示にserveのホストと、失敗した操作ま"
        "たは検索条件を示す。実ブラウザーの観測とtrace回収が必要（CPU最大実測0.619秒）。"
    ),
)
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
    alert = page.locator("#update-status")
    if failure == "list":
        await playwright.async_api.expect(alert).to_contain_text("計画一覧を取得できません。")
    else:
        await playwright.async_api.expect(alert).to_contain_text("計画を検索できません。")


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.cpu_limit(0.75, reason="Chromiumの2つのタブと計画・WI画面の遷移を実サーバーで確認する")
async def test_plan_wi_links_open_here_and_support_middle_click(screen_harness: _ScreenHarness) -> None:
    root = screen_harness.root.parent
    filename = "20261011-203736-001.md"
    legacy = "旧式 WI.md"
    for name, body in ((filename, f"本文から `{legacy}` を参照。"), (legacy, "旧式の本文")):
        _entry_path(root, name).write_text(f"---\ntype: awi\nstate: inbox\n---\n\n{body}\n", encoding="utf-8")
    plan = plan_fixture.awi_plan(repo=root, related_wi=((filename, "確認する"),))
    (screen_harness.root / "01-0000_plan.md").write_text(
        plan + f"\n本文: `{legacy}`、20261011-203736-1001.md、AGENTS.md、[既存の {filename}](/plans)\n", encoding="utf-8"
    )
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/plans")
    await page.locator("#files .pane-item").filter(has_text="01-0000_plan.md").click()
    expect = playwright.async_api.expect
    preview = page.locator("#preview")
    related = preview.get_by_role("link", name=filename, exact=True)
    await expect(related).to_be_visible()
    await expect(preview.get_by_role("link", name=legacy, exact=True)).to_be_visible()
    await expect(preview.get_by_role("link", name="AGENTS.md")).to_have_count(0)
    await expect(preview.get_by_role("link", name="20261011-203736-1001.md")).to_be_visible()
    await related.click()
    first = page.locator(".wi-reference-dialog").first
    await expect(first.get_by_role("heading", name=f"本文から `{legacy}` を参照。", exact=True)).to_be_visible()
    await expect(first.get_by_text(filename, exact=True)).to_be_visible()
    if directory := os.environ.get("ATK_SERVE_SCREENSHOT_DIR"):
        await page.screenshot(path=Path(directory) / "005-plan-dialog.png")
    await first.get_by_role("link", name=legacy).click()
    await expect(page.locator(".wi-reference-dialog").last).to_contain_text("旧式の本文")
    await page.keyboard.press("Escape")
    await page.keyboard.press("Escape")
    await expect(related).to_be_focused()
    async with page.context.expect_page() as opened:
        await related.click(button="middle")
    tab = await opened.value
    try:
        await expect(tab.locator("#detail-filename")).to_have_text(filename)
    finally:
        await tab.close()
    await related.click()
    await first.get_by_role("button", name="このWIを開く").click()
    await expect(page.locator("#detail-filename")).to_have_text(filename)


@pytest.mark.asyncio(loop_scope="session")
async def test_plan_and_wi_detail_share_scroll_edge_controls(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    screen_harness.plan_path.write_text("# 先頭\n\n" + "長い記録の段落です。\n\n" * 100 + "最後の段落\n", encoding="utf-8")
    await page.goto(screen_harness.base_url + "/plans")
    await page.locator("#files .pane-item").first.click()
    main = page.locator("#screen-plans main")
    await main.get_by_role("button", name="末尾へ", exact=True).click()
    await playwright.async_api.expect(main.get_by_text("最後の段落")).to_be_in_viewport()
    await main.get_by_role("button", name="先頭へ", exact=True).click()
    assert await main.evaluate("el => el.scrollTop") == 0

    await page.goto(screen_harness.base_url + "/?filename=question.md")
    dialog = page.locator("#detail-dialog")
    await dialog.get_by_role("button", name="末尾へ", exact=True).click()
    body = dialog.locator("#detail-dialog-body")
    assert await body.evaluate("el => el.scrollTop + el.clientHeight >= el.scrollHeight - 1")
    await dialog.get_by_role("button", name="先頭へ", exact=True).click()
    assert await body.evaluate("el => el.scrollTop") == 0


@pytest.mark.parametrize("warm", [False, True])
@pytest.mark.parametrize("failures", [1, 2])
@pytest.mark.asyncio(loop_scope="session")
async def test_plan_text_recovers_without_a_missing_notice(screen_harness: _ScreenHarness, warm: bool, failures: int) -> None:
    page = screen_harness.page
    preview = page.locator("#preview")
    expect = playwright.async_api.expect
    if warm:
        await page.goto(screen_harness.base_url + "/plans")
        await expect(preview).to_contain_text("本文-初回")
    first = asyncio.Event()
    release = asyncio.Event()
    retried = asyncio.Event()
    attempts = 0

    async def temporary_error(route: playwright.async_api.Route) -> None:
        nonlocal attempts
        attempts += 1
        if attempts <= failures:
            await route.fulfill(status=503)
            first.set()
        else:
            retried.set()
            await release.wait()
            await route.continue_()

    await page.route("**/api/plans/file?*", temporary_error)
    try:
        if warm:
            await page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        else:
            await page.goto(screen_harness.base_url + "/plans")
        await asyncio.wait_for(first.wait(), 5)
        await asyncio.wait_for(retried.wait(), 8)
        notice = page.locator("#update-status")
        if failures == 2:
            await expect(notice).to_be_visible()
        else:
            await expect(notice).to_be_hidden()
        if warm:
            await expect(preview).to_contain_text("本文-初回")
    finally:
        release.set()
    await expect(preview).to_contain_text("本文-初回")
    await expect(page.locator("#update-status")).to_be_hidden()
    assert attempts == failures + 1


@pytest.mark.asyncio(loop_scope="session")
async def test_search_does_not_show_empty_while_retrying(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    attempts = 0
    waiting = asyncio.Event()
    release = asyncio.Event()

    async def temporary_error(route: playwright.async_api.Route) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            await route.fulfill(status=503)
        elif attempts == 2:
            waiting.set()
            await release.wait()
            await route.continue_()
        else:
            await route.continue_()

    await page.route("**/api/plans/search?*", temporary_error)
    await page.goto(screen_harness.base_url + "/plans")
    try:
        await page.locator("#plans-filter").fill("本文-初回")
        await asyncio.wait_for(waiting.wait(), 5)
        await playwright.async_api.expect(page.locator("#plans-empty")).to_be_hidden()
        await playwright.async_api.expect(page.locator("#update-status")).to_be_hidden()
    finally:
        release.set()
    await playwright.async_api.expect(page.locator("#files .file").first).to_be_visible()
    await page.locator("#plans-filter").fill("存在しない検索語")
    await playwright.async_api.expect(page.locator("#plans-empty")).to_be_visible()
    assert attempts == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_search_recovers_after_notifying_without_another_input(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    attempts = 0

    async def fail_twice(route: playwright.async_api.Route) -> None:
        nonlocal attempts
        attempts += 1
        if attempts <= 2:
            await route.fulfill(status=503)
        else:
            await route.continue_()

    await page.route("**/api/plans/search?*", fail_twice)
    await page.goto(screen_harness.base_url + "/plans")
    await page.locator("#plans-filter").fill("本文-初回")
    notice = page.locator("#update-status")
    await playwright.async_api.expect(notice).to_be_visible()
    assert attempts == 2
    await playwright.async_api.expect(page.locator("#files .file").first).to_be_visible()
    await playwright.async_api.expect(notice).to_be_hidden()
    assert attempts == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_partial_search_keeps_success_and_retries_only_failed_host(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    local = {"host": "browser-test", "path": "plan.md", "name": "plan.md", "source_id": "explicit"}
    remotes = [
        {"host": "good", "path": "ok.md", "name": "ok.md"},
        {"host": "slow", "path": "missing-until-recovery.md", "name": "missing-until-recovery.md"},
    ]
    calls: list[list[str]] = []
    waiting = asyncio.Event()
    release = asyncio.Event()

    async def list_files(route: playwright.async_api.Route) -> None:
        response = await route.fetch()
        entries = await response.json()
        local.update(entries[0])
        await route.fulfill(json=[*entries, *remotes])

    async def search(route: playwright.async_api.Route) -> None:
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(route.request.url).query)
        hosts = params.get("host", [])
        calls.append(hosts)
        if len(calls) == 1:
            await route.fulfill(json={"entries": [local, remotes[0]], "failures": {"slow": 503}})
        else:
            waiting.set()
            await release.wait()
            await route.fulfill(json={"entries": remotes[1:], "failures": {}})

    await page.route("**/api/plans/files", list_files)
    await page.route("**/api/plans/search?*", search)
    await page.goto(screen_harness.base_url + "/plans")
    await page.locator("#files .file").first.wait_for()
    await page.locator("#plans-filter").fill("本文-初回")
    expect = playwright.async_api.expect
    try:
        await asyncio.wait_for(waiting.wait(), 5)
        await expect(page.locator("#files .file")).to_have_count(2)
        await expect(page.locator("#plans-empty")).to_be_hidden()
        assert calls == [[], ["slow"]]
    finally:
        release.set()
    await expect(page.locator("#files .file")).to_have_count(3)


@pytest.mark.asyncio(loop_scope="session")
async def test_permission_denied_does_not_offer_retry(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    calls = 0

    async def forbidden(route: playwright.async_api.Route) -> None:
        nonlocal calls
        calls += 1
        await route.fulfill(status=403)

    await page.route("**/api/plans/file?*", forbidden)
    await page.goto(screen_harness.base_url + "/plans")
    await playwright.async_api.expect(page.locator("#plans-selection-status")).to_contain_text("読む権限がありません")
    await playwright.async_api.expect(page.locator("#update-status")).to_be_hidden()
    assert calls == 1


@pytest.mark.parametrize("during_first", [False, True])
@pytest.mark.asyncio(loop_scope="session")
async def test_search_retry_stops_when_query_changes(screen_harness: _ScreenHarness, during_first: bool) -> None:
    page = screen_harness.page
    old_attempts = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def search(route: playwright.async_api.Route) -> None:
        nonlocal old_attempts
        if "q=old-query" in route.request.url:
            old_attempts += 1
            started.set()
            if during_first:
                await release.wait()
            await route.fulfill(status=503)
        else:
            await route.fulfill(json=[])

    await page.route("**/api/plans/search?*", search)
    await page.goto(screen_harness.base_url + "/plans")
    await page.clock.install()
    query = page.locator("#plans-filter")
    await query.fill("old-query")
    notice = page.locator("#update-status")
    try:
        if during_first:
            await asyncio.wait_for(started.wait(), 5)
        else:
            await playwright.async_api.expect(notice).to_be_visible()
        await query.fill("new-query")
        await playwright.async_api.expect(page.locator("#plans-empty")).to_be_visible()
    finally:
        release.set()
    await page.clock.fast_forward(31_000)
    assert old_attempts == (1 if during_first else 2)
    await playwright.async_api.expect(notice).to_be_hidden()


@pytest.mark.asyncio(loop_scope="session")
async def test_preview_retry_stops_when_another_plan_is_selected(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    (screen_harness.root / "second.md").write_text("# 別の計画\n\n切替先の本文", encoding="utf-8")
    old_calls = 0
    release_events = asyncio.Event()

    async def hold_events(route: playwright.async_api.Route) -> None:
        await release_events.wait()
        await route.continue_()

    async def fail_old(route: playwright.async_api.Route) -> None:
        nonlocal old_calls
        target = urllib.parse.parse_qs(urllib.parse.urlsplit(route.request.url).query).get("path")
        if target == ["plan.md"]:
            old_calls += 1
            await route.fulfill(status=503)
        else:
            await route.continue_()

    await page.route("**/api/plans/events", hold_events)
    await page.route("**/api/plans/file?*", fail_old)
    await page.clock.install()
    try:
        await page.goto(screen_harness.base_url + "/plans?host=browser-test&path=plan.md")
        notice = page.locator("#update-status")
        await playwright.async_api.expect(notice.filter(has_text="計画の本文を取得できません。")).to_be_visible()
        assert old_calls == 2
        await page.locator("#files .file").filter(has_text="second.md").click()
        await playwright.async_api.expect(page.locator("#preview")).to_contain_text("切替先の本文")
        await page.clock.fast_forward(31_000)
        await playwright.async_api.expect(notice).to_be_hidden()
        assert old_calls == 2
    finally:
        release_events.set()

"""claude-plans-viewerの実ブラウザー統合テスト。"""

import asyncio
import contextlib
import dataclasses
import os
import socket
import urllib.parse
from collections.abc import AsyncGenerator
from pathlib import Path

import playwright.async_api
import pytest
import pytest_asyncio

from pytools.claude_plans_viewer import _app, _state

_BROWSER_TEST_ENV = "CLAUDE_PLANS_VIEWER_BROWSER_TESTS"
_SERVER_START_TIMEOUT_SEC = 10.0


def _browser_tests_enabled() -> bool:
    value = os.environ.get(_BROWSER_TEST_ENV, "")
    return value.lower() in {"1", "true", "yes", "on"}


pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(
        not _browser_tests_enabled(),
        reason=f"{_BROWSER_TEST_ENV}=1の場合のみ実行する",
    ),
]


@dataclasses.dataclass
class _BrowserHarness:
    page: playwright.async_api.Page
    context: playwright.async_api.BrowserContext
    root: Path
    plan_path: Path
    state: _state.BroadcastState
    base_url: str
    requests: list[str]
    responses: list[tuple[str, int]]

    async def notify_file_update(self, markdown: str) -> None:
        self.plan_path.write_text(markdown, encoding="utf-8")
        await _state.schedule_broadcast(self.state)


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return int(server_socket.getsockname()[1])


async def _wait_for_server(port: int) -> None:
    deadline = asyncio.get_running_loop().time() + _SERVER_START_TIMEOUT_SEC
    while True:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError as exc:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("テスト用サーバーが起動しませんでした") from exc
            await asyncio.sleep(0.01)
            continue
        writer.close()
        await writer.wait_closed()
        del reader
        return


@pytest_asyncio.fixture(name="browser_harness")
async def _browser_harness_fixture(tmp_path: Path) -> AsyncGenerator[_BrowserHarness]:
    plan_path = tmp_path / "plan.md"
    plan_path.write_text(_valid_diagram_markdown("初回"), encoding="utf-8")
    app = _app.create_app(tmp_path, hostname="browser-test")
    state: _state.BroadcastState = app.config["PLANS_STATE"]
    port = _reserve_port()
    shutdown = asyncio.Event()

    async def _shutdown_trigger() -> None:
        await shutdown.wait()

    server_task = asyncio.create_task(
        app.run_task("127.0.0.1", port, shutdown_trigger=_shutdown_trigger),
    )
    playwright_instance = None
    browser = None
    context = None
    try:
        await _wait_for_server(port)
        playwright_instance = await playwright.async_api.async_playwright().start()
        browser = await playwright_instance.chromium.launch()
        context = await browser.new_context()
        page = await context.new_page()
        requests: list[str] = []
        responses: list[tuple[str, int]] = []
        page.on("request", lambda request: requests.append(request.url))
        page.on("response", lambda response: responses.append((response.url, response.status)))
        yield _BrowserHarness(
            page=page,
            context=context,
            root=tmp_path,
            plan_path=plan_path,
            state=state,
            base_url=f"http://127.0.0.1:{port}",
            requests=requests,
            responses=responses,
        )
    finally:
        if context is not None:
            await context.close()
        if browser is not None:
            await browser.close()
        if playwright_instance is not None:
            await playwright_instance.stop()
        shutdown.set()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task


def _valid_diagram_markdown(label: str) -> str:
    return (
        f"# {label}\n\n"
        f"本文-{label}\n\n"
        "```mermaid\n"
        f'graph TD\n  A["{label}"] --> B["完了"]\n'
        "```\n\n"
        "```svg\n"
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40">'
        '<rect width="120" height="40" fill="#4f46e5"/>'
        f'<text x="8" y="25" fill="white">{label}</text>'
        "</svg>\n"
        "```\n"
    )


@pytest.mark.asyncio
async def test_diagrams_render_and_refresh_safely(browser_harness: _BrowserHarness) -> None:
    harness = browser_harness
    await harness.page.goto(harness.base_url + "/")

    mermaid = harness.page.locator("#preview .diagram-mermaid svg")
    svg_image = harness.page.locator("#preview .diagram-svg img")
    await mermaid.wait_for(state="visible")
    await svg_image.wait_for(state="visible")
    assert await svg_image.evaluate("(image) => image.complete && image.naturalWidth > 0")

    initial_blob_url = await svg_image.get_attribute("src")
    assert initial_blob_url is not None
    mermaid_responses = [status for url, status in harness.responses if url.endswith("/static/mermaid.min.js")]
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
async def test_attached_plan_navigation_is_symmetric(browser_harness: _BrowserHarness) -> None:
    """左一覧に付属計画を表示せず、右ペインからbase・detail・bugsを相互に開ける。"""
    harness = browser_harness
    (harness.root / "plan.detail.md").write_text("# 詳細ページ\n", encoding="utf-8")
    (harness.root / "plan.bugs.md").write_text("# バグページ\n", encoding="utf-8")
    await harness.page.goto(harness.base_url + "/")

    await harness.page.get_by_role("heading", name="初回").wait_for(state="visible")
    assert await harness.page.get_by_text("plan.detail.md", exact=True).count() == 0
    assert await harness.page.get_by_text("plan.bugs.md", exact=True).count() == 0
    await harness.page.locator('a[data-plan-path="plan.detail.md"]').click()
    await harness.page.get_by_role("heading", name="詳細ページ").wait_for(state="visible")
    assert await harness.page.title() == "browser-test: plan.detail.md"

    await harness.page.locator('a[data-plan-path="plan.bugs.md"]').click()
    await harness.page.get_by_role("heading", name="バグページ").wait_for(state="visible")
    assert await harness.page.title() == "browser-test: plan.bugs.md"

    await harness.page.locator('a[data-plan-path="plan.md"]').click()
    await harness.page.get_by_role("heading", name="初回").wait_for(state="visible")
    assert await harness.page.title() == "browser-test: plan.md"


@pytest.mark.asyncio
async def test_mermaid_strict_security_blocks_active_content(browser_harness: _BrowserHarness) -> None:
    harness = browser_harness
    await harness.page.goto(harness.base_url + "/")
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

    assert harness.page.url == harness.base_url + "/"
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
    browser_harness: _BrowserHarness,
) -> None:
    harness = browser_harness
    await harness.page.goto(harness.base_url + "/")
    await harness.page.locator("#preview .diagram-mermaid svg").wait_for(state="visible")
    (harness.root / "first.md").write_text("# 先の選択\n\n先の内容\n", encoding="utf-8")
    (harness.root / "second.md").write_text("# 後の選択\n\n後の内容\n", encoding="utf-8")
    await _state.schedule_broadcast(harness.state)
    first_item = harness.page.get_by_text("first.md", exact=True)
    second_item = harness.page.get_by_text("second.md", exact=True)
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

    await harness.page.route("**/api/file?*", delay_first_response)
    await first_item.click()
    await asyncio.wait_for(first_requested.wait(), timeout=5)
    await second_item.click()
    await harness.page.get_by_role("heading", name="後の選択").wait_for(state="visible")
    release_first.set()
    await asyncio.wait_for(first_fulfilled.wait(), timeout=5)
    await harness.page.evaluate(
        "() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
    )
    await harness.page.wait_for_function("document.title === 'browser-test: second.md'")

    assert await harness.page.get_by_role("heading", name="後の選択").is_visible()
    assert not await harness.page.get_by_role("heading", name="先の選択").is_visible()
    assert await harness.page.title() == "browser-test: second.md"


@pytest.mark.asyncio
async def test_selection_state_survives_preview_resync(browser_harness: _BrowserHarness) -> None:
    harness = browser_harness
    await harness.page.goto(harness.base_url + "/")
    await harness.page.locator("#preview .diagram-mermaid svg").wait_for(state="visible")
    second_path = harness.root / "second.md"
    second_path.write_text("# 選択直後\n\n更新前の内容\n", encoding="utf-8")
    await _state.schedule_broadcast(harness.state)
    second_item = harness.page.get_by_text("second.md", exact=True)
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

    await harness.page.route("**/api/file?*", delay_first_response)
    await second_item.click()
    await asyncio.wait_for(first_requested.wait(), timeout=5)
    second_path.write_text("# 再同期後\n\n更新後の内容\n", encoding="utf-8")
    stat = second_path.stat()
    os.utime(second_path, (stat.st_atime, stat.st_mtime + 1))
    await harness.page.evaluate("forceResync()")
    await harness.page.get_by_role("heading", name="再同期後").wait_for(state="visible")
    release_first.set()
    await asyncio.wait_for(first_fulfilled.wait(), timeout=5)
    await harness.page.evaluate(
        "() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))",
    )

    assert await harness.page.title() == "browser-test: second.md"
    assert not await harness.page.locator("#copy-btn").is_disabled()
    assert not await harness.page.locator("#copy-path-btn").is_disabled()
    assert await harness.page.locator("#meta-mobile .meta-path").text_content() == "second.md"
    prev_disabled = await harness.page.locator("#prev-btn").is_disabled()
    next_disabled = await harness.page.locator("#next-btn").is_disabled()
    assert not (prev_disabled and next_disabled)


@pytest.mark.asyncio
async def test_mermaid_error_stays_near_source_and_keeps_preview(browser_harness: _BrowserHarness) -> None:
    harness = browser_harness
    await harness.page.goto(harness.base_url + "/")
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
async def test_forwarded_prefix_uses_base_path_for_diagrams(browser_harness: _BrowserHarness) -> None:
    harness = browser_harness
    await harness.context.set_extra_http_headers({"X-Forwarded-Prefix": "/plans"})
    response = await harness.page.goto(harness.base_url + "/plans/")

    assert response is not None
    assert response.status == 200
    await harness.page.locator("#preview .diagram-mermaid svg").wait_for(state="visible")
    svg_image = harness.page.locator("#preview .diagram-svg img")
    await svg_image.wait_for(state="visible")
    assert await svg_image.evaluate("(image) => image.complete && image.naturalWidth > 0")
    assert any(url.endswith("/plans/static/mermaid.min.js") for url in harness.requests)

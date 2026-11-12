"""`atk serve`の計画ファイル画面の実ブラウザー統合テスト（Mermaidの図表の描画・失敗・安全設定）。"""

import asyncio
import contextlib

import playwright.async_api
import pytest

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
_MERMAID_CDN_URL = "https://cdn.jsdelivr.net/npm/mermaid@12.0.0/dist/mermaid.min.js"


@pytest.mark.asyncio(loop_scope="session")
async def test_diagrams_render_and_refresh_safely(screen_harness: _ScreenHarness) -> None:
    """MermaidとSVGを描画し、更新後も能動的な内容を実行させず、古いblob URLを解放する。"""
    harness = screen_harness
    harness.plan_path.write_text(_valid_diagram_markdown("初回"), encoding="utf-8")
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


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("valid", [False, True])
async def test_delayed_svg_result_survives_same_html_resync(screen_harness: _ScreenHarness, valid: bool) -> None:
    """同じ本文の再同期後も、表示中のSVGの描画結果とblob URLの所有を維持する。"""
    page = screen_harness.page
    # SVGだけを確かめる試験のため、共有の入力にあるMermaid図の外部の読込を待たない（読込の失敗は図の位置へ表示されるだけ）。
    await page.route(_MERMAID_CDN_URL, lambda route: route.abort())
    source = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>' if valid else "<svg>broken"
    screen_harness.plan_path.write_text(f"# 遅延SVG\n\n```svg\n{source}\n```\n", encoding="utf-8")
    await page.add_init_script("""
        const descriptor = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
        window.pendingSvgLoads = [];
        Object.defineProperty(HTMLImageElement.prototype, 'src', {
          ...descriptor,
          set(value) {
            if (String(value).startsWith('blob:')) {
              window.pendingSvgLoads.push(() => descriptor.set.call(this, value));
            } else {
              descriptor.set.call(this, value);
            }
          },
        });
    """)
    await page.goto(screen_harness.base_url + "/plans")
    await playwright.async_api.expect(page.locator("#preview")).to_have_attribute("aria-busy", "false")
    await page.wait_for_function("window.pendingSvgLoads.length > 0")
    await page.locator("#preview .svg-output").evaluate("image => { window.originalSvg = image; }")
    async with page.expect_response("**/api/plans/file?*"):
        await page.evaluate("window.dispatchEvent(new Event('focus'))")
    await playwright.async_api.expect(page.locator("#preview")).to_have_attribute("aria-busy", "false")
    assert await page.locator("#preview .svg-output").evaluate("image => image === window.originalSvg")
    await page.evaluate("window.pendingSvgLoads.splice(0).forEach(load => load())")
    if valid:
        await page.wait_for_function("window.originalSvg.complete && window.originalSvg.naturalWidth > 0")
        source_url = await page.locator("#preview .svg-output").get_attribute("src")
        assert source_url is not None and source_url.startswith("blob:")
        assert await page.evaluate("url => fetch(url).then(() => true, () => false)", source_url)
    else:
        await playwright.async_api.expect(page.locator("#preview .diagram-error")).to_contain_text("plan.md")


@pytest.mark.asyncio(loop_scope="session")
async def test_mermaid_strict_security_blocks_active_content(screen_harness: _ScreenHarness) -> None:
    """Mermaid図のクリック定義と埋め込みHTMLから、能動的な内容を実行させない。"""
    harness = screen_harness
    harness.plan_path.write_text(_valid_diagram_markdown("初回"), encoding="utf-8")
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


@pytest.mark.asyncio(loop_scope="session")
async def test_mermaid_error_stays_near_source_and_keeps_preview(screen_harness: _ScreenHarness) -> None:
    """Mermaidの構文エラーは図の位置へ表示し、本文と他の図の描画を保つ。"""
    harness = screen_harness
    harness.plan_path.write_text(_valid_diagram_markdown("初回"), encoding="utf-8")
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
    assert "計画 / ブラウザー /" in await error.inner_text()
    assert "plan.md" in await error.inner_text()
    assert await harness.page.locator("#preview .diagram-mermaid details").is_visible()
    source = await harness.page.locator("#preview .diagram-mermaid details pre").text_content()
    assert source is not None
    assert "graph TD" in source
    assert "残る本文" in await harness.page.locator("#preview").inner_text()
    assert await harness.page.locator("#preview .diagram-svg img").is_visible()


@pytest.mark.asyncio(loop_scope="session")
async def test_mermaid_cdn_failure_stays_near_source_and_keeps_preview(screen_harness: _ScreenHarness) -> None:
    """CDNからMermaidを取得できない場合も図の位置へエラーを表示し、本文を保つ。"""
    harness = screen_harness
    harness.plan_path.write_text(_valid_diagram_markdown("初回"), encoding="utf-8")
    await harness.page.route(_MERMAID_CDN_URL, lambda route: route.abort())

    await harness.page.goto(harness.base_url + "/plans")

    error = harness.page.locator("#preview .diagram-mermaid .diagram-error")
    await error.wait_for(state="visible")
    assert "Mermaidの読み込みに失敗しました" in await error.inner_text()
    assert "計画 / ブラウザー /" in await error.inner_text()
    assert "plan.md" in await error.inner_text()
    assert await harness.page.locator("#preview .diagram-mermaid details").is_visible()
    assert "本文-初回" in await harness.page.locator("#preview").inner_text()
    assert await harness.page.locator("#preview .diagram-svg img").is_visible()


@pytest.mark.asyncio(loop_scope="session")
async def test_mermaid_cdn_without_response_ends_loading_with_error(screen_harness: _ScreenHarness) -> None:
    """CDNが応答を返さない場合も、上限を過ぎたら図の位置へエラーを表示し、プレビューの描画中を終える。"""
    harness = screen_harness
    harness.plan_path.write_text(_valid_diagram_markdown("初回"), encoding="utf-8")
    started = asyncio.Event()
    release = asyncio.Event()
    await harness.page.clock.install()

    async def hold(route: playwright.async_api.Route) -> None:
        started.set()
        await release.wait()
        # 上限を過ぎた画面は読込の要素を外すため、ブラウザーが要求を取り消し済みのことがある。
        with contextlib.suppress(playwright.async_api.Error):
            await route.abort()

    await harness.page.route(_MERMAID_CDN_URL, hold)
    try:
        await harness.page.goto(harness.base_url + "/plans")
        await asyncio.wait_for(started.wait(), timeout=30)
        preview = harness.page.locator("#preview")
        assert await preview.get_attribute("aria-busy") == "true"
        await harness.page.clock.run_for(30_000)
        error = harness.page.locator("#preview .diagram-mermaid .diagram-error")
        await playwright.async_api.expect(error).to_contain_text("Mermaidの読み込みが30秒以内に終わりませんでした")
        await playwright.async_api.expect(preview).to_have_attribute("aria-busy", "false")
        assert "本文-初回" in await preview.inner_text()
    finally:
        release.set()
        # この試験専用pageのルートを解除し、保留していたコールバックの終了まで所有する。
        await harness.page.unroute_all(behavior="wait")


@pytest.mark.asyncio(loop_scope="session")
async def test_forwarded_prefix_uses_cdn_for_mermaid(screen_harness: _ScreenHarness) -> None:
    """ベースパス配下でもMermaidは同じCDNから読み込む。"""
    harness = screen_harness
    harness.plan_path.write_text(_valid_diagram_markdown("初回"), encoding="utf-8")
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

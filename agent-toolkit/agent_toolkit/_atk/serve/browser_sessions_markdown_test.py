from pathlib import Path

import playwright.async_api
import pytest

from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _isolate_creation_time_index,
    _screen_harness_fixture,
)
from agent_toolkit._testing.serve_browser_sessions_support import (  # noqa: F401  # pylint: disable=unused-import
    _MARKDOWN_REMOTE_HOST,
    _MARKDOWN_REMOTE_PATH,
    _SESSION_MARKDOWN,
    _assistant_record,
    _markdown_sessions_page,
    _open_session,
    _remote_sessions_harness_fixture,
    _write_local_session,
)

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
async def test_session_assistant_markdown_rendered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: playwright.async_api.Browser
) -> None:
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


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("variant", ["named", "unnamed", "long"])
async def test_session_detail_title_shows_engine_and_session_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: playwright.async_api.Browser, variant: str
) -> None:
    titles = {"named": "名前付きセッション", "unnamed": "Claude Code", "long": "長いセッション名の折り返し確認用" * 4}
    expected = titles[variant]
    records = []
    if variant == "named":
        records.append({"type": "ai-title", "aiTitle": "生成題名"})
    if variant != "unnamed":
        records.append({"type": "custom-title", "customTitle": expected})
    path = _write_local_session(tmp_path, variant, *records, _assistant_record("応答"))
    async with _markdown_sessions_page(tmp_path, monkeypatch, browser) as (page, base_url):
        await _open_session(page, base_url, "browser-test", path)
        title = page.locator("#detail-title")
        expect = playwright.async_api.expect
        await expect(title).to_have_text(expected)
        if variant == "named":
            await expect(page.locator("#session-context")).to_contain_text("Claude Code")
            item = page.locator(f'#sessions .session-item[data-path="{path}"]')
            await expect(item).to_contain_text("/home/aki/md")
            await expect(item).to_contain_text("browser-test")
        if variant == "long":
            await page.set_viewport_size({"width": 320, "height": 720})
            metrics = await title.evaluate(
                "element => ({scroll: element.scrollWidth, client: element.clientWidth,"
                " right: element.getBoundingClientRect().right, viewport: document.documentElement.clientWidth})"
            )
            assert metrics["scroll"] <= metrics["client"], metrics
            assert metrics["right"] <= metrics["viewport"], metrics
            assert await title.evaluate("element => getComputedStyle(element).textOverflow") != "ellipsis"


@pytest.mark.asyncio(loop_scope="session")
async def test_session_non_assistant_events_stay_preformatted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: playwright.async_api.Browser
) -> None:
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


@pytest.mark.asyncio(loop_scope="session")
async def test_session_assistant_markdown_escapes_raw_html(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: playwright.async_api.Browser
) -> None:
    hostile = (
        '<script>window.__sessionXss = 1</script>\n\n<img src=x onerror="window.__sessionXss = 2">\n\n'
        "[リンク](javascript:window.__sessionXss=3)\n"
    )
    path = _write_local_session(tmp_path, "hostile", _assistant_record(hostile))
    async with _markdown_sessions_page(tmp_path, monkeypatch, browser) as (page, base_url):
        await _open_session(page, base_url, "browser-test", path)
        body = page.locator("#detail .event.kind-assistant .event-markdown")
        await playwright.async_api.expect(body).to_contain_text("<script>window.__sessionXss = 1</script>")
        assert await page.evaluate("window.__sessionXss") is None
        # 同じ実行環境では生scriptが動き、試験用の観測値へ反映されることを先に確認する。
        await page.evaluate(
            """() => {
                const script = document.createElement('script');
                script.textContent = 'window.__sessionXss = 1';
                document.body.append(script);
                script.remove();
            }"""
        )
        assert await page.evaluate("window.__sessionXss") == 1
        await page.evaluate("delete window.__sessionXss")
        assert await body.locator("script, img").count() == 0
        assert await body.locator('a[href^="javascript:"]').count() == 0
        await page.wait_for_timeout(100)
        assert await page.evaluate("window.__sessionXss") is None

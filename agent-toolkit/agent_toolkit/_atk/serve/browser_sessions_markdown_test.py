"""serveのbrowser_sessionsに関するmarkdownの試験。"""

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
    """ローカルとリモートの記録で、アシスタントの発言の見出し・箇条書き・表・コードを要素として表示する。

    `<pre>`のまま表示すると、Markdownの記号が並んだ本文を読み手が頭の中で組み立てる必要がある。
    リモートの記録はヘルパーが生データだけを返し、整形はサーバー側で行う。
    """
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
async def test_session_detail_title_shows_engine_and_session_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: playwright.async_api.Browser
) -> None:
    """右ペインの見出しはエンジン名と取得できたセッション名を表示し、ホスト名と作業ディレクトリを表示しない。

    ホスト名と作業ディレクトリは左ペインの一覧項目が示す。名前の記録が無い記録はエンジン名だけを表示する。
    セッション名は見出しだけが示すため、長い名前も狭い幅で省略せず全文を表示する。
    """
    named = _write_local_session(
        tmp_path,
        "named",
        {"type": "ai-title", "aiTitle": "生成題名"},
        {"type": "custom-title", "customTitle": "名前付きセッション"},
        _assistant_record("応答"),
    )
    unnamed = _write_local_session(tmp_path, "unnamed", _assistant_record("応答"))
    long_name = "長いセッション名の折り返し確認用" * 4
    long_named = _write_local_session(
        tmp_path, "long-named", {"type": "custom-title", "customTitle": long_name}, _assistant_record("応答")
    )
    async with _markdown_sessions_page(tmp_path, monkeypatch, browser) as (page, base_url):
        title = page.locator("#detail-title")
        await _open_session(page, base_url, "browser-test", named)
        await playwright.async_api.expect(title).to_have_text("Claude Code / 名前付きセッション")
        item = page.locator(f'#sessions .session-item[data-path="{named}"]')
        # 左ペインの一覧項目は従来どおり作業ディレクトリとホスト名を表示する。
        assert "/home/aki/md" in await item.inner_text()
        assert "browser-test" in await item.inner_text()
        await _open_session(page, base_url, "browser-test", unnamed)
        await playwright.async_api.expect(title).to_have_text("Claude Code")
        # 長いセッション名は狭い幅（320px。1280pxの400%拡大と同じCSS幅）でも省略せず、折り返して全文を表示する。
        await _open_session(page, base_url, "browser-test", long_named)
        await playwright.async_api.expect(title).to_have_text(f"Claude Code / {long_name}")
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
    """アシスタント以外の発言・挿入本文・思考・ツール・圧縮のイベントは、記号をそのまま`<pre>`で表示する。"""
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
async def test_session_thinking_with_text_opens_like_assistant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: playwright.async_api.Browser
) -> None:
    """本文のある思考・要約は発言と同じく開いて排他の対象から外し、空の思考は閉じて排他の対象に残す。"""
    path = _write_local_session(
        tmp_path,
        "thinking-open",
        {
            "type": "assistant",
            "timestamp": "2026-09-30T00:00:01Z",
            "message": {
                "content": [
                    {"type": "thinking", "thinking": ""},
                    {"type": "thinking", "thinking": "本文のある思考"},
                    {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}},
                    {"type": "text", "text": "発言"},
                ]
            },
        },
    )
    async with _markdown_sessions_page(tmp_path, monkeypatch, browser) as (page, base_url):
        await _open_session(page, base_url, "browser-test", path)
        thinking = page.locator("#detail .event.kind-thinking")
        await playwright.async_api.expect(thinking).to_have_count(2)
        empty, filled = thinking.nth(0), thinking.nth(1)
        await playwright.async_api.expect(filled).to_have_attribute("open", "")
        await playwright.async_api.expect(filled).not_to_have_attribute("data-exclusive-event", "true")
        await playwright.async_api.expect(page.locator("#detail .event.kind-assistant")).to_have_attribute("open", "")
        await playwright.async_api.expect(empty).not_to_have_attribute("open", "")
        await playwright.async_api.expect(empty).to_have_attribute("data-exclusive-event", "true")
        # 排他の項目を開いても、本文のある思考は閉じない。
        await page.locator("#detail .event.kind-tool_call summary").click()
        await playwright.async_api.expect(filled).to_have_attribute("open", "")


@pytest.mark.asyncio(loop_scope="session")
async def test_session_assistant_markdown_escapes_raw_html(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: playwright.async_api.Browser
) -> None:
    """アシスタントの発言の生HTMLは文字として表示し、スクリプトを実行せず、`javascript:`をリンクにしない。"""
    hostile = (
        '<script>window.__sessionXss = 1</script>\n\n<img src=x onerror="window.__sessionXss = 2">\n\n'
        "[リンク](javascript:window.__sessionXss=3)\n"
    )
    path = _write_local_session(tmp_path, "hostile", _assistant_record(hostile))
    async with _markdown_sessions_page(tmp_path, monkeypatch, browser) as (page, base_url):
        await _open_session(page, base_url, "browser-test", path)
        body = page.locator("#detail .event.kind-assistant .event-markdown")
        await playwright.async_api.expect(body).to_contain_text("<script>window.__sessionXss = 1</script>")
        assert await body.locator("script, img").count() == 0
        assert await body.locator('a[href^="javascript:"]').count() == 0
        await page.wait_for_timeout(200)
        assert await page.evaluate("window.__sessionXss") is None

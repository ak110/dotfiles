"""`atk serve`の実ブラウザー統合テスト。"""

import asyncio
import base64
import contextlib
import dataclasses
import functools
import json
import os
import shutil
import socket
import threading
import urllib.parse
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import playwright.async_api
import pytest
import pytest_asyncio

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config
from agent_toolkit._atk.serve import plans as serve_plans
from agent_toolkit._atk.serve import sessions as serve_sessions
from agent_toolkit._atk.serve import state as serve_state
from agent_toolkit._atk.wi import user_comment as user_comment_mutations

_BROWSER_TEST_ENV = "AGENT_TOOLKIT_SERVE_BROWSER_TESTS"
_MERMAID_CDN_URL = "https://cdn.jsdelivr.net/npm/mermaid@12.0.0/dist/mermaid.min.js"
_SERVER_START_TIMEOUT_SEC = 10.0
_SAVE_RELEASE_DELAY_SEC = 1.0
_LONG_UNKNOWN_FRONTMATTER_KEY = "unknown_" + "x" * (500 - len("unknown_"))


async def _hold_route(
    route: playwright.async_api.Route,
    *,
    started: asyncio.Event,
    release: asyncio.Event,
) -> None:
    started.set()
    await release.wait()
    await route.continue_()


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


class _BrowserOperations(serve_app.Operations):
    """Gitを使わず、更新処理の待機を同期イベントで制御する。"""

    def __init__(self, private_notes: Path) -> None:
        super().__init__(private_notes)
        self.delay_edit = False
        self.edit_started = threading.Event()
        self.edit_release = threading.Event()
        self.edit_release.set()
        self.edit_calls = 0
        self.answer_calls = 0
        self.delay_remove = False
        self.remove_started = threading.Event()
        self.remove_release = threading.Event()
        self.remove_release.set()
        self.remove_calls = 0
        self.last_transition: tuple[str, list[str], str | None] | None = None
        self.persist_mutations = False
        self.add_calls: list[dict[str, Any]] = []
        self.batch_calls: list[str] = []
        self.delay_user_comment = False
        self.user_comment_started = threading.Event()
        self.user_comment_release = threading.Event()
        self.user_comment_release.set()
        self.user_comment_calls = 0

    def sync(self) -> bool:
        """テストでは外部Git操作を行わない。"""
        return True

    def user_comment(self, state: str, filename: str, comment: str, expected_content: str) -> bool:
        """Gitを使わず、ユーザーコメントの期待本文照合と保存を行う。"""
        self.user_comment_calls += 1
        if self.delay_user_comment:
            self.user_comment_started.set()
            if not self.user_comment_release.wait(timeout=5):
                raise TimeoutError("ユーザーコメント保存の解放を待機できませんでした")
            self.delay_user_comment = False
        path = self.private_notes / state / filename
        current = path.read_text(encoding="utf-8")
        if current != expected_content:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
        updated = user_comment_mutations.update_user_comment(current, comment)
        path.write_text(updated, encoding="utf-8")
        return True

    def background_sync(self) -> bool:
        return False

    def edit(
        self,
        state: str,
        filename: str,
        content: str,
        expected_content: str | None = None,
    ) -> bool:
        del expected_content
        self.edit_calls += 1
        if self.persist_mutations:
            (self.private_notes / state / filename).write_text(content, encoding="utf-8")
        if self.delay_edit:
            self.edit_started.set()
            if not self.edit_release.wait(timeout=5):
                raise TimeoutError("編集処理の解放を待機できませんでした")
            self.delay_edit = False
        return True

    def transition(
        self,
        action: str,
        filenames: list[str],
        **kwargs: Any,
    ) -> list[str]:
        self.last_transition = (action, filenames, kwargs.get("note"))
        if action in {"hold", "unhold", "return-to-inbox"} and self.persist_mutations:
            source = kwargs.get("state") or (
                "hold" if action == "unhold" else "processing" if action == "return-to-inbox" else "inbox"
            )
            destination = "hold" if action == "hold" else "inbox"
            (self.private_notes / destination).mkdir(exist_ok=True)
            for filename in filenames:
                (self.private_notes / source / filename).rename(self.private_notes / destination / filename)
        if action == "adopt" and self.persist_mutations:
            source = kwargs.get("state") or "inbox"
            for filename in filenames:
                (self.private_notes / source / filename).rename(self.private_notes / "adopted" / filename)
        if action == "remove":
            self.remove_calls += 1
            if self.persist_mutations:
                state = kwargs.get("state")
                expected_content = kwargs.get("expected_content")
                force = kwargs.get("force", False)
                for filename in filenames:
                    state_names = (state,) if state is not None else ("processing", "inbox")
                    for state_name in state_names:
                        path = self.private_notes / state_name / filename
                        if not path.exists():
                            continue
                        if state_name == "processing" and not force:
                            raise serve_app.common.WebInputError("指定したエントリを操作できません")
                        if expected_content is not None:
                            try:
                                current_content = path.read_text(encoding="utf-8")
                            except (OSError, UnicodeError) as error:
                                raise RuntimeError("編集中に他プロセスが対象を変更しました") from error
                            if current_content != expected_content:
                                raise RuntimeError("編集中に他プロセスが対象を変更しました")
                        path.unlink()
                        break
            if self.delay_remove:
                self.remove_started.set()
                if not self.remove_release.wait(timeout=5):
                    raise TimeoutError("削除処理の解放を待機できませんでした")
                self.delay_remove = False
        return filenames

    def add(
        self,
        messages: list[str],
        *,
        entry_type: str,
        target_repo: str | None,
        scope: str | None = None,
        question_type: str | None = None,
        choices: list[str] | None = None,
    ) -> list[str]:
        """Gitを使わず、対象リポジトリの必須検証と一時リポジトリへの書込みだけを行う。"""
        del scope, question_type, choices
        self.add_calls.append({"messages": messages, "target_repo": target_repo})
        filenames: list[str] = []
        for index, message in enumerate(messages):
            parsed = serve_app.frontmatter.parse_frontmatter(message)
            metadata, body = parsed if parsed is not None else ({}, message)
            repo = target_repo if target_repo is not None else metadata.get("target_repo")
            if not isinstance(repo, str) or not repo:
                raise serve_app.common.WebInputError("target_repoを指定するか各メッセージのfrontmatterへ記載してください")
            filename = f"created-{len(self.add_calls)}-{index}.md"
            (self.private_notes / "inbox" / filename).write_text(
                f"---\ntarget_repo: {repo}\ntype: {entry_type}\n---\n\n{body.strip()}\n",
                encoding="utf-8",
            )
            filenames.append(filename)
        return filenames

    def add_batch(self, text: str) -> dict[str, object]:
        """Gitを使わず、実装と同じ解析結果を一時リポジトリへ原文保持で書き込む。"""
        self.batch_calls.append(text)
        entries = serve_app.awi_batch.parse_show_batch(text)
        for entry in entries:
            (self.private_notes / "inbox" / entry.original_name).write_text(entry.raw_text, encoding="utf-8")
        return {
            "filenames": [entry.original_name for entry in entries],
            "mapping": {entry.original_name: entry.original_name for entry in entries},
            "warnings": [],
        }

    def answer_uwi(
        self,
        filename: str,
        answer: str,
        expected_content: str | None = None,
        state: str | None = None,
    ) -> bool:
        self.answer_calls += 1
        if self.persist_mutations:
            marker = "<!-- ユーザーはこの行以降に回答を追記する -->"
            state_names = (state,) if state is not None else ("processing", "inbox")
            for state_name in state_names:
                path = self.private_notes / state_name / filename
                if not path.exists():
                    continue
                content = path.read_text(encoding="utf-8")
                if expected_content is not None and content != expected_content:
                    raise RuntimeError("編集中に他プロセスが対象を変更しました")
                updated = content.rsplit(marker, maxsplit=1)[0] + marker + "\n" + answer.strip() + "\n"
                path.write_text(updated, encoding="utf-8")
                break
        return True

    def arm_edit_delay(self) -> None:
        self.delay_edit = True
        self.edit_started.clear()
        self.edit_release.clear()

    def arm_remove_delay(self) -> None:
        self.delay_remove = True
        self.remove_started.clear()
        self.remove_release.clear()

    def arm_user_comment_delay(self) -> None:
        self.delay_user_comment = True
        self.user_comment_started.clear()
        self.user_comment_release.clear()

    def enable_file_mutations(self) -> None:
        """回答・削除で一時リポジトリの実ファイルを更新する。"""
        self.persist_mutations = True


@dataclasses.dataclass
class _BrowserHarness:
    page: playwright.async_api.Page
    context: playwright.async_api.BrowserContext
    root: Path
    current_state: serve_state.ServeState
    operations: _BrowserOperations
    base_url: str


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return int(server_socket.getsockname()[1])


async def _wait_for_server(port: int) -> None:
    deadline = asyncio.get_running_loop().time() + _SERVER_START_TIMEOUT_SEC
    while True:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError as error:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("テスト用サーバーが起動しませんでした") from error
            await asyncio.sleep(0.01)
            continue
        writer.close()
        await writer.wait_closed()
        del reader
        return


def _write_entries(root: Path) -> None:
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    adopted = root / "adopted"
    adopted.mkdir()
    rejected = root / "rejected"
    rejected.mkdir()
    long_body = "\n\n".join(f"段落{i} `inline-{i}`" for i in range(80))
    (inbox / "question.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\nsource: session-review\npriority: high\n"
        f"z_key: z\na_key: a\n{_LONG_UNKNOWN_FRONTMATTER_KEY}: long\n"
        "metadata:\n  branch: main\n  flags:\n    - one\nquestion_type: choice\nchoices: A, B\n---\n\n"
        f"## 質問\n\n{long_body}\n\n```text\n折り返す長いコード {'x' * 240}\n```\n\n"
        "## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    (inbox / "awi.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: alert-monitor\nplan_file: /tmp/plan.md\n---\n\n編集対象の本文\n",
        encoding="utf-8",
    )
    (inbox / "unknown.md").write_text("種別を判定できない本文\n", encoding="utf-8")
    (inbox / "empty.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: browser\n---\n",
        encoding="utf-8",
    )
    (inbox / "invalid.md").write_bytes(b"\xff")
    (adopted / "adopted.md").write_text(
        "---\ntype: awi\ntarget_repo: adopted/repo\nsource: browser\n---\n\n採用済みの本文\n",
        encoding="utf-8",
    )
    (rejected / "rejected.md").write_text(
        "---\ntype: awi\ntarget_repo: rejected/repo\nsource: browser\n---\n\n不採用の本文\n",
        encoding="utf-8",
    )
    for index in range(6):
        (rejected / f"many-terminal-{index}.md").write_text(
            "---\ntype: awi\ntarget_repo: rejected/repo\nsource: browser\n---\n\n多数終端検索\n",
            encoding="utf-8",
        )


@pytest_asyncio.fixture(scope="session", name="browser")
async def _browser_fixture() -> AsyncGenerator[playwright.async_api.Browser]:
    playwright_instance = await playwright.async_api.async_playwright().start()
    browser = None
    try:
        browser = await playwright_instance.chromium.launch()
        yield browser
    finally:
        if browser is not None:
            await browser.close()
        await playwright_instance.stop()


@pytest_asyncio.fixture(name="browser_harness")
async def _browser_harness_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    browser: playwright.async_api.Browser,
) -> AsyncGenerator[_BrowserHarness]:
    _write_entries(tmp_path)
    original_parse = serve_app.frontmatter.parse_frontmatter

    def parse_with_integer_key(text: str) -> tuple[dict[Any, Any], str] | None:
        parsed = original_parse(text)
        if parsed is None or "priority: high\n" not in text:
            return parsed
        metadata, body = parsed
        enriched: dict[Any, Any] = {}
        for key, value in metadata.items():
            enriched[key] = value
            if key == "z_key":
                enriched[1] = "numeric"
                enriched["1"] = "textual"
        return enriched, body

    monkeypatch.setattr(serve_app.frontmatter, "parse_frontmatter", parse_with_integer_key)
    current_state = serve_state.ServeState(tmp_path)
    operations = _BrowserOperations(tmp_path)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        current_state,
        operations=operations,
    )
    port = _reserve_port()
    shutdown = asyncio.Event()

    async def shutdown_trigger() -> None:
        await shutdown.wait()

    server_task = asyncio.create_task(app.run_task("127.0.0.1", port, shutdown_trigger=shutdown_trigger))
    context = None
    try:
        await _wait_for_server(port)
        context = await browser.new_context()
        page = await context.new_page()
        yield _BrowserHarness(
            page=page,
            context=context,
            root=tmp_path,
            current_state=current_state,
            operations=operations,
            base_url=f"http://127.0.0.1:{port}",
        )
    finally:
        if context is not None:
            await context.close()
        shutdown.set()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task


async def _open_question(page: playwright.async_api.Page) -> playwright.async_api.Locator:
    row = page.locator('#entry-list .entry-select[data-kind="uwi"]')
    await row.click()
    await page.get_by_role("dialog", name="詳細").wait_for(state="visible")
    return row


async def _open_filters(page: playwright.async_api.Page) -> None:
    """WI一覧のフィルターが閉じている場合に利用者操作で開く。"""
    details = page.locator(".filters details")
    if not await details.evaluate("element => element.open"):
        await details.locator("summary").click()


async def _shift_click_default_prevented(link: playwright.async_api.Locator) -> bool:
    """Shiftクリックを送り、アプリケーション処理後の既定動作抑止状態を返す。"""
    default_prevented = await link.evaluate(
        """element => {
          let defaultPrevented = null;
          element.addEventListener("click", event => {
            defaultPrevented = event.defaultPrevented;
            event.preventDefault();
          }, {once: true});
          element.dispatchEvent(new MouseEvent("click", {
            bubbles: true,
            cancelable: true,
            button: 0,
            shiftKey: true,
          }));
          return defaultPrevented;
        }"""
    )
    assert default_prevented is not None, "Shiftクリックの観測ハンドラーへ到達しなかった"
    return bool(default_prevented)


@pytest.mark.asyncio
async def test_responsive_layout_dialog_scroll_and_markdown(browser_harness: _BrowserHarness) -> None:
    """代表3画面幅で横overflow、固定領域、タッチ寸法、Markdown表示を検証する。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await _open_filters(page)

    for width, height, columns_visible in [(390, 844, False), (768, 1024, False), (1280, 800, True)]:
        await page.set_viewport_size({"width": width, "height": height})
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert await page.locator(".entry-columns").is_visible() is columns_visible
        row = await _open_question(page)
        dialog = page.get_by_role("dialog", name="詳細")
        await playwright.async_api.expect(dialog.locator("#detail-metadata")).to_contain_text("priority")
        await playwright.async_api.expect(dialog.locator("#detail-metadata")).to_contain_text('"branch": "main"')
        assert await dialog.locator("#detail-metadata dt").filter(has_text=_LONG_UNKNOWN_FRONTMATTER_KEY).count() == 1
        metadata_labels = await dialog.locator("#detail-metadata dt").all_text_contents()
        assert metadata_labels.index("z_key") < metadata_labels.index("int: 1")
        assert metadata_labels.index("int: 1") < metadata_labels.index("1")
        assert metadata_labels.index("1") < metadata_labels.index("a_key")
        metadata_columns = await dialog.locator("#detail-metadata").evaluate(
            "element => getComputedStyle(element).gridTemplateColumns.trim().split(/\\s+/).length"
        )
        assert metadata_columns == (1 if width < 1024 else 2)
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        close_button = dialog.get_by_role("button", name="閉じる")
        close_box = await close_button.bounding_box()
        assert close_box is not None
        assert close_box["width"] >= 44
        assert close_box["height"] >= 44

        body = dialog.locator(".dialog-body")
        scroll_metrics = await body.evaluate("element => ({client: element.clientHeight, scroll: element.scrollHeight})")
        assert scroll_metrics["scroll"] > scroll_metrics["client"]
        header_before = await dialog.locator(".dialog-header").bounding_box()
        footer_before = await dialog.locator(".dialog-footer").bounding_box()
        await body.evaluate("element => { element.scrollTop = 300; }")
        header_after = await dialog.locator(".dialog-header").bounding_box()
        footer_after = await dialog.locator(".dialog-footer").bounding_box()
        assert header_before == header_after
        assert footer_before == footer_after
        assert await dialog.locator("pre").evaluate("element => getComputedStyle(element).whiteSpace") == "pre-wrap"
        inline_background = await dialog.locator("p code").first.evaluate(
            "element => getComputedStyle(element).backgroundColor"
        )
        assert inline_background not in {"rgba(0, 0, 0, 0)", "transparent"}
        if width == 390:
            header_box = await page.locator("#screen-wi .app-header").bounding_box()
            assert header_box is not None
            assert header_box["height"] < 150
        await page.keyboard.press("Escape")
        await playwright.async_api.expect(row).to_be_focused()
        await row.click()
        await playwright.async_api.expect(dialog).to_be_visible()
        assert await body.evaluate("element => element.scrollTop") == 0
        await page.keyboard.press("Escape")
        await playwright.async_api.expect(row).to_be_focused()


@pytest.mark.asyncio
async def test_mobile_wi_list_starts_with_compact_two_row_entries(browser_harness: _BrowserHarness) -> None:
    """390px幅では一覧を先に示し、各項目を2段で描画する。"""
    page = browser_harness.page
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.goto(browser_harness.base_url + "/")
    row = page.locator("#entry-list .entry-row").first
    await row.wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")

    assert not await page.locator(".filters details").evaluate("element => element.open")
    cells = row.locator(".entry-cell")
    pseudo_display = await cells.evaluate_all(
        "elements => elements.map(element => getComputedStyle(element, '::before').display)"
    )
    assert pseudo_display == ["none"] * await cells.count()
    summary_box = await row.locator(".summary-cell").bounding_box()
    status_box = await row.locator(".status-cell").bounding_box()
    row_box = await row.bounding_box()
    assert summary_box is not None
    assert status_box is not None
    assert row_box is not None
    assert summary_box["y"] < status_box["y"]
    assert row_box["height"] <= 96
    assert row_box["y"] >= 0
    assert row_box["y"] + row_box["height"] <= 844


@pytest.mark.asyncio
async def test_global_error_can_be_closed_and_redisplayed_on_narrow_screen(
    browser_harness: _BrowserHarness,
) -> None:
    """共通エラーをキーボードで消去し、後続の失敗で再表示できることを検証する。"""
    page = browser_harness.page
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    error_region = page.locator("#operation-notice")
    error_message = page.locator("#operation-notice-message")
    close_button = page.get_by_role("button", name="操作通知を閉じる")

    async def fail_first_list_request(route: playwright.async_api.Route) -> None:
        await route.fulfill(
            status=500,
            content_type="application/json",
            body='{"error":"一覧取得失敗"}',
        )

    await page.route("**/api/entries?*", fail_first_list_request)
    await page.locator("#refresh-button").click()
    await playwright.async_api.expect(error_message).to_have_text("一覧取得失敗")
    await playwright.async_api.expect(error_region).to_be_visible()
    await page.unroute("**/api/entries?*", fail_first_list_request)
    await close_button.focus()
    await playwright.async_api.expect(close_button).to_be_focused()
    await page.keyboard.press("Enter")
    await playwright.async_api.expect(error_region).to_be_hidden()
    await playwright.async_api.expect(page.locator("#refresh-button")).to_be_focused()

    async def fail_second_list_request(route: playwright.async_api.Route) -> None:
        await route.fulfill(
            status=500,
            content_type="application/json",
            body='{"error":"後続のエラー"}',
        )

    await page.route("**/api/entries?*", fail_second_list_request)
    await page.locator("#refresh-button").click()
    await playwright.async_api.expect(error_message).to_have_text("後続のエラー")
    await playwright.async_api.expect(error_region).to_be_visible()

    metrics = await error_region.evaluate(
        """element => {
          const message = document.getElementById('operation-notice-message').getBoundingClientRect();
          const close = document.getElementById('operation-notice-close-button').getBoundingClientRect();
          return {
            scrollWidth: document.documentElement.scrollWidth,
            viewportWidth: window.innerWidth,
            regionRight: element.getBoundingClientRect().right,
            messageRight: message.right,
            closeLeft: close.left,
            closeRight: close.right,
            closeWidth: close.width,
            closeHeight: close.height
          };
        }"""
    )
    assert metrics["scrollWidth"] <= metrics["viewportWidth"]
    assert metrics["regionRight"] <= metrics["viewportWidth"]
    assert metrics["messageRight"] <= metrics["closeLeft"]
    assert metrics["closeRight"] <= metrics["viewportWidth"]
    assert metrics["closeWidth"] >= 44
    assert metrics["closeHeight"] >= 44
    await page.unroute("**/api/entries?*", fail_second_list_request)


@pytest.mark.asyncio
async def test_global_error_closed_during_sync_restores_refresh_focus(
    browser_harness: _BrowserHarness,
) -> None:
    """同期進行中に共通エラーを閉じても、同期完了時に同期ボタンへフォーカスが戻る。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    refresh_button = page.locator("#refresh-button")
    close_button = page.get_by_role("button", name="操作通知を閉じる")

    sync_started = asyncio.Event()
    release_sync = asyncio.Event()

    async def delay_sync(route: playwright.async_api.Route) -> None:
        response = await route.fetch()
        sync_started.set()
        await release_sync.wait()
        await route.fulfill(response=response)

    async def fail_entries(route: playwright.async_api.Route) -> None:
        await route.fulfill(
            status=500,
            content_type="application/json",
            body='{"error":"一覧取得失敗"}',
        )

    await page.route("**/api/sync", delay_sync)
    await refresh_button.click()
    await asyncio.wait_for(sync_started.wait(), timeout=5)
    await playwright.async_api.expect(refresh_button).to_be_disabled()

    await page.route("**/api/entries?*", fail_entries)
    # 種別フィルターの変更経路を、値を変えずに起動する。
    await page.locator("#kind-filter").dispatch_event("change")
    await playwright.async_api.expect(page.locator("#operation-notice")).to_be_visible()
    await page.unroute("**/api/entries?*", fail_entries)

    await close_button.focus()
    await close_button.click()
    await playwright.async_api.expect(page.locator("#operation-notice")).to_be_hidden()
    await playwright.async_api.expect(refresh_button).to_be_disabled()

    release_sync.set()
    await playwright.async_api.expect(refresh_button).to_be_enabled()
    await playwright.async_api.expect(refresh_button).to_be_focused()
    await page.unroute("**/api/sync", delay_sync)


@pytest.mark.asyncio
async def test_long_unknown_metadata_key_wraps_at_narrow_viewport(
    browser_harness: _BrowserHarness,
) -> None:
    """狭幅画面で500文字の未知キーを折り返し、横overflowを生じさせない。"""
    page = browser_harness.page
    await page.set_viewport_size({"width": 390, "height": 844})
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await _open_question(page)

    dialog = page.get_by_role("dialog", name="詳細")
    long_key_term = dialog.locator("#detail-metadata dt").filter(has_text=_LONG_UNKNOWN_FRONTMATTER_KEY)
    await playwright.async_api.expect(long_key_term).to_have_count(1)
    term_box = await long_key_term.bounding_box()
    metadata_box = await dialog.locator("#detail-metadata").bounding_box()
    assert term_box is not None
    assert metadata_box is not None
    assert term_box["width"] <= metadata_box["width"]
    assert await long_key_term.evaluate("element => element.scrollWidth <= element.clientWidth")
    assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.asyncio
async def test_accessible_workflows_filters_warnings_and_sse_status(browser_harness: _BrowserHarness) -> None:
    """回答・削除フォーカス、条件依存、警告、利用者起点だけの件数通知を検証する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    assert await page.locator(".filters details").evaluate("element => element.open")
    await _open_filters(page)

    warning = page.get_by_role("alert").filter(has_text="invalid.md")
    await warning.wait_for(state="visible")
    assert "UTF-8として読み取れません" in await warning.inner_text()
    assert await page.locator('#entry-list .entry-select[data-kind="unknown"]').count() == 1

    awi_row = page.locator('#entry-list .entry-select[data-kind="awi"]').filter(has_text="awi.md")
    assert await awi_row.locator(".entry-kind").text_content() == "awi"
    assert await awi_row.locator(".plan-badge").text_content() == "plan"
    assert await awi_row.locator(".state-badge").text_content() == "inbox"
    assert await awi_row.locator(".filename-cell").text_content() == "awi.md"
    assert await awi_row.locator(".summary-cell").text_content() == "編集対象の本文"

    empty_row = page.locator("#entry-list .entry-select").filter(has_text="empty.md")
    assert await empty_row.locator(".plan-badge").count() == 0
    await empty_row.click()
    empty_dialog = page.get_by_role("dialog", name="詳細")
    assert await empty_dialog.locator("#detail-content").inner_html() == ""
    assert await empty_dialog.locator("#detail-content table.frontmatter").count() == 0
    await page.keyboard.press("Escape")

    harness.operations.enable_file_mutations()
    row = await _open_question(page)
    dialog = page.get_by_role("dialog", name="詳細")
    await dialog.get_by_role("button", name="回答", exact=True).click()
    assert await dialog.locator("#detail-content").is_visible()
    await dialog.get_by_role("button", name="A", exact=True).click()
    answer = dialog.locator("#answer-input")
    assert await answer.input_value() == "A"
    await answer.fill("Aを補足")
    assert await answer.input_value() == "Aを補足"
    await dialog.get_by_role("button", name="回答を保存").click()
    await playwright.async_api.expect(dialog).to_be_hidden()
    await page.get_by_role("status").filter(has_text="回答しました").wait_for(state="visible")
    await playwright.async_api.expect(row).to_be_focused()
    assert harness.operations.answer_calls == 1

    await awi_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="編集", exact=True).click()
    edit_input = detail.locator("#edit-content")
    await edit_input.fill((await edit_input.input_value()) + "\n追記")
    await detail.get_by_role("button", name="保存", exact=True).click()
    await page.get_by_role("status").filter(has_text="保存しました").wait_for(state="visible")
    await playwright.async_api.expect(detail).to_be_hidden()
    await awi_row.click()
    detail = page.get_by_role("dialog", name="詳細")

    await detail.get_by_role("button", name="削除").click()
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    await delete_dialog.wait_for(state="visible")
    await playwright.async_api.expect(delete_dialog.get_by_role("button", name="閉じる")).to_be_focused()
    await page.keyboard.press("Escape")
    assert await page.get_by_role("dialog", name="詳細").is_visible()
    await page.keyboard.press("Escape")

    await page.locator("#kind-filter").select_option("awi")
    await playwright.async_api.expect(page.locator("#answer-filter")).to_be_disabled()
    assert await page.locator("#answer-filter").input_value() == "all"
    assert await page.locator("#source-filter option").all_text_contents() == ["all", "human", "agent"]
    async with page.expect_response(
        lambda response: response.url.endswith("/api/entries?type=awi&status=active&answered=all&source_kind=agent&page=1")
    ):
        await page.locator("#source-filter").select_option("agent")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(2)
    await playwright.async_api.expect(awi_row).to_be_visible()
    await playwright.async_api.expect(empty_row).to_be_visible()
    async with page.expect_response(
        lambda response: response.url.endswith("/api/entries?type=awi&status=active&answered=all&source_kind=human&page=1")
    ):
        await page.locator("#source-filter").select_option("human")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(0)
    await page.locator("#clear-filters-button").click()

    await page.locator("#target-filter").select_option("example/repo")
    await page.locator("#state-filter").select_option("adopted")
    await playwright.async_api.expect(page.locator("#target-filter")).to_have_value("")
    assert await page.locator("#target-filter option").all_text_contents() == ["all", "adopted/repo"]
    await page.locator("#entry-list .entry-select").filter(has_text="adopted.md").wait_for(state="visible")

    async with page.expect_response(
        lambda response: (
            response.request.method == "GET"
            and response.url.endswith("/api/entries?type=all&status=active&answered=all&page=1")
        )
    ):
        await page.locator("#clear-filters-button").click()
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(4)
    await playwright.async_api.expect(page.locator("#result-status")).to_have_text("4件を表示")

    async with page.expect_response(
        lambda response: (
            response.request.method == "GET"
            and response.url.endswith(
                "/api/entries?type=all&status=active&answered=all&q=%E7%B7%A8%E9%9B%86%E5%AF%BE%E8%B1%A1&page=1"
            )
        )
    ):
        await page.locator("#search-input").fill("編集対象")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(1)
    await playwright.async_api.expect(awi_row).to_be_visible()
    await playwright.async_api.expect(page.locator("#result-status")).to_have_text("1件を表示")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await playwright.async_api.expect(page.locator("#connection-status")).to_be_hidden()
    (harness.root / "inbox" / "sse.md").write_text(
        "---\ntype: awi\ntarget_repo: sse/repo\nsource: browser\n---\n\n編集対象の外部追加\n",
        encoding="utf-8",
    )
    harness.current_state.publish()
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(2)
    await page.locator("#entry-list .entry-select").filter(has_text="sse.md").wait_for(state="visible")
    await playwright.async_api.expect(page.locator('#target-filter option[value="sse/repo"]')).to_have_count(1)
    await playwright.async_api.expect(page.locator("#result-status")).to_have_text("1件を表示")


@pytest.mark.asyncio
async def test_one_choice_uwi_can_be_answered_then_adopted(browser_harness: _BrowserHarness) -> None:
    """回答後は詳細を閉じ、再び開いて採用できる。"""
    harness = browser_harness
    filename = "one-choice.md"
    (harness.root / "inbox" / filename).write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\nquestion_type: choice\n"
        "choices: 問題がある（是正内容をこの欄へ書いてください）\n---\n\n"
        "## 質問\n\n問題はありますか？\n\n## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    harness.operations.enable_file_mutations()
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator(f'.entry-select[data-key="inbox/{filename}"]').click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="回答", exact=True).click()
    await detail.get_by_role("button", name="問題がある（是正内容をこの欄へ書いてください）").click()
    await detail.locator("#answer-input").fill("問題がある。是正内容を確認しました。")
    await detail.get_by_role("button", name="回答を保存").click()
    await playwright.async_api.expect(detail).to_be_hidden()
    await page.get_by_role("status").filter(has_text="回答しました").wait_for(state="visible")
    await page.locator(f'.entry-select[data-key="inbox/{filename}"]').click()
    await playwright.async_api.expect(detail.locator("#detail-metadata")).to_contain_text("answered")
    await playwright.async_api.expect(detail.locator("#detail-metadata")).to_contain_text("yes")
    await playwright.async_api.expect(detail.locator("#decision-panel")).to_be_hidden()
    await detail.get_by_role("button", name="採用", exact=True).click()
    note = detail.get_by_label("メモ（任意）")
    await playwright.async_api.expect(note).to_be_focused()
    await playwright.async_api.expect(detail.get_by_role("button", name="保留", exact=True)).to_be_hidden()
    await note.fill("回答どおり採用する")
    await detail.get_by_role("button", name="採用を確定").click()
    await page.get_by_role("status").filter(has_text="採用しました").wait_for(state="visible")
    assert harness.operations.last_transition == ("adopt", [filename], "回答どおり採用する")
    assert (harness.root / "adopted" / filename).exists()
    assert not (harness.root / "inbox" / filename).exists()


@pytest.mark.asyncio
async def test_search_fallback_shows_limited_terminal_matches_and_keeps_filters(
    browser_harness: _BrowserHarness,
) -> None:
    """既定状態で終端状態を検索し、少数結果だけを補助表示して条件を維持する。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await _open_filters(page)
    notice = page.locator("#list-fallback-notice")
    expected_notice = (
        "状態などの条件では一致しなかったため、検索欄の条件だけで見つかった項目を表示しています。"
        "フィルターの選択値は変更していません。"
    )

    async with page.expect_response(
        lambda response: response.url.endswith("/api/entries?q=%E6%8E%A1%E7%94%A8%E6%B8%88%E3%81%BF&page=1")
    ):
        await page.locator("#search-input").fill("採用済み")
    await page.locator('.entry-select[data-key="adopted/adopted.md"]').wait_for(state="visible")
    await playwright.async_api.expect(notice).to_have_text(expected_notice)
    await playwright.async_api.expect(page.locator("#state-filter")).to_have_value("active")
    await playwright.async_api.expect(page.locator("#kind-filter")).to_have_value("all")
    await playwright.async_api.expect(page.locator("#answer-filter")).to_have_value("all")

    async with page.expect_response(
        lambda response: response.url.endswith("/api/entries?q=%E4%B8%8D%E6%8E%A1%E7%94%A8&page=1")
    ):
        await page.locator("#search-input").fill("不採用")
    await page.locator('.entry-select[data-key="rejected/rejected.md"]').wait_for(state="visible")
    await playwright.async_api.expect(notice).to_be_visible()
    await playwright.async_api.expect(page.locator("#state-filter")).to_have_value("active")

    async with page.expect_response(lambda response: response.url.endswith("/api/entries?q=many-terminal&page=1")):
        await page.locator("#search-input").fill("many-terminal")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(0)
    await playwright.async_api.expect(notice).to_be_hidden()
    await playwright.async_api.expect(page.locator("#state-filter")).to_have_value("active")

    request_urls: list[str] = []
    page.on("request", lambda request: request_urls.append(request.url) if "/api/entries?" in request.url else None)
    await page.locator("#search-input").fill("")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(4)
    await page.wait_for_timeout(100)
    assert request_urls == [f"{browser_harness.base_url}/api/entries?type=all&status=active&answered=all&page=1"]

    request_urls.clear()
    async with page.expect_response(
        lambda response: response.url.endswith(
            "/api/entries?type=all&status=active&answered=all&q=%E7%B7%A8%E9%9B%86%E5%AF%BE%E8%B1%A1&page=1"
        )
    ):
        await page.locator("#search-input").fill("編集対象")
    await page.locator('.entry-select[data-key="inbox/awi.md"]').wait_for(state="visible")
    await playwright.async_api.expect(notice).to_be_hidden()
    await playwright.async_api.expect(page.locator("#state-filter")).to_have_value("active")
    await page.wait_for_timeout(100)
    assert request_urls == [
        f"{browser_harness.base_url}/api/entries?type=all&status=active&answered=all&q=%E7%B7%A8%E9%9B%86%E5%AF%BE%E8%B1%A1&page=1"
    ]

    await page.locator("#kind-filter").select_option("all")
    await page.locator("#state-filter").select_option("all")
    await page.locator("#answer-filter").select_option("all")
    await page.locator("#target-filter").select_option("")
    await page.locator("#source-filter").select_option("")
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    request_urls.clear()
    async with page.expect_response(
        lambda response: response.url.endswith("/api/entries?type=all&status=all&answered=all&q=all-filters-only&page=1")
    ):
        await page.locator("#search-input").fill("all-filters-only")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(0)
    await playwright.async_api.expect(notice).to_be_hidden()
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    assert request_urls == [
        f"{browser_harness.base_url}/api/entries?type=all&status=all&answered=all&q=all-filters-only&page=1"
    ]


@pytest.mark.asyncio
async def test_answer_change_terminal_read_only_and_identifier_surfaces(browser_harness: _BrowserHarness) -> None:
    """既存回答の変更、終端状態の編集制限、削除及び識別子表示を検証する。"""
    harness = browser_harness
    page = harness.page
    question_path = harness.root / "inbox" / "question.md"
    question_path.write_text(question_path.read_text(encoding="utf-8") + "既存回答\n", encoding="utf-8")
    await page.goto(harness.base_url + "/")
    await _open_filters(page)

    question_row = page.locator('.entry-select[data-key="inbox/question.md"]')
    await question_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("uwi / inbox")
    await detail.get_by_role("button", name="回答を変更", exact=True).click()
    await playwright.async_api.expect(detail.locator("#answer-input")).to_have_value("既存回答")
    await page.keyboard.press("Escape")

    await page.locator("#state-filter").select_option("all")
    await page.locator('.entry-select[data-key="adopted/adopted.md"]').click()
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("awi / adopted")
    await playwright.async_api.expect(detail.locator("#readonly-notice")).to_be_visible()
    await playwright.async_api.expect(detail.locator("#readonly-notice")).to_have_text("この項目は編集と回答の対象外です。")
    await playwright.async_api.expect(detail.locator("#edit-button")).to_be_hidden()
    await playwright.async_api.expect(detail.locator("#answer-button")).to_be_hidden()
    await playwright.async_api.expect(detail.locator("#delete-button")).to_be_visible()
    await playwright.async_api.expect(detail.locator("#hold-button")).to_be_visible()
    await playwright.async_api.expect(detail.locator("#return-to-inbox-button")).to_be_visible()


@pytest.mark.asyncio
async def test_hold_and_rejected_details_offer_recovery_operations(browser_harness: _BrowserHarness) -> None:
    """保留中の全操作と、不採用項目のinbox復帰操作を実ブラウザーで表示する。"""
    harness = browser_harness
    page = harness.page
    hold = harness.root / "hold"
    hold.mkdir()
    (hold / "held-awi.md").write_text(
        "---\ntype: awi\ntarget_repo: held/repo\nsource: session-review\n---\n\n保留中の本文\n",
        encoding="utf-8",
    )
    (hold / "held-question.md").write_text(
        "---\ntype: uwi\ntarget_repo: held/repo\nquestion_type: free-form\n---\n\n"
        "## 質問\n\n保留中の質問\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    await page.goto(browser_harness.base_url + "/")
    await _open_filters(page)
    await page.locator("#state-filter").select_option("all")
    detail = page.get_by_role("dialog", name="詳細")

    await page.locator('.entry-select[data-key="hold/held-awi.md"]').click()
    for button_name in ("編集", "採用", "却下", "保留を解除", "削除"):
        await playwright.async_api.expect(detail.get_by_role("button", name=button_name, exact=True)).to_be_visible()
    await playwright.async_api.expect(detail.locator("#user-comment-button")).to_be_visible()
    await page.keyboard.press("Escape")

    await page.locator('.entry-select[data-key="hold/held-question.md"]').click()
    await playwright.async_api.expect(detail.get_by_role("button", name="回答", exact=True)).to_be_visible()
    await playwright.async_api.expect(detail.get_by_role("button", name="採用", exact=True)).to_be_visible()
    await playwright.async_api.expect(detail.locator("#reject-button")).to_be_hidden()
    await page.keyboard.press("Escape")

    await page.locator('.entry-select[data-key="rejected/rejected.md"]').click()
    await playwright.async_api.expect(detail.locator("#readonly-notice")).to_be_hidden()
    await playwright.async_api.expect(detail.get_by_role("button", name="受信へ戻す", exact=True)).to_be_visible()
    await playwright.async_api.expect(detail.locator("#hold-button")).to_be_visible()


@pytest.mark.asyncio
async def test_terminal_work_items_reopen_and_hold_through_browser(browser_harness: _BrowserHarness) -> None:
    """両終端状態のWIを画面から再開・保留・削除し、結果まで確認する。"""
    harness = browser_harness
    harness.operations.persist_mutations = True
    page = harness.page
    delete_paths = {}
    for state in ("adopted", "rejected"):
        path = harness.root / state / f"delete-{state}.md"
        path.write_text(f"---\ntype: awi\ntarget_repo: example/repo\n---\n\n{state}の削除対象\n", encoding="utf-8")
        delete_paths[state] = path
    await page.goto(harness.base_url + "/")
    await _open_filters(page)
    await page.locator("#state-filter").select_option("all")

    await page.locator('.entry-select[data-key="adopted/adopted.md"]').click()
    await page.locator("#hold-button").click()
    await playwright.async_api.expect(page.get_by_role("dialog", name="詳細")).to_be_hidden()
    await page.locator('.entry-select[data-key="hold/adopted.md"]').wait_for(state="visible")

    await page.locator('.entry-select[data-key="rejected/rejected.md"]').click()
    await page.locator("#return-to-inbox-button").click()
    await playwright.async_api.expect(page.get_by_role("dialog", name="詳細")).to_be_hidden()
    await page.locator('.entry-select[data-key="inbox/rejected.md"]').wait_for(state="visible")

    detail = page.get_by_role("dialog", name="詳細")
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    for state, path in delete_paths.items():
        row = page.locator(f'.entry-select[data-key="{state}/{path.name}"]')
        await row.click()
        await detail.get_by_role("button", name="削除", exact=True).click()
        await playwright.async_api.expect(delete_dialog.locator("#delete-state")).to_have_text(f"awi / {state}")
        async with page.expect_request("**/api/entries/remove") as request_info:
            await delete_dialog.get_by_role("button", name="削除する").click()
        payload = (await request_info.value).post_data_json
        assert isinstance(payload, dict)
        assert payload["state"] == state
        await delete_dialog.wait_for(state="hidden")
        await playwright.async_api.expect(row).to_have_count(0)
        assert not path.exists()


@pytest.mark.asyncio
async def test_browser_notification_uses_filename_registration_identity(browser_harness: _BrowserHarness) -> None:
    """初回と既知UWIの属性変化を通知せず、新規未回答UWIだけを通知する。"""
    harness = browser_harness
    page = harness.page
    await page.add_init_script(
        """
window.__notificationCalls = [];
class TestNotification {
  static permission = 'default';
  static async requestPermission() { TestNotification.permission = 'granted'; return 'granted'; }
  constructor(title, options) { window.__notificationCalls.push({title, body: options.body}); }
}
Object.defineProperty(window, 'Notification', {configurable: true, value: TestNotification});
"""
    )
    async with page.expect_response(lambda response: response.url.endswith("/api/entries?type=uwi&status=all&answered=all")):
        await page.goto(harness.base_url + "/")
    assert await page.evaluate("window.__notificationCalls.length") == 0
    notification_button = page.get_by_role("button", name="通知を有効化")
    await notification_button.click()
    await playwright.async_api.expect(notification_button).to_be_hidden()

    async def publish_and_wait() -> None:
        async with page.expect_response(
            lambda response: response.url.endswith("/api/entries?type=uwi&status=all&answered=all")
        ):
            harness.current_state.publish()

    marker = "<!-- ユーザーはこの行以降に回答を追記する -->"
    new_path = harness.root / "inbox" / "new-question.md"
    new_unanswered = f"---\ntype: uwi\ntarget_repo: new/repo\n---\n\n## 質問\n\n新規\n\n## 回答\n\n{marker}\n"
    new_path.write_text(new_unanswered, encoding="utf-8")
    await publish_and_wait()
    await page.wait_for_function("window.__notificationCalls.length === 1")
    assert await page.evaluate("window.__notificationCalls") == [{"title": "新規未回答UWI", "body": "new-question.md"}]

    question_path = harness.root / "inbox" / "question.md"
    original_question = question_path.read_text(encoding="utf-8")
    question_path.write_text(original_question + "回答済み\n", encoding="utf-8")
    await publish_and_wait()
    question_path.write_text(original_question, encoding="utf-8")
    await publish_and_wait()
    question_path.write_text(original_question.replace("example/repo", "changed/repo"), encoding="utf-8")
    await publish_and_wait()

    processing_path = harness.root / "processing" / "question.md"
    processing_path.parent.mkdir(exist_ok=True)
    question_path.rename(processing_path)
    await publish_and_wait()
    processing_path.rename(question_path)
    await publish_and_wait()

    new_path.unlink()
    await publish_and_wait()
    new_path.write_text(new_unanswered, encoding="utf-8")
    await publish_and_wait()
    await page.wait_for_timeout(100)
    assert await page.evaluate("window.__notificationCalls.length") == 1


@pytest.mark.asyncio
async def test_pending_edit_and_closed_delete_route_results(browser_harness: _BrowserHarness) -> None:
    """未解決更新の二重送信、入力固定、処理中閉鎖、完了結果の配送先を検証する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")

    awi_row = page.locator('#entry-list .entry-select[data-kind="awi"]').filter(has_text="awi.md")
    await awi_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="編集", exact=True).click()
    edit_input = detail.locator("#edit-content")
    await edit_input.fill((await edit_input.input_value()) + "\n追記")
    harness.operations.arm_edit_delay()
    await page.evaluate(
        "document.getElementById('save-entry-button').click(); document.getElementById('save-entry-button').click()"
    )
    assert await asyncio.to_thread(harness.operations.edit_started.wait, 5)
    await playwright.async_api.expect(edit_input).to_be_disabled()
    await playwright.async_api.expect(detail.locator("#save-entry-button")).to_be_disabled()
    await playwright.async_api.expect(detail.get_by_role("button", name="閉じる")).to_be_enabled()
    assert await detail.locator("#detail-shell").get_attribute("aria-busy") == "true"
    assert harness.operations.edit_calls == 1
    await detail.get_by_role("button", name="閉じる").click()
    harness.operations.edit_release.set()
    await page.get_by_role("status").filter(has_text="保存しました").wait_for(state="visible")

    await awi_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="削除").click()
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    harness.operations.arm_remove_delay()
    await delete_dialog.get_by_role("button", name="削除する").click()
    assert await asyncio.to_thread(harness.operations.remove_started.wait, 5)
    await playwright.async_api.expect(delete_dialog.get_by_role("button", name="閉じる")).to_be_enabled()
    await delete_dialog.get_by_role("button", name="閉じる").click()
    assert await detail.is_visible()
    harness.operations.remove_release.set()
    await page.get_by_role("status").filter(has_text="削除しました").wait_for(state="visible")
    assert harness.operations.remove_calls == 1


@pytest.mark.asyncio
async def test_edit_success_does_not_depend_on_auxiliary_detail_get(
    browser_harness: _BrowserHarness,
) -> None:
    """本文保存の成功を後続の詳細取得失敗へ変換しない。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    awi_row = page.locator('.entry-select[data-key="inbox/awi.md"]')
    await awi_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="編集", exact=True).click()
    edit_input = detail.locator("#edit-content")
    await edit_input.fill((await edit_input.input_value()) + "\n保存追記")
    request_methods: list[str] = []

    async def fail_detail_get(route: playwright.async_api.Route) -> None:
        request_methods.append(route.request.method)
        if route.request.method == "GET":
            await route.fulfill(status=500, json={"error": "詳細を再取得できませんでした。"})
            return
        await route.continue_()

    await page.route("**/api/entries/inbox/awi.md", fail_detail_get)
    await detail.get_by_role("button", name="保存", exact=True).click()
    await page.get_by_role("status").filter(has_text="保存しました").wait_for(state="visible")
    await detail.wait_for(state="hidden")
    assert request_methods == ["PUT"]
    await page.unroute("**/api/entries/inbox/awi.md", fail_detail_get)


@pytest.mark.asyncio
async def test_sse_refreshes_open_detail_preserves_input_and_closes_missing_entry(
    browser_harness: _BrowserHarness,
) -> None:
    """SSE更新時の詳細再取得、入力保持、消失時の閉鎖と復帰先を検証する。"""
    harness = browser_harness
    page = harness.page
    awi_path = harness.root / "inbox" / "awi.md"
    await page.goto(harness.base_url + "/")
    awi_row = page.locator('#entry-list .entry-select[data-kind="awi"]').filter(has_text="awi.md")
    await awi_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.wait_for(state="visible")

    awi_path.write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: browser\n---\n\n外部更新後の本文\n",
        encoding="utf-8",
    )
    harness.current_state.publish()
    await playwright.async_api.expect(detail.locator("#detail-content")).to_contain_text("外部更新後の本文")

    await detail.get_by_role("button", name="編集", exact=True).click()
    edit_input = detail.locator("#edit-content")
    await edit_input.fill("利用者の未保存本文")
    awi_path.write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: browser\n---\n\n編集中の外部更新\n",
        encoding="utf-8",
    )
    harness.current_state.publish()
    await detail.get_by_role("alert").filter(has_text="外部で項目が更新されました").wait_for(state="visible")
    assert await edit_input.input_value() == "利用者の未保存本文"
    await playwright.async_api.expect(detail.get_by_role("button", name="保存", exact=True)).to_be_disabled()

    awi_path.unlink()
    harness.current_state.publish()
    await detail.wait_for(state="hidden")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select").first).to_be_focused()


@pytest.mark.asyncio
async def test_detail_focus_falls_back_after_answer_filter_and_delete(
    browser_harness: _BrowserHarness,
) -> None:
    """回答後の詳細を閉じたときや削除・0件化後に操作可能な一覧要素へ戻る。"""
    harness = browser_harness
    page = harness.page
    harness.operations.enable_file_mutations()
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await playwright.async_api.expect(page.locator("#connection-status")).to_be_hidden()
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await _open_filters(page)

    await page.locator("#answer-filter").select_option("no")
    question_row = page.locator('#entry-list .entry-select[data-kind="uwi"]')
    await question_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="回答", exact=True).click()
    await detail.locator("#answer-input").fill("回答済みにする")
    await detail.get_by_role("button", name="回答を保存").click()
    await page.get_by_role("status").filter(has_text="回答しました").wait_for(state="visible")
    await page.keyboard.press("Escape")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(0)
    await playwright.async_api.expect(page.locator("#empty-clear-button")).to_be_focused()

    await page.locator("#empty-clear-button").click()
    awi_row = page.locator('#entry-list .entry-select[data-kind="awi"]').filter(has_text="awi.md")
    await awi_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="削除").click()
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    await delete_dialog.get_by_role("button", name="削除する").click()
    await playwright.async_api.expect(awi_row).to_have_count(0)
    if await detail.is_visible():
        await page.keyboard.press("Escape")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select").first).to_be_focused()


@pytest.mark.asyncio
async def test_sse_reconciliation_preserves_identity_and_owned_dialogs(
    browser_harness: _BrowserHarness,
) -> None:
    """同名項目、状態移動、親子ダイアログ消失を実ブラウザーで調停する。"""
    harness = browser_harness
    page = harness.page
    processing = harness.root / "processing"
    processing.mkdir(exist_ok=True)
    inbox_same = harness.root / "inbox" / "same.md"
    processing_same = processing / "same.md"
    moving = harness.root / "inbox" / "moving.md"
    inbox_same.write_text("---\ntype: awi\n---\n\n未処理の同名本文\n", encoding="utf-8")
    processing_same.write_text("---\ntype: awi\n---\n\n処理中の同名本文\n", encoding="utf-8")
    moving.write_text("---\ntype: awi\n---\n\n移動対象\n", encoding="utf-8")
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await playwright.async_api.expect(page.locator("#connection-status")).to_be_hidden()

    processing_row = page.locator('.entry-select[data-key="processing/same.md"]')
    await processing_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("awi / processing")
    await playwright.async_api.expect(detail.locator("#detail-content")).to_contain_text("処理中の同名本文")
    harness.current_state.publish()
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("awi / processing")
    await playwright.async_api.expect(detail.locator("#detail-content")).to_contain_text("処理中の同名本文")

    await detail.get_by_role("button", name="編集", exact=True).click()
    edit_input = detail.locator("#edit-content")
    await edit_input.fill((await edit_input.input_value()) + "\n処理中だけを保存")
    async with page.expect_request(
        lambda request: request.method == "PUT" and request.url.endswith("/api/entries/processing/same.md")
    ):
        await detail.get_by_role("button", name="保存", exact=True).click()
    await page.get_by_role("status").filter(has_text="保存しました").wait_for(state="visible")
    await detail.wait_for(state="hidden")

    await processing_row.click()
    await detail.wait_for(state="visible")
    adopted_same = harness.root / "adopted" / "same.md"
    processing_same.rename(adopted_same)
    harness.current_state.publish()
    await detail.wait_for(state="hidden")
    await page.get_by_role("alert").filter(has_text="移動先を一意に特定できません").wait_for(state="visible")

    moving_row = page.locator('.entry-select[data-key="inbox/moving.md"]')
    await moving_row.click()
    await detail.get_by_role("button", name="削除").click()
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    moved = processing / "moving.md"
    moving.rename(moved)
    harness.current_state.publish()
    await delete_dialog.wait_for(state="hidden")
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("awi / processing")
    await playwright.async_api.expect(detail.locator("#detail-dialog-body")).to_be_focused()

    await detail.get_by_role("button", name="削除").click()
    await playwright.async_api.expect(delete_dialog.locator("#force-delete-row")).to_be_visible()
    moved.unlink()
    harness.current_state.publish()
    await delete_dialog.wait_for(state="hidden")
    await detail.wait_for(state="hidden")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select").first).to_be_focused()


@pytest.mark.asyncio
async def test_self_write_sse_alert_clears_after_save_and_answer_success(
    browser_harness: _BrowserHarness,
) -> None:
    """自書込みSSEが応答より先でも、保存・回答成功後に警告を残さない。"""
    harness = browser_harness
    page = harness.page
    harness.operations.enable_file_mutations()
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await playwright.async_api.expect(page.locator("#connection-status")).to_be_hidden()

    awi_row = page.locator('.entry-select[data-key="inbox/awi.md"]')
    await awi_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="編集", exact=True).click()
    edit_input = detail.locator("#edit-content")
    await edit_input.fill((await edit_input.input_value()) + "\n保存追記")
    edit_finished = asyncio.Event()
    release_edit_response = asyncio.Event()

    async def delay_edit_response(route: playwright.async_api.Route) -> None:
        if route.request.method != "PUT":
            await route.continue_()
            return
        response = await route.fetch()
        edit_finished.set()
        await release_edit_response.wait()
        await route.fulfill(response=response)

    await page.route("**/api/entries/inbox/awi.md", delay_edit_response)
    await detail.get_by_role("button", name="保存", exact=True).click()
    await asyncio.wait_for(edit_finished.wait(), timeout=5)
    harness.current_state.publish()
    try:
        await detail.get_by_role("alert").filter(has_text="外部で項目が更新されました").wait_for(state="visible", timeout=4_000)
    finally:
        release_edit_response.set()
    await page.get_by_role("status").filter(has_text="保存しました").wait_for(state="visible")
    await detail.wait_for(state="hidden")
    await page.unroute("**/api/entries/inbox/awi.md", delay_edit_response)
    await awi_row.click()
    await detail.wait_for(state="visible")
    async with page.expect_response(
        lambda response: response.request.method == "GET" and response.url.endswith("/api/entries/inbox/awi.md")
    ):
        harness.current_state.publish()
    await playwright.async_api.expect(detail.locator("#detail-alert")).to_be_hidden()
    await page.keyboard.press("Escape")

    question_row = page.locator('.entry-select[data-key="inbox/question.md"]')
    await question_row.click()
    await detail.get_by_role("button", name="回答", exact=True).click()
    await detail.locator("#answer-input").fill("競合しない回答")
    answer_finished = asyncio.Event()
    release_answer_response = asyncio.Event()

    async def delay_answer_response(route: playwright.async_api.Route) -> None:
        response = await route.fetch()
        answer_finished.set()
        await release_answer_response.wait()
        await route.fulfill(response=response)

    await page.route("**/api/entries/answer", delay_answer_response)
    await detail.get_by_role("button", name="回答を保存").click()
    await asyncio.wait_for(answer_finished.wait(), timeout=5)
    harness.current_state.publish()
    try:
        await detail.get_by_role("alert").filter(has_text="外部で項目が更新されました").wait_for(state="visible", timeout=4_000)
    finally:
        release_answer_response.set()
    await page.get_by_role("status").filter(has_text="回答しました").wait_for(state="visible")
    await page.unroute("**/api/entries/answer", delay_answer_response)
    await page.keyboard.press("Escape")
    await question_row.click()
    await detail.wait_for(state="visible")
    async with page.expect_response(
        lambda response: response.request.method == "GET" and response.url.endswith("/api/entries/inbox/question.md")
    ):
        harness.current_state.publish()
    await playwright.async_api.expect(detail.locator("#detail-alert")).to_be_hidden()


@pytest.mark.asyncio
async def test_delete_and_sse_completion_orders_close_owned_dialogs_once(
    browser_harness: _BrowserHarness,
) -> None:
    """削除応答とSSEの到着順にかかわらず、親子を閉じて一覧へ戻す。"""
    harness = browser_harness
    page = harness.page
    for filename in ("sse-first.md", "response-first.md"):
        (harness.root / "inbox" / filename).write_text(
            "---\ntype: awi\n---\n\n削除順序の検証\n",
            encoding="utf-8",
        )
    harness.operations.enable_file_mutations()
    await page.goto(harness.base_url + "/")

    detail = page.get_by_role("dialog", name="詳細")
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    sse_first = page.locator('.entry-select[data-key="inbox/sse-first.md"]')
    await sse_first.click()
    await detail.get_by_role("button", name="削除").click()
    harness.operations.arm_remove_delay()
    await delete_dialog.get_by_role("button", name="削除する").click()
    assert await asyncio.to_thread(harness.operations.remove_started.wait, 5)
    harness.current_state.publish()
    await delete_dialog.wait_for(state="hidden")
    await detail.wait_for(state="hidden")
    harness.operations.remove_release.set()
    await page.get_by_role("status").filter(has_text="削除しました").wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select").first).to_be_focused()

    response_first = page.locator('.entry-select[data-key="inbox/response-first.md"]')
    await response_first.click()
    await detail.get_by_role("button", name="削除").click()
    await delete_dialog.get_by_role("button", name="削除する").click()
    await delete_dialog.wait_for(state="hidden")
    await detail.wait_for(state="hidden")
    harness.current_state.publish()
    await playwright.async_api.expect(page.locator("#entry-list .entry-select").first).to_be_focused()


@pytest.mark.asyncio
async def test_user_filter_announcement_survives_same_state_sse_repo_request(
    browser_harness: _BrowserHarness,
) -> None:
    """同一状態の後発SSE候補要求後も、利用者の件数通知を完了する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#result-status")).to_have_text("")
    await playwright.async_api.expect(page.locator('#target-filter option[value="example/repo"]')).to_have_count(1)
    await _open_filters(page)
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()
    request_count = 0

    async def delay_first_repo_request(route: playwright.async_api.Route) -> None:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            first_started.set()
            await release_first.wait()
        else:
            second_started.set()
        await route.continue_()

    await page.route("**/api/repos?status=active", delay_first_repo_request)
    await page.locator("#result-status").evaluate("element => { element.textContent = '変更前の通知'; }")
    await page.locator("#kind-filter").evaluate("element => { element.value = 'awi'; }")
    # 状態フィルターの変更経路を起動し、対象リポジトリの再取得を伴う一覧の更新を実行する。
    await page.locator("#state-filter").dispatch_event("change")
    await asyncio.wait_for(first_started.wait(), timeout=5)
    try:
        harness.current_state.publish()
        await playwright.async_api.expect(page.locator("#entry-list .entry-select[data-kind=awi]")).to_have_count(2)
    finally:
        release_first.set()
    await playwright.async_api.expect(page.locator("#result-status")).to_have_text("2件を表示")
    await asyncio.wait_for(second_started.wait(), timeout=5)
    assert request_count == 2


@pytest.mark.asyncio
async def test_answer_and_delete_target_the_visible_state(browser_harness: _BrowserHarness) -> None:
    """同名項目が両状態にあっても、回答と削除は表示中の複合キーへ作用する。"""
    harness = browser_harness
    page = harness.page
    processing = harness.root / "processing"
    processing.mkdir(exist_ok=True)
    marker = "<!-- ユーザーはこの行以降に回答を追記する -->"
    uwi_content = (
        "---\ntype: uwi\ntarget_repo: example/repo\nquestion_type: free-form\n---\n\n"
        f"## 質問\n\n状態を指定しますか？\n\n## 回答\n\n{marker}\n"
    )
    awi_content = "---\ntype: awi\ntarget_repo: example/repo\n---\n\n状態付き削除\n"
    for state_name in ("inbox", "processing"):
        (harness.root / state_name / "answer-same.md").write_text(uwi_content, encoding="utf-8")
        (harness.root / state_name / "remove-same.md").write_text(awi_content, encoding="utf-8")
    harness.operations.enable_file_mutations()
    await page.goto(harness.base_url + "/")

    answer_row = page.locator('.entry-select[data-key="inbox/answer-same.md"]')
    await answer_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="回答", exact=True).click()
    await detail.locator("#answer-input").fill("未処理側だけへの回答")
    async with page.expect_request("**/api/entries/answer") as answer_request_info:
        await detail.get_by_role("button", name="回答を保存").click()
    answer_request = await answer_request_info.value
    answer_payload = answer_request.post_data_json
    assert isinstance(answer_payload, dict)
    assert answer_payload["state"] == "inbox"
    await page.get_by_role("status").filter(has_text="回答しました").wait_for(state="visible")
    assert (harness.root / "inbox/answer-same.md").read_text(encoding="utf-8").endswith("未処理側だけへの回答\n")
    assert (processing / "answer-same.md").read_text(encoding="utf-8") == uwi_content

    await page.keyboard.press("Escape")
    remove_row = page.locator('.entry-select[data-key="inbox/remove-same.md"]')
    await remove_row.click()
    await detail.get_by_role("button", name="削除").click()
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    async with page.expect_request("**/api/entries/remove") as remove_request_info:
        await delete_dialog.get_by_role("button", name="削除する").click()
    remove_request = await remove_request_info.value
    remove_payload = remove_request.post_data_json
    assert isinstance(remove_payload, dict)
    assert remove_payload["state"] == "inbox"
    assert remove_payload["expected_content"] == awi_content
    await delete_dialog.wait_for(state="hidden")
    assert not (harness.root / "inbox/remove-same.md").exists()
    assert (processing / "remove-same.md").read_text(encoding="utf-8") == awi_content


@pytest.mark.asyncio
async def test_external_update_recovery_survives_save_and_answer_failures(
    browser_harness: _BrowserHarness,
) -> None:
    """自書込み相当のSSE後に更新APIが失敗しても、入力と復旧手順を維持する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    detail = page.get_by_role("dialog", name="詳細")

    async def exercise(
        row_key: str,
        mutation_pattern: str,
        submit_name: str,
        input_selector: str,
        updated_content: str,
    ) -> None:
        await page.locator(f'.entry-select[data-key="{row_key}"]').click()
        mode_name = "回答" if input_selector == "#answer-input" else "編集"
        await detail.get_by_role("button", name=mode_name, exact=True).click()
        field = detail.locator(input_selector)
        user_input = "保持する回答" if input_selector == "#answer-input" else "保持する編集本文"
        await field.fill(user_input)
        started = asyncio.Event()
        release = asyncio.Event()

        async def fail_mutation(route: playwright.async_api.Route) -> None:
            if route.request.method not in {"POST", "PUT"}:
                await route.continue_()
                return
            started.set()
            await release.wait()
            await route.fulfill(
                status=500,
                content_type="application/json",
                body='{"error":"Git同期に失敗しました"}',
            )

        await page.route(mutation_pattern, fail_mutation)
        await detail.get_by_role("button", name=submit_name, exact=True).click()
        await asyncio.wait_for(started.wait(), timeout=5)
        state_name, filename = row_key.split("/", maxsplit=1)
        (harness.root / state_name / filename).write_text(updated_content, encoding="utf-8")
        harness.current_state.publish()
        await detail.get_by_role("alert").filter(has_text="外部で項目が更新されました").wait_for(state="visible")
        release.set()
        await detail.get_by_role("alert").filter(has_text="Git同期に失敗しました").wait_for(state="visible")
        assert "詳細を閉じて開き直してから保存してください" in await detail.locator("#detail-alert").inner_text()
        await playwright.async_api.expect(page.locator("#operation-notice")).to_be_hidden()
        assert await field.input_value() == user_input
        await playwright.async_api.expect(detail.get_by_role("button", name=submit_name, exact=True)).to_be_disabled()
        await page.unroute(mutation_pattern, fail_mutation)
        async with page.expect_event("dialog") as confirmation:
            press = asyncio.create_task(page.keyboard.press("Escape"))
        await (await confirmation.value).accept()
        await press

    await exercise(
        "inbox/awi.md",
        "**/api/entries/inbox/awi.md",
        "保存",
        "#edit-content",
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n保存中の外部更新\n",
    )
    await exercise(
        "inbox/question.md",
        "**/api/entries/answer",
        "回答を保存",
        "#answer-input",
        "---\ntype: uwi\ntarget_repo: example/repo\nquestion_type: free-form\n---\n\n"
        "## 質問\n\n回答中の外部更新ですか？\n\n## 回答\n\n"
        "<!-- ユーザーはこの行以降に回答を追記する -->\n",
    )


@pytest.mark.asyncio
async def test_delete_confirmation_closes_on_detail_failure_and_content_conflict(
    browser_harness: _BrowserHarness,
) -> None:
    """一覧後の詳細失敗と確認後の内容競合では、古い削除確認を閉じる。"""
    harness = browser_harness
    page = harness.page
    harness.operations.enable_file_mutations()
    failure_path = harness.root / "inbox/dialog-failure.md"
    conflict_path = harness.root / "inbox/delete-conflict.md"
    original = "---\ntype: awi\ntarget_repo: example/old\n---\n\n変更前の要約\n"
    failure_path.write_text(original, encoding="utf-8")
    conflict_path.write_text(original, encoding="utf-8")
    await page.goto(harness.base_url + "/")
    detail = page.get_by_role("dialog", name="詳細")
    delete_dialog = page.get_by_role("dialog", name="削除の確認")

    await page.locator('.entry-select[data-key="inbox/dialog-failure.md"]').click()
    await detail.get_by_role("button", name="削除").click()

    async def fail_detail(route: playwright.async_api.Route) -> None:
        await route.fulfill(
            status=500,
            content_type="application/json",
            body='{"error":"詳細取得に失敗しました"}',
        )

    await page.route("**/api/entries/inbox/dialog-failure.md", fail_detail)
    failure_path.write_text(
        "---\ntype: awi\ntarget_repo: example/new\n---\n\n変更後の要約\n",
        encoding="utf-8",
    )
    harness.current_state.publish()
    await delete_dialog.wait_for(state="hidden")
    await detail.get_by_role("alert").filter(has_text="詳細取得に失敗しました").wait_for(state="visible")
    assert "削除操作をやり直してください" in await detail.locator("#detail-alert").inner_text()
    await playwright.async_api.expect(detail.locator("#detail-dialog-body")).to_be_focused()
    await page.unroute("**/api/entries/inbox/dialog-failure.md", fail_detail)
    await page.keyboard.press("Escape")

    await page.locator('.entry-select[data-key="inbox/delete-conflict.md"]').click()
    await detail.get_by_role("button", name="削除").click()
    conflict_path.write_text(original.replace("変更前", "確認後の外部更新"), encoding="utf-8")
    await delete_dialog.get_by_role("button", name="削除する").click()
    await delete_dialog.wait_for(state="hidden")
    await detail.get_by_role("alert").filter(has_text="削除できませんでした").wait_for(state="visible")
    assert "詳細を閉じて開き直してから削除してください" in await detail.locator("#detail-alert").inner_text()
    await playwright.async_api.expect(detail.locator("#detail-dialog-body")).to_be_focused()
    await playwright.async_api.expect(detail.get_by_role("button", name="削除")).to_be_disabled()
    await playwright.async_api.expect(detail.get_by_role("button", name="編集")).to_be_disabled()
    assert conflict_path.exists()


@pytest.mark.asyncio
async def test_create_dialog_supports_batch_import_and_omitted_target_repo(
    browser_harness: _BrowserHarness,
) -> None:
    """一括登録種別の入力切替と取り込み、frontmatter指定による対象リポジトリ省略投入を検証する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")

    await page.get_by_role("button", name="新規追加").click()
    create_dialog = page.get_by_role("dialog", name="新規追加")
    await create_dialog.wait_for(state="visible")
    await create_dialog.locator("#create-kind").select_option("batch")
    await playwright.async_api.expect(create_dialog.locator("#create-repo-fields")).to_be_hidden()
    await playwright.async_api.expect(create_dialog.locator("#create-content-label")).to_have_text("show形式テキスト（必須）")
    batch_text = (
        "# awi\n## target_repo: batch/repo\n"
        "### imported.md [inbox]\n---\ntarget_repo: batch/repo\ntype: awi\n---\n\n一括取り込みの本文\n\n"
    )
    await create_dialog.locator("#create-content").fill(batch_text)
    async with page.expect_request("**/api/entries/batch") as batch_request_info:
        await create_dialog.get_by_role("button", name="追加").click()
    batch_request = await batch_request_info.value
    assert batch_request.post_data_json == {"text": batch_text}
    await create_dialog.wait_for(state="hidden")
    await page.get_by_role("status").filter(has_text="1件を取り込みました").wait_for(state="visible")
    await page.locator('.entry-select[data-key="inbox/imported.md"]').wait_for(state="visible")
    assert (harness.root / "inbox" / "imported.md").read_text(encoding="utf-8") == (
        "---\ntarget_repo: batch/repo\ntype: awi\n---\n\n一括取り込みの本文\n"
    )

    await page.get_by_role("button", name="新規追加").click()
    await create_dialog.wait_for(state="visible")
    await playwright.async_api.expect(create_dialog.locator("#create-repo-fields")).to_be_visible()
    await playwright.async_api.expect(create_dialog.locator("#create-target")).to_have_value("")
    await create_dialog.locator("#create-content").fill("---\ntarget_repo: frontmatter/repo\n---\n\nfrontmatter指定の本文")
    async with page.expect_request(lambda request: request.url.endswith("/api/entries") and request.method == "POST"):
        await create_dialog.get_by_role("button", name="追加").click()
    await create_dialog.wait_for(state="hidden")
    assert harness.operations.add_calls[-1]["target_repo"] is None
    detail = page.get_by_role("dialog", name="詳細")
    await playwright.async_api.expect(detail).to_be_hidden()
    await page.locator("#entry-list .entry-select").filter(has_text="frontmatter指定の本文").wait_for(state="visible")


@pytest.mark.asyncio
async def test_create_failure_is_visible_inside_open_dialog(browser_harness: _BrowserHarness) -> None:
    """新規追加の失敗は開いたダイアログ内へ表示し、ページ通知を表示しない。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")

    async def fail_create(route: playwright.async_api.Route) -> None:
        await route.fulfill(status=500, json={"error": "追加処理に失敗しました"})

    await page.route("**/api/entries", fail_create)
    await page.get_by_role("button", name="新規追加").click()
    create_dialog = page.get_by_role("dialog", name="新規追加")
    await create_dialog.locator("#create-content").fill("新規本文")
    await create_dialog.locator("#create-target").fill("example/repo")
    await create_dialog.get_by_role("button", name="追加").click()

    await playwright.async_api.expect(create_dialog.locator("#create-alert")).to_contain_text("追加処理に失敗しました")
    await playwright.async_api.expect(create_dialog.locator("#create-alert")).to_be_visible()
    await playwright.async_api.expect(page.locator("#operation-notice")).to_be_hidden()
    await page.unroute("**/api/entries", fail_create)


@pytest.mark.asyncio
async def test_save_failure_message_stays_at_scrolled_dialog_top(browser_harness: _BrowserHarness) -> None:
    """本文末尾までスクロールした保存失敗でも結果をダイアログ本文の上端へ留める。"""
    harness = browser_harness
    path = harness.root / "inbox" / "long-entry.md"
    path.write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n" + "長い本文\n\n" * 120,
        encoding="utf-8",
    )
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator('.entry-select[data-key="inbox/long-entry.md"]').click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="編集", exact=True).click()
    body = detail.locator(".dialog-body")
    await body.evaluate("element => { element.scrollTop = element.scrollHeight; }")

    async def fail_save(route: playwright.async_api.Route) -> None:
        await route.fulfill(status=500, json={"error": "保存処理に失敗しました"})

    await page.route("**/api/entries/inbox/long-entry.md", fail_save)
    await detail.get_by_role("button", name="保存", exact=True).click()
    alert = detail.locator("#detail-alert")
    await playwright.async_api.expect(alert).to_contain_text("保存処理に失敗しました")
    await playwright.async_api.expect(detail.locator("#edit-content")).to_be_focused()
    metrics = await body.evaluate(
        """element => {
          const bodyRect = element.getBoundingClientRect();
          const alertRect = document.getElementById('detail-alert').getBoundingClientRect();
          return {
            bodyTop: bodyRect.top,
            bodyBottom: bodyRect.bottom,
            alertTop: alertRect.top,
            alertBottom: alertRect.bottom,
            paddingTop: Number.parseFloat(getComputedStyle(element).paddingTop)
          };
        }"""
    )
    assert metrics["bodyTop"] <= metrics["alertTop"] <= metrics["bodyTop"] + metrics["paddingTop"] + 1
    assert metrics["alertBottom"] <= metrics["bodyBottom"]
    await playwright.async_api.expect(page.locator("#operation-notice")).to_be_hidden()
    await page.unroute("**/api/entries/inbox/long-entry.md", fail_save)


async def _release_save_after_delay(release: threading.Event) -> None:
    """一定時間の経過後に保存処理を解放する。

    保存が即座に完了する場合、前段の通知が残存したまま待機が成立する欠陥があっても、
    本文の読み取りは保存の完了後に到達し、テストは通過する。解放を遅延させると、
    保存の完了を待たない待機条件は実行速度によらず失敗として現れる。
    """
    await asyncio.sleep(_SAVE_RELEASE_DELAY_SEC)
    release.set()


async def _wait_and_close_operation_notice(page: playwright.async_api.Page, text: str) -> None:
    """操作の通知が表示されるまで待ち、成立した通知を閉じる。

    通知は自動で消えないため、閉じずに次の待機へ進むと残存した通知で待機が即座に成立する。
    待機のたびに閉じることで、次の待機が当該操作で新たに表示された通知だけで成立する。
    """
    await page.get_by_role("status").filter(has_text=text).wait_for(state="visible")
    await page.locator("#operation-notice-close-button").click()
    await playwright.async_api.expect(page.locator("#operation-notice")).to_be_hidden()


@pytest.mark.asyncio
async def test_user_comment_ui_appends_replaces_and_recovers_from_external_updates(
    browser_harness: _BrowserHarness,
) -> None:
    """エージェント由来UIの追記・置換・pending・SSE・競合復旧を実ブラウザーで検証する。"""
    harness = browser_harness
    page = harness.page
    path = harness.root / "inbox" / "awi.md"
    original = "---\ntype: awi\ntarget_repo: example/repo\nsource: session-review\n---\n\n通常本文\n"
    path.write_text(original, encoding="utf-8")
    await page.goto(harness.base_url + "/")
    detail = page.get_by_role("dialog", name="詳細")

    await page.locator('.entry-select[data-key="inbox/empty.md"]').click()
    await playwright.async_api.expect(detail).to_be_visible()
    await playwright.async_api.expect(detail.locator("#user-comment-button")).to_be_visible()
    await page.keyboard.press("Escape")
    await playwright.async_api.expect(detail).to_be_hidden()

    await page.locator('.entry-select[data-key="inbox/awi.md"]').click()
    comment_button = detail.get_by_role("button", name="ユーザーコメント", exact=True)
    await playwright.async_api.expect(comment_button).to_be_visible()
    await comment_button.click()
    comment_input = detail.locator("#user-comment-input")
    await playwright.async_api.expect(comment_input).to_be_focused()
    await playwright.async_api.expect(comment_input).to_have_value("")
    await page.set_viewport_size({"width": 390, "height": 700})
    input_box = await comment_input.bounding_box()
    assert input_box is not None
    assert input_box["x"] >= 0
    assert input_box["x"] + input_box["width"] <= 390

    await comment_input.fill("最初のコメント")
    async with page.expect_request("**/api/entries/user-comment") as first_request_info:
        await detail.get_by_role("button", name="コメントを保存").click()
    first_payload = (await first_request_info.value).post_data_json
    assert first_payload == {
        "state": "inbox",
        "filename": "awi.md",
        "comment": "最初のコメント",
        "expected_content": original,
    }
    await _wait_and_close_operation_notice(page, "ユーザーコメントを保存しました")
    assert "## ユーザーコメント\n\n最初のコメント" in path.read_text(encoding="utf-8")
    await playwright.async_api.expect(detail).to_be_hidden()

    await page.locator('.entry-select[data-key="inbox/awi.md"]').click()
    await detail.wait_for(state="visible")
    comment_button = detail.get_by_role("button", name="ユーザーコメント", exact=True)
    await comment_button.click()
    await playwright.async_api.expect(comment_input).to_have_value("最初のコメント")
    await comment_input.fill("置換後のコメント")
    harness.operations.arm_user_comment_delay()
    await page.evaluate(
        "document.getElementById('save-user-comment-button').click(); "
        "document.getElementById('save-user-comment-button').click()"
    )
    assert await asyncio.to_thread(harness.operations.user_comment_started.wait, 5)
    await playwright.async_api.expect(comment_input).to_be_disabled()
    await playwright.async_api.expect(detail.locator("#save-user-comment-button")).to_be_disabled()
    assert harness.operations.user_comment_calls == 2
    release_task = asyncio.create_task(_release_save_after_delay(harness.operations.user_comment_release))
    await _wait_and_close_operation_notice(page, "ユーザーコメントを保存しました")
    await release_task
    await playwright.async_api.expect(detail).to_be_hidden()
    saved = path.read_text(encoding="utf-8")
    assert "置換後のコメント" in saved
    assert "最初のコメント" not in saved

    await page.locator('.entry-select[data-key="inbox/awi.md"]').click()
    await detail.wait_for(state="visible")
    comment_button = detail.get_by_role("button", name="ユーザーコメント", exact=True)
    await comment_button.click()
    await comment_input.fill("SSE中も保持する入力")
    path.write_text(saved.replace("通常本文", "SSE外部更新本文"), encoding="utf-8")
    harness.current_state.publish()
    await detail.get_by_role("alert").filter(has_text="最新内容を再取得しました").wait_for(state="visible")
    await playwright.async_api.expect(comment_input).to_have_value("SSE中も保持する入力")
    await playwright.async_api.expect(comment_input).to_be_focused()
    await playwright.async_api.expect(detail.locator("#save-user-comment-button")).to_be_enabled()

    latest = path.read_text(encoding="utf-8").replace("SSE外部更新本文", "競合後の最新本文")
    path.write_text(latest, encoding="utf-8")
    await comment_input.fill("競合後も保持する入力")
    await detail.get_by_role("button", name="コメントを保存").click()
    await detail.get_by_role("alert").filter(has_text="内容を確認して再度保存してください").wait_for(state="visible")
    await playwright.async_api.expect(comment_input).to_have_value("競合後も保持する入力")
    await playwright.async_api.expect(comment_input).to_be_focused()
    harness.operations.arm_user_comment_delay()
    async with page.expect_request("**/api/entries/user-comment") as retry_request_info:
        await detail.get_by_role("button", name="コメントを保存").click()
    retry_payload = (await retry_request_info.value).post_data_json
    assert isinstance(retry_payload, dict)
    assert retry_payload["expected_content"] == latest
    assert await asyncio.to_thread(harness.operations.user_comment_started.wait, 5)
    release_task = asyncio.create_task(_release_save_after_delay(harness.operations.user_comment_release))
    await _wait_and_close_operation_notice(page, "ユーザーコメントを保存しました")
    await release_task
    await playwright.async_api.expect(detail).to_be_hidden()
    assert "競合後も保持する入力" in path.read_text(encoding="utf-8")
    assert harness.operations.user_comment_calls == 4


@pytest.mark.asyncio
async def test_user_comment_ui_keeps_input_when_sse_moves_entry_to_processing(
    browser_harness: _BrowserHarness,
) -> None:
    """processingへのSSE移動後も入力へ到達でき、保存だけを無効にする。"""
    harness = browser_harness
    page = harness.page
    path = harness.root / "inbox" / "awi.md"
    path.write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: session-review\n---\n\n通常本文\n",
        encoding="utf-8",
    )
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    detail = page.get_by_role("dialog", name="詳細")
    await page.locator('.entry-select[data-key="inbox/awi.md"]').click()
    await detail.get_by_role("button", name="ユーザーコメント", exact=True).click()
    comment_input = detail.locator("#user-comment-input")
    await comment_input.fill("processing移動後も保持する入力")

    processing = harness.root / "processing"
    processing.mkdir(exist_ok=True)
    path.replace(processing / path.name)
    harness.current_state.publish()

    await detail.get_by_role("alert").filter(has_text="processingへ移動したため").wait_for(state="visible")
    await playwright.async_api.expect(detail).to_be_visible()
    await playwright.async_api.expect(comment_input).to_have_value("processing移動後も保持する入力")
    await playwright.async_api.expect(comment_input).to_be_focused()
    await playwright.async_api.expect(detail.locator("#save-user-comment-button")).to_be_disabled()


@pytest.mark.asyncio
@pytest.mark.parametrize("external_change", ["delete", "move"])
async def test_selecting_deleted_entry_refreshes_list_without_error(
    browser_harness: _BrowserHarness,
    external_change: str,
) -> None:
    """一覧表示後に外部で削除・移動された行を選んでも、エラーを表示せず最新状態を示して一覧を更新する。"""
    harness = browser_harness
    page = harness.page
    path = harness.root / "inbox" / "stale.md"
    path.write_text("---\ntype: awi\ntarget_repo: example/repo\n---\n\n外部で変わる本文\n", encoding="utf-8")
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    stale_row = page.locator('.entry-select[data-key="inbox/stale.md"]')
    await stale_row.wait_for(state="visible")

    # SSEの変更通知を発行せず、一覧が古いままの状態で行を選ぶ。
    if external_change == "delete":
        path.unlink()
    else:
        processing = harness.root / "processing"
        processing.mkdir(exist_ok=True)
        path.replace(processing / path.name)
    await stale_row.click()

    notice = page.locator("#operation-notice")
    detail = page.get_by_role("dialog", name="詳細")
    if external_change == "delete":
        await playwright.async_api.expect(page.locator("#operation-notice-message")).to_have_text(
            "stale.mdは削除されたため表示できません。一覧を更新しました。"
        )
        await playwright.async_api.expect(notice).to_have_attribute("data-error", "false")
        await playwright.async_api.expect(detail).to_be_hidden()
        await playwright.async_api.expect(page.locator('.entry-select[data-key$="/stale.md"]')).to_have_count(0)
    else:
        await playwright.async_api.expect(detail).to_be_visible()
        await playwright.async_api.expect(detail.locator("#detail-state")).to_have_attribute("data-state", "processing")
        await playwright.async_api.expect(notice).to_be_hidden()
        await playwright.async_api.expect(page.locator('.entry-select[data-key="inbox/stale.md"]')).to_have_count(0)


# --------------------------------------------------------------------------------------
# 計画ファイル画面とセッション画面
# --------------------------------------------------------------------------------------


def _valid_diagram_markdown(label: str) -> str:
    """MermaidとSVGの両方を含む計画本文を組み立てる。"""
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


@dataclasses.dataclass
class _ScreenHarness:
    page: playwright.async_api.Page
    context: playwright.async_api.BrowserContext
    root: Path
    plan_path: Path
    plans_state: serve_plans.BroadcastState
    base_url: str
    requests: list[str]
    responses: list[tuple[str, int]]

    async def notify_file_update(self, markdown: str) -> None:
        """計画ファイルを書き換え、更新通知を配信する。"""
        for _ in range(100):
            if self.plans_state.subscribers:
                break
            await asyncio.sleep(0.01)
        assert self.plans_state.subscribers
        if self.plans_state.debounce_task is not None:
            await self.plans_state.debounce_task
        previous_mtime_ns = self.plan_path.stat().st_mtime_ns
        self.plan_path.write_text(markdown, encoding="utf-8")
        updated = self.plan_path.stat()
        os.utime(self.plan_path, ns=(updated.st_atime_ns, max(updated.st_mtime_ns, previous_mtime_ns + 1_000_000_000)))
        await serve_plans.schedule_broadcast(self.plans_state)
        if self.plans_state.debounce_task is not None:
            await self.plans_state.debounce_task


@dataclasses.dataclass
class _RemoteSessionsHarness:
    page: playwright.async_api.Page
    context: playwright.async_api.BrowserContext
    base_url: str


@dataclasses.dataclass
class _MultiRootHarness:
    page: playwright.async_api.Page
    context: playwright.async_api.BrowserContext
    new_plan: Path
    legacy_plan: Path
    plans_state: serve_plans.BroadcastState
    base_url: str


def _isolate_creation_time_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """作成日時インデックスを一時ディレクトリへ隔離し、開発環境の索引を書き換えない。"""
    monkeypatch.setattr(serve_plans, "_CREATION_TIME_INDEX_PATH", tmp_path / "cache" / "index.json")


@contextlib.asynccontextmanager
async def _serve(
    app: Any,
    browser: playwright.async_api.Browser,
) -> AsyncGenerator[tuple[playwright.async_api.BrowserContext, playwright.async_api.Page, int]]:
    """テスト用サーバーを起動し、ページとブラウザーコンテキストを提供する。"""
    port = _reserve_port()
    shutdown = asyncio.Event()

    async def shutdown_trigger() -> None:
        await shutdown.wait()

    server_task = asyncio.create_task(app.run_task("127.0.0.1", port, shutdown_trigger=shutdown_trigger))
    context = None
    try:
        await _wait_for_server(port)
        context = await browser.new_context()
        await context.grant_permissions(["clipboard-read", "clipboard-write"], origin=f"http://127.0.0.1:{port}")
        page = await context.new_page()
        yield context, page, port
    finally:
        if context is not None:
            await context.close()
        shutdown.set()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task


@pytest_asyncio.fixture(name="screen_harness")
async def _screen_harness_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    browser: playwright.async_api.Browser,
) -> AsyncGenerator[_ScreenHarness]:
    """3画面を登録したアプリと、計画ファイル1件・両実行系のセッション記録を用意する。"""
    _isolate_creation_time_index(tmp_path, monkeypatch)
    _write_entries(tmp_path)
    plans_root = tmp_path / "plans"
    plans_root.mkdir()
    plan_path = plans_root / "plan.md"
    plan_path.write_text(_valid_diagram_markdown("初回"), encoding="utf-8")
    _write_session_records(tmp_path)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        serve_state.ServeState(tmp_path),
        operations=_BrowserOperations(tmp_path),
        plans_context=serve_plans.create_context(root=plans_root, hostname="browser-test"),
        sessions_context=serve_sessions.create_context(
            hostname="browser-test",
            claude_home=tmp_path / "claude",
            codex_home=tmp_path / "codex",
        ),
    )
    plans_state: serve_plans.BroadcastState = app.config["PLANS_CONTEXT"].state
    async with _serve(app, browser) as (context, page, port):
        requests: list[str] = []
        responses: list[tuple[str, int]] = []
        page.on("request", lambda request: requests.append(request.url))
        page.on("response", lambda response: responses.append((response.url, response.status)))
        yield _ScreenHarness(
            page=page,
            context=context,
            root=plans_root,
            plan_path=plan_path,
            plans_state=plans_state,
            base_url=f"http://127.0.0.1:{port}",
            requests=requests,
            responses=responses,
        )


_NEW_REMOTE_HOST = "new-host"
_LEGACY_REMOTE_HOST = "legacy-host"
_REMOTE_RECORD_PATH = "/home/remote/.claude/projects/-home-remote-proj/33333333-4444-5555-6666-777777777777.jsonl"


async def _remote_sessions_runner(host: str, op: str, _args: list[str]) -> str:
    """リモートヘルパーの応答を模す。旧版のホストは`read`応答へサブエージェント一覧を含めない。

    `read`の対象は1件だけであり、渡されたパスを見分ける必要が無いため引数を使わない。
    """
    if op == "list":
        return json.dumps(
            {
                "ok": True,
                "host": host,
                "entries": [
                    {
                        "engine": "claude",
                        "cwd": "/home/remote/proj",
                        "first_user_message": "リモートの最初の発話",
                        "session_id": f"{host}-session",
                        "path": _REMOTE_RECORD_PATH,
                        "updated_at": 1_800_000_000,
                        "size": 120,
                    }
                ],
            },
            ensure_ascii=False,
        )
    text = (
        json.dumps(
            {"type": "user", "timestamp": "2026-09-01T00:00:00Z", "message": {"content": f"{host}の発話"}},
            ensure_ascii=False,
        )
        + "\n"
    )
    payload: dict[str, Any] = {"ok": True, "data": base64.b64encode(text.encode("utf-8")).decode("ascii")}
    if host == _NEW_REMOTE_HOST:
        payload["subagents"] = [
            {
                "agent_id": "agent-remote",
                "agent_type": "Explore",
                "description": "リモートの調査",
                "spawn_depth": 1,
                "parent_agent_id": None,
                "model": "opus",
                "path": None,
            }
        ]
    return json.dumps(payload, ensure_ascii=False)


@pytest_asyncio.fixture(name="remote_sessions_harness")
async def _remote_sessions_harness_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    browser: playwright.async_api.Browser,
) -> AsyncGenerator[_RemoteSessionsHarness]:
    """サブエージェント一覧を返すリモートホストと、返さない旧版のリモートホストを登録した画面を用意する。"""
    _isolate_creation_time_index(tmp_path, monkeypatch)
    _write_entries(tmp_path)
    plans_root = tmp_path / "plans"
    plans_root.mkdir()
    (plans_root / "plan.md").write_text(_valid_diagram_markdown("初回"), encoding="utf-8")
    # 常駐接続は実際のsshを起動するため開始させず、単発SSHの差し替えだけでリモートの応答を与える。
    monkeypatch.setattr(serve_sessions, "start_remote_clients", lambda context: None)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        serve_state.ServeState(tmp_path),
        operations=_BrowserOperations(tmp_path),
        plans_context=serve_plans.create_context(root=plans_root, hostname="browser-test"),
        sessions_context=serve_sessions.create_context(
            hostname="browser-test",
            claude_home=tmp_path / "claude",
            codex_home=tmp_path / "codex",
            remote_hosts=[_NEW_REMOTE_HOST, _LEGACY_REMOTE_HOST],
            ssh_runner=_remote_sessions_runner,
        ),
    )
    async with _serve(app, browser) as (context, page, port):
        yield _RemoteSessionsHarness(page=page, context=context, base_url=f"http://127.0.0.1:{port}")


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


def _write_session_records(root: Path) -> None:
    """Claude CodeとCodexのセッション記録を1件ずつ作成する。"""
    claude_path = root / "claude" / "projects" / "-home-aki-proj" / "11111111-2222-3333-4444-555555555555.jsonl"
    claude_path.parent.mkdir(parents=True, exist_ok=True)
    claude_path.write_text(
        json.dumps(
            {
                "type": "user",
                "timestamp": "2026-09-01T00:00:00Z",
                "cwd": "/home/aki/proj",
                "message": {"content": "Claudeの発話"},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-09-01T00:00:01Z",
                "message": {
                    "usage": {"input_tokens": 12, "output_tokens": 3},
                    "content": [
                        {"type": "thinking", "thinking": "Claudeの思考"},
                        {"type": "tool_use", "name": "Bash", "input": {"command": f"ls {'x' * 160}\npwd"}},
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/input.md"}},
                        {"type": "tool_use", "name": "Search", "input": {"pattern": "needle", "count": 1}},
                    ],
                },
            },
            ensure_ascii=False,
        )
        + "\n"
        # 書き込み途中の行が混在しても他の行を失わせないことを画面側でも確認する。
        + '{"type":"user","message":{"content":"途中で途絶\n',
        encoding="utf-8",
    )
    # 記録本体を持つサブエージェントと、meta情報だけが残るより深いサブエージェントを1件ずつ用意する。
    subagents = claude_path.with_suffix("") / "subagents"
    subagents.mkdir(parents=True, exist_ok=True)
    (subagents / "agent-first.meta.json").write_text(
        json.dumps({"agentType": "Explore", "description": "所在の調査", "spawnDepth": 1}, ensure_ascii=False),
        encoding="utf-8",
    )
    (subagents / "agent-first.jsonl").write_text(
        json.dumps(
            {"type": "user", "timestamp": "2026-09-01T00:10:00Z", "message": {"content": "サブエージェントの発話"}},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (subagents / "agent-second.meta.json").write_text(
        json.dumps({"agentType": "Plan", "description": "設計の検討", "spawnDepth": 2}, ensure_ascii=False),
        encoding="utf-8",
    )
    codex_path = (
        root
        / "codex"
        / "sessions"
        / "2026"
        / "09"
        / "01"
        / "rollout-2026-09-01T00-00-00-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
    )
    codex_path.parent.mkdir(parents=True, exist_ok=True)
    codex_path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "timestamp": "2026-09-01T01:00:00Z",
                "payload": {"cwd": "/home/aki/other", "timestamp": "2026-09-01T01:00:00Z"},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "timestamp": "2026-09-01T01:00:01Z",
                "payload": {"type": "message", "role": "user", "content": [{"text": "Codexの発話"}]},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "timestamp": "2026-09-01T01:00:02Z",
                "payload": {"type": "message", "role": "developer", "content": [{"text": "Codexの開発者指示"}]},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "timestamp": "2026-09-01T01:00:03Z",
                "payload": {"type": "message", "role": "assistant", "content": [{"text": "Codexの応答"}]},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "timestamp": "2026-09-01T01:00:04Z",
                "payload": {"type": "reasoning", "summary": [{"text": "Codexの思考"}]},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "timestamp": "2026-09-01T01:00:05Z",
                "payload": {"type": "function_call_output", "output": "Codexのツール結果"},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "compacted",
                "timestamp": "2026-09-01T01:00:06Z",
                "payload": {"message": "コンテキストを圧縮しました", "window_number": 2},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "timestamp": "2026-09-01T01:00:07Z",
                "payload": {"type": "function_call", "name": "shell", "arguments": '{"cmd":"pwd"}'},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "timestamp": "2026-09-01T01:00:08Z",
                "payload": {"type": "function_call", "name": "broken", "arguments": "{invalid-json"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_navigation_switches_three_screens_in_declared_order(screen_harness: _ScreenHarness) -> None:
    """同一のベースパスから3画面へ遷移でき、ナビゲーションの表示順と表記が指定どおりである。"""
    harness = screen_harness
    await harness.page.goto(harness.base_url + "/")

    navigation = harness.page.locator(".screen:not([hidden]) nav.app-nav")
    await navigation.wait_for(state="visible")
    assert await navigation.locator("a").all_inner_texts() == ["ワークアイテム", "計画ファイル", "セッション"]
    assert await navigation.locator('a[aria-current="page"]').inner_text() == "ワークアイテム"

    await navigation.get_by_role("link", name="計画ファイル").click()
    await harness.page.locator("#preview h1", has_text="初回").wait_for(state="visible")
    assert harness.page.url == harness.base_url + "/plans"
    assert await harness.page.locator('.screen:not([hidden]) nav.app-nav a[aria-current="page"]').inner_text() == "計画ファイル"

    await harness.page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
    await harness.page.locator("#sessions .session-item").first.wait_for(state="visible")
    assert harness.page.url == harness.base_url + "/sessions"
    assert await harness.page.locator('.screen:not([hidden]) nav.app-nav a[aria-current="page"]').inner_text() == "セッション"

    await harness.page.locator("nav.app-nav").get_by_role("link", name="ワークアイテム").click()
    await harness.page.locator("#entry-list").wait_for(state="visible")
    assert harness.page.url == harness.base_url + "/"


@pytest.mark.asyncio
async def test_navigation_does_not_reload_document(screen_harness: _ScreenHarness) -> None:
    """3画面を順に移動してもドキュメントを再読み込みせず、URLだけが切り替わる。"""
    harness = screen_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list").wait_for(state="visible")
    # 再読み込みが起きると失われる値を置き、遷移後も同じドキュメントが生きていることを判定する。
    await page.evaluate("() => { window.__atkReloadMarker = 'kept'; }")

    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    await page.locator("#preview h1", has_text="初回").wait_for(state="visible")
    assert await page.title() == "計画ファイル - atk serve"
    assert await page.evaluate("() => window.__atkReloadMarker") == "kept"
    assert await page.evaluate("() => location.pathname") == "/plans"

    await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
    await page.locator("#sessions .session-item").first.wait_for(state="visible")
    assert await page.evaluate("() => window.__atkReloadMarker") == "kept"
    assert await page.evaluate("() => location.pathname") == "/sessions"


@pytest.mark.asyncio
async def test_navigation_uses_one_html_document_across_all_screens(screen_harness: _ScreenHarness) -> None:
    """起動時のHTMLだけを使い、3画面の切り替えで文書を追加取得しない。"""
    harness = screen_harness
    page = harness.page
    screen_urls = [harness.base_url + path for path in ("/", "/plans", "/sessions")]
    await page.goto(screen_urls[0])
    await page.locator("#entry-list").wait_for(state="visible")
    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    await page.locator("#preview h1", has_text="初回").wait_for(state="visible")
    await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
    await page.locator("#sessions .session-item").first.wait_for(state="visible")

    assert [harness.requests.count(url) for url in screen_urls] == [1, 0, 0]


@pytest.mark.asyncio
async def test_navigation_preserves_filters_and_screen_styles(screen_harness: _ScreenHarness) -> None:
    """3画面の入力値とワークアイテム画面の算出フォントサイズを往復後も保持する。"""
    harness = screen_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list").wait_for(state="visible")
    await _open_filters(page)
    initial_font_size = await page.locator("body").evaluate("element => getComputedStyle(element).fontSize")
    await page.locator("#search-input").fill("ワークアイテム条件")

    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    await page.locator("#preview h1", has_text="初回").wait_for(state="visible")
    await page.locator("#plans-filter").fill("計画条件")
    await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
    await page.locator("#sessions .session-item").first.wait_for(state="visible")
    await page.locator("#sessions-filter").fill("セッション条件")

    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    assert await page.locator("#plans-filter").input_value() == "計画条件"
    await page.locator("nav.app-nav").get_by_role("link", name="ワークアイテム").click()
    assert await page.locator("#search-input").input_value() == "ワークアイテム条件"
    assert await page.locator("body").evaluate("element => getComputedStyle(element).fontSize") == initial_font_size
    await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
    assert await page.locator("#sessions-filter").input_value() == "セッション条件"


@pytest.mark.asyncio
async def test_navigation_uses_one_stylesheet(screen_harness: _ScreenHarness) -> None:
    """3画面を1つの有効なスタイルシートで描画する。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/")
    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
    await page.locator("nav.app-nav").get_by_role("link", name="ワークアイテム").click()

    assert await page.evaluate("() => document.styleSheets.length === 1 && !document.styleSheets[0].disabled")


@pytest.mark.asyncio
async def test_revisiting_screens_does_not_repeat_initial_requests(screen_harness: _ScreenHarness) -> None:
    """初期化済みの画面へ再訪しても、同期と一覧の初期要求を繰り返さない。"""
    harness = screen_harness
    page = harness.page
    plans_resynced = asyncio.Event()
    plans_request_count = 0

    def record_initial_plan_requests(request: playwright.async_api.Request) -> None:
        nonlocal plans_request_count
        if request.url.endswith("/api/plans/files"):
            plans_request_count += 1
            if plans_request_count >= 2:
                plans_resynced.set()

    page.on("request", record_initial_plan_requests)
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    await page.locator("#files .file").first.wait_for(state="visible")
    await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
    await page.locator("#sessions .session-item").first.wait_for(state="visible")
    await asyncio.wait_for(plans_resynced.wait(), timeout=5)
    tracked_suffixes = ("/api/sync", "/api/plans/files", "/api/sessions/list")
    before = {suffix: sum(url.endswith(suffix) for url in harness.requests) for suffix in tracked_suffixes}

    await page.locator("nav.app-nav").get_by_role("link", name="ワークアイテム").click()
    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()

    assert {suffix: sum(url.endswith(suffix) for url in harness.requests) for suffix in tracked_suffixes} == before


@pytest.mark.asyncio
async def test_initial_screen_request_does_not_block_other_screen_prefetch(
    screen_harness: _ScreenHarness,
) -> None:
    """初期画面の応答待ち中にも他画面の初回要求を開始する。"""
    harness = screen_harness
    sessions_release = asyncio.Event()
    sessions_handled = asyncio.Event()
    sync_requested = asyncio.Event()
    plans_requested = asyncio.Event()

    async def delay_sessions(route: playwright.async_api.Route) -> None:
        try:
            await sessions_release.wait()
            await route.continue_()
        finally:
            sessions_handled.set()

    def record_requests(request: playwright.async_api.Request) -> None:
        if request.url.endswith("/api/sync"):
            sync_requested.set()
        if request.url.endswith("/api/plans/files"):
            plans_requested.set()

    harness.page.on("request", record_requests)
    await harness.page.route("**/api/sessions/list", delay_sessions)
    try:
        await harness.page.goto(harness.base_url + "/sessions")
        await playwright.async_api.expect(harness.page.locator("#screen-sessions")).to_be_visible()
        await asyncio.wait_for(plans_requested.wait(), timeout=5)
        assert not sync_requested.is_set()
    finally:
        sessions_release.set()
        await asyncio.wait_for(sessions_handled.wait(), timeout=5)
        await harness.page.unroute("**/api/sessions/list", delay_sessions)


@pytest.mark.asyncio
async def test_three_screen_bodies_use_matching_typography(screen_harness: _ScreenHarness) -> None:
    """3画面の本文コンテナーで書体、文字サイズ、行高、字間及び文字色をそろえる。"""
    harness = screen_harness
    page = harness.page
    read_styles = "(element, names) => Object.fromEntries(names.map((name) => [name, getComputedStyle(element)[name]]))"
    typography = ["fontFamily", "fontSize", "lineHeight", "letterSpacing", "color"]
    gutters = ["maxWidth", "paddingLeft", "paddingRight"]

    await page.goto(harness.base_url + "/")
    await _open_question(page)
    work_item_styles = await page.locator("#detail-content").evaluate(read_styles, typography)
    await page.keyboard.press("Escape")

    await page.goto(harness.base_url + "/plans")
    await page.locator("#preview h1", has_text="初回").wait_for(state="visible")
    plan_styles = await page.locator("#preview").evaluate(read_styles, typography)
    plan_gutters = await page.locator("#preview").evaluate(read_styles, gutters)
    plan_toolbar = await page.locator("#screen-plans main > .toolbar").evaluate(read_styles, ["padding"])

    await page.goto(harness.base_url + "/sessions")
    await page.locator("#sessions .session-item").first.click()
    await page.locator("#detail details").first.wait_for(state="visible")
    session_styles = await page.locator("#detail").evaluate(read_styles, typography)
    session_gutters = await page.locator("#detail").evaluate(read_styles, gutters)
    session_toolbar = await page.locator("#screen-sessions main > .toolbar").evaluate(read_styles, ["padding"])

    assert work_item_styles == plan_styles == session_styles
    assert session_gutters == plan_gutters
    assert session_toolbar == plan_toolbar


@pytest.mark.asyncio
async def test_plan_and_session_toolbars_have_matching_heights(screen_harness: _ScreenHarness) -> None:
    """計画とセッションの詳細ツールバーを同じ高さで描画する。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/plans")
    plans_toolbar = page.locator("#screen-plans main > .toolbar")
    await plans_toolbar.wait_for(state="visible")
    plans_height = await plans_toolbar.evaluate("element => element.getBoundingClientRect().height")
    await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
    sessions_toolbar = page.locator("#screen-sessions main > .toolbar")
    await sessions_toolbar.wait_for(state="visible")
    sessions_height = await sessions_toolbar.evaluate("element => element.getBoundingClientRect().height")

    assert sessions_height == plans_height


@pytest.mark.asyncio
async def test_session_list_omits_message_preview(screen_harness: _ScreenHarness) -> None:
    """セッション一覧には発話内容のプレビューを表示しない。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    first_item = page.locator("#sessions .session-item").first
    await first_item.wait_for(state="visible")

    child_classes = await first_item.locator(":scope > *").evaluate_all(
        "elements => elements.map(element => element.className)"
    )
    assert child_classes == ["session-cwd pane-item-title", "session-meta pane-item-meta"]


@pytest.mark.asyncio
async def test_session_previous_and_next_buttons_navigate_list(screen_harness: _ScreenHarness) -> None:
    """セッション詳細の前後ボタンで一覧の隣接項目へ移動する。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    items = page.locator("#sessions .session-item")
    await playwright.async_api.expect(items).to_have_count(2)
    names = await items.all_inner_texts()
    await items.first.click()
    await page.locator("#sessions-next-btn").click()
    assert await page.locator('#sessions .session-item[aria-current="true"]').inner_text() == names[1]
    await page.locator("#sessions-prev-btn").click()
    assert await page.locator('#sessions .session-item[aria-current="true"]').inner_text() == names[0]


@pytest.mark.asyncio
async def test_work_item_rows_keep_fixed_columns_and_equal_heights(screen_harness: _ScreenHarness) -> None:
    """最長の状態表示を含む一覧でも固定列を折り返さず、全行を同じ高さで描画する。"""
    processing = screen_harness.plan_path.parent.parent / "processing"
    processing.mkdir()
    (processing / "20260906-123456-001.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/long-repository\nsource: human\n"
        "plan_file: /tmp/plan.md\n---\n\n長い要約を持つ確認事項 "
        + "要約" * 80
        + "\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    page = screen_harness.page
    await page.set_viewport_size({"width": 1600, "height": 800})
    await page.goto(screen_harness.base_url + "/")
    rows = page.locator("#entry-list .entry-row")
    await playwright.async_api.expect(rows).to_have_count(5)

    metrics = await rows.evaluate_all(
        """rows => rows.map(row => {
          const filename = row.querySelector('.filename-cell');
          const status = row.querySelector('.status-cell');
          const childHeights = Array.from(status.children, child => child.getBoundingClientRect().height);
          return {
            height: row.getBoundingClientRect().height,
            filenameClientWidth: filename.clientWidth,
            filenameScrollWidth: filename.scrollWidth,
            statusHeight: status.getBoundingClientRect().height,
            tallestStatusChild: Math.max(...childHeights)
          };
        })"""
    )
    assert len({round(metric["height"], 1) for metric in metrics}) == 1
    assert all(metric["filenameScrollWidth"] <= metric["filenameClientWidth"] for metric in metrics)
    assert all(metric["statusHeight"] <= metric["tallestStatusChild"] + 1 for metric in metrics)


@pytest.mark.asyncio
async def test_three_screens_scroll_below_the_fixed_header(screen_harness: _ScreenHarness) -> None:
    """3画面ともビューポートではなくヘッダー下の本文要素をスクロールする。"""
    screen_harness.plan_path.write_text(
        "# 長い計画\n\n" + "\n\n".join(f"計画の段落{index}" for index in range(60)) + "\n",
        encoding="utf-8",
    )
    session_path = next((screen_harness.root.parent / "claude").rglob("*.jsonl"))
    with session_path.open("a", encoding="utf-8") as stream:
        for index in range(40):
            stream.write(
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": f"2026-09-01T00:20:{index:02d}Z",
                        "message": {"content": f"スクロール用の発話{index}"},
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    page = screen_harness.page
    await page.set_viewport_size({"width": 900, "height": 240})
    for path, selector in (
        ("/", "#screen-wi .wi-screen-scroll"),
        ("/plans", "#screen-plans main"),
        ("/sessions", "#screen-sessions main"),
    ):
        await page.goto(screen_harness.base_url + path)
        scrollable = page.locator(selector)
        await scrollable.wait_for(state="visible")
        if path == "/plans":
            await page.locator("#preview h1", has_text="長い計画").wait_for(state="visible")
        elif path == "/sessions":
            await page.locator("#sessions .session-item").first.click()
            await page.locator("#detail details").first.wait_for(state="visible")
        metrics = await scrollable.evaluate(
            """element => {
              element.scrollTop = element.scrollHeight;
              const header = element.closest('.screen').querySelector('.app-header').getBoundingClientRect();
              const rect = element.getBoundingClientRect();
              return {
                documentClientHeight: document.scrollingElement.clientHeight,
                documentScrollHeight: document.scrollingElement.scrollHeight,
                clientHeight: element.clientHeight,
                scrollHeight: element.scrollHeight,
                headerTop: header.top,
                headerBottom: header.bottom,
                contentTop: rect.top
              };
            }"""
        )
        assert metrics["documentScrollHeight"] <= metrics["documentClientHeight"], path
        assert metrics["scrollHeight"] > metrics["clientHeight"], path
        assert abs(metrics["headerTop"]) <= 1, path
        assert metrics["contentTop"] >= metrics["headerBottom"] - 1, path


@pytest.mark.asyncio
async def test_navigation_ignores_delayed_detail_and_search_results(
    screen_harness: _ScreenHarness,
) -> None:
    """詳細取得と全文検索の成功・失敗が遷移後に届いても、旧DOMへ書き込まない。"""
    harness = screen_harness
    page = harness.page
    errors: list[str] = []
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: errors.append(str(error)))

    async def exercise_detail(succeeds: bool) -> None:
        requested = asyncio.Event()
        release = asyncio.Event()
        fulfilled = asyncio.Event()

        async def delay_detail(route: playwright.async_api.Route) -> None:
            response = await route.fetch() if succeeds else None
            requested.set()
            await release.wait()
            if response is None:
                await route.fulfill(status=200, content_type="application/json", body="{")
            else:
                await route.fulfill(response=response)
            fulfilled.set()

        await page.route("**/api/entries/inbox/awi.md", delay_detail)
        await page.goto(harness.base_url + "/")
        await page.locator('.entry-select[data-key="inbox/awi.md"]').click()
        await asyncio.wait_for(requested.wait(), timeout=5)
        await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
        await page.locator("#preview h1", has_text="初回").wait_for(state="visible")
        release.set()
        await asyncio.wait_for(fulfilled.wait(), timeout=5)
        await page.wait_for_timeout(50)
        await page.unroute("**/api/entries/inbox/awi.md", delay_detail)

    async def exercise_search(succeeds: bool) -> None:
        requested = asyncio.Event()
        release = asyncio.Event()
        fulfilled = asyncio.Event()

        async def delay_search(route: playwright.async_api.Route) -> None:
            response = await route.fetch() if succeeds else None
            requested.set()
            await release.wait()
            if response is None:
                await route.fulfill(status=200, content_type="application/json", body="{")
            else:
                await route.fulfill(response=response)
            fulfilled.set()

        await page.route("**/api/plans/search?*", delay_search)
        await page.goto(harness.base_url + "/plans")
        await page.locator("#preview h1", has_text="初回").wait_for(state="visible")
        await page.locator("#plans-filter").fill("初回")
        await asyncio.wait_for(requested.wait(), timeout=5)
        await page.locator("nav.app-nav").get_by_role("link", name="セッション").click()
        await page.locator("#sessions .session-item").first.wait_for(state="visible")
        release.set()
        await asyncio.wait_for(fulfilled.wait(), timeout=5)
        await page.wait_for_timeout(50)
        await page.unroute("**/api/plans/search?*", delay_search)

    for succeeds in (True, False):
        await exercise_detail(succeeds)
        await exercise_search(succeeds)

    assert not errors


@pytest.mark.asyncio
async def test_back_navigation_restores_previous_screen(screen_harness: _ScreenHarness) -> None:
    """ブラウザーの戻る操作で直前の画面が再び描画される。"""
    harness = screen_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")

    await page.locator("nav.app-nav").get_by_role("link", name="計画ファイル").click()
    await page.locator("#preview h1", has_text="初回").wait_for(state="visible")

    await page.go_back()
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    assert await page.evaluate("() => location.pathname") == "/"


@pytest.mark.asyncio
async def test_entry_copy_button_copies_filename_and_summary_without_selecting(
    screen_harness: _ScreenHarness,
) -> None:
    """一覧のコピー操作はファイル名と要約を写し、詳細選択を発生させない。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    row = page.locator("#entry-list .entry-row").first
    await row.locator(".entry-copy").wait_for(state="visible")
    filename = await row.locator(".filename-cell").inner_text()
    summary = await row.locator(".summary-cell").inner_text()

    await row.locator(".entry-copy").click()

    assert await page.evaluate("() => navigator.clipboard.readText()") == f"{filename} {summary}"
    await playwright.async_api.expect(row.locator(".entry-copy")).to_have_text("コピーしました")
    await playwright.async_api.expect(page.locator("#result-status")).to_contain_text("コピーしました")
    await playwright.async_api.expect(row.locator(".entry-copy")).to_have_text("コピー", timeout=4000)
    assert await page.locator("#detail-dialog").evaluate("element => element.open") is False


@pytest.mark.asyncio
async def test_entry_copy_label_survives_list_reload(screen_harness: _ScreenHarness) -> None:
    """コピー表示の期間中に一覧の再読込が完了しても、同じ項目のボタンは表示を保ち、期間の終わりに戻る。

    再読込は利用者の操作と無関係な契機（ウィンドウのfocus、SSEの変更通知）でも起きるため、
    一覧取得の応答を保留してクリックを再描画より先に起こし、再描画後の新しいボタンの表示を確かめる。
    """
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    row = page.locator("#entry-list .entry-row").first
    await row.locator(".entry-copy").wait_for(state="visible")
    release = asyncio.Event()

    async def hold_list_request(route: playwright.async_api.Route) -> None:
        await release.wait()
        await route.continue_()

    await page.route("**/api/entries?*", hold_list_request)
    await row.locator(".entry-copy").evaluate("element => { element.dataset.beforeReload = 'true'; }")
    await page.evaluate("() => window.dispatchEvent(new Event('focus'))")
    await row.locator(".entry-copy").click()
    await playwright.async_api.expect(row.locator(".entry-copy")).to_have_text("コピーしました")

    release.set()
    await playwright.async_api.expect(page.locator("#entry-list .entry-copy[data-before-reload]")).to_have_count(0)
    await playwright.async_api.expect(row.locator(".entry-copy")).to_have_text("コピーしました")
    await playwright.async_api.expect(row.locator(".entry-copy")).to_have_text("コピー", timeout=4000)
    await page.unroute("**/api/entries?*", hold_list_request)


@pytest.mark.asyncio
async def test_direct_load_of_each_screen(screen_harness: _ScreenHarness) -> None:
    """3画面のURLへ直接アクセスしても各画面が描画される。"""
    harness = screen_harness
    for path, selector in (
        ("/", "#entry-list .entry-select"),
        ("/plans", "#preview h1"),
        ("/sessions", "#sessions .session-item"),
    ):
        await harness.page.goto(harness.base_url + path)
        await harness.page.locator(selector).first.wait_for(state="visible")
        assert (
            await harness.page.title()
            == {
                "/": "ワークアイテム - atk serve",
                "/plans": "計画ファイル - atk serve",
                "/sessions": "セッション - atk serve",
            }[path]
        )


@pytest.mark.asyncio
async def test_work_item_filename_link_supports_get_and_shift_click(screen_harness: _ScreenHarness) -> None:
    """WI名は詳細を復元するGET URLを持ち、Shiftクリックをブラウザーへ委ねる。"""
    harness = screen_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    link = page.locator('.entry-select[data-key="inbox/awi.md"]')
    await link.wait_for(state="visible")
    href = await link.get_attribute("href")
    assert href == "/?state=inbox&filename=awi.md"

    await link.click()
    await page.get_by_role("dialog", name="詳細").wait_for(state="visible")
    assert urllib.parse.urlsplit(page.url).query == "state=inbox&filename=awi.md"

    await page.go_back()
    await playwright.async_api.expect(page.get_by_role("dialog", name="詳細")).to_be_hidden()
    await page.go_forward()
    await page.get_by_role("dialog", name="詳細").wait_for(state="visible")
    assert await page.locator("#detail-filename").inner_text() == "awi.md"
    await page.keyboard.press("Escape")

    assert not await _shift_click_default_prevented(link)


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
async def test_initial_sync_shows_work_item_loading(browser_harness: _BrowserHarness) -> None:
    """初回表示はGit同期を待たず、一覧取得中だけナビゲーションへ表示する。"""
    page = browser_harness.page
    entries_started = asyncio.Event()
    release_entries = asyncio.Event()
    sync_calls: list[str] = []

    async def track_sync(route: playwright.async_api.Route) -> None:
        sync_calls.append(route.request.url)
        await route.continue_()

    await page.route("**/api/sync", track_sync)
    await page.route("**/api/entries?*", functools.partial(_hold_route, started=entries_started, release=release_entries))
    try:
        await page.goto(browser_harness.base_url + "/")
        await asyncio.wait_for(entries_started.wait(), timeout=5)
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
    finally:
        release_entries.set()
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    assert not sync_calls


@pytest.mark.asyncio
async def test_detail_loading_keeps_list_and_navigation_positions(browser_harness: _BrowserHarness) -> None:
    """詳細取得中のアイコンをナビゲーション内の左右列境界へ置き、リンクと一覧を動かさない。"""
    page = browser_harness.page
    await page.set_viewport_size({"width": 1440, "height": 1000})
    await page.goto(browser_harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    entry = page.locator('.entry-select[data-key="inbox/awi.md"]')
    await entry.wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "false")
    nav = page.locator("#screen-wi .app-nav a")
    before_nav = await nav.evaluate_all(
        "links => links.map(link => [link.getBoundingClientRect().x, link.getBoundingClientRect().y])"
    )
    before_row = await entry.bounding_box()
    assert before_row is not None
    started = asyncio.Event()
    release = asyncio.Event()
    await page.route("**/api/entries/inbox/awi.md", functools.partial(_hold_route, started=started, release=release))
    try:
        await entry.click()
        await asyncio.wait_for(started.wait(), timeout=5)
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
        spinner = await page.locator("#loading-indicator").bounding_box()
        filters = await page.locator("#screen-wi .filters").bounding_box()
        pane = await page.locator("#screen-wi .entry-pane").bounding_box()
        assert spinner is not None and filters is not None and pane is not None
        spinner_center = spinner["x"] + spinner["width"] / 2
        assert filters["x"] + filters["width"] <= spinner_center <= pane["x"]
        # 未定義の色変数を参照すると枠線を描画しないため、位置に加えて描画される状態も確認する。
        spinner_visible = await page.locator("#loading-indicator").evaluate(
            "element => { const style = getComputedStyle(element, '::before'); "
            "return style.borderRightStyle !== 'none' && parseFloat(style.borderRightWidth) > 0 "
            "&& style.animationName !== 'none'; }"
        )
        assert spinner_visible
        assert (
            await nav.evaluate_all(
                "links => links.map(link => [link.getBoundingClientRect().x, link.getBoundingClientRect().y])"
            )
            == before_nav
        )
        assert await entry.bounding_box() == before_row
    finally:
        release.set()
    await page.get_by_role("dialog", name="詳細").wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()


@pytest.mark.asyncio
async def test_work_item_focus_recovers_change_without_sse_event(browser_harness: _BrowserHarness) -> None:
    """SSE通知を受信できなかった場合も、フォーカス復帰時に一覧を再取得する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator('.entry-select[data-key="inbox/awi.md"]').wait_for(state="visible")
    (harness.root / "inbox" / "missed.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n切断中の変更\n", encoding="utf-8"
    )
    await page.evaluate("window.dispatchEvent(new Event('focus'))")
    await page.locator('.entry-select[data-key="inbox/missed.md"]').wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#connection-status")).to_be_hidden()


@pytest.mark.asyncio
async def test_background_sync_failure_offers_retry_in_browser(browser_harness: _BrowserHarness) -> None:
    """定期同期の失敗を通知し、次の成功時に案内を閉じる。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator('.entry-select[data-key="inbox/awi.md"]').wait_for(state="visible")
    harness.current_state.publish("sync-error")
    status = page.locator("#connection-status")
    await playwright.async_api.expect(status).to_contain_text("今すぐ同期")
    await playwright.async_api.expect(status).to_be_visible()
    harness.current_state.publish("sync-ok")
    await playwright.async_api.expect(status).to_be_hidden()


@pytest.mark.asyncio
async def test_external_work_item_refresh_keeps_loading_hidden(browser_harness: _BrowserHarness) -> None:
    """SSEによる背景更新は既存一覧を保ち、読み込み表示を点滅させない。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await page.evaluate(
        """() => {
          window.loadingShown = 0;
          const indicator = document.getElementById('loading-indicator');
          new MutationObserver(() => { if (!indicator.hidden) window.loadingShown += 1; })
            .observe(indicator, {attributes: true, attributeFilter: ['hidden']});
        }"""
    )
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()

    async def delay_background_entries(route: playwright.async_api.Route) -> None:
        if "status=active" in route.request.url:
            fetch_started.set()
            await release_fetch.wait()
        await route.continue_()

    await page.route("**/api/entries?*", delay_background_entries)
    (harness.root / "inbox" / "background.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n背景更新\n",
        encoding="utf-8",
    )
    harness.current_state.publish()
    try:
        await asyncio.wait_for(fetch_started.wait(), timeout=5)
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
        await playwright.async_api.expect(page.locator('.entry-select[data-key="inbox/awi.md"]')).to_be_visible()
    finally:
        release_fetch.set()
    await page.locator('.entry-select[data-key="inbox/background.md"]').wait_for(state="visible")
    assert await page.evaluate("window.loadingShown") == 0


@pytest.mark.asyncio
async def test_manual_sync_refreshes_work_items_without_page_reload(browser_harness: _BrowserHarness) -> None:
    """同期後の一覧取得で新着がページ再読込なしに現れる。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    navigation_count = await page.evaluate("performance.getEntriesByType('navigation').length")
    (browser_harness.root / "inbox" / "new-item.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: browser\n---\n\n新着の本文\n",
        encoding="utf-8",
    )

    await page.locator("#refresh-button").click()

    await page.locator('.entry-select[data-key="inbox/new-item.md"]').wait_for(state="visible")
    assert await page.evaluate("performance.getEntriesByType('navigation').length") == navigation_count


@pytest.mark.asyncio
async def test_manual_sync_stays_busy_until_entries_render(browser_harness: _BrowserHarness) -> None:
    """一覧の新着が描画されるまで同期ボタンと結果表示を完了させない。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    (browser_harness.root / "inbox" / "delayed-sync.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\n---\n\n遅延した同期結果\n",
        encoding="utf-8",
    )
    entries_started = asyncio.Event()
    release_entries = asyncio.Event()

    await page.route("**/api/entries?*", functools.partial(_hold_route, started=entries_started, release=release_entries))
    try:
        await page.locator("#refresh-button").click()
        await asyncio.wait_for(entries_started.wait(), timeout=5)
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
        await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "true")
        await playwright.async_api.expect(page.locator("#refresh-button")).to_be_disabled()
        await playwright.async_api.expect(page.locator("#operation-notice")).to_be_hidden()
        await playwright.async_api.expect(page.locator('.entry-select[data-key="inbox/delayed-sync.md"]')).to_have_count(0)
    finally:
        release_entries.set()
    await playwright.async_api.expect(page.locator('.entry-select[data-key="inbox/delayed-sync.md"]')).to_be_visible()
    await playwright.async_api.expect(page.locator("#operation-notice")).to_be_hidden()
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "false")
    await playwright.async_api.expect(page.locator("#refresh-button")).to_be_enabled()


@pytest.mark.asyncio
async def test_clear_filters_stays_busy_through_repos_and_entries(browser_harness: _BrowserHarness) -> None:
    """条件クリア後の候補取得と一覧取得の両方を処理中として表示する。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await _open_filters(page)
    repos_started = asyncio.Event()
    release_repos = asyncio.Event()
    entries_started = asyncio.Event()
    release_entries = asyncio.Event()

    await page.route("**/api/repos?*", functools.partial(_hold_route, started=repos_started, release=release_repos))
    await page.route("**/api/entries?*", functools.partial(_hold_route, started=entries_started, release=release_entries))
    try:
        await page.locator("#clear-filters-button").click()
        await asyncio.wait_for(repos_started.wait(), timeout=5)
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
        await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "true")
        release_repos.set()
        await asyncio.wait_for(entries_started.wait(), timeout=5)
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
        await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "true")
    finally:
        release_repos.set()
        release_entries.set()
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "false")


@pytest.mark.asyncio
async def test_search_stays_busy_during_debounce_and_entries(browser_harness: _BrowserHarness) -> None:
    """検索入力直後から遅延取得の描画完了まで処理中を保つ。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    entries_started = asyncio.Event()
    release_entries = asyncio.Event()

    await page.route("**/api/entries?*", functools.partial(_hold_route, started=entries_started, release=release_entries))
    try:
        await page.locator("#search-input").fill("awi")
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
        await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "true")
        await asyncio.wait_for(entries_started.wait(), timeout=5)
        browser_harness.current_state.publish()
        await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
        await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "true")
    finally:
        release_entries.set()
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    await playwright.async_api.expect(page.locator("#entry-list")).to_have_attribute("aria-busy", "false")


@pytest.mark.asyncio
async def test_slow_work_item_filter_and_plan_preview_show_loading(
    screen_harness: _ScreenHarness,
) -> None:
    """WI一覧と計画プレビューの処理中表示を確認する。"""
    harness = screen_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await _open_filters(page)

    async def delay_entries(route: playwright.async_api.Route) -> None:
        if "status=adopted" in route.request.url:
            await asyncio.sleep(0.65)
        await route.continue_()

    await page.route("**/api/entries**", delay_entries)
    await page.locator("#state-filter").select_option("adopted")
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_visible()
    await page.locator('.entry-select[data-key="adopted/adopted.md"]').wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#loading-indicator")).to_be_hidden()
    await page.unroute("**/api/entries**", delay_entries)

    async def delay_plan(route: playwright.async_api.Route) -> None:
        await asyncio.sleep(0.65)
        await route.continue_()

    await page.route("**/api/plans/file?*", delay_plan)
    await page.goto(harness.base_url + "/plans")
    await page.locator("#plans-loading-indicator").wait_for(state="visible")
    await page.get_by_role("heading", name="初回").wait_for(state="visible")
    await playwright.async_api.expect(page.locator("#plans-loading-indicator")).to_be_hidden()


@pytest.mark.asyncio
async def test_plan_and_session_sidebars_match_and_session_opens_first_detail(
    screen_harness: _ScreenHarness,
) -> None:
    """二画面の左幅をそろえ、セッションの先頭詳細をデスクトップで自動表示する。"""
    page = screen_harness.page
    await page.set_viewport_size({"width": 1280, "height": 800})
    await page.goto(screen_harness.base_url + "/plans")
    plan_width = await page.locator("#screen-plans aside").evaluate("element => element.getBoundingClientRect().width")
    plan_link = page.locator("#files .file").first
    plan_link_width = await plan_link.evaluate("element => element.getBoundingClientRect().width")
    await plan_link.hover(position={"x": plan_link_width - 2, "y": 2})
    plan_link_background = await plan_link.evaluate("element => getComputedStyle(element).backgroundColor")

    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator("#detail .event").first.wait_for(state="visible")
    session_width = await page.locator("#screen-sessions aside").evaluate("element => element.getBoundingClientRect().width")

    assert plan_width == session_width == 320
    assert plan_link_width <= plan_width
    assert plan_link_background == "rgb(238, 242, 255)"
    assert await page.locator('#sessions .session-item[aria-current="true"]').count() == 1


@pytest.mark.asyncio
async def test_header_navigation_is_centered_on_three_screens(screen_harness: _ScreenHarness) -> None:
    """ヘッダーの子要素の数が画面ごとに異なっても、3画面ともナビゲーションを画面中央へ置く。"""
    harness = screen_harness
    await harness.page.set_viewport_size({"width": 1280, "height": 800})

    for path, screen in (("/", "#screen-wi"), ("/plans", "#screen-plans"), ("/sessions", "#screen-sessions")):
        await harness.page.goto(harness.base_url + path)
        navigation = harness.page.locator(f"{screen} nav.app-nav")
        await navigation.wait_for(state="visible")
        box = await navigation.bounding_box()
        assert box is not None
        viewport_width = await harness.page.evaluate("document.documentElement.clientWidth")
        # 小数の丸めだけを許容し、片側へ寄る配置を検出する。
        assert abs((box["x"] + box["width"] / 2) - viewport_width / 2) <= 1, path


_NAV_LINK_METRICS = """links => links.map(link => {
  const rect = link.getBoundingClientRect();
  const style = getComputedStyle(link);
  return {
    x: Math.round(rect.x * 100) / 100,
    y: Math.round(rect.y * 100) / 100,
    width: Math.round(rect.width * 100) / 100,
    height: Math.round(rect.height * 100) / 100,
    style: [style.fontFamily, style.fontSize, style.fontWeight, style.lineHeight, style.padding,
      style.borderWidth, style.boxSizing].join('|'),
  };
})"""


@pytest.mark.asyncio
@pytest.mark.parametrize("width", [1920, 1440, 1280, 900, 600])
async def test_navigation_links_match_on_three_screens(screen_harness: _ScreenHarness, width: int) -> None:
    """3画面で「ワークアイテム・計画ファイル・セッション」の各リンクの位置、大きさ及び算出スタイルが一致する。

    navの箱の中心だけを比べると、画面固有の要素がnavの内側でリンク群をずらしても検出できないため、
    リンク単位で比べる。現在の画面の強調が字幅を変える場合もここで検出する。
    """
    page = screen_harness.page
    await page.set_viewport_size({"width": width, "height": 800})
    links = page.locator(".screen:not([hidden]) nav.app-nav a")

    direct: dict[str, object] = {}
    for path in ("/", "/plans", "/sessions"):
        await page.goto(screen_harness.base_url + path)
        await links.first.wait_for(state="visible")
        direct[path] = await links.evaluate_all(_NAV_LINK_METRICS)
    assert direct["/plans"] == direct["/"]
    assert direct["/sessions"] == direct["/"]

    clicked: dict[str, object] = {}
    await page.goto(screen_harness.base_url + "/")
    for name, path in (("計画ファイル", "/plans"), ("セッション", "/sessions"), ("ワークアイテム", "/")):
        await page.locator(".screen:not([hidden]) nav.app-nav").get_by_role("link", name=name).click()
        await playwright.async_api.expect(
            page.locator('.screen:not([hidden]) nav.app-nav a[aria-current="page"]')
        ).to_have_text(name)
        clicked[path] = await links.evaluate_all(_NAV_LINK_METRICS)
    assert clicked == direct


@pytest.mark.asyncio
async def test_header_layout_matches_on_three_screens(screen_harness: _ScreenHarness) -> None:
    """3画面のヘッダーの高さと文字サイズがそろい、折り返しが起きる幅でも見出しとナビゲーションの大きさと水平位置がそろう。"""
    harness = screen_harness
    await harness.page.set_viewport_size({"width": 1400, "height": 800})
    headers: dict[str, dict[str, float | str]] = {}

    for path, screen in (("/", "#screen-wi"), ("/plans", "#screen-plans"), ("/sessions", "#screen-sessions")):
        await harness.page.goto(harness.base_url + path)
        header = harness.page.locator(f"{screen} .app-header")
        await header.wait_for(state="visible")
        header_box = await header.bounding_box()
        assert header_box is not None, path
        headers[path] = {
            "height": round(header_box["height"], 1),
            # ヘッダー内の部品は各画面の本文用の指定ではなく共有ヘッダーの文字サイズを継承する。
            "font_size": await header.evaluate("(element) => getComputedStyle(element).fontSize"),
        }

    assert headers["/plans"] == headers["/"]
    assert headers["/sessions"] == headers["/"]

    await harness.page.set_viewport_size({"width": 600, "height": 800})
    layouts: dict[str, dict[str, float | str]] = {}

    for path, screen in (("/", "#screen-wi"), ("/plans", "#screen-plans"), ("/sessions", "#screen-sessions")):
        await harness.page.goto(harness.base_url + path)
        header = harness.page.locator(f"{screen} .app-header")
        await header.wait_for(state="visible")
        title = harness.page.locator(f"{screen} .app-header .header-title")
        navigation = harness.page.locator(f"{screen} nav.app-nav")
        await navigation.wait_for(state="visible")
        header_box = await header.bounding_box()
        title_box = await title.bounding_box()
        navigation_box = await navigation.bounding_box()
        assert header_box is not None and title_box is not None and navigation_box is not None, path
        # 折り返す幅ではWI画面だけが同期操作の欄を別の行に置くため、ヘッダー全体の高さと絶対位置は画面ごとに異なる。
        # 3画面が共通して持つ部品の大きさと、ヘッダー左端からの水平位置を比較する。
        layouts[path] = {
            "title_height": round(title_box["height"], 1),
            "nav_height": round(navigation_box["height"], 1),
            "nav_offset_x": round(navigation_box["x"] - header_box["x"], 1),
            "title_font_size": await harness.page.locator(f"{screen} .app-header h1").evaluate(
                "(element) => getComputedStyle(element).fontSize"
            ),
        }

    assert layouts["/plans"] == layouts["/"]
    assert layouts["/sessions"] == layouts["/"]


@pytest.mark.asyncio
async def test_panes_follow_header_height_on_narrow_width(screen_harness: _ScreenHarness) -> None:
    """ヘッダーが折り返す幅でも、主要領域の上端がヘッダーの下端と、下端がビューポートの下端と一致する。"""
    harness = screen_harness
    await harness.page.set_viewport_size({"width": 600, "height": 800})

    for path, screen, selector in (
        ("/", "#screen-wi", "#screen-wi .wi-screen-scroll"),
        ("/plans", "#screen-plans", "#plans-app"),
        ("/sessions", "#screen-sessions", "#sessions-app"),
    ):
        await harness.page.goto(harness.base_url + path)
        header = harness.page.locator(f"{screen} .app-header")
        await header.wait_for(state="visible")
        pane = harness.page.locator(selector)
        await pane.wait_for(state="visible")
        header_box = await header.bounding_box()
        pane_box = await pane.bounding_box()
        assert header_box is not None and pane_box is not None, path
        viewport_height = await harness.page.evaluate("document.documentElement.clientHeight")
        # 小数の丸めだけを許容し、固定値の見積もりによるずれを検出する。
        assert abs(pane_box["y"] - (header_box["y"] + header_box["height"])) <= 1, path
        assert abs((pane_box["y"] + pane_box["height"]) - viewport_height) <= 1, path


@pytest.mark.asyncio
async def test_shell_parts_share_computed_style_across_three_screens(screen_harness: _ScreenHarness) -> None:
    """共有シェル部品の計算スタイルを3画面と反復遷移の前後で維持する。"""
    harness = screen_harness
    page = harness.page
    properties = ["fontFamily", "fontSize", "lineHeight", "letterSpacing", "color"]
    read_styles = "(element, names) => Object.fromEntries(names.map((name) => [name, getComputedStyle(element)[name]]))"
    screens = (
        ("ワークアイテム", "#screen-wi"),
        ("計画ファイル", "#screen-plans"),
        ("セッション", "#screen-sessions"),
    )

    async def shell_styles(screen: str) -> dict[str, dict[str, str]]:
        await page.locator(screen).wait_for(state="visible")
        return {
            "header": await page.locator(f"{screen} .app-header").evaluate(read_styles, properties),
            "title": await page.locator(f"{screen} .app-header h1").evaluate(read_styles, properties),
            "nav": await page.locator(f"{screen} .app-nav").evaluate(read_styles, properties),
        }

    await page.goto(harness.base_url + "/")
    expected: dict[str, dict[str, str]] | None = None
    for _ in range(2):
        for link_name, screen in screens:
            await page.locator("nav.app-nav").get_by_role("link", name=link_name).click()
            current = await shell_styles(screen)
            if expected is None:
                expected = current
            else:
                assert current == expected

    await page.goto(harness.base_url + "/plans")
    plan_toolbar = await page.locator("#screen-plans main > .toolbar").evaluate(read_styles, properties)
    await page.goto(harness.base_url + "/sessions")
    session_toolbar = await page.locator("#screen-sessions main > .toolbar").evaluate(read_styles, properties)
    assert session_toolbar == plan_toolbar

    await page.goto(harness.base_url + "/")
    dialog_styles = [
        await page.locator(selector).evaluate(read_styles, properties)
        for selector in ("#detail-dialog", "#create-dialog", "#delete-dialog")
    ]
    assert dialog_styles[1:] == dialog_styles[:-1]


@pytest.mark.asyncio
async def test_buttons_share_the_common_style_on_three_screens(screen_harness: _ScreenHarness) -> None:
    """3画面のボタンを共通の配色・境界・角丸で表示し、無効なボタンは不透明度を下げる。"""
    harness = screen_harness
    properties = [
        "backgroundColor",
        "color",
        "borderTopColor",
        "borderTopWidth",
        "borderRadius",
        "paddingTop",
        "paddingRight",
        "paddingBottom",
        "paddingLeft",
        "fontSize",
    ]
    # 一覧を開くボタンは狭い画面でだけ表示するため、3画面とも同じ幅で比較する。
    await harness.page.set_viewport_size({"width": 600, "height": 800})
    styles: dict[str, dict[str, str]] = {}

    for path, selector in (
        ("/", "#refresh-button"),
        ("/plans", "#plans-menu-btn"),
        ("/sessions", "#sessions-menu-btn"),
    ):
        await harness.page.goto(harness.base_url + path)
        button = harness.page.locator(selector)
        await button.wait_for(state="visible")
        styles[path] = await button.evaluate(
            "(element, names) => Object.fromEntries(names.map((name) => [name, getComputedStyle(element)[name]]))",
            properties,
        )

    assert styles["/plans"] == styles["/"]
    assert styles["/sessions"] == styles["/"]

    await harness.page.goto(harness.base_url + "/plans")
    # 計画ファイルが1件だけの構成では、前のファイルへ移動するボタンが無効のまま表示される。
    previous_button = harness.page.locator("#prev-btn")
    await previous_button.wait_for(state="visible")
    assert await previous_button.is_disabled()
    enabled = await harness.page.locator("#plans-menu-btn").evaluate("(element) => getComputedStyle(element).opacity")
    disabled = await previous_button.evaluate("(element) => getComputedStyle(element).opacity")
    assert float(enabled) == 1
    assert float(disabled) < 1


@pytest.mark.asyncio
async def test_sidebar_items_share_style_between_plans_and_sessions(screen_harness: _ScreenHarness) -> None:
    """計画ファイル画面とセッション画面の左ペイン項目を、同じ共通クラスと算出スタイルで描画する。"""
    harness = screen_harness
    page = harness.page
    item_properties = [
        "borderTopLeftRadius",
        "borderTopRightRadius",
        "borderBottomLeftRadius",
        "borderBottomRightRadius",
        "paddingTop",
        "paddingRight",
        "paddingBottom",
        "paddingLeft",
        "backgroundColor",
        "boxShadow",
        "borderTopWidth",
        "borderLeftWidth",
        "textAlign",
    ]
    text_properties = ["fontSize", "fontWeight", "color", "marginTop"]
    script = """(element, names) => {
      const pick = (target, keys) => Object.fromEntries(keys.map((key) => [key, getComputedStyle(target)[key]]));
      const row = element.closest('.session-tree-row') || element;
      return {
        classes: [...element.classList].filter((name) => name.startsWith('pane-')),
        item: pick(element, names.item),
        title: pick(element.querySelector('.pane-item-title'), names.text),
        meta: pick(element.querySelector('.pane-item-meta'), names.text),
        separator: getComputedStyle(row).borderBottomWidth,
      };
    }"""
    names = {"item": item_properties, "text": text_properties}
    styles = {}
    for path, selector in (
        ("/plans", '#files .file[aria-current="true"]'),
        ("/sessions", '#sessions .session-item[aria-current="true"]'),
    ):
        await page.goto(harness.base_url + path)
        if path == "/sessions":
            await page.locator("#sessions .session-item").first.click()
        item = page.locator(selector).first
        await item.wait_for(state="visible")
        # hoverの背景と選択の背景を区別せず比べるため、ポインターを項目の外へ置く。
        await page.mouse.move(0, 0)
        styles[path] = await item.evaluate(script, names)

    assert styles["/plans"]["classes"] == ["pane-item"]
    assert styles["/sessions"] == styles["/plans"]
    assert styles["/plans"]["item"]["borderTopLeftRadius"] == "0px"


@pytest.mark.asyncio
async def test_selecting_deleted_plan_refreshes_list(screen_harness: _ScreenHarness) -> None:
    """一覧表示後に削除された計画ファイルを選ぶと、再試行を求めず移動又は削除済みを示して一覧を更新する。"""
    harness = screen_harness
    page = harness.page
    # 更新通知の購読を止め、削除後も一覧が古いままの状態で項目を選ぶ。
    await page.route("**/api/plans/events", lambda route: route.abort())
    await page.goto(harness.base_url + "/plans")
    item = page.locator("#files .file", has_text="plan.md")
    await item.wait_for(state="visible")

    harness.plan_path.unlink()
    await item.click()

    preview = page.locator("#preview")
    await playwright.async_api.expect(preview.get_by_role("status")).to_have_text(
        "選択した計画ファイルは移動又は削除されたため表示できません。一覧を更新しました。"
    )
    await playwright.async_api.expect(preview.get_by_role("button", name="再読み込み")).to_have_count(0)
    await playwright.async_api.expect(page.locator("#files .file")).to_have_count(0)
    await playwright.async_api.expect(page.locator("#copy-btn")).to_be_disabled()


@pytest.mark.asyncio
async def test_session_screen_lists_and_renders_both_engines(screen_harness: _ScreenHarness) -> None:
    """左ペインを内容と非表示の識別子で限定し、右ペインへ発話とツール入力を表示する。"""
    harness = screen_harness
    await harness.page.goto(harness.base_url + "/sessions")

    items = harness.page.locator("#sessions .session-item")
    await items.nth(1).wait_for(state="visible")
    assert await items.count() == 2
    listing = await harness.page.locator("#sessions").inner_text()
    assert "browser-test" in listing
    assert "/home/aki/proj" in listing
    assert "Claudeの発話" not in listing
    assert "11111111-2222-3333-4444-555555555555" not in listing
    # 実行系による限定の操作、実行系のバッジ、ホストの接続状態及び件数の表示は画面へ現れない。
    for selector in ("#sessions .engine-badge", ".engine-filter", "#host-status", "#list-status"):
        assert await harness.page.locator(selector).count() == 0, selector

    # 文字列による限定。
    await harness.page.locator("#sessions-filter").fill("aaaaaaaa")
    await harness.page.wait_for_function("document.querySelectorAll('#sessions .session-item').length === 1")
    assert await harness.page.locator('#sessions .session-item[data-engine="codex"]').count() == 1

    await harness.page.locator("#sessions .session-item").first.click()
    await harness.page.locator("#detail .event").first.wait_for(state="visible")
    assert "Codexの発話" in await harness.page.locator("#detail").inner_text()
    developer = harness.page.locator("#detail .kind-developer")
    compacted = harness.page.locator("#detail .kind-compact_boundary")
    assert "開発者" in await developer.locator("summary").inner_text()
    assert "コンテキスト圧縮" in await compacted.locator("summary").inner_text()
    developer_background = await developer.locator(".event-kind").evaluate(
        "element => getComputedStyle(element).backgroundColor"
    )
    default_background = await harness.page.evaluate(
        """() => {
            const event = document.createElement("div");
            event.className = "event";
            const kind = document.createElement("span");
            kind.className = "event-kind";
            event.append(kind);
            document.body.append(event);
            const color = getComputedStyle(kind).backgroundColor;
            event.remove();
            return color;
        }"""
    )
    existing_backgrounds = []
    for kind in ("user", "assistant", "thinking", "tool_call", "tool_result", "compact_boundary"):
        existing_backgrounds.append(
            await harness.page.locator(f"#detail .kind-{kind} .event-kind").first.evaluate(
                "element => getComputedStyle(element).backgroundColor"
            )
        )
    assert developer_background != default_background
    assert developer_background not in existing_backgrounds
    assert await developer.evaluate("element => element.open")
    assert not await compacted.evaluate("element => element.open")
    assert "/home/aki/other" in await harness.page.locator("#detail-title").inner_text()
    shell_summary = harness.page.locator("#detail .kind-tool_call", has_text="shell").locator("summary")
    assert "pwd" in await shell_summary.inner_text()
    broken_summary = harness.page.locator("#detail .kind-tool_call", has_text="broken").locator("summary")
    assert "invalid-json" not in await broken_summary.inner_text()

    await harness.page.locator("#sessions-filter").fill("Claudeの発話")
    await harness.page.locator('#sessions .session-item[data-engine="claude"]').wait_for(state="visible")
    assert await harness.page.locator('#sessions .session-item[data-engine="claude"]').count() == 1

    await harness.page.locator("#sessions-filter").fill("/home/aki/proj")
    await harness.page.wait_for_function("document.querySelectorAll('#sessions .session-item').length === 1")
    assert await harness.page.locator('#sessions .session-item[data-engine="claude"]').count() == 1

    await harness.page.locator("#sessions-filter").fill("browser-test")
    await harness.page.wait_for_function("document.querySelectorAll('#sessions .session-item').length === 3")

    await harness.page.locator("#sessions-filter").fill("")
    await harness.page.wait_for_function("document.querySelectorAll('#sessions .session-item').length === 2")
    await harness.page.set_viewport_size({"width": 390, "height": 844})
    await harness.page.locator("#sessions-menu-btn").click()
    await harness.page.locator('#sessions .session-item[data-engine="claude"]').click()
    await harness.page.locator("#detail .kind-thinking").wait_for(state="visible")
    tool_call = harness.page.locator("#detail .kind-tool_call").first
    assert "ツール呼び出し" in await tool_call.locator(".event-kind").inner_text()
    header_metrics = await tool_call.locator("summary").evaluate(
        """element => {
          const lineCount = child => {
            const range = document.createRange();
            range.selectNodeContents(child);
            return range.getClientRects().length;
          };
          const kind = element.querySelector('.event-kind');
          const time = element.querySelector('.event-time');
          const name = element.querySelector(':scope > span:not([class])');
          const input = element.querySelector('.event-input-summary');
          const inputStyle = getComputedStyle(input);
          return {
            kindLines: lineCount(kind),
            timeLines: lineCount(time),
            name: name.textContent,
            inputOverflow: input.scrollWidth > input.clientWidth,
            inputTextOverflow: inputStyle.textOverflow,
            inputWhiteSpace: inputStyle.whiteSpace,
          };
        }"""
    )
    assert header_metrics == {
        "kindLines": 1,
        "timeLines": 1,
        "name": "Bash",
        "inputOverflow": True,
        "inputTextOverflow": "ellipsis",
        "inputWhiteSpace": "nowrap",
    }
    # 思考とツール呼び出しは既定で畳み、要求された時だけ本文を展開する。
    await harness.page.locator("#detail .kind-thinking summary").click()
    detail_text = await harness.page.locator("#detail").inner_text()
    assert "Claudeの発話" in detail_text
    assert "Claudeの思考" in detail_text
    assert "Bash" in detail_text
    tool_summaries = await harness.page.locator("#detail .kind-tool_call summary").all_inner_texts()
    assert any("Bash" in summary and "ls" in summary and "pwd" not in summary for summary in tool_summaries)
    assert any("Read" in summary and "/tmp/input.md" in summary for summary in tool_summaries)
    assert any("Search" in summary and "needle" in summary for summary in tool_summaries)
    assert "入力: 12" in await harness.page.locator("#detail-usage").inner_text()
    # 破損した行は該当セッションの警告として示し、他の発話を失わせない。
    assert "解析できない行が1件あります" in detail_text


@pytest.mark.asyncio
async def test_session_details_use_exclusive_default_closed_sections(screen_harness: _ScreenHarness) -> None:
    """思考とツール呼び出しは既定で畳み、同時に1項目だけを展開する。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator('#sessions .session-item[data-engine="claude"]').click()
    sections = page.locator('#detail details[data-exclusive-event="true"]')
    await sections.nth(1).wait_for(state="visible")

    assert not await sections.nth(0).evaluate("element => element.open")
    assert not await sections.nth(1).evaluate("element => element.open")
    await sections.nth(0).locator("summary").click()
    assert await sections.nth(0).evaluate("element => element.open")
    await sections.nth(1).locator("summary").click()
    await playwright.async_api.expect(sections.nth(0)).not_to_have_attribute("open", "")
    await playwright.async_api.expect(sections.nth(1)).to_have_attribute("open", "")


@pytest.mark.asyncio
async def test_session_details_open_developer_by_default(screen_harness: _ScreenHarness) -> None:
    """利用者、アシスタント及び開発者の本文を既定で開く。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator('#sessions .session-item[data-engine="codex"]').click()

    for kind in ("user", "assistant", "developer"):
        section = page.locator(f"#detail .kind-{kind}").first
        await section.wait_for(state="visible")
        assert await section.evaluate("element => element.open")


@pytest.mark.asyncio
async def test_session_detail_toolbar_scrolls_with_content(screen_harness: _ScreenHarness) -> None:
    """狭い画面では右ペイン全体がスクロールし、タイトルとトークン表示も本文とともに移動する。"""
    page = screen_harness.page
    await page.set_viewport_size({"width": 900, "height": 360})
    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator('#sessions .session-item[data-engine="claude"]').click()
    await page.locator("#detail .event").first.wait_for(state="visible")
    await page.locator("#detail").evaluate(
        "element => { const spacer = document.createElement('div'); spacer.style.height = '600px'; element.append(spacer); }"
    )
    toolbar = page.locator("#screen-sessions main .toolbar")
    before = await toolbar.bounding_box()
    assert before is not None

    scroll_top = await page.locator("#screen-sessions main").evaluate(
        "element => { element.scrollTop = 120; return element.scrollTop; }"
    )
    after = await toolbar.bounding_box()

    assert scroll_top > 0
    assert after is not None
    assert after["y"] < before["y"]


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
async def test_plan_and_session_drawers_share_the_768px_boundary(screen_harness: _ScreenHarness) -> None:
    """700・701・768pxでは一覧をメニューで開き、769pxから通常配置へ戻す。"""
    page = screen_harness.page
    for width in (700, 701, 768, 769):
        mobile = width <= 768
        await page.set_viewport_size({"width": width, "height": 800})

        await page.goto(screen_harness.base_url + "/plans")
        await page.locator("#files .file").first.wait_for(state="visible")
        assert not await page.locator("#screen-plans").evaluate("element => element.classList.contains('drawer-open')")
        if mobile:
            await page.locator("#plans-menu-btn").click()
            assert await page.locator("#plans-menu-btn").get_attribute("aria-expanded") == "true"
            assert await page.locator("#copy-btn .short-label").is_visible()
            assert not await page.locator("#copy-btn .wide-label").is_visible()
            assert (
                await page.locator("#screen-plans main .toolbar").evaluate("element => getComputedStyle(element).flexWrap")
                == "nowrap"
            )
            await page.locator("#files .file").first.click()
            await page.locator("#preview h1").wait_for(state="visible")
            assert not await page.locator("#screen-plans").evaluate("element => element.classList.contains('drawer-open')")
        else:
            await page.locator("#preview h1").wait_for(state="visible")

        await page.goto(screen_harness.base_url + "/sessions")
        await page.locator("#sessions .session-item").first.wait_for(state="visible")
        assert not await page.locator("#screen-sessions").evaluate("element => element.classList.contains('drawer-open')")
        if mobile:
            await page.locator("#sessions-menu-btn").click()
        await page.locator("#sessions .session-item").first.click()
        await page.locator("#detail .event").first.wait_for(state="visible")
        assert not await page.locator("#screen-sessions").evaluate("element => element.classList.contains('drawer-open')")


@pytest.mark.asyncio
async def test_subagent_records_open_from_the_parent_detail(screen_harness: _ScreenHarness) -> None:
    """親セッションの詳細からサブエージェント記録を開いて呼び出し元へ戻り、記録本体が無い項目は選択できない表示とする。"""
    harness = screen_harness
    await harness.page.goto(harness.base_url + "/sessions")
    await harness.page.locator('#sessions .session-item[data-engine="claude"]').click()

    items = harness.page.locator(".subagent-item")
    await items.first.wait_for(state="visible")
    assert await items.count() == 2
    assert "所在の調査" in await items.nth(0).inner_text()
    assert "設計の検討" in await items.nth(1).inner_text()
    # 記録本体が残っていない項目は開けないため選択できない。
    assert not await items.nth(0).is_disabled()
    assert await items.nth(1).is_disabled()
    # 起動の深さを字下げで表す。
    offsets = [
        await items.nth(index).evaluate("(element) => parseFloat(getComputedStyle(element).paddingLeft)") for index in (0, 1)
    ]
    assert offsets[1] > offsets[0]

    # 呼び出し元の記録は左ペインの一覧から選び直せるが、サブエージェントの記録は一覧に現れないため戻る操作を置く。
    assert await harness.page.locator("#detail .detail-back").count() == 0
    await items.nth(0).click()
    await harness.page.locator("#detail .event").first.wait_for(state="visible")
    assert "サブエージェントの発話" in await harness.page.locator("#detail").inner_text()

    await harness.page.locator("#detail .detail-back").click()
    await harness.page.locator("#detail .kind-thinking").wait_for(state="visible")
    assert "Claudeの発話" in await harness.page.locator("#detail").inner_text()
    assert await harness.page.locator("#detail .detail-back").count() == 0


@pytest.mark.asyncio
async def test_remote_subagents_are_shown_or_reported_as_unavailable(
    remote_sessions_harness: _RemoteSessionsHarness,
) -> None:
    """リモートホストのセッションでもサブエージェントを表示し、一覧を返さないホストでは取得不能を示す。"""
    harness = remote_sessions_harness
    await harness.page.goto(harness.base_url + "/sessions")
    await harness.page.locator("#sessions .session-item").nth(1).wait_for(state="visible")

    await harness.page.locator(f'#sessions .session-item[data-host="{_NEW_REMOTE_HOST}"]').click()
    await harness.page.locator(".subagent-item").first.wait_for(state="visible")
    assert "リモートの調査" in await harness.page.locator("#detail").inner_text()
    assert await harness.page.locator("#detail .unavailable").count() == 0

    # 一覧を返さない版のヘルパーが動くホストでは、サブエージェントが無い場合と区別して取得不能を示す。
    await harness.page.locator(f'#sessions .session-item[data-host="{_LEGACY_REMOTE_HOST}"]').click()
    await harness.page.locator("#detail .unavailable").first.wait_for(state="visible")
    assert "サブエージェントの一覧を取得できません" in await harness.page.locator("#detail").inner_text()
    assert await harness.page.locator(".subagent-item").count() == 0


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
async def test_attached_plan_navigation_is_symmetric(screen_harness: _ScreenHarness) -> None:
    """左一覧に付属ファイルを表示せず、右ペインから同じstemの5ファイルを相互に開ける。"""
    harness = screen_harness
    (harness.root / "plan.detail.md").write_text("# 詳細ページ\n", encoding="utf-8")
    (harness.root / "plan.bugs.md").write_text("# バグページ\n", encoding="utf-8")
    review_row = '"1"\t"implementation-review"\t"a.py:1"\t"指摘"\t"要"\t"対応済み"\t""\n'
    (harness.root / "plan.plan-review.tsv").write_text(review_row, encoding="utf-8")
    (harness.root / "plan.exec-review.tsv").write_text(review_row, encoding="utf-8")
    await harness.page.goto(harness.base_url + "/plans")

    await harness.page.get_by_role("heading", name="初回").wait_for(state="visible")
    for attached in ("plan.detail.md", "plan.bugs.md", "plan.plan-review.tsv", "plan.exec-review.tsv"):
        assert await harness.page.locator("#files").get_by_text(attached, exact=True).count() == 0
    detail_link = harness.page.locator('a[data-plan-path="plan.detail.md"]')
    assert await detail_link.inner_text() == "詳細"
    detail_href = await detail_link.get_attribute("href")
    assert detail_href is not None
    assert urllib.parse.parse_qs(urllib.parse.urlsplit(detail_href).query)["path"] == ["plan.detail.md"]

    await harness.page.locator('a[data-plan-path="plan.detail.md"]').click()
    await harness.page.get_by_role("heading", name="詳細ページ").wait_for(state="visible")
    assert await harness.page.title() == "計画ファイル - atk serve"

    await harness.page.locator('a[data-plan-path="plan.bugs.md"]').click()
    await harness.page.get_by_role("heading", name="バグページ").wait_for(state="visible")
    assert await harness.page.title() == "計画ファイル - atk serve"

    await harness.page.locator('a[data-plan-path="plan.plan-review.tsv"]').click()
    await harness.page.get_by_role("columnheader", name="ラウンド").wait_for(state="visible")
    await harness.page.get_by_role("cell", name="implementation-review").wait_for(state="visible")
    assert await harness.page.title() == "計画ファイル - atk serve"

    await harness.page.locator('a[data-plan-path="plan.exec-review.tsv"]').click()
    await harness.page.get_by_role("columnheader", name="対応不要理由").wait_for(state="visible")
    assert await harness.page.title() == "計画ファイル - atk serve"

    await harness.page.locator('a[data-plan-path="plan.md"]').click()
    await harness.page.get_by_role("heading", name="初回").wait_for(state="visible")
    assert await harness.page.title() == "計画ファイル - atk serve"


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
    # ウィンドウのフォーカス復帰と同じ経路で強制再同期を起動する。
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


@pytest.mark.asyncio
async def test_session_tree_expands_one_branch_and_retains_keyboard_focus(screen_harness: _ScreenHarness) -> None:
    """親の展開操作で子だけを表示し、子の選択後もフォーカスを維持する。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    rows = page.locator("#sessions .session-tree-row")
    await playwright.async_api.expect(rows).to_have_count(2)
    parent = rows.filter(has=page.locator('.session-item[data-engine="claude"]'))
    toggle = parent.locator(".session-tree-toggle")
    await toggle.focus()
    await page.keyboard.press("Enter")
    await playwright.async_api.expect(toggle).to_have_attribute("aria-expanded", "true")
    await playwright.async_api.expect(rows).to_have_count(3)
    child = rows.filter(has=page.locator('.session-item[data-path$="agent-first.jsonl"]'))
    await playwright.async_api.expect(child).to_have_attribute("aria-level", "2")
    child_button = child.locator(".session-item")
    await child_button.focus()
    await page.keyboard.press("Enter")
    await playwright.async_api.expect(child_button).to_be_focused()
    await playwright.async_api.expect(child_button).to_have_attribute("aria-current", "true")
    await toggle.focus()
    await page.keyboard.press("Enter")
    await playwright.async_api.expect(toggle).to_have_attribute("aria-expanded", "false")
    await playwright.async_api.expect(rows).to_have_count(2)


@pytest.mark.asyncio
async def test_session_tree_aligns_rows_without_children(screen_harness: _ScreenHarness) -> None:
    """子の無い行も開閉欄の幅を持ち、操作できない「−」を表示して項目の開始位置を子のある行と揃える。"""
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")
    rows = page.locator("#sessions .session-tree-row")
    await playwright.async_api.expect(rows).to_have_count(2)
    parent = rows.filter(has=page.locator(".session-tree-toggle[aria-expanded]"))
    leaf = rows.filter(has=page.locator(".session-tree-leaf"))
    await playwright.async_api.expect(parent).to_have_count(1)
    await playwright.async_api.expect(leaf).to_have_count(1)
    marker = leaf.locator(".session-tree-leaf")
    await playwright.async_api.expect(marker).to_have_text("−")
    await playwright.async_api.expect(marker).to_have_attribute("aria-hidden", "true")
    assert await marker.evaluate("(element) => element.tagName") == "SPAN"
    # 展開済みの開閉ボタンの「−」と、子の無い行の「−」を表示から見分けられる。
    marker_opacity = await marker.evaluate("(element) => getComputedStyle(element).opacity")
    parent_toggle = parent.locator(".session-tree-toggle")
    await parent_toggle.click()
    await playwright.async_api.expect(parent_toggle).to_have_text("−")
    toggle_opacity = await parent_toggle.evaluate("(element) => getComputedStyle(element).opacity")
    assert float(marker_opacity) < float(toggle_opacity)
    await parent_toggle.click()
    assert await leaf.locator("button.session-tree-toggle").count() == 0
    parent_box = await parent.locator(".session-item").bounding_box()
    leaf_box = await leaf.locator(".session-item").bounding_box()
    assert parent_box is not None
    assert leaf_box is not None
    assert parent_box["x"] == leaf_box["x"]
    # Tab移動は子のある行の開閉ボタンと項目にだけ止まり、子の無い行の「−」には止まらない。
    await leaf.locator(".session-item").focus()
    await page.keyboard.press("Shift+Tab")
    focused_class = await page.evaluate("() => document.activeElement.className")
    assert "session-tree-leaf" not in focused_class


@pytest.mark.asyncio
async def test_mobile_drawers_labels_titles_and_reduced_motion(screen_harness: _ScreenHarness) -> None:
    """狭幅の一覧は閉じた状態でTab対象から外れ、開閉と画面切替の焦点を保つ。"""
    page = screen_harness.page
    await page.set_viewport_size({"width": 320, "height": 720})
    await page.emulate_media(reduced_motion="reduce")
    await page.goto(screen_harness.base_url + "/plans")
    for name, heading in (("plans", "計画ファイル"), ("sessions", "セッション")):
        if name == "sessions":
            await page.locator('#screen-plans nav.app-nav a[href$="/sessions"]').click()
            await playwright.async_api.expect(page.locator("#screen-sessions h1")).to_be_focused()
        assert await page.title() == f"{heading} - atk serve"
        sidebar = page.locator(f"#{name}-sidebar")
        menu = page.locator(f"#{name}-menu-btn")
        search = page.locator(f"#{name}-filter")
        await playwright.async_api.expect(sidebar).to_have_attribute("inert", "")
        await playwright.async_api.expect(menu).to_have_attribute("aria-expanded", "false")
        assert await search.get_attribute("id") == await page.get_by_label(f"{heading}を検索").get_attribute("id")
        await menu.click()
        await playwright.async_api.expect(menu).to_have_attribute("aria-expanded", "true")
        await playwright.async_api.expect(search).to_be_focused()
        await page.keyboard.press("Escape")
        await playwright.async_api.expect(menu).to_be_focused()
        await playwright.async_api.expect(sidebar).to_have_attribute("inert", "")
        await playwright.async_api.expect(menu).to_have_attribute("aria-expanded", "false")
        await page.evaluate("() => window.scrollTo(0, 0)")
        duration = await menu.evaluate("element => getComputedStyle(element).transitionDuration")
        assert duration == "0s"


@pytest.mark.asyncio
async def test_work_item_filter_fit_order_and_computed_contrast(browser_harness: _BrowserHarness) -> None:
    """標準幅でフィルター値が収まり、操作順と文字・入力境界のコントラストを保つ。"""
    page = browser_harness.page
    await page.set_viewport_size({"width": 1280, "height": 800})
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    metrics = await page.evaluate("""() => {
      const select = document.querySelector('#state-filter');
      const label = document.querySelector('label[for="state-filter"]');
      const canvas = document.createElement('canvas');
      const ctx = canvas.getContext('2d');
      ctx.font = getComputedStyle(select).font;
      const valueWidth = ctx.measureText(select.selectedOptions[0].textContent).width;
      const rgb = value => value.match(/[\\d.]+/g).slice(0, 3).map(Number);
      const luminance = value => rgb(value).map(channel => {
        const v = channel / 255;
        return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
      }).reduce((sum, channel, index) => sum + channel * [0.2126, 0.7152, 0.0722][index], 0);
      const contrast = (first, second) => {
        const values = [luminance(first), luminance(second)].sort((a, b) => b - a);
        return (values[0] + 0.05) / (values[1] + 0.05);
      };
      const secondary = document.querySelector('#entry-count');
      const search = document.querySelector('#search-input');
      const row = document.querySelector('#entry-list .entry-select');
      return {
        fits: select.clientWidth >= valueWidth + 32,
        clientWidth: select.clientWidth,
        valueWidth,
        sidebarWidth: select.closest('.filters').clientWidth,
        labelAbove: label.getBoundingClientRect().bottom <= select.getBoundingClientRect().top,
        filterBeforeList: Boolean(search.compareDocumentPosition(row) & Node.DOCUMENT_POSITION_FOLLOWING),
        textContrast: contrast(getComputedStyle(secondary).color, getComputedStyle(secondary.closest('.card')).backgroundColor),
        borderContrast: contrast(getComputedStyle(select).borderTopColor, getComputedStyle(select).backgroundColor)
      };
    }""")
    assert metrics["fits"] and metrics["labelAbove"] and metrics["filterBeforeList"], metrics
    assert metrics["textContrast"] >= 4.5
    assert metrics["borderContrast"] >= 3


@pytest.mark.asyncio
async def test_changed_detail_inputs_confirm_discard_and_unchanged_input_closes(browser_harness: _BrowserHarness) -> None:
    """変更済みの編集・回答・コメント入力を閉じる際は破棄確認を表示し、取消時に入力を保持する。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    detail = page.get_by_role("dialog", name="詳細")

    await page.locator('.entry-select[data-key="inbox/awi.md"]').click()
    await detail.get_by_role("button", name="編集", exact=True).click()
    await page.keyboard.press("Escape")
    await playwright.async_api.expect(detail).to_be_hidden()

    for key, mode, field_id in (
        ("inbox/awi.md", "編集", "edit-content"),
        ("inbox/question.md", "回答", "answer-input"),
        ("inbox/awi.md", "ユーザーコメント", "user-comment-input"),
    ):
        await page.locator(f'.entry-select[data-key="{key}"]').click()
        await detail.get_by_role("button", name=mode, exact=True).click()
        field = detail.locator(f"#{field_id}")
        await field.fill((await field.input_value()) + "変更")
        async with page.expect_event("dialog") as confirmation:
            press = asyncio.create_task(page.keyboard.press("Escape"))
        prompt = await confirmation.value
        assert prompt.message == "変更した入力を破棄しますか？"
        await prompt.dismiss()
        await press
        await playwright.async_api.expect(detail).to_be_visible()
        assert (await field.input_value()).endswith("変更")
        async with page.expect_event("dialog") as confirmation:
            click = asyncio.create_task(detail.locator("#detail-close-button").click())
        await (await confirmation.value).accept()
        await click
        await playwright.async_api.expect(detail).to_be_hidden()


@pytest.mark.asyncio
async def test_plan_and_session_lists_expand_after_hundred_items(
    screen_harness: _ScreenHarness,
    tmp_path: Path,
) -> None:
    """50件は全件表示し、100件を超えた一覧だけスクロールに応じて追加描画する。"""
    page = screen_harness.page
    for index in range(49):
        (screen_harness.root / f"bulk-{index:03d}.md").write_text(f"# 計画 {index}\n", encoding="utf-8")
    await page.goto(screen_harness.base_url + "/plans")
    await playwright.async_api.expect(page.locator("#files .file")).to_have_count(50)
    for index in range(49, 110):
        (screen_harness.root / f"bulk-{index:03d}.md").write_text(f"# 計画 {index}\n", encoding="utf-8")
    await page.reload()
    await playwright.async_api.expect(page.locator("#files .file")).to_have_count(100)
    await page.locator("#files-sentinel").scroll_into_view_if_needed()
    await playwright.async_api.expect(page.locator("#files .file")).to_have_count(111)

    project = tmp_path / "claude" / "projects" / "bulk"
    project.mkdir(parents=True)
    for index in range(110):
        record = {"type": "user", "timestamp": "2026-09-02T00:00:00Z", "message": {"content": f"会話 {index}"}}
        (project / f"bulk-{index:03d}.jsonl").write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    await page.goto(screen_harness.base_url + "/sessions")
    await playwright.async_api.expect(page.locator("#sessions .session-item")).to_have_count(100)
    await page.locator("#sessions-sentinel").scroll_into_view_if_needed()
    await playwright.async_api.expect(page.locator("#sessions .session-item")).to_have_count(112)


def _append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """JSON Linesの記録へ行を追記する。記録が無ければ作成する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.writelines(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def _session_list_requests(harness: _ScreenHarness) -> int:
    return sum(1 for url in harness.requests if "/api/sessions/list" in url)


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_session_detail_follows_appended_events_and_keeps_view_state(
    screen_harness: _ScreenHarness,
    tmp_path: Path,
) -> None:
    """開いている記録への追記が右ペインへ現れ、開閉・表示件数・スクロール位置を保ち、一覧は再取得しない。"""
    page = screen_harness.page
    await page.set_viewport_size({"width": 1280, "height": 600})
    path = tmp_path / "claude" / "projects" / "-home-aki-long" / "long.jsonl"
    records: list[dict[str, Any]] = [
        {"type": "user", "timestamp": "2026-09-04T00:00:00Z", "cwd": "/home/aki/long", "message": {"content": "開始"}}
    ]
    for index in range(150):
        records.append(
            {
                "type": "assistant",
                "timestamp": "2026-09-04T00:00:01Z",
                "message": {"content": [{"type": "thinking", "thinking": f"思考{index}"}]},
            }
        )
    _append_jsonl(path, records)
    # 記録の作成による一覧の再取得の通知は、画面を開く前に配信を終えさせ、追記の検証へ混ぜない。
    await asyncio.sleep(1.5)
    await page.goto(screen_harness.base_url + "/sessions")
    await page.locator("#sessions .session-item", has_text="/home/aki/long").click()
    events = page.locator("#detail details.event")
    await playwright.async_api.expect(events).to_have_count(100)
    await page.get_by_role("button", name="さらに100件表示").click()
    await playwright.async_api.expect(events).to_have_count(151)
    # 最初から展開されるユーザー発話を閉じ、折りたたまれた思考を1件開く。
    await events.nth(0).locator("summary").click()
    await events.nth(120).locator("summary").click()
    await playwright.async_api.expect(events.nth(120)).to_have_attribute("open", "")
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

    await playwright.async_api.expect(events).to_have_count(152, timeout=5000)
    await playwright.async_api.expect(page.locator("#detail")).to_contain_text("追記されたイベント")
    assert not await events.nth(0).evaluate("element => element.open")
    assert await events.nth(120).evaluate("element => element.open")
    assert await page.evaluate("() => document.querySelector('#screen-sessions main').scrollTop") == scroll_top
    await asyncio.sleep(1.0)
    assert _session_list_requests(screen_harness) == list_requests

    # 末尾より上を読んでいる間に届いたイベントは件数で知らせ、その操作で末尾へ移る。
    notice = page.get_by_role("button", name="新しいイベントが1件あります。末尾へ移動")
    await playwright.async_api.expect(notice).to_be_visible()
    await notice.click()
    await playwright.async_api.expect(notice).to_be_hidden()
    assert await page.evaluate(_DETAIL_DISTANCE_FROM_END_JS) <= 1


# 右ペインのスクロール位置から末尾までの距離（px）を返す。
_DETAIL_DISTANCE_FROM_END_JS = (
    "() => { const main = document.querySelector('#screen-sessions main');"
    " return main.scrollHeight - main.scrollTop - main.clientHeight; }"
)


@pytest.mark.asyncio
async def test_session_detail_follows_the_tail_while_reading_the_latest_events(
    screen_harness: _ScreenHarness,
    tmp_path: Path,
) -> None:
    """末尾を読んでいる間の追記では新着の通知を表示せず、追記した末尾へ追従する。"""
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
    await asyncio.sleep(1.5)
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
    await playwright.async_api.expect(page.locator("#sessions-new-events-btn")).to_be_hidden()


@pytest.mark.asyncio
async def test_session_empty_state_distinguishes_records_without_user_messages(
    screen_harness: _ScreenHarness,
    tmp_path: Path,
) -> None:
    """発話を含む記録が1件も無いときは、記録の不在ではなく発話を含む記録が無いことと、自動で加わることを示す。"""
    shutil.rmtree(tmp_path / "claude")
    shutil.rmtree(tmp_path / "codex")
    _append_jsonl(tmp_path / "claude" / "projects" / "-home-aki-silent" / "silent.jsonl", [{"type": "permission-mode"}])
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/sessions")

    empty = page.locator("#sessions-empty")
    await playwright.async_api.expect(empty).to_be_visible()
    await playwright.async_api.expect(empty).to_contain_text("ユーザーの発話を含むセッション記録はまだありません")
    await playwright.async_api.expect(empty).to_contain_text("自動で一覧へ加わります")


@pytest.mark.asyncio
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


# 3画面のSSEの購読先と、再同期で取り直す一覧APIの対応。
_SCREEN_STREAMS = {
    "wi": ("/api/events", "/api/entries"),
    "plans": ("/api/plans/events", "/api/plans/files"),
    "sessions": ("/api/sessions/events", "/api/sessions/list"),
}


def _request_count(harness: _ScreenHarness, path: str) -> int:
    return sum(1 for url in harness.requests if urllib.parse.urlsplit(url).path == path)


async def _wait_until(predicate: Any, timeout: float = 10.0) -> bool:
    """条件が成立するまで短い間隔で待つ。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.05)
    return bool(predicate())


async def _open_all_screens(harness: _ScreenHarness) -> None:
    """3画面を初期化し、各画面のSSE購読と初期取得が済むまで待つ。"""
    await harness.page.goto(harness.base_url + "/sessions")
    await playwright.async_api.expect(harness.page.locator("#sessions .session-item")).to_have_count(2)
    assert await _wait_until(
        lambda: all(
            _request_count(harness, events) >= 1 and _request_count(harness, listing) >= 1
            for events, listing in _SCREEN_STREAMS.values()
        )
    )


@pytest.mark.asyncio
async def test_silent_sse_is_reconnected_and_lists_are_refetched(
    screen_harness: _ScreenHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """通知もheartbeatも届かない時間が閾値を超えると、3画面とも再接続して一覧を取り直す。"""
    monkeypatch.setattr(serve_app, "SSE_HEARTBEAT_SEC", 3600.0)
    monkeypatch.setattr(serve_app, "SSE_STALL_SEC", 1.0)
    await _open_all_screens(screen_harness)
    before = {
        name: (_request_count(screen_harness, stream), _request_count(screen_harness, listing))
        for name, (stream, listing) in _SCREEN_STREAMS.items()
    }

    for name, (events, listing) in _SCREEN_STREAMS.items():
        assert await _wait_until(
            lambda events=events, listing=listing, name=name: (
                _request_count(screen_harness, events) > before[name][0]
                and _request_count(screen_harness, listing) > before[name][1]
            )
        ), name


@pytest.mark.asyncio
async def test_regular_heartbeat_keeps_sse_without_refetching(
    screen_harness: _ScreenHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """heartbeatが届き続ける間は、閾値を超えて待っても再接続も一覧の再取得も起きない。"""
    monkeypatch.setattr(serve_app, "SSE_HEARTBEAT_SEC", 0.2)
    monkeypatch.setattr(serve_app, "SSE_STALL_SEC", 1.0)
    await _open_all_screens(screen_harness)
    await asyncio.sleep(0.5)
    before = {path: _request_count(screen_harness, path) for pair in _SCREEN_STREAMS.values() for path in pair}

    await asyncio.sleep(3.0)

    assert {path: _request_count(screen_harness, path) for path in before} == before


@pytest.mark.asyncio
async def test_visible_tab_and_bfcache_restore_resynchronize_screens(screen_harness: _ScreenHarness) -> None:
    """タブが表示へ戻るとWI画面とセッション画面が一覧を取り直し、bfcacheからの復帰では再接続する。"""
    page = screen_harness.page
    await _open_all_screens(screen_harness)
    await asyncio.sleep(0.5)
    lists = {name: _request_count(screen_harness, _SCREEN_STREAMS[name][1]) for name in ("wi", "sessions")}

    await page.evaluate(
        """() => {
            Object.defineProperty(document, "visibilityState", {value: "visible", configurable: true});
            document.dispatchEvent(new Event("visibilitychange"));
        }"""
    )

    for name, count in lists.items():
        assert await _wait_until(
            lambda name=name, count=count: _request_count(screen_harness, _SCREEN_STREAMS[name][1]) > count
        ), name

    events = {name: _request_count(screen_harness, stream) for name, (stream, _listing) in _SCREEN_STREAMS.items()}
    await page.evaluate(
        """() => {
            window.dispatchEvent(new PageTransitionEvent("pagehide", {persisted: true}));
            window.dispatchEvent(new PageTransitionEvent("pageshow", {persisted: true}));
        }"""
    )

    for name, count in events.items():
        assert await _wait_until(
            lambda name=name, count=count: _request_count(screen_harness, _SCREEN_STREAMS[name][0]) > count
        ), name

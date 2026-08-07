"""`atk serve`の実ブラウザー統合テスト。"""

import asyncio
import contextlib
import dataclasses
import os
import socket
import threading
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import _atk_serve_app as serve_app
import _atk_serve_config as config
import _atk_serve_state as serve_state
import playwright.async_api
import pytest
import pytest_asyncio

_BROWSER_TEST_ENV = "AGENT_TOOLKIT_SERVE_BROWSER_TESTS"
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
        self.persist_mutations = False

    def sync(self) -> bool:
        """テストでは外部Git操作を行わない。"""
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

    def answer_tbd(
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
    long_body = "\n\n".join(f"段落{i} `inline-{i}`" for i in range(80))
    (inbox / "question.md").write_text(
        "---\ntype: tbd\ntarget_repo: example/repo\nquestion_type: choice\nchoices: A, B\n---\n\n"
        f"## 質問\n\n{long_body}\n\n```text\n折り返す長いコード {'x' * 240}\n```\n\n"
        "## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    (inbox / "feedback.md").write_text(
        "---\ntype: feedback\ntarget_repo: example/repo\nsource: browser\n---\n\n編集対象の本文\n",
        encoding="utf-8",
    )
    (inbox / "unknown.md").write_text("種別を判定できない本文\n", encoding="utf-8")
    (inbox / "empty.md").write_text(
        "---\ntype: feedback\ntarget_repo: example/repo\nsource: browser\n---\n",
        encoding="utf-8",
    )
    (inbox / "invalid.md").write_bytes(b"\xff")
    (adopted / "adopted.md").write_text(
        "---\ntype: feedback\ntarget_repo: adopted/repo\nsource: browser\n---\n\n採用済みの本文\n",
        encoding="utf-8",
    )


@pytest_asyncio.fixture(name="browser_harness")
async def _browser_harness_fixture(tmp_path: Path) -> AsyncGenerator[_BrowserHarness]:
    _write_entries(tmp_path)
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
    playwright_instance = None
    browser = None
    context = None
    try:
        await _wait_for_server(port)
        playwright_instance = await playwright.async_api.async_playwright().start()
        browser = await playwright_instance.chromium.launch()
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
        if browser is not None:
            await browser.close()
        if playwright_instance is not None:
            await playwright_instance.stop()
        shutdown.set()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task


async def _open_question(page: playwright.async_api.Page) -> playwright.async_api.Locator:
    row = page.locator('#entry-list .entry-select[data-kind="tbd"]')
    await row.click()
    await page.get_by_role("dialog", name="詳細").wait_for(state="visible")
    return row


@pytest.mark.asyncio
async def test_responsive_layout_dialog_scroll_and_markdown(browser_harness: _BrowserHarness) -> None:
    """代表3画面幅で横overflow、固定領域、タッチ寸法、Markdown表示を検証する。"""
    page = browser_harness.page
    await page.goto(browser_harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")

    for width, height, columns_visible in [(390, 844, False), (768, 1024, False), (1280, 800, True)]:
        await page.set_viewport_size({"width": width, "height": height})
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert await page.locator(".entry-columns").is_visible() is columns_visible
        row = await _open_question(page)
        dialog = page.get_by_role("dialog", name="詳細")
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
            header_box = await page.locator(".app-header").bounding_box()
            assert header_box is not None
            assert header_box["height"] < 150
        await page.keyboard.press("Escape")
        await playwright.async_api.expect(row).to_be_focused()


@pytest.mark.asyncio
async def test_accessible_workflows_filters_warnings_and_sse_status(browser_harness: _BrowserHarness) -> None:
    """回答・削除フォーカス、条件依存、警告、利用者起点だけの件数通知を検証する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")

    warning = page.get_by_role("alert").filter(has_text="invalid.md")
    await warning.wait_for(state="visible")
    assert "UTF-8として読み取れません" in await warning.inner_text()
    assert await page.locator('#entry-list .entry-select[data-kind="unknown"]').count() == 1

    feedback_row = page.locator('#entry-list .entry-select[data-kind="feedback"]').filter(has_text="feedback.md")
    assert await feedback_row.locator(".status-cell").text_content() == "フィードバック未処理"
    time_text = await feedback_row.locator(".time-cell").text_content()
    assert time_text is not None
    assert not time_text.startswith("—")

    empty_row = page.locator("#entry-list .entry-select").filter(has_text="empty.md")
    await empty_row.click()
    empty_dialog = page.get_by_role("dialog", name="詳細")
    assert await empty_dialog.locator("#detail-content").inner_html() == ""
    assert await empty_dialog.locator("#detail-content table.frontmatter").count() == 0
    await page.keyboard.press("Escape")

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
    await playwright.async_api.expect(dialog.locator("#detail-dialog-body")).to_be_focused()
    assert harness.operations.answer_calls == 1
    await page.keyboard.press("Escape")
    await playwright.async_api.expect(row).to_be_focused()

    await feedback_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="編集", exact=True).click()
    edit_input = detail.locator("#edit-content")
    await edit_input.fill((await edit_input.input_value()) + "\n追記")
    await detail.get_by_role("button", name="保存", exact=True).click()
    await playwright.async_api.expect(detail.locator("#detail-dialog-body")).to_be_focused()

    await detail.get_by_role("button", name="削除").click()
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    await delete_dialog.wait_for(state="visible")
    await playwright.async_api.expect(delete_dialog.get_by_role("button", name="閉じる")).to_be_focused()
    await page.keyboard.press("Escape")
    assert await page.get_by_role("dialog", name="詳細").is_visible()
    await page.keyboard.press("Escape")

    await page.locator("#kind-filter").select_option("feedback")
    await playwright.async_api.expect(page.locator("#answer-filter")).to_be_disabled()
    assert await page.locator("#answer-filter").input_value() == "all"
    await page.locator("#source-filter").fill("browser")
    await page.locator("#source-empty-filter").check()
    await playwright.async_api.expect(page.locator("#source-filter")).to_be_disabled()
    assert await page.locator("#source-filter").input_value() == ""
    await page.locator("#clear-filters-button").click()

    await page.locator("#target-filter").select_option("example/repo")
    await page.locator("#state-filter").select_option("adopted")
    await playwright.async_api.expect(page.locator("#target-filter")).to_have_value("")
    assert await page.locator("#target-filter option").all_text_contents() == ["すべて", "adopted/repo"]
    await page.locator("#entry-list .entry-select").filter(has_text="adopted.md").wait_for(state="visible")

    await page.locator("#clear-filters-button").click()
    await page.locator("#search-input").fill("編集対象")
    await playwright.async_api.expect(page.locator("#result-status")).to_have_text("1件を表示")
    status_before_sse = await page.locator("#result-status").text_content()
    (harness.root / "inbox" / "sse.md").write_text(
        "---\ntype: feedback\ntarget_repo: sse/repo\nsource: browser\n---\n\n編集対象の外部追加\n",
        encoding="utf-8",
    )
    harness.current_state.publish()
    await page.locator("#entry-list .entry-select").filter(has_text="sse.md").wait_for(state="visible")
    await playwright.async_api.expect(page.locator('#target-filter option[value="sse/repo"]')).to_have_count(1)
    assert await page.locator("#result-status").text_content() == status_before_sse


@pytest.mark.asyncio
async def test_pending_edit_and_closed_delete_route_results(browser_harness: _BrowserHarness) -> None:
    """未解決更新の二重送信、入力固定、処理中閉鎖、完了結果の配送先を検証する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")

    feedback_row = page.locator('#entry-list .entry-select[data-kind="feedback"]').filter(has_text="feedback.md")
    await feedback_row.click()
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

    await feedback_row.click()
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
    await detail.get_by_role("status").filter(has_text="削除しました").wait_for(state="visible")
    assert harness.operations.remove_calls == 1


@pytest.mark.asyncio
async def test_sse_refreshes_open_detail_preserves_input_and_closes_missing_entry(
    browser_harness: _BrowserHarness,
) -> None:
    """SSE更新時の詳細再取得、入力保持、消失時の閉鎖と復帰先を検証する。"""
    harness = browser_harness
    page = harness.page
    feedback_path = harness.root / "inbox" / "feedback.md"
    await page.goto(harness.base_url + "/")
    feedback_row = page.locator('#entry-list .entry-select[data-kind="feedback"]').filter(has_text="feedback.md")
    await feedback_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.wait_for(state="visible")

    feedback_path.write_text(
        "---\ntype: feedback\ntarget_repo: example/repo\nsource: browser\n---\n\n外部更新後の本文\n",
        encoding="utf-8",
    )
    harness.current_state.publish()
    await playwright.async_api.expect(detail.locator("#detail-content")).to_contain_text("外部更新後の本文")

    await detail.get_by_role("button", name="編集", exact=True).click()
    edit_input = detail.locator("#edit-content")
    await edit_input.fill("利用者の未保存本文")
    feedback_path.write_text(
        "---\ntype: feedback\ntarget_repo: example/repo\nsource: browser\n---\n\n編集中の外部更新\n",
        encoding="utf-8",
    )
    harness.current_state.publish()
    await detail.get_by_role("alert").filter(has_text="外部で項目が更新されました").wait_for(state="visible")
    assert await edit_input.input_value() == "利用者の未保存本文"
    await playwright.async_api.expect(detail.get_by_role("button", name="保存", exact=True)).to_be_disabled()

    feedback_path.unlink()
    harness.current_state.publish()
    await detail.wait_for(state="hidden")
    await playwright.async_api.expect(page.locator("#entry-list .entry-select").first).to_be_focused()


@pytest.mark.asyncio
async def test_detail_focus_falls_back_after_answer_filter_and_delete(
    browser_harness: _BrowserHarness,
) -> None:
    """回答条件からの除外、削除、0件化後も操作可能な一覧要素へ戻る。"""
    harness = browser_harness
    page = harness.page
    harness.operations.enable_file_mutations()
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_text("自動更新に接続済み")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")

    await page.locator("#answer-filter").select_option("no")
    question_row = page.locator('#entry-list .entry-select[data-kind="tbd"]')
    await question_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="回答", exact=True).click()
    await detail.locator("#answer-input").fill("回答済みにする")
    await detail.get_by_role("button", name="回答を保存").click()
    await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(0)
    await page.keyboard.press("Escape")
    await playwright.async_api.expect(page.locator("#empty-clear-button")).to_be_focused()

    await page.locator("#empty-clear-button").click()
    feedback_row = page.locator('#entry-list .entry-select[data-kind="feedback"]').filter(has_text="feedback.md")
    await feedback_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await detail.get_by_role("button", name="削除").click()
    delete_dialog = page.get_by_role("dialog", name="削除の確認")
    await delete_dialog.get_by_role("button", name="削除する").click()
    await playwright.async_api.expect(feedback_row).to_have_count(0)
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
    inbox_same.write_text("---\ntype: feedback\n---\n\n未処理の同名本文\n", encoding="utf-8")
    processing_same.write_text("---\ntype: feedback\n---\n\n処理中の同名本文\n", encoding="utf-8")
    moving.write_text("---\ntype: feedback\n---\n\n移動対象\n", encoding="utf-8")
    await page.goto(harness.base_url + "/")
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_text("自動更新に接続済み")

    processing_row = page.locator('.entry-select[data-key="processing/same.md"]')
    await processing_row.click()
    detail = page.get_by_role("dialog", name="詳細")
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("処理中")
    await playwright.async_api.expect(detail.locator("#detail-content")).to_contain_text("処理中の同名本文")
    harness.current_state.publish()
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("処理中")
    await playwright.async_api.expect(detail.locator("#detail-content")).to_contain_text("処理中の同名本文")

    await detail.get_by_role("button", name="編集", exact=True).click()
    edit_input = detail.locator("#edit-content")
    await edit_input.fill((await edit_input.input_value()) + "\n処理中だけを保存")
    async with page.expect_request(
        lambda request: request.method == "PUT" and request.url.endswith("/api/entries/processing/same.md")
    ):
        await detail.get_by_role("button", name="保存", exact=True).click()
    await detail.get_by_role("status").filter(has_text="保存しました").wait_for(state="visible")
    await page.keyboard.press("Escape")

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
    await playwright.async_api.expect(detail.locator("#detail-state")).to_have_text("処理中")
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
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_text("自動更新に接続済み")

    feedback_row = page.locator('.entry-select[data-key="inbox/feedback.md"]')
    await feedback_row.click()
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

    await page.route("**/api/entries/inbox/feedback.md", delay_edit_response)
    await detail.get_by_role("button", name="保存", exact=True).click()
    await asyncio.wait_for(edit_finished.wait(), timeout=5)
    harness.current_state.publish()
    try:
        await detail.get_by_role("alert").filter(has_text="外部で項目が更新されました").wait_for(state="visible", timeout=4_000)
    finally:
        release_edit_response.set()
    await detail.get_by_role("status").filter(has_text="保存しました").wait_for(state="visible")
    await playwright.async_api.expect(detail.locator("#detail-alert")).to_be_hidden()
    await page.unroute("**/api/entries/inbox/feedback.md", delay_edit_response)
    async with page.expect_response(
        lambda response: response.request.method == "GET" and response.url.endswith("/api/entries/inbox/feedback.md")
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
    await detail.get_by_role("status").filter(has_text="回答しました").wait_for(state="visible")
    await playwright.async_api.expect(detail.locator("#detail-alert")).to_be_hidden()
    await page.unroute("**/api/entries/answer", delay_answer_response)
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
            "---\ntype: feedback\n---\n\n削除順序の検証\n",
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
    await playwright.async_api.expect(page.locator("#result-status")).to_have_text("4件を表示")
    await playwright.async_api.expect(page.locator('#target-filter option[value="example/repo"]')).to_have_count(1)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    request_count = 0

    async def delay_first_repo_request(route: playwright.async_api.Route) -> None:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            first_started.set()
            await release_first.wait()
        await route.continue_()

    await page.route("**/api/repos?status=active", delay_first_repo_request)
    await page.locator("#result-status").evaluate("element => { element.textContent = '変更前の通知'; }")
    await page.locator("#kind-filter").evaluate("element => { element.value = 'feedback'; }")
    await page.evaluate("void handleFilterChange({reloadRepos: true})")
    await asyncio.wait_for(first_started.wait(), timeout=5)
    try:
        harness.current_state.publish()
        await playwright.async_api.expect(page.locator("#entry-list .entry-select[data-kind=feedback]")).to_have_count(2)
    finally:
        release_first.set()
    await playwright.async_api.expect(page.locator("#result-status")).to_have_text("2件を表示")
    assert request_count == 2


@pytest.mark.asyncio
async def test_answer_and_delete_target_the_visible_state(browser_harness: _BrowserHarness) -> None:
    """同名項目が両状態にあっても、回答と削除は表示中の複合キーへ作用する。"""
    harness = browser_harness
    page = harness.page
    processing = harness.root / "processing"
    processing.mkdir(exist_ok=True)
    marker = "<!-- ユーザーはこの行以降に回答を追記する -->"
    tbd_content = (
        "---\ntype: tbd\ntarget_repo: example/repo\nquestion_type: free-form\n---\n\n"
        f"## 質問\n\n状態を指定しますか？\n\n## 回答\n\n{marker}\n"
    )
    feedback_content = "---\ntype: feedback\ntarget_repo: example/repo\n---\n\n状態付き削除\n"
    for state_name in ("inbox", "processing"):
        (harness.root / state_name / "answer-same.md").write_text(tbd_content, encoding="utf-8")
        (harness.root / state_name / "remove-same.md").write_text(feedback_content, encoding="utf-8")
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
    await detail.get_by_role("status").filter(has_text="回答しました").wait_for(state="visible")
    assert (harness.root / "inbox/answer-same.md").read_text(encoding="utf-8").endswith("未処理側だけへの回答\n")
    assert (processing / "answer-same.md").read_text(encoding="utf-8") == tbd_content
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
    assert remove_payload["expected_content"] == feedback_content
    await delete_dialog.wait_for(state="hidden")
    assert not (harness.root / "inbox/remove-same.md").exists()
    assert (processing / "remove-same.md").read_text(encoding="utf-8") == feedback_content


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
        assert await field.input_value() == user_input
        await playwright.async_api.expect(detail.get_by_role("button", name=submit_name, exact=True)).to_be_disabled()
        await page.unroute(mutation_pattern, fail_mutation)
        await page.keyboard.press("Escape")

    await exercise(
        "inbox/feedback.md",
        "**/api/entries/inbox/feedback.md",
        "保存",
        "#edit-content",
        "---\ntype: feedback\ntarget_repo: example/repo\n---\n\n保存中の外部更新\n",
    )
    await exercise(
        "inbox/question.md",
        "**/api/entries/answer",
        "回答を保存",
        "#answer-input",
        "---\ntype: tbd\ntarget_repo: example/repo\nquestion_type: free-form\n---\n\n"
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
    original = "---\ntype: feedback\ntarget_repo: example/old\n---\n\n変更前の要約\n"
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
        "---\ntype: feedback\ntarget_repo: example/new\n---\n\n変更後の要約\n",
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

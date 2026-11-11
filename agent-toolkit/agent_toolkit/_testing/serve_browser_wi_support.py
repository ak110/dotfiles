"""serveのbrowser_wi試験が共有する入力と補助処理。"""

import asyncio
import dataclasses
import datetime
import functools
import pathlib
import threading
import urllib.parse

import playwright.async_api
import pytest

from agent_toolkit import atk
from agent_toolkit._atk.serve import entry_index, wi_operations
from agent_toolkit._atk.serve.browser_support_test import _BrowserHarness
from agent_toolkit._atk.wi import entries as wi_entries
from agent_toolkit._atk.wi import frontmatter as wi_frontmatter
from agent_toolkit._testing import git_repository


def _entry_path(root: pathlib.Path, filename: str) -> pathlib.Path:
    """画面試験用WIの固定パスを解決し、保存先を用意する。"""
    path = wi_entries.entry_path(root, filename, fallback_date=datetime.date(2026, 10, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _state_text(content: str, state: str) -> str:
    """外部操作を再現する入力の状態をfrontmatterへ設定する。"""
    parsed = wi_frontmatter.parse_frontmatter(content)
    metadata, body = parsed if parsed is not None else ({}, content)
    metadata["state"] = state
    return wi_frontmatter.serialize_frontmatter(metadata, body)


def _set_entry_state(path: pathlib.Path, state: str) -> None:
    """同じパスを保った外部状態変更を保存する。"""
    path.write_text(_state_text(path.read_text(encoding="utf-8"), state), encoding="utf-8")


_POST_APPROVAL_UWI = (
    "---\ntype: uwi\nstate: inbox\ntarget_repo: example/repo\nquestion_type: choice\n"
    "choices: その対応で問題無い, 問題がある\n---\n\n"
    "## 質問\n\nこの対応で進めた。問題無いか？\n\n## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n"
)


_FREE_FORM_UWI = (
    "---\ntype: uwi\nstate: inbox\ntarget_repo: example/repo\nquestion_type: free-form\n---\n\n"
    "## 質問\n\n方針はどうするか？\n\n## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n"
)


_AGENT_AWI = "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\nsource: session-review\n---\n\n変更操作の対象\n"


_SELF_WRITE_WARNINGS = ("外部で項目が更新されました", "削除されたため表示できません")


@dataclasses.dataclass(frozen=True)
class _DetailMutationCase:
    """詳細ダイアログから送信する変更操作1件の手順と期待結果。"""

    state: str
    content: str
    mode_button: str | None
    field: tuple[str, str] | None
    submit: str
    route: str
    method: str
    message: str
    final_state: str | None


_DETAIL_MUTATION_CASES = {
    "answer-auto-adopt": _DetailMutationCase(
        "inbox",
        _POST_APPROVAL_UWI,
        None,
        ("#answer-input", "その対応で問題無い"),
        "#save-answer-button",
        "**/api/entries/answer",
        "POST",
        "の回答を保存し、採用済み（adopted）へ移しました。",
        "adopted",
    ),
    "answer": _DetailMutationCase(
        "inbox",
        _FREE_FORM_UWI,
        None,
        ("#answer-input", "自由記述の回答"),
        "#save-answer-button",
        "**/api/entries/answer",
        "POST",
        "へ回答しました。",
        "inbox",
    ),
    "save": _DetailMutationCase(
        "inbox",
        _AGENT_AWI,
        "#edit-button",
        ("#edit-content", _AGENT_AWI + "\n保存追記\n"),
        "#save-entry-button",
        "**/api/entries/target.md",
        "PUT",
        "を保存しました。",
        "inbox",
    ),
    "user-comment": _DetailMutationCase(
        "inbox",
        _AGENT_AWI,
        "#user-comment-button",
        ("#user-comment-input", "ユーザーのコメント"),
        "#save-user-comment-button",
        "**/api/entries/user-comment",
        "POST",
        "のユーザーコメントを保存しました。",
        "inbox",
    ),
    "adopt": _DetailMutationCase(
        "inbox",
        _AGENT_AWI,
        "#adopt-button",
        None,
        "#confirm-adopt-button",
        "**/api/entries/adopt",
        "POST",
        "を採用しました。",
        "adopted",
    ),
    "reject": _DetailMutationCase(
        "inbox",
        _AGENT_AWI,
        "#reject-button",
        None,
        "#confirm-reject-button",
        "**/api/entries/reject",
        "POST",
        "を却下しました。",
        "rejected",
    ),
    "hold": _DetailMutationCase(
        "inbox",
        _AGENT_AWI,
        None,
        None,
        "#hold-button",
        "**/api/entries/hold",
        "POST",
        "を保留しました。",
        "hold",
    ),
    "unhold": _DetailMutationCase(
        "hold",
        _AGENT_AWI,
        None,
        None,
        "#unhold-button",
        "**/api/entries/unhold",
        "POST",
        "を保留解除しました。",
        "inbox",
    ),
    "return-to-inbox": _DetailMutationCase(
        "adopted",
        _AGENT_AWI,
        None,
        None,
        "#return-to-inbox-button",
        "**/api/entries/return-to-inbox",
        "POST",
        "をinboxへ戻しました。",
        "inbox",
    ),
    "return-rejected-to-inbox": _DetailMutationCase(
        "rejected",
        _AGENT_AWI,
        None,
        None,
        "#return-to-inbox-button",
        "**/api/entries/return-to-inbox",
        "POST",
        "をinboxへ戻しました。",
        "inbox",
    ),
    "delete": _DetailMutationCase(
        "inbox",
        _AGENT_AWI,
        "#delete-button",
        None,
        "#delete-submit-button",
        "**/api/entries/remove",
        "POST",
        "を削除しました。",
        None,
    ),
}


async def _record_self_write_warnings(page: playwright.async_api.Page) -> None:
    """詳細と通知の表示へ、自分の書込みを外部更新とみなした表示が一度でも現れたかを記録する。"""
    await page.evaluate(
        """warnings => {
          window.__selfWriteWarnings = [];
          const check = () => {
            for (const id of ['detail-alert', 'detail-status', 'operation-notice-message', 'delete-alert']) {
              const text = document.getElementById(id)?.textContent || '';
              if (warnings.some(warning => text.includes(warning))) window.__selfWriteWarnings.push(text);
            }
          };
          new MutationObserver(check).observe(document.body, {subtree: true, childList: true, characterData: true});
        }""",
        list(_SELF_WRITE_WARNINGS),
    )


async def _saved_mutation_commit_failure(
    browser_harness: _BrowserHarness,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    operation: str,
    timing: str,
    *,
    low_height: bool = False,
) -> None:
    """実Gitとファイル監視を起動し、保存後のcommit失敗と復旧を確認する。"""
    harness = browser_harness
    root = harness.root / "commit-failure"
    monkeypatch.setattr(harness.operations, "private_notes", root)
    monkeypatch.setattr(harness.operations, "_entry_index", entry_index.EntryIndex(root))
    monkeypatch.setattr(harness.current_state, "root", root)
    for name in ("edit", "answer_uwi", "user_comment", "important", "transition", "add", "add_batch"):
        monkeypatch.setattr(
            harness.operations, name, functools.partial(getattr(wi_operations.Operations, name), harness.operations)
        )
    case = _DETAIL_MUTATION_CASES.get(operation)
    content = _state_text(case.content if case else _AGENT_AWI, "inbox")
    path = _entry_path(root, "target.md")
    path.write_text(content, encoding="utf-8")
    remote = git_repository.init_bare_repository(root.parent / "commit-failure-remote.git")
    git_repository.init_repository(root, initial_branch="main", origin=str(remote), commit_message="試験の初期状態")
    git_repository.run_git(root, "push", "--set-upstream", "origin", "main")
    before = git_repository.git_output(root, "rev-parse", "HEAD")
    hook = root / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    await harness.current_state.start_async(asyncio.get_running_loop())
    request.addfinalizer(harness.current_state.stop)
    page = harness.page
    await page.goto(harness.base_url + "/?filename=target.md")
    await playwright.async_api.expect(page.locator("#detail-dialog")).to_be_visible()
    await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
    await _record_self_write_warnings(page)
    if operation in {"create", "batch"}:
        await page.keyboard.press("Escape")
        await page.locator("#create-button").click()
        if operation == "batch":
            await page.locator("#create-kind").select_option("batch")
            await page.locator("#create-content").fill("### imported.md [inbox]\n" + content)
        else:
            await page.locator("#create-target").fill("github.com/example/repo")
            await page.locator("#create-content").fill("# 保存する新規要求\n\n新規本文")
        route_pattern = "**/api/entries/batch" if operation == "batch" else "**/api/entries"
        submit = "#create-submit-button"
    elif operation == "important":
        route_pattern = "**/api/entries/important"
        submit = "#important-button"
    else:
        assert case is not None
        if case.mode_button:
            await page.locator(case.mode_button).click()
        if case.field:
            await page.locator(case.field[0]).fill(case.field[1])
        route_pattern, submit = case.route, case.submit
    list_reloaded = asyncio.Event()
    mutation_finished = asyncio.Event()

    def observe(response: playwright.async_api.Response) -> None:
        if response.request.method == "GET" and urllib.parse.urlsplit(response.url).path == "/api/entries":
            list_reloaded.set()

    page.on("response", observe)
    if timing == "during":

        async def hold_response(route: playwright.async_api.Route) -> None:
            if route.request.method == "GET":
                await route.continue_()
                return
            list_reloaded.clear()
            response = await route.fetch()
            await asyncio.wait_for(list_reloaded.wait(), timeout=10)
            await route.fulfill(response=response)
            mutation_finished.set()

        await page.route(route_pattern, hold_response)
    list_reloaded.clear()
    async with page.expect_response(lambda response: response.request.method != "GET" and response.status == 500) as pending:
        await page.locator(submit).click()
    assert (await (await pending.value).json())["code"] == "sync_incomplete"
    await asyncio.wait_for(list_reloaded.wait(), timeout=10)
    if timing == "during":
        await asyncio.wait_for(mutation_finished.wait(), timeout=10)
    if operation in {"create", "batch"}:
        alert = page.locator("#create-alert")
        await playwright.async_api.expect(alert).to_contain_text("登録済み")
        await playwright.async_api.expect(alert).to_contain_text("重複")
        assert len(list(root.glob("**/*.md"))) == 2
        await playwright.async_api.expect(page.locator("#entry-list .entry-select")).to_have_count(2)
    elif operation == "delete":
        await playwright.async_api.expect(page.locator("#detail-dialog")).to_be_hidden()
        alert = page.locator("#operation-notice-message")
        await playwright.async_api.expect(alert).to_contain_text("削除済み")
        await playwright.async_api.expect(page.locator('.entry-select[data-key="target.md"]')).to_have_count(0)
        assert not path.exists()
    else:
        alert = page.locator("#detail-alert")
        await playwright.async_api.expect(alert).to_contain_text("保存")
        saved = path.read_text(encoding="utf-8")
        await playwright.async_api.expect(page.locator("#detail-content")).to_be_visible()
        if operation == "save":
            await playwright.async_api.expect(page.locator("#detail-content")).to_contain_text("保存追記")
        elif operation == "answer":
            await playwright.async_api.expect(page.locator("#detail-content")).to_contain_text("自由記述の回答")
        elif operation == "user-comment":
            await playwright.async_api.expect(page.locator("#detail-content")).to_contain_text("ユーザーのコメント")
        elif operation == "important":
            assert "important: true" in saved
            await playwright.async_api.expect(page.locator("#important-button")).to_contain_text("重要解除")
        else:
            assert case is not None
            await playwright.async_api.expect(page.locator("#detail-state")).to_have_text(f"awi / {case.final_state}")
    await playwright.async_api.expect(alert).to_contain_text("他ホストへは反映していない")
    await playwright.async_api.expect(alert).to_contain_text("atk wi commit")
    assert await page.evaluate("window.__selfWriteWarnings") == []
    assert git_repository.git_output(root, "rev-parse", "HEAD") == before
    assert git_repository.git_output(remote, "rev-parse", "main") == before
    if low_height:
        await alert.evaluate("e => e.scrollIntoView({block: 'start', behavior: 'instant'})")
        await page.screenshot(path=str(harness.root / f"{operation}-notice.png"))
        await _assert_detail_notice_and_controls_reachable(page, harness.root / f"{operation}-notice-end.png")
    hook.unlink()
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(root))
    with pytest.raises(SystemExit) as committed:
        atk.main(["wi", "commit"], home=harness.root)
    assert committed.value.code == 0
    assert git_repository.git_output(root, "rev-parse", "HEAD") != before
    assert git_repository.git_output(remote, "rev-parse", "main") == git_repository.git_output(root, "rev-parse", "HEAD")


async def _assert_detail_notice_and_controls_reachable(page: playwright.async_api.Page, notice_image: pathlib.Path) -> None:
    """通知の各文字と表示中の全ボタンが、スクロール後の可視領域へ入り覆われないことを確認する。"""
    assert await page.evaluate("""() => {
      const body = document.getElementById('detail-dialog-body').getBoundingClientRect();
      const footer = document.getElementById('detail-footer').getBoundingClientRect();
      return body.bottom <= footer.top;
    }""")
    # stickyな通知が表示領域より高いと、scrollHeightだけでは末尾の可読性を保証できない。
    await page.evaluate("""async () => {
      const dialog = document.getElementById('detail-dialog');
      const alert = document.getElementById('detail-alert');
      const walker = document.createTreeWalker(alert, NodeFilter.SHOW_TEXT);
      const nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      for (const node of nodes) {
        for (let index = 0; index < node.length; index++) {
          if (!node.textContent[index].trim()) continue;
          const range = document.createRange();
          range.setStart(node, index); range.setEnd(node, index + 1);
          const rect = range.getBoundingClientRect();
          const clip = dialog.getBoundingClientRect();
          dialog.scrollTo({top: dialog.scrollTop + rect.top - clip.top - 16, behavior: 'instant'});
          const visible = range.getBoundingClientRect();
          const hit = document.elementFromPoint(visible.x + visible.width / 2, visible.y + visible.height / 2);
          if (visible.top < clip.top || visible.bottom > clip.bottom || !alert.contains(hit)) {
            throw new Error(
              `通知の${index}文字目が読めません。` +
              '#detail-alertの配置と折り返し、#detail-dialogのスクロール位置、操作欄との重なりを確認してください。'
            );
          }
        }
      }
    }""")
    await page.screenshot(path=str(notice_image))
    buttons = page.locator("#detail-dialog button")
    required = {await button.get_attribute("id") for button in await buttons.all() if await button.is_visible()}
    visited = {"detail-close-button"}
    await page.locator("#detail-close-button").focus()
    for _ in range(len(required) + 3):
        await page.keyboard.press("Tab")
        focused = await page.evaluate("""() => {
          const element = document.activeElement;
          const rect = element.getBoundingClientRect();
          const clip = document.getElementById('detail-dialog').getBoundingClientRect();
          const hit = document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2);
          return {id: element.id, rect: rect.toJSON(), clip: clip.toJSON(), hit: hit?.id,
            visible: rect.top >= clip.top && rect.bottom <= clip.bottom && element.contains(hit)};
        }""")
        if focused["id"] in required:
            assert focused["visible"], focused
            visited.add(focused["id"])
            if focused["id"] == "edit-button":
                await page.screenshot(path=str(notice_image.with_name(notice_image.name.replace("notice-end", "controls"))))
    assert visited == required


async def _release_save_when_waiting(page: playwright.async_api.Page, release: threading.Event) -> None:
    """保存中の表示と前回通知の消去を観測してから保存を解放する。"""
    try:
        await playwright.async_api.expect(page.locator("#save-user-comment-button")).to_be_disabled()
        await playwright.async_api.expect(
            page.get_by_role("status").filter(has_text="ユーザーコメントを保存しました")
        ).to_be_hidden()
    finally:
        release.set()


async def _wait_and_close_operation_notice(page: playwright.async_api.Page, text: str) -> None:
    """操作の通知が表示されるまで待ち、成立した通知を閉じる。

    通知は自動で消えないため、閉じずに次の待機へ進むと残存した通知で待機が即座に成立する。
    待機のたびに閉じることで、次の待機がその操作で新たに表示された通知だけで成立する。
    """
    await page.get_by_role("status").filter(has_text=text).wait_for(state="visible")
    await page.locator("#operation-notice-close-button").click()
    await playwright.async_api.expect(page.locator("#operation-notice")).to_be_hidden()

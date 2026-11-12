"""serveのbrowser_wiに関するcreationの試験。"""

import re

import playwright.async_api
import pytest

from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _browser_harness_fixture,
    _BrowserHarness,
    _screen_harness_fixture,
)
from agent_toolkit._testing.serve_browser_wi_support import _entry_path

pytestmark = [
    pytest.mark.browser,
]


@pytest.mark.asyncio(loop_scope="session")
async def test_work_item_hostile_html_keeps_text_without_execution_or_external_requests(
    browser_harness: _BrowserHarness,
) -> None:
    """WIの悪意ある本文は文字として保持し、能動的なHTMLと外部通信を発生させない。"""
    harness = browser_harness
    hostile = (
        "安全な本文\n\n<script>window.__wiXss = 1</script>\n\n"
        '<img src="https://invalid.example/tracker" onerror="window.__wiXss = 2">\n\n'
        "[危険リンク](javascript:window.__wiXss=3)\n"
    )
    _entry_path(harness.root, "hostile.md").write_text(
        "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\n---\n\n" + hostile, encoding="utf-8"
    )
    page = harness.page
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))

    async def allow_local_only(route: playwright.async_api.Route) -> None:
        if route.request.url.startswith(harness.base_url + "/"):
            await route.continue_()
        else:
            await route.abort()

    await page.route("**/*", allow_local_only)
    await page.goto(harness.base_url + "/?filename=hostile.md")
    body = page.locator("#detail-content")
    await playwright.async_api.expect(body).to_contain_text("安全な本文")
    await playwright.async_api.expect(body).to_contain_text("<script>window.__wiXss = 1</script>")
    assert await body.locator("script, img, [onerror], [onload], a[href^='javascript:']").count() == 0
    assert await page.evaluate("window.__wiXss") is None
    assert any(url.endswith("/api/entries/hostile.md") for url in requests)
    assert all(url.startswith(harness.base_url + "/") for url in requests)


@pytest.mark.asyncio(loop_scope="session")
async def test_batch_input_error_shows_reason_and_fix_action(browser_harness: _BrowserHarness) -> None:
    """一括登録の形式エラーは、理由と入力を直す操作を登録ダイアログへ表示し、入力を保持する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")
    await page.get_by_role("button", name="新規追加").click()
    create_dialog = page.get_by_role("dialog", name="新規追加")
    await create_dialog.wait_for(state="visible")
    await create_dialog.locator("#create-kind").select_option("batch")
    invalid = "### broken.md [inbox]\n---\ntype: awi\n---\n\n対象リポジトリの無い本文\n"
    await create_dialog.locator("#create-content").fill(invalid)

    await create_dialog.get_by_role("button", name="追加").click()

    alert = create_dialog.get_by_role("alert").filter(has_text="項目を追加できませんでした")
    await playwright.async_api.expect(alert).to_be_visible()
    await playwright.async_api.expect(alert).to_contain_text("項目を追加できませんでした。")
    await playwright.async_api.expect(alert).to_contain_text("broken.md")
    await playwright.async_api.expect(alert).to_contain_text("次の操作: ")
    await playwright.async_api.expect(alert).to_contain_text("再投入する")
    await playwright.async_api.expect(create_dialog.locator("#create-content")).to_have_value(invalid)
    assert not _entry_path(harness.root, "broken.md").exists()


@pytest.mark.asyncio(loop_scope="session")
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
    await page.locator('.entry-select[data-key="imported.md"]').wait_for(state="visible")
    assert (_entry_path(harness.root, "imported.md")).read_text(encoding="utf-8") == (
        "---\ntarget_repo: batch/repo\ntype: awi\nstate: inbox\n---\n\n一括取り込みの本文\n"
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


@pytest.mark.asyncio(loop_scope="session")
async def test_create_dialog_auto_switches_show_format_to_batch(browser_harness: _BrowserHarness) -> None:
    """種別uwiのままshow形式の本文を送ると一括登録として取り込み、使わなかった入力欄を通知する。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-select").first.wait_for(state="visible")

    await page.get_by_role("button", name="新規追加").click()
    create_dialog = page.get_by_role("dialog", name="新規追加")
    await create_dialog.wait_for(state="visible")
    await create_dialog.locator("#create-kind").select_option("uwi")
    # 初期値は選択肢の入力が要らないはい／いいえとする。
    question_type = create_dialog.locator("#create-question-type")
    await playwright.async_api.expect(question_type).to_have_value("yes-no")
    assert await question_type.locator("option").evaluate_all("options => options.map(option => option.value)") == [
        "yes-no",
        "post-approval",
        "choice",
    ]
    await question_type.select_option("choice")
    await playwright.async_api.expect(create_dialog.locator("#choice-fields")).to_be_visible()
    await question_type.select_option("post-approval")
    await playwright.async_api.expect(create_dialog.locator("#choice-fields")).to_be_hidden()
    await question_type.select_option("yes-no")
    await create_dialog.locator("#create-target").fill("ignored/repo")
    show_text = (
        "## target_repo: batch/repo\n"
        "### auto-imported.md [inbox]\n---\ntarget_repo: batch/repo\ntype: awi\n---\n\n自動切替の本文  \n"
    )
    await create_dialog.locator("#create-content").fill(show_text)
    async with page.expect_request(
        lambda request: request.url.endswith("/api/entries") and request.method == "POST"
    ) as request_info:
        await create_dialog.get_by_role("button", name="追加").click()
    request = await request_info.value
    request_body = request.post_data_json
    assert isinstance(request_body, dict)
    assert request_body["raw_text"] == show_text
    await create_dialog.wait_for(state="hidden")
    notice = page.get_by_role("status").filter(has_text="一括登録として取り込みました")
    await notice.wait_for(state="visible")
    # 使わなかった入力欄はユーザーが入力したtarget-repoだけであり、初期値へ戻した回答形式は数えない。
    # 列挙の末尾まで一致を求め、回答形式などの余分な入力欄が後ろへ連結された通知を検出する。
    await playwright.async_api.expect(page.locator("#operation-notice-message")).to_have_text(
        re.compile(r"1件を取り込みました。.*使わなかった入力欄: target-repo$")
    )
    await page.locator('.entry-select[data-key="auto-imported.md"]').wait_for(state="visible")
    assert harness.operations.batch_calls[-1] == show_text
    assert (_entry_path(harness.root, "auto-imported.md")).read_text(encoding="utf-8") == (
        "---\ntarget_repo: batch/repo\ntype: awi\nstate: inbox\n---\n\n自動切替の本文  \n"
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_create_dialog_choice_validation_precedes_only_single_entry(browser_harness: _BrowserHarness) -> None:
    """単件の選択肢は検証し、show形式ではサーバーの形式判定へ原文を渡す。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.get_by_role("button", name="新規追加").click()
    dialog = page.get_by_role("dialog", name="新規追加")
    await dialog.locator("#create-kind").select_option("uwi")
    await dialog.locator("#create-question-type").select_option("choice")
    await playwright.async_api.expect(dialog.locator("label[for='create-choices']")).to_have_text(
        "選択肢（1行1件、2件以上必須）"
    )
    await dialog.locator("#create-target").fill("example/repo")
    await dialog.locator("#create-content").fill("質問の本文")
    choices = dialog.locator("#create-choices")
    await choices.fill("一択")
    await dialog.get_by_role("button", name="追加").click()
    await playwright.async_api.expect(dialog.locator("#create-choices-error")).to_have_text("選択肢を2件以上入力してください。")
    await playwright.async_api.expect(choices).to_be_focused()
    assert harness.operations.add_calls == []

    await choices.fill("第一\n第二")
    await playwright.async_api.expect(dialog.locator("#create-choices-error")).to_be_hidden()
    await playwright.async_api.expect(choices).not_to_have_attribute("aria-invalid", "true")
    async with page.expect_request(lambda request: request.url.endswith("/api/entries") and request.method == "POST") as info:
        await dialog.get_by_role("button", name="追加").click()
    single_payload = (await info.value).post_data_json
    assert isinstance(single_payload, dict)
    assert single_payload["choices"] == ["第一", "第二"]
    await dialog.wait_for(state="hidden")
    assert harness.operations.add_calls[-1]["messages"] == ["質問の本文"]
    await page.locator('.entry-select[data-key="created-1-0.md"]').wait_for(state="visible")

    await page.get_by_role("button", name="新規追加").click()
    await dialog.locator("#create-kind").select_option("uwi")
    await dialog.locator("#create-question-type").select_option("choice")
    show_text = (
        "## target_repo: batch/repo\n### pasted.md [inbox]\n---\ntarget_repo: batch/repo\ntype: awi\n---\n\n貼り付けた本文\n"
    )
    await dialog.locator("#create-content").fill(show_text)
    await playwright.async_api.expect(dialog.locator("#create-choices")).to_have_value("")
    await playwright.async_api.expect(dialog.locator("#create-mode-hint")).to_be_visible()
    await playwright.async_api.expect(dialog.locator("#create-mode-hint")).to_contain_text("一括取り込みします")
    await playwright.async_api.expect(dialog.locator("#uwi-fields")).to_be_hidden()
    await playwright.async_api.expect(dialog.locator("#create-repo-fields")).to_be_hidden()
    await dialog.locator("#create-content").fill("単件の本文")
    await playwright.async_api.expect(dialog.locator("#create-mode-hint")).to_be_hidden()
    await playwright.async_api.expect(dialog.locator("#uwi-fields")).to_be_visible()
    await dialog.locator("#create-content").fill(show_text)
    async with page.expect_request(
        lambda request: request.url.endswith("/api/entries") and request.method == "POST", timeout=3000
    ) as info:
        await dialog.get_by_role("button", name="追加").click()
    show_payload = (await info.value).post_data_json
    assert isinstance(show_payload, dict)
    assert show_payload["raw_text"] == show_text
    await dialog.wait_for(state="hidden")
    assert harness.operations.batch_calls[-1] == show_text
    assert (_entry_path(harness.root, "pasted.md")).read_text(encoding="utf-8") == (
        "---\ntarget_repo: batch/repo\ntype: awi\nstate: inbox\n---\n\n貼り付けた本文\n"
    )
    await page.locator('.entry-select[data-key="pasted.md"]').wait_for(state="visible")


@pytest.mark.asyncio(loop_scope="session")
async def test_create_dialog_show_text_after_preface_stays_single_entry(browser_harness: _BrowserHarness) -> None:
    """先頭エントリより前に説明文がある本文はサーバーと同じく単件として扱い、単件の入力欄と検証を保つ。"""
    harness = browser_harness
    page = harness.page
    await page.goto(harness.base_url + "/")
    await page.get_by_role("button", name="新規追加").click()
    dialog = page.get_by_role("dialog", name="新規追加")
    await dialog.locator("#create-kind").select_option("uwi")
    await dialog.locator("#create-question-type").select_option("choice")
    await dialog.locator("#create-target").fill("example/repo")
    await dialog.locator("#create-content").fill(
        "次の項目について確認したい。\n\n"
        "### pasted.md [inbox]\n---\ntarget_repo: batch/repo\ntype: awi\n---\n\n貼り付けた本文\n"
    )
    await playwright.async_api.expect(dialog.locator("#create-mode-hint")).to_be_hidden()
    await playwright.async_api.expect(dialog.locator("#uwi-fields")).to_be_visible()
    await playwright.async_api.expect(dialog.locator("#create-repo-fields")).to_be_visible()
    await dialog.locator("#create-choices").fill("一択")
    await dialog.get_by_role("button", name="追加").click()
    await playwright.async_api.expect(dialog.locator("#create-choices-error")).to_have_text("選択肢を2件以上入力してください。")
    assert harness.operations.add_calls == []
    assert harness.operations.batch_calls == []


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(("width", "height"), [(320, 720), (700, 720), (1280, 720), (1280, 900)])
@pytest.mark.parametrize("input_height", [None, 480])
async def test_invalid_input_focus_keeps_whole_error_in_dialog_body(
    browser_harness: _BrowserHarness, width: int, height: int, input_height: int | None
) -> None:
    """不正な入力欄へフォーカスが戻った直後に対応するエラーの全文が見え、本文をスクロールしてもヘッダーとフッターの位置は変わらない。

    回答欄の入力エラーはフッター内に表示されるため、フッターの可視範囲で判定する。
    """
    harness = browser_harness
    page = harness.page
    (_entry_path(harness.root, "long-entry.md")).write_text(
        "---\ntype: awi\nstate: inbox\ntarget_repo: example/repo\n---\n\n" + "長い先行本文\n\n" * 120,
        encoding="utf-8",
    )
    await page.set_viewport_size({"width": width, "height": height})
    for case in ("content", "choice", "both", "edit", "answer"):
        await page.goto(harness.base_url + "/")
        await playwright.async_api.expect(page.locator("#connection-status")).to_have_attribute("data-connected", "true")
        if case in {"content", "choice", "both"}:
            await page.get_by_role("button", name="新規追加").click()
            dialog = page.get_by_role("dialog", name="新規追加")
            if case != "content":
                await dialog.locator("#create-kind").select_option("uwi")
                await dialog.locator("#create-question-type").select_option("choice")
                await dialog.locator("#create-choices").fill("選択肢1")
            if case == "choice":
                await dialog.locator("#create-content").fill("確認する本文")
            input_id = "create-choices" if case == "choice" else "create-content"
            submit = "追加"
        else:
            key = "long-entry.md" if case == "edit" else "question.md"
            await page.locator(f'.entry-select[data-key="{key}"]').click()
            dialog = page.get_by_role("dialog", name="詳細")
            if case == "edit":
                await dialog.get_by_role("button", name="編集", exact=True).click()
            input_id = "edit-content" if case == "edit" else "answer-input"
            await dialog.locator(f"#{input_id}").fill("")
            submit = "保存" if case == "edit" else "回答を保存"
        field = dialog.locator(f"#{input_id}")
        if input_height is not None:
            await field.evaluate(
                "(element, height) => { element.style.minHeight = '0'; element.style.height = `${height}px`; }",
                input_height,
            )
        await dialog.get_by_role("button", name=submit, exact=True).click()
        await playwright.async_api.expect(field).to_be_focused()
        error = dialog.locator(f"#{input_id}-error")
        await playwright.async_api.expect(error).to_be_visible()
        # 回答欄はフッターに常設され、その入力エラーもフッター内へ表示する。所属する領域の可視範囲で判定する。
        metrics = await error.evaluate("""error => {
          const body = error.closest('.dialog-body') || error.closest('.dialog-footer');
          const bounds = body.getBoundingClientRect();
          const message = error.getBoundingClientRect();
          return {
            top: message.top, bottom: message.bottom,
            visibleTop: bounds.top + body.clientTop,
            visibleBottom: bounds.top + body.clientTop + body.clientHeight
          };
        }""")
        assert metrics["top"] >= metrics["visibleTop"], (case, metrics)
        assert metrics["bottom"] <= metrics["visibleBottom"], (case, metrics)
        header = await dialog.locator(".dialog-header").bounding_box()
        footer = await dialog.locator(".dialog-footer").bounding_box()
        await dialog.locator(".dialog-body").evaluate("element => { element.scrollTop = 0; }")
        assert await dialog.locator(".dialog-header").bounding_box() == header
        assert await dialog.locator(".dialog-footer").bounding_box() == footer


@pytest.mark.asyncio(loop_scope="session")
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

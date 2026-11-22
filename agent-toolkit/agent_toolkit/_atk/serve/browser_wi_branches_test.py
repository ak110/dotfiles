import json
import pathlib

import playwright.async_api
import pytest

from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _browser_harness_fixture,
    _BrowserHarness,
    _screen_harness_fixture,
)
from agent_toolkit._testing.config_support import _isolate_platformdirs, _write_config
from agent_toolkit._testing.serve_browser_wi_support import _entry_path

__all__ = ["_isolate_platformdirs"]

pytestmark = [
    pytest.mark.browser,
]

_REGISTERED = "github.com/example/branched"
_PLAIN = "github.com/example/plain"


def _write_entry(harness: _BrowserHarness, filename: str, repo: str, *, branch: str | None = None) -> None:
    branch_line = f"target_branch: {branch}\n" if branch is not None else ""
    _entry_path(harness.root, filename).write_text(
        f"---\ntype: awi\nstate: inbox\ntarget_repo: {repo}\n{branch_line}---\n\n{filename}の本文\n", encoding="utf-8"
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_registered_repository_shows_branch_filters_by_it_and_creates_with_it(
    browser_harness: _BrowserHarness, tmp_path: pathlib.Path
) -> None:
    harness = browser_harness
    page = harness.page
    _write_config(
        tmp_path,
        {
            "repositories": json.dumps(
                {_REGISTERED: {"edit": "allow", "branches": ["maint", "develop"], "default_branch": "develop"}}
            )
        },
    )
    _write_entry(harness, "stored.md", _REGISTERED, branch="maint")
    _write_entry(harness, "legacy.md", _REGISTERED)
    _write_entry(harness, "plain.md", _PLAIN)
    await page.goto(harness.base_url + "/")
    rows = page.locator("#entry-list .entry-row")
    await rows.filter(has_text="plain.md").wait_for(state="visible")

    badges = page.locator("#entry-list .branch-badge")
    await playwright.async_api.expect(badges).to_have_count(2)
    await playwright.async_api.expect(rows.filter(has_text="stored.md").locator(".branch-badge")).to_have_text("maint")
    await playwright.async_api.expect(rows.filter(has_text="legacy.md").locator(".branch-badge")).to_have_text("develop")
    await playwright.async_api.expect(rows.filter(has_text="plain.md").locator(".branch-badge")).to_have_count(0)

    branch_filter = page.locator("#target-branch-filter")
    await playwright.async_api.expect(branch_filter).to_be_visible()
    await page.get_by_label("条件検索").check()
    await branch_filter.select_option("maint")
    await playwright.async_api.expect(rows).to_have_count(1)
    await playwright.async_api.expect(rows.first).to_contain_text("stored.md")

    await page.get_by_role("button", name="新規追加").click()
    create_dialog = page.get_by_role("dialog", name="新規追加")
    await create_dialog.wait_for(state="visible")
    branch_choice = create_dialog.locator("#create-branch")
    await playwright.async_api.expect(branch_choice).to_be_hidden()
    await create_dialog.locator("#create-target").fill(_PLAIN)
    await playwright.async_api.expect(branch_choice).to_be_hidden()
    await create_dialog.locator("#create-target").fill(_REGISTERED)
    await playwright.async_api.expect(branch_choice).to_be_visible()
    await playwright.async_api.expect(branch_choice).to_have_value("develop")
    await branch_choice.select_option("maint")
    await create_dialog.locator("#create-content").fill("系統を選んだ本文")
    async with page.expect_request(lambda request: request.url.endswith("/api/entries") and request.method == "POST") as posted:
        await create_dialog.get_by_role("button", name="追加").click()
    body = (await posted.value).post_data_json
    assert body is not None
    assert body["target_branch"] == "maint"
    await create_dialog.wait_for(state="hidden")
    created = page.locator("#entry-list .entry-row").filter(has_text="系統を選んだ本文")
    await created.wait_for(state="visible")
    await playwright.async_api.expect(created.locator(".branch-badge")).to_have_text("maint")


@pytest.mark.asyncio(loop_scope="session")
async def test_unregistered_repositories_show_no_branch_ui_and_send_no_branch(browser_harness: _BrowserHarness) -> None:
    harness = browser_harness
    page = harness.page
    _write_entry(harness, "plain.md", _PLAIN)
    await page.goto(harness.base_url + "/")
    await page.locator("#entry-list .entry-row").filter(has_text="plain.md").wait_for(state="visible")

    await playwright.async_api.expect(page.locator("#entry-list .branch-badge")).to_have_count(0)
    await playwright.async_api.expect(page.locator("#target-branch-filter")).to_be_hidden()
    await page.get_by_role("button", name="新規追加").click()
    create_dialog = page.get_by_role("dialog", name="新規追加")
    await create_dialog.wait_for(state="visible")
    await create_dialog.locator("#create-target").fill(_PLAIN)
    await create_dialog.locator("#create-content").fill("系統の無い本文")
    await playwright.async_api.expect(create_dialog.locator("#create-branch")).to_be_hidden()
    async with page.expect_request(lambda request: request.url.endswith("/api/entries") and request.method == "POST") as posted:
        await create_dialog.get_by_role("button", name="追加").click()
    body = (await posted.value).post_data_json
    assert body is not None
    assert "target_branch" not in body
    await create_dialog.wait_for(state="hidden")

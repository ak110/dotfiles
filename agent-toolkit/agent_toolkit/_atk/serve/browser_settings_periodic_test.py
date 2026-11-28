import asyncio
import copy
import pathlib
import urllib.parse

import playwright.async_api
import pytest

from agent_toolkit._atk import config
from agent_toolkit._atk.serve.browser_settings_test import _open_setting
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _screen_harness_fixture,
    _ScreenHarness,
)

pytestmark = [pytest.mark.browser]


@pytest.mark.parametrize("can_observe", [True, False])
@pytest.mark.asyncio(loop_scope="session")
async def test_periodic_settings_reads_neither_refresh_usage_nor_replace_a_draft(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, can_observe: bool
) -> None:
    snapshot = config.settings_snapshot(tmp_path, read_only=True)
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    state = "initial"
    remote_reads: list[str] = []

    async def settings(route: playwright.async_api.Route) -> None:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(route.request.url).query)
        host = query.get("host", [""])[0]
        observing = query.get("observe_usage") == ["1"]
        data = copy.deepcopy(snapshot)
        data.update(hosts=["demo-host"], can_observe_usage=can_observe)
        value = "local" if not host else "before" if state == "initial" else "after"
        data["settings"]["serve_host"].update(saved=value, effective=value)
        if host and state == "hold" and not observing:
            started.set()
            await release.wait()
            data["settings"]["serve_host"].update(saved="late", effective="late")
        if host:
            remote_reads.append("usage" if observing else "settings")
        if host and state == "unavailable" and not observing:
            await route.fulfill(status=403, json={"error": "隔離試験の読み取り失敗"})
        else:
            await route.fulfill(json=data)
        if host and state == "hold" and not observing:
            finished.set()

    page = screen_harness.page
    await page.clock.install()
    await page.route("**/api/settings**", settings)
    await page.goto(screen_harness.base_url + "/settings")
    picker = page.get_by_label("設定対象ホスト")
    await picker.select_option("demo-host")
    nav = page.get_by_role("navigation", name="設定の分類")
    await nav.get_by_text("atk serve", exact=True).click()
    editor = page.locator('[data-setting="serve_port"]')
    await _open_setting(editor)
    draft = editor.get_by_label("保存値")
    await draft.fill("28777")
    host_setting = page.locator('[data-setting="serve_host"]').locator(":scope > summary")
    expect = playwright.async_api.expect
    await expect(host_setting).to_contain_text("before")
    reads_before = len(remote_reads)
    state = "changed"
    await page.clock.fast_forward(30_000)
    await expect(host_setting).to_contain_text("after")
    await expect(draft).to_have_value("28777")
    assert remote_reads[reads_before:] == ["settings"]

    state = "unavailable"
    await page.clock.fast_forward(30_000)
    await expect(page.locator("#settings-status")).to_contain_text("前回取得した設定を表示中")
    await expect(draft).to_have_value("28777")

    state = "hold"
    try:
        await page.clock.fast_forward(30_000)
        await asyncio.wait_for(started.wait(), 5)
        await picker.select_option("")
        await expect(host_setting).to_contain_text("local")
    finally:
        release.set()
    await asyncio.wait_for(finished.wait(), 5)
    await expect(host_setting).not_to_contain_text("late")

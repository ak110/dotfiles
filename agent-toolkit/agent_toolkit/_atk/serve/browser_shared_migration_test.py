import asyncio
import json
import pathlib

import playwright.async_api
import pytest
import pytest_asyncio

from agent_toolkit._atk import config, config_settings, config_shared_migration, usage_refresh
from agent_toolkit._atk.config_sharing_test import _Hosts
from agent_toolkit._atk.serve.browser_settings_test import _open_setting
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _screen_harness_fixture,
    _ScreenHarness,
)

pytestmark = [pytest.mark.browser]


@pytest.fixture(autouse=True)
def isolate_observation_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usage_refresh, "refresh", lambda **_kwargs: [])


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def isolate_settings_screen(screen_harness: _ScreenHarness) -> None:
    await screen_harness.page.route(
        "**/static/{wi,plans,sessions,agents,usage}.js",
        lambda route: route.fulfill(content_type="text/javascript", body="export {};"),
    )


@pytest.mark.cpu_limit(
    1.5, reason="独立2 cloneの作成、実Gitの同期、ブラウザーと別端末から旧許可により自動利用が有効にならないことを確認する。"
)
@pytest.mark.asyncio(loop_scope="session")
async def test_shared_migration_keeps_legacy_grant_without_enabling_automatic_reset(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts = _Hosts(tmp_path, monkeypatch)
    hosts.use("a")
    permission = {
        "id": "permitted-period",
        "engine": "codex",
        "account": "example@example.invalid",
        "scope": "codex",
        "count": 2,
        "from": "2020-01-01T00:00:00Z",
        "until": "2099-01-01T00:00:00Z",
    }
    hosts.write_local("a", {"usage_reset_permissions": json.dumps([permission])})
    plan = config_shared_migration.compare()
    config_shared_migration.apply(plan["revision"], {})

    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/settings")
    nav = page.get_by_role("navigation", name="設定の分類")
    await nav.get_by_role("button", name="利用枠", exact=True).click()
    await playwright.async_api.expect(page.locator('[data-setting="usage_reset_permissions"]')).to_have_count(0)
    hosts.use("b")
    assert config.resolve_mutable_setting("codex_auto_reset") == "false"
    assert config.resolve_mutable_setting("claude_auto_reset") == "false"


@pytest.mark.asyncio(loop_scope="session")
async def test_settings_only_asks_about_conflicts_and_saves_the_choice(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts = _Hosts(tmp_path, monkeypatch)
    hosts.use("a")
    config.save_mutable_settings({"review_model": "claude:sonnet/medium"})
    hosts.write_local("b", {"review_model": "codex:sol/high", "execute_model": "codex:sol/low"})
    hosts.use("b")
    config_shared_migration.migrate_at_startup()

    page = screen_harness.page
    await page.clock.install()
    await page.set_viewport_size({"width": 360, "height": 700})
    await page.goto(screen_harness.base_url + "/settings")
    expect = playwright.async_api.expect
    shortcut = page.get_by_role("button", name="共有設定に残す値を選ぶ")
    await shortcut.press("Enter")
    await expect(page.get_by_role("heading", name="共有設定の選択")).to_be_focused()
    panel = page.locator("[data-shared-migration]")
    await expect(panel.get_by_role("group", name="review_model")).to_be_visible()
    await expect(panel.get_by_text("execute_model", exact=False)).to_have_count(0)
    await expect(panel.get_by_role("button", name="差を確認")).to_have_count(0)
    options = panel.locator(".settings-migration-option")
    first, second = await options.nth(0).bounding_box(), await options.nth(1).bounding_box()
    assert first is not None and second is not None and second["y"] >= first["y"] + first["height"]
    radio = panel.get_by_role("radio", name="この端末の値: codex:sol/high")
    await radio.check()
    await radio.focus()
    async with page.expect_response(lambda response: "background=1" in response.url and response.ok):
        await page.clock.fast_forward(30_000)
    save = panel.get_by_role("button", name="選んだ値を保存")
    await expect(save).to_have_class("button-primary")
    await expect(save).to_be_enabled()
    await expect(radio).to_be_checked()
    await expect(radio).to_be_focused()
    await page.keyboard.press("Tab")
    await expect(save).to_be_focused()
    async with page.expect_response(lambda response: "background=1" in response.url and response.ok):
        await page.clock.fast_forward(30_000)
    await expect(save).to_be_enabled()
    await expect(save).to_be_focused()
    await page.keyboard.press("Enter")
    await expect(panel).to_have_count(0)
    await expect(shortcut).to_be_hidden()

    assert hosts.local("b") == {}
    hosts.use("a")
    assert config.resolve_mutable_setting("review_model") == "codex:sol/high"
    assert config.resolve_mutable_setting("execute_model") == "codex:sol/low"


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("persistent", [False, True])
async def test_migration_save_keeps_focus_while_settings_refresh_recovers(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, persistent: bool
) -> None:
    hosts = _Hosts(tmp_path, monkeypatch)
    hosts.use("a")
    config.save_mutable_settings({"review_model": "claude:sonnet/medium"})
    hosts.write_local("b", {"review_model": "codex:sol/high"})
    hosts.use("b")
    page = screen_harness.page
    expect = playwright.async_api.expect
    await page.clock.install()
    await page.goto(screen_harness.base_url + "/settings")
    await page.get_by_role("navigation", name="設定の分類").get_by_role("button", name="共有設定の選択").click()
    panel = page.locator("[data-shared-migration]")
    await panel.get_by_role("radio", name="この端末の値: codex:sol/high").check()
    save = panel.get_by_role("button", name="選んだ値を保存")
    await save.focus()
    release = asyncio.Event()

    async def fail_once(route: playwright.async_api.Route) -> None:
        await release.wait()
        await route.fulfill(status=503, json={"error": "一時的な取得不能"})

    read_route = "**/api/settings?background=1"
    await page.route(read_route, fail_once, times=None if persistent else 1)
    requests: list[str] = []

    def record_migration(request: playwright.async_api.Request) -> None:
        if request.method == "POST" and request.url.endswith("/api/settings/shared-migration"):
            requests.append(request.url)

    page.on("request", record_migration)
    await page.clock.fast_forward(30_000)
    await expect(save).to_be_disabled()
    await expect(save).to_be_focused()
    await expect(panel.get_by_text("最新の共有設定を確認できるまで保存できません。")).to_be_visible()
    await page.keyboard.press("Enter")
    assert not requests
    async with page.expect_response(lambda response: "background=1" in response.url and response.status == 503):
        release.set()
    if persistent:
        for _ in range(3):
            async with page.expect_response(lambda response: "background=1" in response.url and response.status == 503):
                await page.clock.run_for(2_000)
        retry = page.locator("#update-status").get_by_role("button", name="再取得")
        await expect(retry).to_be_visible()
        await expect(save).to_be_disabled()
        await expect(save).to_be_focused()
        await expect(panel.get_by_text("最新の共有設定を確認できるまで保存できません。")).to_be_visible()
        await page.keyboard.press("Enter")
        assert not requests
        await page.unroute(read_route, fail_once)
        await retry.click()
        await expect(save).to_be_enabled()
        await save.focus()
    else:
        async with page.expect_response(lambda response: "background=1" in response.url and response.ok):
            await page.clock.run_for(2_000)
    await expect(save).to_be_enabled()
    await expect(save).to_be_focused()
    await page.keyboard.press("Enter")
    await expect(panel).to_have_count(0)
    assert len(requests) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_invalid_shared_setting_has_repair_destination_without_blocking_choice(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hosts = _Hosts(tmp_path, monkeypatch)
    hosts.use("a")
    config.save_mutable_settings({"review_model": "claude:sonnet/medium"})
    hosts.use("b")
    path = config_settings.config_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"review_model": "codex:sol/high", "codex_fast_mode": false}', encoding="utf-8")

    page = screen_harness.page
    expect = playwright.async_api.expect
    await page.goto(screen_harness.base_url + "/settings")
    nav = page.get_by_role("navigation", name="設定の分類")
    await nav.get_by_role("button", name="共有設定の選択").click()
    panel = page.locator("[data-shared-migration]")
    await expect(panel.get_by_text("この端末のconfig.jsonで無効な値だけを修正してください。", exact=False)).to_be_visible()
    await panel.get_by_role("radio", name="この端末の値: codex:sol/high").check()
    await panel.get_by_role("button", name="選んだ値を保存").click()
    await expect(panel.get_by_role("radio")).to_have_count(0)
    await expect(panel.get_by_text("codex_fast_mode", exact=False)).to_be_visible()
    assert json.loads(path.read_text(encoding="utf-8")) == {"codex_fast_mode": False}
    await nav.get_by_role("button", name="その他の設定とパス").click()
    row = page.locator('[data-setting="codex_fast_mode"]')
    await _open_setting(row)
    await expect(row.get_by_text("この端末のconfig.jsonに無効な値があります。", exact=False)).to_be_visible()
    await expect(row.get_by_role("button", name="保存", exact=True)).to_have_count(0)
    path.write_text('{"codex_fast_mode": "false"}', encoding="utf-8")
    await page.reload()
    await expect(panel.get_by_text("無効な値", exact=False)).to_have_count(0)
    hosts.use("a")
    assert config.resolve_mutable_setting("codex_fast_mode") == "auto"

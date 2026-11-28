import asyncio

import pytest
from playwright.async_api import Route, expect

from agent_toolkit._atk import config, config_settings, usage_refresh
from agent_toolkit._atk.serve import settings_routes
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _screen_harness_fixture,
    _ScreenHarness,
)

pytestmark = [pytest.mark.browser]


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(usage_refresh, "refresh", lambda **_kwargs: [])


@pytest.mark.parametrize(
    ("engine", "label"),
    [("claude", "手動リセットを推奨する"), ("codex", "リセット権を自動で使う")],
)
@pytest.mark.asyncio(loop_scope="session")
async def test_usage_reset_settings_save_explicitly_and_read_back(screen_harness: _ScreenHarness, engine, label) -> None:
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/usage")
    usage = page.locator("#screen-usage")
    await expect(usage.get_by_role("link", name="リセットの許可を設定")).to_have_count(0)
    refresh = usage.get_by_role("button", name="再取得")
    await expect(refresh).to_be_enabled()
    async with page.expect_response(lambda response: response.url.endswith("/api/usage/refresh")) as pending:
        await refresh.click()
    assert (await pending.value).ok
    checkbox = usage.get_by_role("checkbox", name=label)
    form = checkbox.locator("..")
    key = f"{engine}_auto_reset"
    await expect(checkbox).not_to_be_checked()
    await checkbox.check()
    assert config_settings.load_config().get(key) is None
    await form.get_by_role("button", name="保存").click()
    await expect(form.get_by_role("status")).to_contain_text("現在: 有効。 保存しました")
    await page.reload()
    await expect(checkbox).to_be_checked()
    assert config_settings.load_config()[key] == "true"
    await expect(page.get_by_text("自動消費は未対応です。", exact=False)).to_be_visible()


@pytest.mark.asyncio(loop_scope="session")
async def test_settings_does_not_offer_any_reset_controls(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/settings")
    await expect(page.get_by_role("navigation", name="設定の分類")).to_be_visible()
    settings = page.locator("#settings-content")
    await expect(settings.get_by_label("手動リセットを推奨する")).to_have_count(0)
    await expect(settings.get_by_label("リセット権を自動で使う")).to_have_count(0)


@pytest.mark.asyncio(loop_scope="session")
async def test_failed_save_shows_reason_without_changing_other_setting(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page

    async def fail_save(route: Route) -> None:
        await route.fulfill(status=400, json={"error": "共有先へ書き込めません"})

    await page.route("**/api/usage/reset-setting", fail_save)
    await page.goto(screen_harness.base_url + "/usage")
    codex = page.get_by_label("リセット権を自動で使う")
    await codex.check()
    await codex.locator("..").get_by_role("button", name="保存").click()
    await expect(codex.locator("..").get_by_role("status")).to_contain_text("共有先へ書き込めません")
    await expect(page.get_by_label("手動リセットを推奨する")).to_be_enabled()
    assert "codex_auto_reset" not in config_settings.load_config()


@pytest.mark.asyncio(loop_scope="session")
async def test_saved_setting_stays_saved_when_observation_needs_retry(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    await page.route(
        "**/api/usage/reset-setting",
        lambda route: route.fulfill(
            json={
                "reset_settings": {"codex": {"enabled": True}},
                "claude_reset_recommended": False,
                "observation_error": "利用枠を読み取れません。利用枠の観測を再取得してください。",
            }
        ),
    )
    await page.goto(screen_harness.base_url + "/usage")
    choice = page.get_by_role("checkbox", name="リセット権を自動で使う")
    form = choice.locator("..")
    await choice.check()
    await form.get_by_role("button", name="保存").click()
    await expect(form.get_by_role("status")).to_have_text("現在: 有効。 保存しました。")
    await expect(page.get_by_role("checkbox", name="手動リセットを推奨する")).not_to_be_checked()
    banner = page.locator("#update-status")
    await expect(banner).to_contain_text("利用枠を読み取れません")
    await expect(banner.get_by_role("button", name="再取得")).to_be_visible()


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize(
    ("engine", "label"),
    [("claude", "手動リセットを推奨する"), ("codex", "リセット権を自動で使う")],
)
@pytest.mark.asyncio(loop_scope="session")
async def test_reset_save_keeps_keyboard_in_the_setting(screen_harness: _ScreenHarness, width, engine, label) -> None:
    page = screen_harness.page
    await page.set_viewport_size({"width": width, "height": 900})
    started = asyncio.Event()
    release = asyncio.Event()
    writes = []

    async def fail_once(route: Route) -> None:
        writes.append(route.request.post_data_json)
        if len(writes) == 1:
            started.set()
            await release.wait()
            await route.fulfill(status=400, json={"error": "共有先へ書き込めません"})
        else:
            await route.continue_()

    await page.route("**/api/usage/reset-setting", fail_once)
    await page.goto(screen_harness.base_url + "/usage")
    choice = page.get_by_role("checkbox", name=label)
    form = choice.locator("..")
    save = form.get_by_role("button", name="保存")
    status = form.get_by_role("status")
    await choice.press("Space")
    await page.keyboard.press("Tab")
    await expect(save).to_be_focused()
    await page.keyboard.press("Enter")
    try:
        await asyncio.wait_for(started.wait(), 5)
        progress = form.get_by_role("progressbar", name="保存中")
        await expect(progress).to_be_in_viewport()
        assert await progress.evaluate("""node => [
          node.closest('[role="status"]').textContent.trim(),
          getComputedStyle(node, '::before').borderTopStyle,
          node.closest('form').querySelector('input[type="checkbox"]').matches(':disabled'),
        ]""") == ["現在: 無効。", "solid", True]
        await expect(status).to_be_focused(timeout=1000)
        await expect(status).to_be_in_viewport()
        await page.keyboard.press("Enter")
        await expect(save).to_be_disabled()
    finally:
        release.set()
    await expect(status).to_contain_text("共有先へ書き込めません")
    await expect(progress).to_have_count(0)
    await expect(status).to_be_in_viewport()
    await expect(choice).to_be_checked()
    await expect(choice).to_be_focused()
    assert f"{engine}_auto_reset" not in config_settings.load_config()
    await page.keyboard.press("Tab")
    await expect(save).to_be_focused()
    await page.keyboard.press("Enter")
    await expect(status).to_contain_text("現在: 有効。 保存しました")
    await expect(status).to_be_focused()
    await expect(status).to_be_in_viewport()
    assert writes == [{"engine": engine, "enabled": True}] * 2


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("first_engine", ["claude", "codex"])
@pytest.mark.parametrize("first_succeeds", [True, False])
@pytest.mark.asyncio(loop_scope="session")
async def test_reset_response_preserves_other_save_focus(
    screen_harness: _ScreenHarness, width: int, first_engine: str, first_succeeds: bool
) -> None:
    page = screen_harness.page
    await page.set_viewport_size({"width": width, "height": 844})
    labels = {"claude": "手動リセットを推奨する", "codex": "リセット権を自動で使う"}
    second_engine = next(engine for engine in labels if engine != first_engine)
    choices = {engine: page.get_by_role("checkbox", name=label) for engine, label in labels.items()}
    forms = {engine: choice.locator("..") for engine, choice in choices.items()}
    saves = {engine: form.get_by_role("button", name="保存") for engine, form in forms.items()}
    first_started = asyncio.Event()
    first_release = asyncio.Event()
    submitted = []

    async def save_one(route: Route) -> None:
        payload = route.request.post_data_json
        assert isinstance(payload, dict)
        engine = payload["engine"]
        submitted.append(engine)
        if engine == first_engine:
            first_started.set()
            await first_release.wait()
            if not first_succeeds:
                await route.fulfill(status=400, json={"error": "共有先へ書き込めません"})
                return
        await route.continue_()

    await page.route("**/api/usage/reset-setting", save_one)
    await page.goto(screen_harness.base_url + "/usage")
    await choices[first_engine].press("Space")
    await page.keyboard.press("Tab")
    await expect(saves[first_engine]).to_be_focused()
    await page.keyboard.press("Enter")
    try:
        await asyncio.wait_for(first_started.wait(), 5)
        await choices[second_engine].press("Space")
        await page.keyboard.press("Tab")
        await expect(saves[second_engine]).to_be_focused()
    finally:
        first_release.set()
    first_result = "保存しました" if first_succeeds else "共有先へ書き込めません"
    await expect(forms[first_engine].get_by_role("status")).to_contain_text(first_result)
    await expect(saves[second_engine]).to_be_focused(timeout=1000)
    await expect(saves[second_engine]).to_be_in_viewport()
    await expect(choices[second_engine]).to_be_checked()
    await page.keyboard.press("Enter")
    await expect(forms[second_engine].get_by_role("status")).to_have_text("現在: 有効。 保存しました。")
    assert submitted == [first_engine, second_engine]


@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.asyncio(loop_scope="session")
async def test_generic_settings_api_never_writes_reset_settings(screen_harness: _ScreenHarness, monkeypatch, remote) -> None:
    calls = []
    monkeypatch.setattr(config, "update_settings", lambda payload: calls.append(("local", payload)))
    monkeypatch.setattr(settings_routes, "_remote_hosts", lambda **_kwargs: ("old-host",))
    monkeypatch.setattr(settings_routes, "_remote_update", lambda host, payload: calls.append((host, payload)))
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/usage")
    url = "/api/settings?host=old-host" if remote else "/api/settings"
    statuses = await page.evaluate(
        """async url => {
          const calls = [
            {action: "set", key: "codex_auto_reset", value: "true"},
            {action: "set", key: "claude_auto_reset", value: "true"},
            {action: "set", key: "usage_reset_permissions", value: "[]"},
            {action: "reset-reissue", id: "old"},
            {action: "set", key: "codex_fast_mode", value: "false"},
          ];
          const statuses = [];
          for (const payload of calls) {
            const response = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"},
              body: JSON.stringify(payload)});
            statuses.push(response.status);
          }
          return statuses;
        }""",
        url,
    )
    assert statuses == [400, 400, 400, 400, 200]
    assert calls == [("old-host" if remote else "local", {"action": "set", "key": "codex_fast_mode", "value": "false"})]


@pytest.mark.asyncio(loop_scope="session")
async def test_removed_permission_asset_does_not_affect_other_scripts(screen_harness: _ScreenHarness) -> None:
    request = screen_harness.page.request
    base = screen_harness.base_url
    assert (await request.get(base + "/static/reset_permissions.js")).status == 404
    for name in ("usage", "settings", "settings_view"):
        result = await request.get(f"{base}/static/{name}.js")
        assert result.ok
        assert "cache-control" in result.headers

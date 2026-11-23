import pathlib

import playwright.async_api
import pytest

from agent_toolkit._atk import config_settings
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _screen_harness_fixture,
    _ScreenHarness,
)

pytestmark = [pytest.mark.browser]


@pytest.mark.asyncio(loop_scope="session")
async def test_settings_shows_supported_keys_and_saves_with_cli_validation(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_SERVE_PORT", "54001")
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/settings")
    await playwright.async_api.expect(page.locator("[data-setting]")).to_have_count(len(config_settings.MUTABLE_KEY_DEFAULTS))
    port = page.locator('[data-setting="serve_port"]')
    await playwright.async_api.expect(port).to_contain_text("AGENT_TOOLKIT_CONFIG_SERVE_PORT")
    await port.get_by_label("保存値").fill("65536")
    await port.get_by_role("button", name="保存").click()
    await playwright.async_api.expect(port.get_by_role("alert")).to_contain_text("65535")
    assert "serve_port" not in config_settings.load_config()
    draft = page.locator('[data-setting="execute_model"]').get_by_label("保存値")
    await draft.fill("codex:sol/high")
    await port.get_by_label("保存値").fill("54002")
    await port.get_by_role("button", name="保存").click()
    await playwright.async_api.expect(page.locator("#settings-status")).to_have_text("設定を保存しました。")
    assert config_settings.load_config()["serve_port"] == "54002"
    await playwright.async_api.expect(port).to_contain_text("実効値: 54001")
    await playwright.async_api.expect(port.get_by_role("alert")).to_be_focused()
    await playwright.async_api.expect(draft).to_have_value("codex:sol/high")


@pytest.mark.asyncio(loop_scope="session")
async def test_settings_save_notice_is_visible_at_bottom_of_narrow_screen(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    page = screen_harness.page
    await page.set_viewport_size({"width": 320, "height": 360})
    await page.goto(screen_harness.base_url + "/settings")
    port = page.locator('[data-setting="serve_port"]')
    await port.get_by_label("保存値").fill("54002")
    save = port.get_by_role("button", name="保存")
    await save.focus()
    await save.evaluate("(button) => { button.closest('main').scrollTop += button.getBoundingClientRect().bottom - 345; }")
    await page.keyboard.press("Enter")
    notice = port.get_by_role("alert")
    await playwright.async_api.expect(notice).to_be_focused()
    await playwright.async_api.expect(notice).to_be_in_viewport(ratio=1)


@pytest.mark.cpu_limit(1.5, reason="設定の複数操作を隔離した実サーバーとブラウザーで確かめる（途中までのCPU実測0.719秒）。")
@pytest.mark.asyncio(loop_scope="session")
async def test_settings_temporary_preset_repository_and_array_operations(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/settings")

    hosts = page.locator('[data-setting="serve_sessions_remote_hosts"]')
    await hosts.get_by_label("追加するホスト").fill("example-host")
    await hosts.get_by_role("button", name="追加", exact=True).click()
    await playwright.async_api.expect(hosts.get_by_role("button", name="example-hostを削除")).to_be_visible()
    await hosts.get_by_role("button", name="example-hostを削除").click()
    await playwright.async_api.expect(hosts.get_by_role("button", name="example-hostを削除")).to_have_count(0)

    repos = page.locator("section").filter(has=page.get_by_role("heading", name="リポジトリ", exact=True)).last
    await repos.get_by_text("リポジトリを追加", exact=True).click()
    await repos.get_by_label("リポジトリID・URL・ローカルパス").fill("github.com/example/project")
    await repos.get_by_role("button", name="追加", exact=True).click()
    await repos.get_by_text("github.com/example/project", exact=True).click()
    existing = repos.locator("details").filter(has=page.get_by_text("github.com/example/project", exact=True))
    await existing.get_by_label("編集可否").select_option("awi")
    await existing.get_by_role("button", name="属性を保存").click()
    await playwright.async_api.expect(page.locator("#settings-status")).to_have_text("設定を保存しました。")
    assert "github.com/example/project" in config_settings.load_config()["repositories"]
    page.once("dialog", lambda dialog: dialog.accept())
    await repos.get_by_role("button", name="登録を削除").click()
    await playwright.async_api.expect(repos.get_by_text("github.com/example/project", exact=True)).to_have_count(0)

    temporary = page.locator("section").filter(has=page.get_by_role("heading", name="一時指定", exact=True)).last
    await temporary.get_by_label("キー", exact=True).select_option("codex_fast_mode")
    await temporary.get_by_label("値", exact=True).fill("true")
    await temporary.get_by_label("期限（タイムゾーン付きISO 8601）").fill("2099-01-01T00:00:00+00:00")
    await temporary.get_by_role("button", name="一時指定を保存").click()
    key = page.locator('[data-setting="codex_fast_mode"]')
    await playwright.async_api.expect(key).to_contain_text("実効値: true（出所: 一時指定）")
    await temporary.get_by_role("button", name="一時指定を全解除").click()
    await playwright.async_api.expect(key).to_contain_text("実効値: false")

    preset = page.locator("details").filter(has=page.get_by_text("claude-primary", exact=True))
    await preset.get_by_text("claude-primary", exact=True).click()
    await preset.get_by_role("button", name="適用").click()
    await playwright.async_api.expect(page.locator("#settings-status")).to_have_text("設定を保存しました。")
    assert all(
        config_settings.load_config()[name] == value for name, value in config_settings.PRESET_MODELS["claude-primary"].items()
    )

import json
import pathlib
import subprocess

import playwright.async_api
import pytest

from agent_toolkit._atk import config, config_settings
from agent_toolkit._atk.serve import settings_routes
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
    await playwright.async_api.expect(page.locator("[data-setting]")).to_have_count(
        len(config_settings.MUTABLE_KEY_DEFAULTS) - 1
    )
    await page.get_by_role("navigation", name="設定の分類").get_by_text("atk serve", exact=True).click()
    port = page.locator('[data-setting="serve_port"]')
    await playwright.async_api.expect(port).to_contain_text("AGENT_TOOLKIT_CONFIG_SERVE_PORT")
    await port.get_by_label("保存値").fill("65536")
    await port.get_by_role("button", name="保存").click()
    await playwright.async_api.expect(port.get_by_role("alert")).to_contain_text("65535")
    assert "serve_port" not in config_settings.load_config()
    draft = page.locator('[data-setting="execute_model"]').get_by_label("保存値")
    await page.get_by_role("navigation", name="設定の分類").get_by_text("工程別モデル", exact=True).click()
    await draft.fill("codex:sol/high")
    await page.get_by_role("navigation", name="設定の分類").get_by_text("atk serve", exact=True).click()
    await port.get_by_label("保存値").fill("54002")
    await port.get_by_role("button", name="保存").click()
    await playwright.async_api.expect(page.get_by_text("設定を保存しました。", exact=True)).to_have_count(1)
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
    await page.get_by_role("navigation", name="設定の分類").get_by_text("atk serve", exact=True).click()
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

    nav = page.get_by_role("navigation", name="設定の分類")
    await nav.get_by_text("atk serve", exact=True).click()
    hosts = page.locator('[data-setting="remote_hosts"]')
    await hosts.get_by_label("追加するホスト").fill("example-host")
    await hosts.get_by_role("button", name="追加", exact=True).click()
    await playwright.async_api.expect(hosts.get_by_role("button", name="example-hostを削除")).to_be_visible()
    await hosts.get_by_role("button", name="example-hostを削除").click()
    await playwright.async_api.expect(hosts.get_by_role("button", name="example-hostを削除")).to_have_count(0)

    await nav.get_by_text("リポジトリ", exact=True).click()
    repos = page.locator("section").filter(has=page.get_by_role("heading", name="リポジトリ", exact=True)).last
    await repos.get_by_text("リポジトリを追加", exact=True).click()
    await repos.get_by_label("リポジトリID・URL・ローカルパス").fill("github.com/example/project")
    await repos.get_by_role("button", name="追加", exact=True).click()
    await repos.get_by_text("github.com/example/project", exact=True).click()
    existing = repos.locator("details").filter(has=page.get_by_text("github.com/example/project", exact=True))
    await existing.get_by_label("編集可否").select_option("awi")
    await existing.get_by_role("button", name="属性を保存").click()
    await playwright.async_api.expect(page.get_by_text("設定を保存しました。", exact=True)).to_have_count(1)
    assert "github.com/example/project" in config_settings.load_config()["repositories"]
    page.once("dialog", lambda dialog: dialog.accept())
    await repos.get_by_role("button", name="登録を削除").click()
    await playwright.async_api.expect(repos.get_by_text("github.com/example/project", exact=True)).to_have_count(0)

    await nav.get_by_text("一時指定", exact=True).click()
    temporary = page.locator("section").filter(has=page.get_by_role("heading", name="一時指定", exact=True)).last
    await temporary.get_by_label("キー", exact=True).select_option("codex_fast_mode")
    await temporary.get_by_label("値", exact=True).fill("true")
    await temporary.get_by_label("期限（このブラウザーの時刻）").fill("2099-01-01T00:00")
    await temporary.get_by_role("button", name="一時指定を保存").click()
    key = page.locator('[data-setting="codex_fast_mode"]')
    await playwright.async_api.expect(key).to_contain_text("実効値: true（出所: 一時指定）")
    await temporary.get_by_role("button", name="一時指定を全解除").click()
    await playwright.async_api.expect(key).to_contain_text("実効値: auto")

    await nav.get_by_text("工程別モデル", exact=True).click()
    preset = page.locator("details").filter(has=page.get_by_text("claude-primary", exact=True))
    await preset.get_by_text("claude-primary", exact=True).click()
    await preset.get_by_role("button", name="適用").click()
    await playwright.async_api.expect(page.get_by_text("設定を保存しました。", exact=True)).to_have_count(1)
    assert all(
        config_settings.load_config()[name] == value for name, value in config_settings.PRESET_MODELS["claude-primary"].items()
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_settings_host_scope_and_native_deadline(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/settings")
    expect = playwright.async_api.expect
    await expect(page.get_by_label("設定対象ホスト")).to_have_value("")
    await page.get_by_role("navigation", name="設定の分類").get_by_text("一時指定", exact=True).click()
    await expect(page.get_by_label("期限（このブラウザーの時刻）")).to_have_attribute("type", "datetime-local")
    await expect(page.get_by_role("heading", name="工程別モデル")).not_to_be_visible()
    await page.get_by_role("navigation", name="設定の分類").get_by_text("利用枠", exact=True).click()
    permission = page.locator('[data-setting="usage_reset_permissions"]')
    await permission.get_by_text("許可を追加").click()
    await permission.get_by_label("許可ID").fill("example")
    await permission.get_by_label("アカウント").fill("account")
    await permission.get_by_label("範囲").fill("five-hour")
    await permission.get_by_label("回数").fill("0")
    await permission.get_by_label("開始（このブラウザーの時刻）").fill("2099-01-01T00:00")
    await permission.get_by_label("終了（このブラウザーの時刻）").fill("2099-01-02T00:00")
    await permission.get_by_role("button", name="追加", exact=True).click()
    await expect(permission.get_by_text("example", exact=True)).to_be_visible()
    saved = json.loads(config_settings.load_config()["usage_reset_permissions"])
    expected_start = await page.evaluate("new Date('2099-01-01T00:00').toISOString()")
    assert saved[0]["from"] == expected_start


@pytest.mark.asyncio(loop_scope="session")
async def test_settings_switches_hosts_and_saves_through_remote_cli(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    config_settings.save_config({"remote_hosts": '["old-host", "new-host"]', "serve_host": "local"})
    remote_value = "remote"
    operations = []

    run = subprocess.run

    def ssh(args, **kwargs):
        nonlocal remote_value
        if args[0] != "ssh" or args[-2] not in {"old-host", "new-host"}:
            return run(args, check=kwargs.pop("check", False), **kwargs)
        assert args[-3] == "--"
        host, command = args[-2:]
        operations.append((host, command))
        if command == "uname -s":
            return subprocess.CompletedProcess(args, 0, b"Linux\n", b"")
        if host == "old-host":
            if command == "atk config snapshot":
                return subprocess.CompletedProcess(args, 2, b"", b"")
            assert command == "atk config show"
            return subprocess.CompletedProcess(args, 0, b"serve_host old\nserve_sessions_codex_home old-path\n", b"")
        assert host == "new-host"
        if command == "atk config update":
            assert json.loads(kwargs["input"]) == {"action": "set", "key": "serve_host", "value": "updated"}
            remote_value = "updated"
            return subprocess.CompletedProcess(args, 0, b"", b"")
        assert command == "atk config snapshot"
        snapshot = config.settings_snapshot(tmp_path)
        snapshot["settings"]["serve_host"].update(effective=remote_value, saved=remote_value)
        return subprocess.CompletedProcess(args, 0, json.dumps(snapshot).encode(), b"")

    monkeypatch.setattr(settings_routes, "run_with_timeout", ssh)
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/settings")
    expect = playwright.async_api.expect
    await page.get_by_role("navigation", name="設定の分類").get_by_text("atk serve", exact=True).click()
    picker = page.get_by_label("設定対象ホスト")
    await picker.select_option("old-host")
    legacy = page.locator('[data-setting="serve_sessions_codex_home"]')
    await expect(legacy).to_contain_text("読み取り専用")
    await expect(legacy.get_by_role("button")).to_have_count(0)
    response = await page.request.post(
        screen_harness.base_url + "/api/settings?host=old-host",
        data={"action": "set", "key": "serve_sessions_codex_home", "value": "blocked"},
    )
    assert response.status == 400
    await picker.select_option("new-host")
    port = page.locator('[data-setting="serve_host"]')
    await expect(port).to_contain_text("実効値: remote")
    await port.get_by_label("保存値").fill("updated")
    await port.get_by_role("button", name="保存").click()
    await expect(port).to_contain_text("実効値: updated")
    assert ("new-host", "atk config update") in operations
    assert config_settings.load_config()["serve_host"] == "local"
    await picker.select_option("")
    await expect(port).to_contain_text("実効値: local")

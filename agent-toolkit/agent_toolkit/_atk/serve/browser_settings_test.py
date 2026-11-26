import copy
import datetime
import json
import os
import pathlib
import re
import subprocess
import threading
import time

import playwright.async_api
import pytest

from agent_toolkit._atk import config, config_settings
from agent_toolkit._atk.serve import settings_routes
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _screen_harness_fixture,
    _ScreenHarness,
)
from agent_toolkit._common import usage_observation, usage_settings

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
    expect = playwright.async_api.expect
    warning = port.get_by_role("alert")
    await expect(warning).to_be_visible()
    await expect(warning).to_contain_text("serve_portの指定値は保存済み")
    await expect(warning).to_contain_text("実効値は別の設定を優先している: AGENT_TOOLKIT_CONFIG_SERVE_PORT")
    await expect(warning).to_contain_text(
        "環境変数AGENT_TOOLKIT_CONFIG_SERVE_PORTを解除し、atk config get serve_portで確かめる"
    )
    await expect(page.get_by_text("設定を保存しました。", exact=True)).to_have_count(0)
    assert config_settings.load_config()["serve_port"] == "54002"
    assert config.resolve_mutable_setting("serve_port") == "54001"
    await expect(port).to_contain_text("実効値: 54001")
    await expect(draft).to_have_value("codex:sol/high")
    monkeypatch.delenv("AGENT_TOOLKIT_CONFIG_SERVE_PORT")
    await page.reload()
    await page.get_by_role("navigation", name="設定の分類").get_by_text("atk serve", exact=True).click()
    await expect(port).to_contain_text("実効値: 54002")


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
    value = temporary.get_by_label("値", exact=True)
    await playwright.async_api.expect(value.locator("option")).to_have_text(
        ["自動判定（auto）", "高速（true）", "通常（false）"]
    )
    await value.select_option("true")
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
    await permission.get_by_text("詳細: アカウントの直接入力").click()
    await permission.get_by_label("アカウントを直接入力").fill("account")
    await permission.get_by_label("回数").fill("0")
    await permission.get_by_label("開始（このブラウザーの時刻）").fill("2099-01-01T00:00")
    await permission.get_by_label("終了（このブラウザーの時刻）").fill("2099-01-02T00:00")
    await permission.get_by_role("button", name="追加", exact=True).click()
    await expect(permission.locator(".permission-caption").first).to_contain_text("回数0（使わない）")
    saved = json.loads(config_settings.load_config()["usage_reset_permissions"])
    expected_start = await page.evaluate("new Date('2099-01-01T00:00').toISOString()")
    assert saved[0]["from"] == expected_start
    assert saved[0]["scope"] == "codex"
    assert re.fullmatch(r"codex-\d{8}-\d{6}-[a-f0-9]{8}", saved[0]["id"])


@pytest.mark.asyncio(loop_scope="session")
async def test_settings_switches_hosts_and_saves_through_remote_cli(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    config_settings.save_config({"remote_hosts": '["old-host", "new-host"]', "serve_host": "local"})
    remote_value = "remote"
    operations = []
    release_update = threading.Event()

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
            return subprocess.CompletedProcess(
                args, 0, b"serve_host old\nserve_sessions_codex_home old-path\nusage_reset_permissions []\n", b""
            )
        assert host == "new-host"
        if command == "atk config update":
            payload = json.loads(kwargs["input"])
            if payload == {"action": "set", "key": "serve_host", "value": "result-unknown"}:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            assert payload == {"action": "set", "key": "serve_host", "value": "updated"}
            assert release_update.wait(10)
            remote_value = "updated"
            return subprocess.CompletedProcess(args, 0, b"", b"")
        assert command in {"atk config snapshot", "atk config snapshot --read-only"}
        snapshot = config.settings_snapshot(tmp_path)
        snapshot["settings"]["serve_host"].update(effective=remote_value, saved=remote_value)
        return subprocess.CompletedProcess(args, 0, json.dumps(snapshot).encode(), b"")

    monkeypatch.setattr(settings_routes, "run_with_timeout", ssh)
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/settings")
    expect = playwright.async_api.expect
    status = page.locator("#settings-status")
    await page.get_by_role("navigation", name="設定の分類").get_by_text("atk serve", exact=True).click()
    picker = page.get_by_label("設定対象ホスト")
    await picker.select_option("old-host")
    legacy = page.locator('[data-setting="serve_sessions_codex_home"]')
    await expect(legacy).to_contain_text("読み取り専用")
    await expect(legacy.get_by_role("button")).to_have_count(0)
    groups = page.get_by_role("navigation", name="設定の分類")
    await groups.get_by_text("利用枠", exact=True).click()
    permissions = page.locator('[data-setting="usage_reset_permissions"]')
    await expect(permissions).to_contain_text("このホストのatkを更新すると画面で許可を編集できます")
    await expect(permissions.get_by_text("許可を追加", exact=True)).to_have_count(0)
    await groups.get_by_text("atk serve", exact=True).click()
    response = await page.request.post(
        screen_harness.base_url + "/api/settings?host=old-host",
        data={"action": "set", "key": "serve_sessions_codex_home", "value": "blocked"},
    )
    assert response.status == 400
    await picker.select_option("new-host")
    port = page.locator('[data-setting="serve_host"]')
    await expect(port).to_contain_text("実効値: remote")
    await port.get_by_label("保存値").fill("updated")
    save = port.get_by_role("button", name="保存")
    await page.set_viewport_size({"width": 320, "height": 720})
    await save.evaluate("(button) => button.scrollIntoView({block: 'end'})")
    await save.click()
    try:
        await expect(save).to_be_disabled()
        await expect(status).to_have_text("設定を保存中…")
        await expect(status).to_be_in_viewport()
    finally:
        release_update.set()
    await expect(save).to_be_enabled()
    await expect(status).to_be_empty()
    await expect(port).to_contain_text("実効値: updated")
    assert ("new-host", "atk config update") in operations
    assert config_settings.load_config()["serve_host"] == "local"
    await port.get_by_label("保存値").fill("result-unknown")
    await save.evaluate("(button) => button.scrollIntoView({block: 'end'})")
    await save.click()
    notice = port.get_by_role("alert")
    await expect(notice).to_contain_text("設定画面を再読み込みし、SSH先の現在の値を確認する")
    await expect(notice).to_be_in_viewport()
    await picker.select_option("")
    await expect(port).to_contain_text("実効値: local")


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("editing", ["model", "permission", "hosts"])
@pytest.mark.asyncio(loop_scope="session")
async def test_remote_save_remains_visible_while_editing_elsewhere(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, fails: bool, editing: str
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    config_settings.save_config({"remote_hosts": '["demo-host"]'})
    snapshot = config.settings_snapshot(tmp_path)
    hosts = snapshot["settings"]["remote_hosts"]
    hosts.update(saved='["first","second"]', effective='["first","second"]')
    release = threading.Event()

    def remote_update(_host: str, _payload: dict[str, object]) -> None:
        assert release.wait(10)
        if fails:
            raise ValueError(
                "接続先の保存結果を確認できません。リモートホストでは設定の保存が続いている可能性があります。"
                "再度保存する前に、設定画面を再読み込みして接続先の現在の値を確認してください。"
            )
        hosts.update(saved='["second"]', effective='["second"]')

    monkeypatch.setattr(settings_routes, "_remote_snapshot", lambda _host: (copy.deepcopy(snapshot), None))
    monkeypatch.setattr(settings_routes, "_remote_update", remote_update)
    page = screen_harness.page
    await page.set_viewport_size({"width": 320, "height": 720})
    await page.goto(screen_harness.base_url + "/settings")
    await page.get_by_label("設定対象ホスト").select_option("demo-host")
    nav = page.get_by_role("navigation", name="設定の分類")
    await nav.get_by_text("工程別モデル", exact=True).click()
    preset = page.locator('details[data-editor="preset:claude-primary"]')
    await preset.get_by_text("claude-primary", exact=True).click()
    await preset.get_by_role("button", name="適用").click()
    expect = playwright.async_api.expect
    status = page.locator("#settings-status")
    hosts_editor = page.locator('[data-setting="remote_hosts"]')
    try:
        if editing == "permission":
            await nav.get_by_text("利用枠", exact=True).click()
            add = page.locator('details[data-editor="usage_reset_permissions:"]')
            await add.get_by_text("許可を追加", exact=True).click()
            await add.get_by_text("詳細: アカウントの直接入力").click()
            draft = add.get_by_label("アカウントを直接入力")
            save = add.get_by_role("button", name="追加", exact=True)
        elif editing == "hosts":
            await nav.get_by_text("atk serve", exact=True).click()
            await expect(hosts_editor.get_by_role("button", name="firstを削除")).to_be_visible()
            draft = hosts_editor.get_by_label("追加するホスト")
            save = hosts_editor.get_by_role("button", name="追加", exact=True)
        else:
            await preset.get_by_text("claude-primary", exact=True).click()
            editor = page.locator('[data-setting="execute_model"]')
            draft = editor.get_by_label("保存値")
            save = editor.get_by_role("button", name="保存", exact=True)
        await expect(status).to_have_text("設定を保存中…")
        await expect(status).to_be_in_viewport(ratio=1)
        await draft.fill("demo")
        await draft.evaluate(
            """field => {
                const main = field.closest("main");
                main.scrollTop += field.getBoundingClientRect().bottom - main.getBoundingClientRect().bottom + 3;
            }"""
        )
        await expect(draft).to_be_in_viewport(ratio=1)
        await expect(save).to_be_disabled()
    finally:
        release.set()
    await expect(status).to_contain_text("接続先の保存結果を確認できません" if fails else "設定を保存しました。")
    await expect(status).to_be_in_viewport(ratio=1)
    await expect(draft).to_be_in_viewport(ratio=1)
    if editing == "hosts" and not fails:
        await expect(hosts_editor.get_by_role("button", name="firstを削除")).to_have_count(0)
    await expect(draft).to_be_focused()
    await page.keyboard.type("-continued")
    await expect(draft).to_have_value("demo-continued")
    await expect(save).to_be_enabled()


@pytest.mark.asyncio(loop_scope="session")
async def test_remote_settings_show_cached_snapshot_and_resolve_draft_conflict(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    config_settings.save_config({"remote_hosts": '["demo-host"]'})
    started = threading.Event()
    release = threading.Event()
    snapshot = config.settings_snapshot(tmp_path)
    snapshot["settings"]["serve_host"].update(effective="old-value", saved="old-value")
    calls = 0

    def remote_snapshot(_host: str):
        nonlocal calls
        calls += 1
        result = copy.deepcopy(snapshot)
        if calls > 1:
            started.set()
            if not release.wait(timeout=10):
                raise TimeoutError
            result["settings"]["serve_host"].update(effective="new-value", saved="new-value")
        return result, None

    monkeypatch.setattr(settings_routes, "_remote_snapshot", remote_snapshot)
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/settings")
    await page.get_by_role("navigation", name="設定の分類").get_by_text("atk serve", exact=True).click()
    picker = page.get_by_label("設定対象ホスト")
    editor = page.locator('[data-setting="serve_host"]')
    field = editor.get_by_label("保存値")
    expect = playwright.async_api.expect
    assert calls == 0
    await picker.select_option("demo-host")
    await expect(field).to_have_value("old-value")
    await picker.select_option("")
    await expect(field).not_to_have_value("old-value")
    start = time.monotonic()
    try:
        await picker.select_option("demo-host")
        await expect(field).to_have_value("old-value", timeout=1000)
        assert time.monotonic() - start < 1
        await expect(page.locator("#settings-status")).to_contain_text("更新中")
        await expect(editor.get_by_role("button", name="保存")).to_be_disabled()
        await field.fill("my-draft")
    finally:
        release.set()
    await expect(editor).to_contain_text("最新の保存値: new-value")
    assert started.is_set()
    await expect(field).to_have_value("my-draft")
    await expect(editor.get_by_role("button", name="保存", exact=True)).to_be_disabled()
    await editor.get_by_role("button", name="最新の値を取り込む").click()
    await expect(field).to_have_value("new-value")
    await expect(editor.get_by_role("button", name="保存", exact=True)).to_be_enabled()


@pytest.mark.asyncio(loop_scope="session")
async def test_remote_settings_failure_keeps_cache_read_only_and_can_retry(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    config_settings.save_config({"remote_hosts": '["cached-host", "uncached-host"]'})
    snapshot = config.settings_snapshot(tmp_path)
    snapshot["settings"]["serve_host"].update(effective="cached-value", saved="cached-value")
    failures: dict[str, str] = {"uncached-host": "SSH先で設定を取得できません"}
    calls: list[str] = []

    def remote_snapshot(host: str):
        calls.append(host)
        if host in failures:
            raise ValueError(failures[host])
        return copy.deepcopy(snapshot), None

    monkeypatch.setattr(settings_routes, "_remote_snapshot", remote_snapshot)
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/settings")
    status = page.locator("#settings-status")
    picker = page.get_by_label("設定対象ホスト")
    editor = page.locator('[data-setting="serve_host"]')
    field = editor.get_by_label("保存値")
    expect = playwright.async_api.expect
    await page.get_by_role("navigation", name="設定の分類").get_by_text("atk serve", exact=True).click()
    assert not calls
    await picker.select_option("uncached-host")
    await expect(page.locator("#update-status")).to_contain_text(failures["uncached-host"])
    await expect(editor).to_have_count(0)
    await expect(page.locator("#update-status").get_by_role("button", name="再取得")).to_be_enabled()

    await picker.select_option("cached-host")
    await expect(field).to_have_value("cached-value")
    await picker.select_option("")
    await expect(field).not_to_have_value("cached-value")
    error = "SSH先で設定を非昇格で取得できません。接続先でユーザーがログオンしていることを確認してください"
    failures["cached-host"] = error
    await picker.select_option("cached-host")
    await expect(page.locator("#update-status")).to_contain_text(error)
    await expect(status).to_contain_text("最新の値が届くまで保存できません")
    await expect(field).to_have_value("cached-value")
    await expect(editor.get_by_role("button", name="保存", exact=True)).to_be_disabled()

    del failures["cached-host"]
    snapshot["settings"]["serve_host"].update(effective="recovered-value", saved="recovered-value")
    await page.locator("#update-status").get_by_role("button", name="再取得").click()
    await expect(field).to_have_value("recovered-value")
    await expect(editor.get_by_role("button", name="保存", exact=True)).to_be_enabled()


@pytest.mark.asyncio(loop_scope="session")
async def test_remote_permission_conflict_preserves_entry_written_during_refresh(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    config_settings.save_config({"remote_hosts": '["demo-host"]'})
    release = threading.Event()
    snapshot = config.settings_snapshot(tmp_path)
    key = "usage_reset_permissions"
    calls = 0
    pending: list[dict] = []
    other = {
        "id": "other-writer",
        "engine": "codex",
        "account": "other-account",
        "scope": "codex",
        "count": 1,
        "from": "2099-01-01T00:00:00Z",
        "until": "2099-01-02T00:00:00Z",
    }

    def remote_snapshot(_host: str):
        nonlocal calls
        calls += 1
        result = copy.deepcopy(snapshot)
        if calls > 1:
            if not release.wait(timeout=8):
                raise TimeoutError
            result["settings"][key].update(saved=json.dumps(pending or [other]))
        return result, None

    def remote_update(_host: str, payload: dict):
        assert payload["key"] == key
        pending[:] = json.loads(payload["value"])

    monkeypatch.setattr(settings_routes, "_remote_snapshot", remote_snapshot)
    monkeypatch.setattr(settings_routes, "_remote_update", remote_update)
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/settings")
    await page.get_by_role("navigation", name="設定の分類").get_by_text("利用枠", exact=True).click()
    picker = page.get_by_label("設定対象ホスト")
    editor = page.locator(f'[data-setting="{key}"]')
    new_permission = editor.locator('details[data-editor="usage_reset_permissions:"]')
    add = new_permission.get_by_text("許可を追加", exact=True)
    expect = playwright.async_api.expect
    await picker.select_option("demo-host")
    await expect(add).to_be_visible()
    await picker.select_option("")
    await expect(page.locator("#settings-status")).to_be_empty()

    async def draft():
        await add.click()
        await new_permission.get_by_text("詳細: アカウントの直接入力").click()
        await new_permission.get_by_label("アカウントを直接入力").fill("my-account")
        await new_permission.get_by_label("回数").fill("1")
        await new_permission.get_by_label("開始（このブラウザーの時刻）").fill("2099-02-01T00:00")
        await new_permission.get_by_label("終了（このブラウザーの時刻）").fill("2099-02-02T00:00")

    try:
        await picker.select_option("demo-host")
        await expect(page.locator("#settings-status")).to_contain_text("更新中")
        await draft()
        await expect(editor.get_by_role("button", name="追加", exact=True)).to_be_disabled()
    finally:
        release.set()
    await expect(editor).to_contain_text("最新の保存値: other-writer")
    await expect(editor.get_by_label("アカウントを直接入力")).to_have_value("my-account")
    await expect(editor.get_by_role("button", name="追加", exact=True)).to_be_disabled()
    await editor.get_by_role("button", name="最新の値を取り込む").click()
    captions = editor.locator('.permission-caption:not([title="許可を追加"])')
    await expect(captions).to_contain_text("other-writer")
    await draft()
    await editor.get_by_role("button", name="追加", exact=True).click()
    await expect(captions).to_have_count(2)
    assert pending[0] == other
    assert pending[1]["account"] == "my-account"


@pytest.mark.parametrize("grace", [1, 2, 3])
@pytest.mark.asyncio(loop_scope="session")
async def test_observed_codex_account_and_weekly_cutoff_save_without_typing_identifiers(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, grace: int
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    account = "fixture-account"
    now = int(time.time())
    day = 86400
    reset = (now + 7 * day // 2) // 60 * 60 + 37
    usage_settings.record_metadata(
        "codex",
        {
            "account": "fixture-workspace",
            "permission_account": account,
            "contract": "promax",
            "weekly_window": {"window_id": "codex:secondary", "resets_at": reset},
        },
    )
    usage_observation.observe(
        [
            usage_observation.QuotaWindow(
                "codex", "fixture-workspace", "codex:secondary", 80, reset, now, contract="promax", duration_seconds=7 * day
            )
        ],
        setting_signature="fixture",
        active_seconds=0,
    )
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/settings")
    await page.get_by_role("navigation", name="設定の分類").get_by_text("利用枠", exact=True).click()
    section = page.locator('[data-setting="usage_reset_permissions"]')
    await section.get_by_text("許可を追加", exact=True).click()
    expect = playwright.async_api.expect
    await expect(section.get_by_label("アカウント", exact=True)).to_have_value(account)
    await section.get_by_role("button", name="今", exact=True).click()
    await section.get_by_label("リセット前の猶予").select_option(str(grace))
    if grace == 1:
        await expect(section).to_contain_text("おすすめ: 1回、今から次の週次リセットの1日前まで")
        await section.get_by_role("button", name="この提案を入力").click()
        assert "usage_reset_permissions" not in config_settings.load_config()
    if grace == 3:
        await expect(section).to_contain_text("今週は許可不要")
    await section.get_by_role("button", name=f"次の週次リセットの{grace}日前まで").click()
    await section.get_by_role("button", name="追加", exact=True).click()
    await expect(section).to_contain_text("期間内")
    saved = section.get_by_text("設定を保存しました。", exact=True)
    await expect(saved).to_be_visible()
    await expect(saved).to_be_focused()
    await expect(section.get_by_role("button", name="追加", exact=True)).to_be_hidden()
    raw = config_settings.load_config()["usage_reset_permissions"]
    usage_settings.validate_permissions(raw)
    permit = json.loads(raw)[0]
    assert permit["account"] == account
    assert permit["scope"] == "codex"
    assert permit["count"] == 1
    assert re.fullmatch(r"codex-\d{8}-\d{6}-[0-9a-f]{8}", permit["id"])
    assert datetime.datetime.fromisoformat(permit["until"]).timestamp() == reset - grace * day


@pytest.mark.asyncio(loop_scope="session")
async def test_settings_periodic_read_preserves_draft_and_detects_conflict(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    page = screen_harness.page
    await page.clock.install()
    await page.goto(screen_harness.base_url + "/settings")
    await page.get_by_role("navigation", name="設定の分類").get_by_text("atk serve", exact=True).click()
    editor = page.locator('[data-setting="serve_host"]')
    field = editor.get_by_label("保存値")
    await field.fill("draft-host")
    config.update_settings({"action": "set", "key": "serve_host", "value": "saved-host"})
    await page.clock.fast_forward(30_000)
    expect = playwright.async_api.expect
    await expect(editor.locator("[data-conflict]")).to_be_visible()
    await expect(field).to_have_value("draft-host")


@pytest.mark.asyncio(loop_scope="session")
async def test_settings_search_and_unsaved_navigation(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/settings")
    expect = playwright.async_api.expect
    search = page.get_by_label("設定を絞り込む")
    await search.fill("serve_host")
    editor = page.locator('[data-setting="serve_host"]')
    await expect(editor).to_be_visible()
    await expect(page.locator('[data-setting="execute_model"]')).to_be_hidden()
    await editor.get_by_label("保存値").fill("draft-host")
    await expect(page.locator("#settings-unsaved")).to_contain_text("未保存の変更: 1件")
    page.once("dialog", lambda dialog: dialog.dismiss())
    await page.get_by_role("navigation", name="画面").get_by_role("link", name="セッション").click()
    await expect(editor.get_by_label("保存値")).to_have_value("draft-host")
    await page.get_by_role("button", name="未保存の変更を取り消す").click()
    await expect(page.locator("#settings-unsaved")).to_be_empty()


@pytest.mark.asyncio(loop_scope="session")
async def test_settings_common_edit_survives_host_switch_and_saves_locally(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    snapshot = config.settings_snapshot(tmp_path)
    shared = snapshot["settings"]["execute_model"]
    shared.update(shared=True, shared_sync=True, saved="claude:sonnet/medium", effective="claude:sonnet/medium")
    snapshot.update(shared_identity="same", hosts=["remote"])
    peer = copy.deepcopy(snapshot)
    snapshot["comparison"] = {"remote": {"state": "ready", **peer}}
    posts = []

    async def settings(route):
        is_remote = "host=remote" in route.request.url
        if route.request.method == "POST":
            assert not is_remote
            posts.append(route.request.post_data_json)
            shared["saved"] = posts[-1]["value"]
            await route.fulfill(json={"saved": True})
        else:
            await route.fulfill(json=peer if is_remote else snapshot)

    page = screen_harness.page
    await page.route("**/api/settings**", settings)
    await page.goto(screen_harness.base_url + "/settings")
    expect = playwright.async_api.expect
    editor = page.locator('#settings-shared [data-setting="execute_model"]')
    await editor.get_by_label("保存値").fill("codex:sol/high")
    await page.get_by_label("設定対象ホスト").select_option("remote")
    await expect(editor.get_by_label("保存値")).to_have_value("codex:sol/high")
    await expect(page.locator('#settings-content [data-setting="execute_model"]')).to_have_count(0)
    await editor.get_by_role("button", name="保存").click()
    await expect(editor.get_by_role("alert")).to_contain_text("設定を保存しました")
    assert posts == [{"action": "set", "key": "execute_model", "value": "codex:sol/high"}]
    peer["shared_identity"] = "different-branch"
    picker = page.get_by_label("設定対象ホスト")
    await picker.select_option("")
    await picker.select_option("remote")
    await expect(page.locator('#settings-content [data-setting="execute_model"]')).to_have_count(1)
    if directory := os.environ.get("ATK_SERVE_SCREENSHOT_DIR"):
        compare = page.locator("#settings-compare")
        await compare.locator("summary").click()
        for width in (1440, 400):
            await page.set_viewport_size({"width": width, "height": 900})
            await compare.scroll_into_view_if_needed()
            await page.screenshot(path=pathlib.Path(directory) / f"003-comparison-{width}.png")

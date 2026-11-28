import asyncio
import copy
import json
import pathlib
import re
import subprocess
import threading
import time

import playwright.async_api
import pytest

from agent_toolkit._atk import config, config_settings, usage_refresh
from agent_toolkit._atk.serve import settings_routes
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _screen_harness_fixture,
    _ScreenHarness,
)

pytestmark = [pytest.mark.browser]


@pytest.fixture(autouse=True)
def isolate_observation_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(usage_refresh, "refresh", lambda **_kwargs: [])


async def _open_setting(row: playwright.async_api.Locator) -> None:
    summary = row.locator(":scope > summary")
    await playwright.async_api.expect(summary).to_be_visible()
    if await row.get_attribute("open") is None:
        await summary.click()
    await playwright.async_api.expect(row).to_have_attribute("open", "")


@pytest.mark.asyncio(loop_scope="session")
async def test_host_selection_reuses_prefetch_and_ignores_late_values(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    config_settings.save_config({"remote_hosts": '["alpha", "beta"]'})
    local = config.settings_snapshot(tmp_path)
    releases = {host: threading.Event() for host in ("alpha", "beta")}
    started = {host: threading.Event() for host in releases}
    calls: list[str] = []

    def remote_snapshot(host: str, *, read_only: bool):
        assert read_only
        calls.append(host)
        started[host].set()
        assert releases[host].wait(6)
        snapshot = copy.deepcopy(local)
        value = f"{host}-{calls.count(host)}"
        snapshot["settings"]["serve_host"].update(effective=value, saved=value)
        return snapshot, None

    monkeypatch.setattr(settings_routes, "_remote_snapshot", remote_snapshot)
    page = screen_harness.page
    base_url = screen_harness.base_url
    picker = page.get_by_label("設定対象ホスト")
    editor = page.locator('[data-scope="host"] [data-setting="serve_host"]')
    save = editor.get_by_role("button", name="保存", exact=True)
    expect = playwright.async_api.expect
    try:
        await page.goto(base_url + "/")
        await expect(picker.locator('option[value="alpha"]')).to_have_count(1)
        assert await asyncio.to_thread(started["alpha"].wait, 3)
        assert await asyncio.to_thread(started["beta"].wait, 3)
        assert await page.locator("body").get_attribute("data-screen") == "wi"
        await page.get_by_role("navigation", name="画面").get_by_role("link", name="設定").click()
        await page.go_back()
        await page.go_forward()
        assert [url for url in screen_harness.requests if url.endswith("/api/settings/prepare")] == [
            base_url + "/api/settings/prepare"
        ]
        await page.get_by_role("navigation", name="設定の分類").get_by_text("atk serve", exact=True).click()
        async with page.expect_request(lambda request: "host=alpha" in request.url and "wait=1" in request.url):
            await picker.select_option("alpha")
        await expect(page.locator("#settings-host-progress")).to_be_visible()
        await expect(editor).to_have_count(0)
        await picker.select_option("beta")
        releases["beta"].set()
        await expect(editor.locator(":scope > summary")).to_contain_text("beta-1")
        await _open_setting(editor)
        await expect(save).to_be_enabled()
        async with page.expect_response(lambda response: "host=alpha" in response.url and "wait=1" in response.url) as late:
            releases["alpha"].set()
        assert (await late.value).ok
        await expect(editor.locator(":scope > summary")).to_contain_text("beta-1")
        assert sorted(calls) == ["alpha", "beta"]
        await picker.select_option("")
        await expect(editor.locator(":scope > summary")).not_to_contain_text("beta-1")
        releases["beta"].clear()
        started["beta"].clear()
        await picker.select_option("beta")
        assert await asyncio.to_thread(started["beta"].wait, 3)
        await expect(editor.locator(":scope > summary")).to_contain_text("beta-1")
        await expect(page.locator("#settings-status")).to_contain_text("前回取得した設定を表示中")
        await expect(save).to_be_disabled()
        releases["beta"].set()
        await expect(editor.locator(":scope > summary")).to_contain_text("beta-2")
        await expect(save).to_be_enabled()
        assert sorted(calls) == ["alpha", "beta", "beta"]
    finally:
        for release in releases.values():
            release.set()


@pytest.mark.cpu_limit(1.0, reason="実サーバーでの入力検証、保存後の実効値、再読み込み後の設定値を確認する。")
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
    await playwright.async_api.expect(port.get_by_label("保存値")).to_be_hidden()
    await _open_setting(port)
    details = port.get_by_text("形式・出所", exact=True)
    source = port.get_by_text("出所: AGENT_TOOLKIT_CONFIG_SERVE_PORT", exact=True)
    await playwright.async_api.expect(source).to_be_hidden()
    await details.click()
    await playwright.async_api.expect(source).to_be_visible()
    await details.click()
    await port.get_by_label("保存値").fill("65536")
    await port.get_by_role("button", name="保存").click()
    await playwright.async_api.expect(port.get_by_role("alert")).to_contain_text("65535")
    assert "serve_port" not in config_settings.load_config()
    model = page.locator('[data-setting="execute_model"]')
    draft = model.get_by_label("保存値")
    await page.get_by_role("navigation", name="設定の分類").get_by_text("工程別モデル", exact=True).click()
    await playwright.async_api.expect(model.locator(":scope > summary strong")).to_have_text("execute_model")
    summary = model.locator(":scope > summary")
    await summary.focus()
    await playwright.async_api.expect(summary).to_have_accessible_description(re.compile("実装のモデル"))
    await summary.press("Enter")
    await draft.focus()
    await playwright.async_api.expect(draft).to_have_accessible_description(re.compile("実装のモデル"))
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
    await expect(port.locator(":scope > summary")).to_contain_text("54001")
    await expect(draft).to_have_value("codex:sol/high")
    monkeypatch.delenv("AGENT_TOOLKIT_CONFIG_SERVE_PORT")
    await page.reload()
    await page.get_by_role("navigation", name="設定の分類").get_by_text("atk serve", exact=True).click()
    await expect(port.locator(":scope > summary")).to_contain_text("54002")


@pytest.mark.asyncio(loop_scope="session")
async def test_settings_save_notice_is_visible_at_bottom_of_narrow_screen(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    page = screen_harness.page
    await page.set_viewport_size({"width": 320, "height": 360})
    await page.goto(screen_harness.base_url + "/settings")
    await page.get_by_role("combobox", name="分類", exact=True).select_option(label="atk serve")
    port = page.locator('[data-setting="serve_port"]')
    await port.locator(":scope > summary").focus()
    await page.keyboard.press("Enter")
    await port.get_by_label("保存値").fill("54002")
    save = port.get_by_role("button", name="保存")
    await save.focus()
    await save.evaluate("(button) => { button.closest('main').scrollTop += button.getBoundingClientRect().bottom - 345; }")
    await page.keyboard.press("Enter")
    notice = port.get_by_role("status")
    await playwright.async_api.expect(notice).to_be_focused()
    await playwright.async_api.expect(notice).to_contain_text("→ 54002。反映:")
    await playwright.async_api.expect(notice).to_be_in_viewport(ratio=1)
    summary = port.locator(":scope > summary")
    await playwright.async_api.expect(summary).to_contain_text("実行中:")
    await playwright.async_api.expect(summary).to_contain_text("次回起動: 54002")
    await summary.click()
    await playwright.async_api.expect(summary).to_contain_text("次回起動: 54002")


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
    await _open_setting(hosts)
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
    await playwright.async_api.expect(value.locator("option")).to_have_text(["自動判定", "高速", "通常"])
    await value.select_option("true")
    await temporary.get_by_label("期限（このブラウザーの時刻）").fill("2099-01-01T00:00")
    await temporary.get_by_role("button", name="一時指定を保存").click()
    key = page.locator('[data-setting="codex_fast_mode"]')
    await playwright.async_api.expect(key).to_contain_text("実効値: 高速")
    await playwright.async_api.expect(key).to_contain_text("出所: 一時指定")
    await temporary.get_by_role("button", name="一時指定を全解除").click()
    await playwright.async_api.expect(key).to_contain_text("実効値: 自動判定")

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
    for key in ("usage_reset_permissions", "codex_auto_reset", "claude_auto_reset"):
        await expect(page.locator(f'[data-setting="{key}"]')).to_have_count(0)


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
            if command in {"atk config snapshot", "atk config snapshot --read-only"}:
                return subprocess.CompletedProcess(args, 2, b"", b"")
            if command == "atk config snapshot --help":
                return subprocess.CompletedProcess(args, 0, b"usage: atk config snapshot", b"")
            assert command == "atk config show"
            return subprocess.CompletedProcess(
                args,
                0,
                b"serve_host old\ncodex_fast_mode auto\nserve_sessions_codex_home old-path\nusage_reset_permissions []\n",
                b"",
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
        snapshot["codex_fast_start"] = {
            "speed": "高速",
            "source": "直前の成功判定",
            "at": "2026-11-26T12:00:00+09:00",
            "reason": "Business",
        }
        return subprocess.CompletedProcess(args, 0, json.dumps(snapshot).encode(), b"")

    monkeypatch.setattr(settings_routes, "run_with_timeout", ssh)
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/settings")
    expect = playwright.async_api.expect
    status = page.locator("#settings-status")
    await page.get_by_role("navigation", name="設定の分類").get_by_text("atk serve", exact=True).click()
    picker = page.get_by_label("設定対象ホスト")
    fast = page.locator("#settings-fast-state")
    await expect(fast).to_contain_text("（ローカル）: 記録なし")
    await picker.select_option("old-host")
    await expect(
        page.get_by_text("SSH先のatkが旧バージョンです。設定を確認するにはSSH先のatkを更新してください。", exact=False)
    ).to_be_visible()
    await expect(page.get_by_text("old-host: 旧バージョンのため自動取得なし", exact=False)).to_have_count(1)
    old = await page.request.get(screen_harness.base_url + "/api/settings?host=old-host")
    assert old.status == 400 and (await old.json())["error"] == "SSH先のatkが旧バージョンです"
    assert ("old-host", "atk config show") not in operations
    response = await page.request.post(
        screen_harness.base_url + "/api/settings?host=old-host",
        data={"action": "set", "key": "serve_sessions_codex_home", "value": "blocked"},
    )
    assert response.status == 400
    await picker.select_option("new-host")
    await page.get_by_role("navigation", name="設定の分類").get_by_text("atk serve", exact=True).click()
    port = page.locator('[data-setting="serve_host"]')
    await expect(port).to_contain_text("実効値: remote")
    await expect(fast).to_contain_text("（new-host）: 前回の判定を使用")
    await _open_setting(port)
    await port.get_by_label("保存値").fill("updated")
    save = port.get_by_role("button", name="保存")
    await page.set_viewport_size({"width": 320, "height": 720})
    await save.evaluate("(button) => button.scrollIntoView({block: 'end'})")
    await save.click()
    try:
        pending = port.get_by_role("button", name="保存: 保存中")
        await expect(pending).to_be_disabled()
        await expect(pending).to_have_attribute("data-saving", "")
        await expect(pending).to_be_in_viewport()
        await expect(status).to_be_empty()
    finally:
        release_update.set()
    await expect(save).to_be_enabled()
    await expect(status).to_be_empty()
    await expect(port).to_contain_text("実効値: updated")
    await expect(port.get_by_role("status")).to_have_text(
        "保存済み（このホスト）: remote → updated。反映: 次回のatk serve起動時。"
    )
    assert ("new-host", "atk config update") in operations
    assert config_settings.load_config()["serve_host"] == "local"
    await port.get_by_label("保存値").fill("result-unknown")
    await save.evaluate("(button) => button.scrollIntoView({block: 'end'})")
    await save.click()
    notice = port.get_by_role("alert")
    await expect(notice).to_contain_text("設定画面を再読み込みし、SSH先の現在の値を確認する")
    await expect(notice).to_be_in_viewport()
    await picker.select_option("")
    await expect(port).to_contain_text("実行中: 127.0.0.1")
    await expect(port).to_contain_text("次回起動: local")
    await expect(fast).to_contain_text("（ローカル）: 記録なし")


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("editing", ["model", "server", "hosts"])
@pytest.mark.asyncio(loop_scope="session")
async def test_remote_save_result_remains_visible_while_editing_elsewhere(
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

    monkeypatch.setattr(settings_routes, "_remote_snapshot", lambda _host, **_kwargs: (copy.deepcopy(snapshot), None))
    monkeypatch.setattr(settings_routes, "_remote_update", remote_update)
    page = screen_harness.page
    await page.set_viewport_size({"width": 320, "height": 720})
    await page.goto(screen_harness.base_url + "/settings")
    await page.get_by_label("設定対象ホスト").select_option("demo-host")
    nav = page.get_by_role("combobox", name="分類", exact=True)
    await nav.select_option(label="工程別モデル")
    preset = page.locator('details[data-editor="preset:claude-primary"]')
    await preset.get_by_text("claude-primary", exact=True).click()
    await preset.get_by_role("button", name="適用").click()
    expect = playwright.async_api.expect
    status = page.locator("#settings-status")
    hosts_editor = page.locator('[data-setting="remote_hosts"]')
    try:
        if editing == "server":
            await nav.select_option(label="atk serve")
            editor = page.locator('[data-setting="serve_host"]')
            await _open_setting(editor)
            draft = editor.get_by_label("保存値")
            save = editor.get_by_role("button", name="保存", exact=True)
        elif editing == "hosts":
            await nav.select_option(label="atk serve")
            await _open_setting(hosts_editor)
            await expect(hosts_editor.get_by_role("button", name="firstを削除")).to_be_visible()
            draft = hosts_editor.get_by_label("追加するホスト")
            save = hosts_editor.get_by_role("button", name="追加", exact=True)
        else:
            await preset.get_by_text("claude-primary", exact=True).click()
            editor = page.locator('[data-setting="execute_model"]')
            await _open_setting(editor)
            draft = editor.get_by_label("保存値")
            save = editor.get_by_role("button", name="保存", exact=True)
        await expect(status).to_be_empty()
        await expect(preset.locator("button[data-saving]")).to_be_disabled()
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
    update_started = threading.Event()
    release = threading.Event()
    snapshot = config.settings_snapshot(tmp_path)
    snapshot["settings"]["serve_host"].update(effective="old-value", saved="old-value")
    calls = 0

    def remote_snapshot(_host: str, **_kwargs):
        nonlocal calls
        calls += 1
        result = copy.deepcopy(snapshot)
        if update_started.is_set():
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
    await picker.select_option("demo-host")
    await expect(field).to_have_value("old-value")
    await picker.select_option("")
    await expect(field).not_to_have_value("old-value")
    start = time.monotonic()
    update_started.set()
    try:
        await picker.select_option("demo-host")
        await expect(field).to_have_value("old-value", timeout=1000)
        assert time.monotonic() - start < 1
        await expect(page.get_by_role("progressbar", name="設定の取得")).to_be_visible()
        await _open_setting(editor)
        await expect(editor.get_by_role("button", name="保存")).to_be_disabled()
        await field.fill("my-draft")
    finally:
        release.set()
    await expect(editor).to_contain_text("最新の保存値: new-value")
    assert started.is_set()
    await expect(field).to_have_value("my-draft")
    await expect(editor.get_by_role("button", name="保存", exact=True)).to_be_disabled()
    await editor.get_by_role("button", name="保存値を使う").click()
    await expect(field).to_have_value("new-value")
    await expect(editor.get_by_role("button", name="保存", exact=True)).to_be_enabled()


@pytest.mark.cpu_limit(0.8, reason="複数ホストの未取得・キャッシュ・復旧を、実サーバーの読込と再取得で確認する。")
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

    def remote_snapshot(host: str, **_kwargs):
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
    await picker.select_option("uncached-host")
    await expect(page.locator("#update-status")).to_contain_text("uncached-hostの設定を取得できません。")
    fast = page.locator("#settings-fast-state")
    await expect(fast).to_contain_text("（uncached-host）: 取得できませんでした")
    await expect(editor).to_have_count(0)
    await expect(page.locator("#update-status").get_by_role("button", name="再取得")).to_be_enabled()

    del failures["uncached-host"]
    await page.locator("#update-status").get_by_role("button", name="再取得").click()
    await expect(fast).to_contain_text("（uncached-host）: 記録なし")
    await picker.select_option("cached-host")
    await expect(field).to_have_value("cached-value")
    await picker.select_option("")
    await expect(field).not_to_have_value("cached-value")
    error = "SSH先で設定を非昇格で取得できません。接続先でユーザーがログオンしていることを確認してください"
    failures["cached-host"] = error
    await picker.select_option("cached-host")
    await expect(page.locator("#update-status")).to_contain_text("cached-hostの設定を取得できません。")
    await expect(status).to_contain_text("前回取得した設定を表示中。保存するには再取得してください。")
    await expect(fast).to_contain_text("記録なし（前回取得）")
    await expect(field).to_have_value("cached-value")
    await _open_setting(editor)
    await expect(editor.get_by_role("button", name="保存", exact=True)).to_be_disabled()

    del failures["cached-host"]
    snapshot["settings"]["serve_host"].update(effective="recovered-value", saved="recovered-value")
    await page.locator("#update-status").get_by_role("button", name="再取得").click()
    await expect(field).to_have_value("recovered-value")
    await expect(editor.get_by_role("button", name="保存", exact=True)).to_be_enabled()


@pytest.mark.asyncio(loop_scope="session")
async def test_saved_settings_are_not_reported_as_failed_when_readback_fails(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config_settings.save_config({"serve_port": "54001"})
    saved = False

    async def settings(route: playwright.async_api.Route) -> None:
        nonlocal saved
        if route.request.method == "POST":
            response = await route.fetch()
            saved = response.ok
            await route.fulfill(response=response)
        elif saved:
            await route.fulfill(status=503, json={"error": "設定を取得できません。"})
        else:
            await route.continue_()

    page = screen_harness.page
    await page.route("**/api/settings**", settings)
    await page.goto(screen_harness.base_url + "/settings")
    await page.get_by_role("navigation", name="設定の分類").get_by_text("atk serve", exact=True).click()
    port = page.locator('[data-setting="serve_port"]')
    await _open_setting(port)
    field = port.get_by_label("保存値")
    await field.fill("54002")
    await port.get_by_role("button", name="保存", exact=True).click()
    expect = playwright.async_api.expect
    await expect(port.get_by_role("alert")).to_contain_text("保存済みです。最新の値を取得できなかった")
    await expect(field).to_have_value("54002")
    assert config_settings.load_config()["serve_port"] == "54002"


@pytest.mark.asyncio(loop_scope="session")
async def test_selected_legacy_host_explanation_survives_an_unrelated_connection_failure(
    screen_harness: _ScreenHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings_routes, "_remote_hosts", lambda *, read_only=False: ("offline", "legacy"))

    def snapshot(host, *, read_only):
        assert read_only
        if host == "legacy":
            raise settings_routes._ReadOnlyUnsupported  # noqa: SLF001  # pylint: disable=protected-access
        raise OSError("SSH unavailable")

    monkeypatch.setattr(settings_routes, "_remote_snapshot", snapshot)
    page = screen_harness.page
    expect = playwright.async_api.expect
    await page.clock.install()
    await page.goto(screen_harness.base_url + "/settings")
    picker = page.get_by_label("設定対象ホスト")
    update = page.locator("#update-status")
    status = page.locator("#settings-status")
    await expect(picker.locator('option[value="legacy"]')).to_have_count(1)
    await page.clock.fast_forward(30_000)
    await expect(update).to_contain_text("offline")
    await picker.select_option("legacy")
    await expect(status).to_contain_text("SSH先のatkが旧バージョンです。設定を確認するにはSSH先のatkを更新してください。")
    await expect(update).to_be_visible()
    await picker.select_option("")
    await expect(status).to_be_empty()


@pytest.mark.asyncio(loop_scope="session")
async def test_shared_comparison_names_only_differing_settings(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page

    async def settings(route: playwright.async_api.Route) -> None:
        response = await route.fetch()
        snapshot = await response.json()
        snapshot["shared_identity"] = "same"
        shared_keys = {"execute_model", "review_model", "chores_model"}
        for key in shared_keys:
            snapshot["settings"][key].update(saved="local", shared=True, migration_pending=False)
        peer_values = {key: {"saved": "remote" if key != "chores_model" else "local", "shared": True} for key in shared_keys}
        snapshot["comparison"] = {
            "alpha": {"state": "ready", "shared_identity": "same", "settings": peer_values},
            "beta": {"state": "ready", "shared_identity": "other", "settings": peer_values},
            "gamma": {"state": "legacy", "settings": {}},
        }
        await route.fulfill(response=response, json=snapshot)

    await page.route("**/api/settings", settings)
    await page.goto(screen_harness.base_url + "/settings")
    await page.get_by_text("ホスト別の設定を比較", exact=True).click()
    summary = page.locator("#settings-shared")
    expect = playwright.async_api.expect
    await expect(summary).to_contain_text("alpha: 共有値に差あり")
    await expect(summary).to_contain_text("execute_model")
    await expect(summary).to_contain_text("review_model")
    await expect(summary).not_to_contain_text("chores_model")
    await expect(summary).to_contain_text("beta: 共有先の一致は未確認")
    await expect(summary).to_contain_text("gamma: 旧バージョンのため自動取得なし")

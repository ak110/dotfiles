import copy
import datetime
import json
import os
import pathlib
import re
import threading
import time

import playwright.async_api
import pytest

from agent_toolkit._atk import config, config_settings
from agent_toolkit._atk.serve import settings_routes
from agent_toolkit._atk.serve.browser_settings_test import (  # noqa: F401  # pylint: disable=unused-import
    _open_setting,
    isolate_observation_refresh,
)
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _screen_harness_fixture,
    _ScreenHarness,
)
from agent_toolkit._common import codex_fast_mode, usage_observation, usage_settings

pytestmark = [pytest.mark.browser]


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
    update_started = threading.Event()
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

    def remote_snapshot(_host: str, **_kwargs):
        result = copy.deepcopy(snapshot)
        if update_started.is_set():
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
    await _open_setting(editor)
    await expect(add).to_be_visible()
    await picker.select_option("")
    await expect(page.locator("#settings-status")).to_be_empty()

    async def draft():
        await _open_setting(editor)
        await add.click()
        await new_permission.get_by_text("詳細: アカウントの直接入力", exact=True).click()
        await new_permission.get_by_label("アカウントを直接入力").fill("my-account")
        await new_permission.get_by_label("回数").fill("1")
        await new_permission.get_by_label("開始（このブラウザーの時刻）").fill("2099-02-01T00:00")
        await new_permission.get_by_label("終了（このブラウザーの時刻）").fill("2099-02-02T00:00")

    update_started.set()
    try:
        await picker.select_option("demo-host")
        await expect(page.get_by_role("progressbar", name="設定の取得")).to_be_visible()
        await draft()
        await expect(editor.get_by_role("button", name="追加", exact=True)).to_be_disabled()
    finally:
        release.set()
    await expect(editor).to_contain_text("最新の保存値: other-writer")
    await expect(editor.get_by_label("アカウントを直接入力")).to_have_value("my-account")
    await expect(editor.get_by_role("button", name="追加", exact=True)).to_be_disabled()
    await editor.get_by_role("button", name="保存値を使う").click()
    captions = editor.locator(
        'details[data-editor^="usage_reset_permissions:"]:not([data-editor="usage_reset_permissions:"]) > summary'
    )
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
    await _open_setting(section)
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
    saved = section.get_by_role("status").filter(has_text="0件の許可 → 1件の許可")
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
    await page.set_viewport_size({"width": 1440, "height": 1000})
    await page.clock.install()
    await page.goto(screen_harness.base_url + "/settings")
    await page.get_by_role("navigation", name="設定の分類").get_by_text("atk serve", exact=True).click()
    editor = page.locator('[data-setting="serve_host"]')
    field = editor.get_by_label("保存値")
    await _open_setting(editor)
    await field.fill("draft-host")
    expect = playwright.async_api.expect
    fast = page.locator("#settings-fast-state")
    await expect(fast).to_contain_text("記録なし")
    comparison = page.locator("#settings-compare summary")
    before = await comparison.evaluate("control => control.getBoundingClientRect().top")
    field_before = await field.evaluate("control => control.getBoundingClientRect().top")
    codex_fast_mode.record_start_decision(
        datetime.datetime.now(datetime.UTC), codex_fast_mode.FastTier("priority", "契約を確認")
    )
    await page.clock.fast_forward(30_000)
    await expect(fast).to_contain_text("高速")
    assert await comparison.evaluate("control => control.getBoundingClientRect().top") == pytest.approx(before, abs=1)
    assert await field.evaluate("control => control.getBoundingClientRect().top") == pytest.approx(field_before, abs=1)
    config.update_settings({"action": "set", "key": "serve_host", "value": "saved-host"})
    await page.clock.fast_forward(30_000)
    conflict = editor.locator("[data-conflict]")
    await expect(conflict.locator("p")).to_have_text(["取得中に保存値が変更されました。", "最新の保存値: saved-host"])
    await expect(field).to_have_value("draft-host")
    await expect(conflict.get_by_role("button", name="保存値を使う")).to_be_visible()
    await conflict.get_by_role("button", name="入力を残す").click()
    await expect(conflict).to_be_hidden()
    await expect(field).to_have_value("draft-host")
    await expect(page.locator("#settings-unsaved")).to_contain_text("未保存の変更: 1件")
    assert config_settings.load_config()["serve_host"] == "saved-host"
    await editor.get_by_role("button", name="保存", exact=True).click()
    await expect(page.locator("#settings-unsaved")).to_be_empty()
    assert config_settings.load_config()["serve_host"] == "draft-host"


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
    search = page.get_by_label("設定を検索")
    await search.fill("serve_host")
    editor = page.locator('[data-setting="serve_host"]')
    await expect(editor).to_be_visible()
    await expect(page.locator('[data-setting="execute_model"]')).to_be_hidden()
    await expect(page.locator('#settings-group-serve [data-scope="shared"]')).to_be_hidden()
    await expect(page.locator('#settings-group-serve [data-scope="host"]')).to_be_visible()
    await _open_setting(editor)
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
    snapshot["settings"]["codex_fast_mode"].update(shared=True, shared_sync=True)
    snapshot.update(
        shared_identity="same",
        hosts=["remote"],
        codex_fast_start={"speed": "高速", "source": "今回確認", "at": "2026-11-26T12:00:00+09:00", "reason": "Business"},
    )
    snapshot["settings"]["review_model"].update(shared=False, shared_sync=False)
    peer = copy.deepcopy(snapshot)
    peer["settings"]["review_model"].update(saved="codex:sol/high", effective="codex:sol/high")
    peer["codex_fast_start"].update(speed="通常", source="直前の成功判定")
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
    editor = page.locator('[data-scope="shared"] [data-setting="execute_model"]')
    fast = page.locator("#settings-fast-state")
    picker = page.get_by_label("設定対象ホスト")
    await expect(fast).to_have_text("Codexの起動時判定（ローカル）: 高速")
    await expect(page.locator("#settings-shared")).not_to_contain_text("今回確認")
    await _open_setting(editor)
    await editor.get_by_label("保存値").fill("codex:sol/high")
    await picker.select_option("remote")
    await expect(fast).to_have_text("Codexの起動時判定（remote）: 前回の判定を使用")
    await expect(page.locator('[data-scope="host"] [data-setting="codex_fast_mode"]')).to_have_count(0)
    await page.locator("#settings-fast-detail summary").click()
    await expect(page.locator("#settings-fast-reason")).to_contain_text("Business")
    await expect(editor.get_by_label("保存値")).to_have_value("codex:sol/high")
    await expect(page.locator('[data-scope="host"] [data-setting="execute_model"]')).to_have_count(0)
    button = editor.get_by_role("button", name="保存")
    for width in (1440, 400):
        await page.set_viewport_size({"width": width, "height": 900})
        assert await button.evaluate(
            "button => Math.abs(button.getBoundingClientRect().right - button.parentElement.getBoundingClientRect().right) < 1"
        )
    await button.click()
    await expect(editor.get_by_role("status")).to_contain_text("保存済み（共有）: claude:sonnet/medium → codex:sol/high")
    assert posts == [{"action": "set", "key": "execute_model", "value": "codex:sol/high"}]
    assert not any(url.endswith("/api/settings/prepare") for url in screen_harness.requests)
    peer["shared_identity"] = "different-branch"
    await picker.select_option("")
    await expect(fast).to_have_text("Codexの起動時判定（ローカル）: 高速")
    await picker.select_option("remote")
    category = page.locator("#settings-group-models")
    shared_scope = category.get_by_role("group", name="全ホスト共通")
    host_scope = category.get_by_role("group", name="このホストだけ（remote）")
    peer_editor = host_scope.locator('[data-setting="execute_model"]')
    peer_field = peer_editor.get_by_label("保存値")
    await expect(shared_scope.locator('[data-setting="execute_model"]')).to_be_visible()
    await expect(peer_field).to_have_value("claude:sonnet/medium")
    await _open_setting(peer_editor)
    await peer_field.fill("codex:sol/low")
    await expect(editor.get_by_label("保存値")).to_have_value("codex:sol/high")
    await picker.select_option("")
    await expect(editor.get_by_label("保存値")).to_have_value("codex:sol/high")
    await picker.select_option("remote")
    await expect(peer_field).to_have_value("codex:sol/low")
    await expect(editor.get_by_label("保存値")).to_have_value("codex:sol/high")
    await expect(page.get_by_role("navigation", name="設定の分類")).to_have_count(1)
    await expect(fast).to_have_text("Codexの起動時判定（remote）: 前回の判定を使用")
    await expect(page.locator("#settings-shared")).not_to_contain_text("今回確認")
    compare = page.locator("#settings-compare")
    await compare.locator("summary").click()
    changed_row = compare.get_by_role("row").filter(has_text="レビューのモデル 相違あり")
    await expect(changed_row).to_have_attribute("data-different", "")
    await expect(changed_row.locator(".settings-different")).to_contain_text("codex:sol/high")
    await compare.locator("summary").click()
    if directory := os.environ.get("ATK_SERVE_SCREENSHOT_DIR"):
        await page.set_viewport_size({"width": 1440, "height": 1000})
        await picker.scroll_into_view_if_needed()
        for scheme in ("light", "dark"):
            await page.emulate_media(color_scheme=scheme)
            await page.screenshot(path=pathlib.Path(directory) / f"codex-fast-{scheme}.png")
        await page.emulate_media(color_scheme=None)
        compare = page.locator("#settings-compare")
        await compare.locator("summary").click()
        for width in (1440, 400):
            await page.set_viewport_size({"width": width, "height": 900})
            await compare.locator("summary").evaluate("element => element.scrollIntoView({block: 'start'})")
            await page.screenshot(path=pathlib.Path(directory) / f"003-comparison-{width}.png")


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("keep", [True, False])
async def test_shared_conflict_keeps_keyboard_choice_on_periodic_update(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, keep: bool
) -> None:
    snapshot = config.settings_snapshot(tmp_path)
    model = snapshot["settings"]["execute_model"]
    original = "claude:sonnet/medium"
    latest = "claude:sonnet/high"
    draft = "codex:sol/high"
    model.update(shared=True, saved=original)
    page = screen_harness.page
    await page.route("**/api/settings**", lambda route: route.fulfill(json=snapshot))
    await page.clock.install()
    await page.goto(screen_harness.base_url + "/settings")
    await page.get_by_label("設定を検索").fill("execute_model")
    editor = page.locator('[data-scope="shared"] [data-setting="execute_model"]')
    field = editor.get_by_label("保存値")
    await _open_setting(editor)
    await field.fill(draft)
    model["saved"] = latest
    await page.clock.fast_forward(30_000)
    conflict = editor.get_by_role("alert")
    expect = playwright.async_api.expect
    await expect(conflict).to_contain_text(latest)
    choice = conflict.get_by_role("button", name="入力を残す" if keep else "保存値を使う")
    await field.focus()
    for _ in range(3):
        await page.keyboard.press("Tab")
        if await choice.evaluate("button => button === document.activeElement"):
            break
    await expect(choice).to_be_focused()
    await conflict.evaluate("node => { window.previousConflict = node; }")
    await page.clock.fast_forward(30_000)
    await page.wait_for_function("() => !window.previousConflict.isConnected")
    await expect(choice).to_be_focused()
    await page.keyboard.press("Enter")
    await expect(conflict).to_have_count(0)
    await expect(field).to_have_value(draft if keep else latest)


@pytest.mark.parametrize("same_shared", [True, False])
@pytest.mark.asyncio(loop_scope="session")
async def test_remote_settings_retry_clears_the_recovered_hosts_status_everywhere(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, same_shared: bool
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    config_settings.save_config({"remote_hosts": '["cold", "offline"]'})
    local = config.settings_snapshot(tmp_path)
    local["shared_identity"] = "local"
    local_item = local["settings"]["execute_model"]
    local_item.update(shared=True, shared_sync=True, saved="claude:sonnet/medium", effective="claude:sonnet/medium")
    snapshot = copy.deepcopy(local)
    snapshot["shared_identity"] = "local" if same_shared else "remote"
    monkeypatch.setattr(config, "settings_snapshot", lambda *_args, **_kwargs: copy.deepcopy(local))
    snapshot["codex_fast_start"] = {
        "speed": "通常",
        "source": "直前の成功判定",
        "at": "2026-11-26T12:00:00+09:00",
        "reason": "Plus",
    }
    unavailable = {"cold", "offline"}
    release = threading.Event()

    def remote_snapshot(host: str, **_kwargs):
        if host in unavailable:
            raise ValueError("SSH接続に失敗しました")
        if not _kwargs.get("read_only"):
            assert release.wait(8)
        return copy.deepcopy(snapshot), None

    monkeypatch.setattr(settings_routes, "_remote_snapshot", remote_snapshot)
    page = screen_harness.page
    api = screen_harness.base_url + "/api/settings"
    for host in sorted(unavailable):
        prefetch = await page.request.get(f"{api}?host={host}&background=1&retry=1")
        assert prefetch.status == 503
    prefetched = await page.request.get(api + "?background=1")
    assert prefetched.ok
    assert set((await prefetched.json())["automatic_errors"]) == unavailable
    await page.goto(screen_harness.base_url + "/settings")
    expect = playwright.async_api.expect
    shared = page.locator("#settings-shared")
    status = page.locator("#update-status")
    fast = page.locator("#settings-fast-state")
    await expect(shared).to_contain_text("cold: 取得失敗")
    await page.get_by_label("設定対象ホスト").select_option("cold")
    await expect(fast).to_contain_text("（cold）: 取得できませんでした")
    unavailable.remove("cold")
    shared_editor = page.locator('[data-scope="shared"] [data-setting="execute_model"]')
    shared_field = shared_editor.get_by_label("保存値")
    await status.get_by_role("button", name="再取得").click()
    try:
        await _open_setting(shared_editor)
        await shared_field.fill("codex:sol/high")
        await shared_field.evaluate("field => { field.setSelectionRange(6, 9); window.recoveryInput = field; }")
    finally:
        release.set()
    await expect(fast).to_contain_text("（cold）: 前回の判定を使用")
    await expect(shared_field).to_be_focused()
    assert await shared_field.evaluate(
        "field => field === window.recoveryInput && field.selectionStart === 6 && field.selectionEnd === 9"
    )
    await page.keyboard.type("x")
    await expect(shared_field).to_have_value("codex:x/high")
    peer_field = page.locator('[data-scope="host"] [data-setting="execute_model"]').get_by_label("保存値")
    if same_shared:
        await expect(peer_field).to_have_count(0)
    else:
        await expect(peer_field).to_have_value("claude:sonnet/medium")
    await expect(shared).not_to_contain_text("cold: 取得失敗")
    await expect(shared).to_contain_text("offline: 取得失敗")
    await expect(status).not_to_contain_text("cold:")
    await expect(status).to_contain_text("offline: 自動取得に失敗しました")
    cached = await page.request.get(api + "?host=cold&background=1")
    assert cached.ok
    assert set((await cached.json())["automatic_errors"]) == {"offline"}


@pytest.mark.asyncio(loop_scope="session")
async def test_codex_start_diagnostic_distinguishes_decision_from_unavailable(
    screen_harness: _ScreenHarness, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    local = config.settings_snapshot(tmp_path)
    local["codex_fast_start"] = {
        "speed": "通常",
        "source": "確認不能",
        "at": "2026-11-26T12:00:00+09:00",
        "reason": "接続情報を取得できない",
    }
    local["hosts"] = ["fixture-host"]
    remote = copy.deepcopy(local)
    remote["codex_fast_start"].update(source="今回確認", reason="現在は提供期間外")

    async def settings(route: playwright.async_api.Route) -> None:
        await route.fulfill(json=remote if "host=fixture-host" in route.request.url else local)

    page = screen_harness.page
    await page.route("**/api/settings**", settings)
    await page.goto(screen_harness.base_url + "/settings")
    details = page.locator("#settings-fast-detail")
    summary = details.locator("summary")
    reason = details.locator("#settings-fast-reason")
    expect = playwright.async_api.expect
    await expect(summary).to_have_text("Codexの起動時判定（ローカル）: 自動判定は未確認")
    await expect(reason).to_be_hidden()
    await summary.click()
    await expect(reason).to_contain_text("この起動で使った速度: 通常")
    await expect(reason).to_contain_text("次の起動後に判定を確認してください")
    await page.get_by_label("設定対象ホスト").select_option("fixture-host")
    await expect(summary).to_have_text("Codexの起動時判定（fixture-host）: 通常")
    await expect(reason).to_contain_text("現在は提供期間外")
    await expect(reason).not_to_contain_text("次の起動後")

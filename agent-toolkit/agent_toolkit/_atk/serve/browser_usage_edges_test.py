import datetime
import json
import pathlib
import threading
import time

import playwright.async_api
import pytest

from agent_toolkit._atk import config_settings, usage_refresh
from agent_toolkit._atk.serve import settings_routes
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _screen_harness_fixture,
    _ScreenHarness,
)
from agent_toolkit._common import state_paths, usage_observation, usage_settings

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="session")]


@pytest.fixture(autouse=True)
def isolate_usage(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(usage_observation, "state_dir", lambda: tmp_path)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_USAGE_RESET_PERMISSIONS", "[]")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))


@pytest.mark.parametrize("crossed_reset", [False, True])
async def test_claude_report_and_sdk_windows_render_once(
    screen_harness: _ScreenHarness, monkeypatch: pytest.MonkeyPatch, crossed_reset: bool
) -> None:
    now = int(time.time())
    day = 86400
    monkeypatch.setattr(usage_refresh, "refresh", lambda **_kwargs: [])
    monkeypatch.setattr(usage_refresh, "enabled", lambda **_kwargs: True)
    usage_settings.record_metadata("claude", {"account": "local-claude", "contract": "max"})
    with monkeypatch.context() as earlier:
        earlier.setattr(usage_refresh.time, "time", lambda: now - 60)
        usage_refresh.record_claude_report(
            {
                "rate_limits": {
                    "limits": [
                        {"kind": "weekly_all", "percent": 80, "resets_at": now - 20 if crossed_reset else now + day},
                        {"kind": "weekly_new_kind", "percent": 20, "resets_at": now + day},
                    ]
                }
            },
            contract="max",
        )
    with monkeypatch.context() as later:
        later.setattr(usage_refresh.time, "time", lambda: now + 1)
        usage_refresh.record_claude_event(
            {
                "rateLimitType": "seven_day",
                "status": "allowed" if crossed_reset else "rejected",
                "resetsAt": now + (7 if crossed_reset else 1) * day - (20 if crossed_reset else 0),
            }
        )
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/usage")
    expect = playwright.async_api.expect
    rows = page.locator("#screen-usage article.metric-row")
    await expect(rows).to_have_count(2)
    unknown = rows.filter(has=page.get_by_role("heading", name="weekly_new_kind", exact=True))
    await expect(unknown).to_contain_text("期間")
    await expect(unknown.locator(".metric-tick")).to_have_count(0)
    weekly = rows.filter(has=page.locator(".metric-tick"))
    await expect(weekly).to_have_count(1)
    if crossed_reset:
        await expect(weekly).to_contain_text("使用率・残りとも不明")
        await expect(weekly).to_contain_text("見込みは出せません")
        await expect(weekly.get_by_role("progressbar")).to_have_count(0)
    else:
        await expect(weekly).to_contain_text("上限に到達")
        await expect(weekly.get_by_role("progressbar")).to_have_attribute("value", "80")


async def test_reset_inventory_distinguishes_zero_and_missing(
    screen_harness: _ScreenHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    usage_refresh.record_codex(
        {
            "ordinaryUsageAllowed": False,
            "spendControlReached": True,
            "rateLimitReachedType": "weekly",
            "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
            "rateLimitResetCredits": {
                "availableCount": 0,
                "credits": [
                    {"status": "used", "resetType": "codexRateLimits"},
                    {"status": "available", "resetType": "codexRateLimits"},
                ],
            },
        },
        account="fixture",
        contract="pro",
    )

    def refresh(**_kwargs):
        usage_refresh.record_codex({}, account="fixture", contract="pro")
        return []

    monkeypatch.setattr(usage_refresh, "refresh", lambda **_kwargs: [])
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/usage")
    expect = playwright.async_api.expect
    inventory = page.locator("dl > div").filter(has=page.get_by_text("在庫", exact=True))
    balance = page.locator("dl > div").filter(has=page.get_by_text("クレジット残高", exact=True))
    await expect(inventory.locator("dd")).to_have_text("0回")
    await expect(balance.locator("dd")).to_have_text("0")
    await expect(page.get_by_text("Codexの利用枠 · 使用済み", exact=True)).to_be_visible()
    await expect(page.get_by_text("Codexの利用枠 · 利用可能", exact=True)).to_be_visible()
    for label, value in (("通常利用", "利用不可"), ("支出上限", "到達"), ("上限に達した枠", "週")):
        field = page.locator("dl > div").filter(has=page.get_by_text(label, exact=True))
        await expect(field.locator("dd")).to_have_text(value)
    monkeypatch.setattr(usage_refresh, "refresh", refresh)
    await page.get_by_role("button", name="再取得").click()
    await expect(inventory.locator("dd")).to_have_text("未取得")
    await expect(balance.locator("dd")).to_have_text("未取得")


async def test_model_quota_displays_names_and_claude_states_after_refresh(
    screen_harness: _ScreenHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = int(time.time())
    reset = now + 3 * 86400
    scope = {"model": {"display_name": "Fable"}, "surface": "CLI"}
    usage_settings.record_metadata("claude", {"account": "local-claude", "contract": "max"})
    usage_refresh.record_claude_report(
        {
            "rate_limits": {
                "limits": [
                    {"kind": "weekly_all", "percent": 5, "resets_at": reset, "group": "weekly", "severity": "normal"},
                    {
                        "kind": "weekly_scoped",
                        "percent": 22,
                        "resets_at": reset,
                        "scope": scope,
                        "group": "weekly",
                        "severity": "warning",
                        "is_active": False,
                    },
                    {"kind": "weekly_scoped", "percent": 40, "scope": "broken", "resets_at": reset},
                    {"kind": "weekly_scoped", "percent": 35, "scope": {"surface": "CLI"}, "resets_at": reset},
                    {"kind": "seven_day_overage_included", "percent": 30, "resets_at": reset, "is_active": True},
                ]
            }
        },
        contract="max",
    )
    monkeypatch.setattr(usage_refresh, "refresh", lambda **_kwargs: [])
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/usage")
    expect = playwright.async_api.expect
    rows = page.locator("article.metric-row")
    name = "週 · Fable（CLI）"
    model = rows.filter(has=page.get_by_role("heading", name=name, exact=True))
    for refresh in (False, True):
        if refresh:
            await page.get_by_role("button", name="再取得").click()
        await expect(model.get_by_role("progressbar", name=f"{name}の使用率")).to_be_visible()
        await expect(model.get_by_text("警告", exact=True)).to_be_visible()
        await expect(model.get_by_text("無効", exact=True)).to_be_visible()
        await expect(model.get_by_text("利用枠の区分", exact=True)).to_have_count(0)
        await expect(rows.get_by_text("display_name", exact=False)).to_have_count(0)
        await expect(rows.get_by_role("heading", name="週 · 対象不明")).to_be_visible()
        await expect(rows.get_by_role("heading", name="週 · 全体")).to_be_visible()
        await expect(rows.get_by_role("heading", name="週の利用枠（追加利用分を含む）")).to_be_visible()

    saved_scope = json.dumps(scope, sort_keys=True)
    unknown_cli = json.dumps({"surface": "CLI"})
    unknown_web = json.dumps({"surface": "Web"})
    usage_settings.save_claude_reset_inventory(
        json.dumps(
            [
                {
                    "id": right,
                    "engine": "claude",
                    "account": "local-claude",
                    "scope": value,
                    "count": 1,
                    "from": datetime.datetime.fromtimestamp(now - 86400, datetime.UTC).isoformat(),
                    "until": datetime.datetime.fromtimestamp(reset, datetime.UTC).isoformat(),
                }
                for right, value in (("fable-right", saved_scope), ("cli-right", unknown_cli), ("web-right", unknown_web))
            ]
        )
    )
    await page.set_viewport_size({"width": 320, "height": 720})
    await page.get_by_role("link", name="リセットの許可を設定").click()
    section = page.locator('[data-setting="usage_reset_permissions"]')
    await section.get_by_text("許可を追加", exact=True).click()
    await section.get_by_label("エンジン").select_option("claude")
    scope_choice = section.get_by_label("範囲")
    await scope_choice.select_option(label=name)
    await expect(scope_choice).to_have_value(saved_scope)
    await expect(scope_choice.locator("option:checked")).to_have_text(name)
    assert await scope_choice.evaluate("(select) => select.getBoundingClientRect().right <= innerWidth")
    await expect(section.get_by_text(f"{name}: ", exact=False)).to_contain_text("にリセット予定")
    for value, surface in ((unknown_cli, "CLI"), (unknown_web, "Web")):
        await scope_choice.select_option(value=value)
        await expect(scope_choice.locator("option:checked")).to_have_text(f"週 · モデル不明（{surface}）")
        await expect(scope_choice).to_have_value(value)


async def test_failed_refresh_displays_newer_independent_observation(
    screen_harness: _ScreenHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    now = int(time.time())

    def observe(percent: int, observed_at: int) -> None:
        usage_observation.observe(
            [
                usage_observation.QuotaWindow(
                    "codex", "fixture", "codex:primary", percent, now + 7200, observed_at, duration_seconds=18000
                )
            ],
            setting_signature="fixture",
            active_seconds=0,
        )

    def refresh(**_kwargs):
        assert release.wait(10)
        return ["Codex: 接続できない"]

    usage_settings.record_metadata("codex", {"account": "fixture"})
    observe(20, now - 7200)
    monkeypatch.setattr(usage_refresh, "refresh", lambda **_kwargs: [])
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/usage")
    expect = playwright.async_api.expect
    status = page.locator("#usage-status")
    await expect(status).to_contain_text("最終取得")
    await expect(page.get_by_role("progressbar")).to_have_attribute("value", "20")
    monkeypatch.setattr(usage_refresh, "refresh", refresh)
    try:
        await page.get_by_role("button", name="再取得").click()
        await expect(status).to_contain_text("取得中")
        observe(45, now)
    finally:
        release.set()
    await expect(status).to_contain_text("取得に失敗した項目")
    await expect(page.get_by_role("progressbar")).to_have_attribute("value", "45")
    await expect(page.get_by_text("取得失敗: Codex:", exact=False)).to_contain_text("最後の観測")
    await expect(page.locator(".metric-row")).not_to_contain_text("1時間以上前")


@pytest.mark.cpu_limit(
    0.65,
    reason="実Chromium・HTTPでのナビゲーションを測る。共有ブラウザーの準備と終了は全体上限だけに計上する",
    call_limit=0.65,
)
async def test_usage_settings_link_selects_local_with_permission_editor(
    screen_harness: _ScreenHarness, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    config_settings.save_config({"remote_hosts": '["fixture-remote"]'})
    snapshot = {
        "settings": {},
        "paths": {},
        "usage": [{"engine": "codex", "window_id": "fixture-remote-window", "used_percent": 57}],
    }
    monkeypatch.setattr(settings_routes, "_remote_snapshot", lambda _host: (snapshot, None))
    monkeypatch.setattr(usage_refresh, "refresh", lambda **_kwargs: [])
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/settings")
    expect = playwright.async_api.expect
    picker = page.get_by_label("設定対象ホスト")
    await picker.select_option("fixture-remote")
    await page.get_by_role("navigation", name="設定の分類").get_by_text("利用枠", exact=True).click()
    await expect(picker).to_have_value("fixture-remote")
    settings = page.locator("#screen-settings")
    await expect(settings).not_to_contain_text("fixture-remote-window")
    await settings.get_by_role("link", name="利用枠の使用率とリセット権を見る").click()
    navigation_started = time.monotonic()
    await page.get_by_role("link", name="リセットの許可を設定").click()
    await expect(picker).to_have_value("")
    editor = settings.locator('[data-setting="usage_reset_permissions"]')
    await expect(editor.get_by_text("許可を追加", exact=True)).to_be_visible()
    request.node.user_properties.append(("usage_settings_navigation_seconds", time.monotonic() - navigation_started))

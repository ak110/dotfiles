import pathlib
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
        {"rateLimitResetCredits": {"availableCount": 0, "credits": [{"status": "used", "resetType": "codexRateLimits"}]}},
        account="fixture",
        contract="pro",
    )

    def refresh(*, quiet, environment):
        usage_refresh.record_codex({}, account="fixture", contract="pro")
        return []

    monkeypatch.setattr(usage_refresh, "refresh", lambda **_kwargs: [])
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/usage")
    expect = playwright.async_api.expect
    inventory = page.locator("dl > div").filter(has=page.get_by_text("在庫", exact=True))
    await expect(inventory.locator("dd")).to_have_text("0回")
    await expect(page.get_by_text("codexRateLimits · used", exact=True)).to_be_visible()
    monkeypatch.setattr(usage_refresh, "refresh", refresh)
    await page.get_by_role("button", name="再取得").click()
    await expect(inventory.locator("dd")).to_have_text("未取得")


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

    def refresh(*, quiet, environment):
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


async def test_usage_settings_link_selects_local_with_permission_editor(
    screen_harness: _ScreenHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_settings.save_config({"remote_hosts": '["fixture-remote"]'})
    snapshot = config.settings_snapshot(screen_harness.root)
    snapshot["usage"] = [{"engine": "codex", "window_id": "fixture-remote-window", "used_percent": 57}]
    monkeypatch.setattr(settings_routes, "_remote_snapshot", lambda _host: (snapshot, None))
    monkeypatch.setattr(usage_refresh, "refresh", lambda **_kwargs: [])
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/settings")
    expect = playwright.async_api.expect
    picker = page.get_by_label("設定対象ホスト")
    await picker.select_option("fixture-remote")
    await page.get_by_role("navigation", name="設定の分類").get_by_text("利用枠", exact=True).click()
    settings = page.locator("#screen-settings")
    await expect(settings).not_to_contain_text("fixture-remote-window")
    await settings.get_by_role("link", name="利用枠の使用率とリセット権を見る").click()
    await page.get_by_role("link", name="リセットの許可を設定").click()
    await expect(picker).to_have_value("")
    editor = settings.locator('[data-setting="usage_reset_permissions"]')
    await expect(editor.get_by_text("許可を追加", exact=True)).to_be_visible()

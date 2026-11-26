import threading
import time

import playwright.async_api
import pytest

from agent_toolkit._atk import usage_refresh
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _screen_harness_fixture,
    _ScreenHarness,
)
from agent_toolkit._common import usage_observation, usage_settings

pytestmark = [pytest.mark.browser]


@pytest.mark.asyncio(loop_scope="session")
async def test_usage_displays_observations_and_refresh_failure_without_losing_values(
    screen_harness: _ScreenHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time()
    day = 86400
    usage_settings.record_metadata("codex", {"account": "fixture-account", "contract": "promax", "reset_available_count": 0})
    usage_observation.observe(
        [
            usage_observation.QuotaWindow(
                "codex",
                "fixture-account",
                "codex:primary",
                80,
                now + 3.5 * day,
                now,
                contract="promax",
                duration_seconds=7 * day,
            )
        ],
        setting_signature="fixture",
        active_seconds=0,
    )
    started = threading.Event()
    release = threading.Event()

    def refresh_for_display(*, force: bool = False):
        if force:
            started.set()
            if not release.wait(timeout=10):
                raise TimeoutError
        return {
            "refresh_context": usage_settings.context_id(),
            "last_refresh_at": time.time(),
            "refresh_failures": ["Codex: 接続できない"] if started.is_set() else [],
        }

    monkeypatch.setattr(usage_refresh, "refresh_for_display", refresh_for_display)
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/usage")
    expect = playwright.async_api.expect
    screen = page.locator("#screen-usage")
    await expect(page.locator("#update-status")).to_be_hidden()
    await expect(screen.get_by_role("progressbar", name="週 · codex:primaryの使用率")).to_have_attribute("value", "80")
    await expect(screen.locator(".metric-tick")).to_have_count(6)
    await expect(screen.locator(".metric-warning")).to_contain_text("上限に達する見込み")
    await expect(screen).to_contain_text("0回")
    await expect(screen).not_to_contain_text("fixture-account")
    try:
        await screen.get_by_role("button", name="再取得").click()
        await expect(screen.get_by_role("button", name="再取得")).to_be_disabled()
        await expect(screen.get_by_role("progressbar")).to_be_visible()
    finally:
        release.set()
    await expect(page.locator("#update-status")).to_contain_text("利用枠を取得できません。")
    await expect(screen.get_by_text("取得失敗: Codex:", exact=False)).to_have_count(0)
    await expect(screen.get_by_role("progressbar")).to_have_attribute("value", "80")
    assert started.is_set()
    await screen.get_by_role("link", name="リセットの許可を設定").click()
    await expect(page.get_by_label("設定対象ホスト")).to_have_value("")
    await expect(page.locator('[data-setting="usage_reset_permissions"]')).to_be_visible()


@pytest.mark.asyncio(loop_scope="session")
async def test_usage_fetch_is_scoped_to_the_visible_screen(
    screen_harness: _ScreenHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refresh_for_display(*, force: bool = False):
        return {
            "refresh_context": usage_settings.context_id(),
            "last_refresh_at": time.time(),
            "refresh_failures": [],
            "force": force,
        }

    monkeypatch.setattr(usage_refresh, "refresh_for_display", refresh_for_display)
    page = screen_harness.page
    expect = playwright.async_api.expect
    await page.goto(screen_harness.base_url + "/")
    await expect(page.locator("#entry-list .entry-row").first).to_be_visible()
    assert not any("/api/usage" in url for url in screen_harness.requests)
    async with page.expect_response("**/api/usage/refresh"):
        await page.get_by_role("navigation", name="画面").get_by_role("link", name="利用枠").click()
    await expect(page).to_have_title("利用枠 - atk serve")
    await expect(page.locator("#screen-usage .metric-section").first).to_be_visible()
    assert sum(url.endswith("/api/usage/refresh") for url in screen_harness.requests) == 1
    await page.go_back()
    await expect(page).to_have_title("ワークアイテム - atk serve")
    await expect(page.locator("#entry-list .entry-row").first).to_be_visible()
    assert sum(url.endswith("/api/usage/refresh") for url in screen_harness.requests) == 1
    await page.go_forward()
    await expect(page).to_have_title("利用枠 - atk serve")
    await expect(page.locator("#screen-usage")).to_be_visible()


@pytest.mark.asyncio(loop_scope="session")
async def test_usage_recovers_without_showing_first_failure(
    screen_harness: _ScreenHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0

    def refresh_for_display(**_kwargs):
        nonlocal attempts
        attempts += 1
        return {
            "refresh_context": usage_settings.context_id(),
            "last_refresh_at": time.time(),
            "refresh_failures": ["Codex: 接続できない"] if attempts == 1 else [],
        }

    monkeypatch.setattr(usage_refresh, "refresh_for_display", refresh_for_display)
    page = screen_harness.page
    await page.clock.install()
    await page.goto(screen_harness.base_url + "/usage")
    banner = page.locator("#update-status")
    await playwright.async_api.expect(page.locator("#screen-usage .metric-section").first).to_be_visible()
    await playwright.async_api.expect(banner).to_be_hidden()
    await page.clock.fast_forward(1800)
    await playwright.async_api.expect(page.locator("#screen-usage").get_by_role("button", name="再取得")).to_be_enabled()
    await playwright.async_api.expect(banner).to_be_hidden()
    assert attempts == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_repeated_usage_failure_has_only_one_recovery_action(
    screen_harness: _ScreenHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0

    def refresh_for_display(**_kwargs):
        nonlocal attempts
        attempts += 1
        return {
            "refresh_context": usage_settings.context_id(),
            "last_refresh_at": time.time(),
            "refresh_failures": ["Codex: 一時失敗"] if attempts <= 2 else [],
        }

    monkeypatch.setattr(usage_refresh, "refresh_for_display", refresh_for_display)
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/usage")
    expect = playwright.async_api.expect
    banner = page.locator("#update-status")
    await expect(banner).to_be_hidden()
    await expect(banner).to_contain_text("利用枠を取得できません。")
    assert attempts == 2
    await expect(page.get_by_role("button", name="再取得", exact=True)).to_have_count(1)
    await banner.get_by_role("button", name="再取得").click()
    await expect(banner).to_be_hidden()
    assert attempts == 3

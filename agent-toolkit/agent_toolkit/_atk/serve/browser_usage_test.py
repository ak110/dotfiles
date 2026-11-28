import asyncio
import datetime
import threading
import time
from typing import Literal

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


@pytest.mark.cpu_limit(
    0.7,
    reason="実ブラウザーで取得障害の前後に、キーボードで利用枠へ移動して計測の詳細を保てるか確かめる",
)
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
    await expect(screen.get_by_role("progressbar", name="週 · 第1枠の使用率")).to_have_attribute("value", "80")
    await expect(screen.locator(".metric-tick")).to_have_count(6)
    await expect(screen.locator(".metric-warning")).to_contain_text("上限に達する見込み")
    await expect(screen).to_contain_text("0回")
    await expect(screen).not_to_contain_text("fixture-account")
    risk = screen.locator("#usage-attention").get_by_role("button", name="Codex · 週 · 第1枠")
    await risk.press("Enter")
    await expect(screen.get_by_role("heading", name="週 · 第1枠", exact=True)).to_be_focused()
    details = screen.locator(".metric-evidence")
    await details.locator("summary").press("Enter")
    await expect(details.get_by_text("codex:primary", exact=True)).to_be_visible()
    try:
        await screen.get_by_role("button", name="再取得").click()
        await expect(screen.get_by_role("button", name="再取得")).to_be_disabled()
        await expect(screen.get_by_role("progressbar")).to_be_visible()
    finally:
        release.set()
    await expect(page.locator("#update-status")).to_contain_text("利用枠を取得できません。")
    await expect(screen.get_by_text("取得失敗: Codex:", exact=False)).to_have_count(0)
    await expect(screen.get_by_role("progressbar")).to_have_attribute("value", "80")
    await expect(details.get_by_text("codex:primary", exact=True)).to_be_visible()
    assert started.is_set()
    await expect(screen.get_by_role("checkbox", name="リセット権を自動で使う")).to_be_enabled()
    await expect(page).to_have_url(screen_harness.base_url + "/usage")


@pytest.mark.asyncio(loop_scope="session")
async def test_usage_is_prepared_before_the_first_screen_switch(
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
    async with page.expect_response("**/api/usage/refresh"):
        await page.goto(screen_harness.base_url + "/")
    await expect(page.locator("#entry-list .entry-row").first).to_be_visible()
    await expect(page.locator("#screen-usage .metric-section").first).to_be_attached()
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
    assert sum(url.endswith("/api/usage/refresh") for url in screen_harness.requests) == 1


def _quota_observation_cases() -> tuple[datetime.datetime, dict[str, object]]:
    fixed_time = datetime.datetime(2026, 11, 27, tzinfo=datetime.UTC)
    now = fixed_time.timestamp()
    week = 7 * 86400
    sample = {"scope": "all", "observed_at": now, "resets_at": now + week / 2, "duration_seconds": week}
    return fixed_time, {
        "windows": [
            dict(sample, engine="claude", window_id="weekly_all", used_percent=32, usage_observed_at=now - 7200),
            dict(sample, engine="codex", window_id="codex:primary", used_percent=80),
            dict(
                sample,
                engine="codex",
                window_id="codex:secondary",
                used_percent=62,
                resets_at=now - 60,
                duration_seconds=18000,
            ),
            dict(sample, engine="codex", window_id="codex:other", used_percent=None, limit_name="観測値なし"),
        ],
        "metadata": {},
        "permissions": [],
        "claude_inventory": [],
        "last_refresh_at": now,
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_usage_distinguishes_observation_age_elapsed_and_forecast(screen_harness: _ScreenHarness) -> None:
    page = screen_harness.page
    fixed_time, payload = _quota_observation_cases()
    release = asyncio.Event()

    async def fulfill(route: playwright.async_api.Route) -> None:
        await release.wait()
        await route.fulfill(json=payload)

    expect = playwright.async_api.expect
    await page.clock.set_fixed_time(fixed_time)
    await page.route("**/api/usage", fulfill)
    await page.route("**/api/usage/refresh", fulfill)
    screen = page.locator("#screen-usage")
    loading = screen.get_by_role("progressbar", name="利用枠を取得中", exact=True)
    try:
        await page.goto(screen_harness.base_url + "/usage")
        await expect(loading).to_be_visible()
        await expect(screen.get_by_role("button", name="再取得")).to_be_disabled()
    finally:
        release.set()
    await expect(loading).to_be_hidden()
    await expect(screen.get_by_role("button", name="再取得")).to_be_enabled()

    rows = screen.locator(".metric-row")
    fresh = rows.filter(has=page.get_by_role("heading", name="週 · 第1枠", exact=True))
    stale = rows.filter(has=page.get_by_role("heading", name="週 · 全体", exact=True))
    expired = rows.filter(has=page.get_by_role("heading", name="5時間 · 第2枠", exact=True))
    unknown = rows.filter(has=page.get_by_role("heading", name="週 · 観測値なし", exact=True))
    await expect(rows).to_have_count(4)
    cases: tuple[tuple[Literal["light", "dark"], int], ...] = (("light", 1280), ("dark", 1280), ("light", 360))
    for color_scheme, width in cases:
        await page.emulate_media(color_scheme=color_scheme)
        await page.set_viewport_size({"width": width, "height": 960})
        await expect(fresh.get_by_role("progressbar", name="週 · 第1枠の使用率")).to_have_attribute("value", "80")
        await expect(fresh.get_by_text("期間の経過 50%", exact=True)).to_be_visible()
        await expect(fresh.get_by_text("1目盛り＝1日", exact=True)).to_be_visible()
        await expect(fresh.locator(".metric-tick")).to_have_count(6)
        await expect(fresh.get_by_text("次回リセット", exact=True)).to_be_visible()
        await expect(screen.get_by_text("2026/11/28(土) 06:00 JST", exact=False)).to_have_count(1)
        await expect(fresh.get_by_text("2026/11/28(土) 06:00 JST", exact=False)).to_be_visible()
        assert await fresh.locator(".metric-track").evaluate("""track => {
            const scale = track.getBoundingClientRect();
            const needle = track.querySelector('.metric-needle').getBoundingClientRect();
            const label = track.querySelector('.metric-marker').getBoundingClientRect();
            return Math.abs(needle.left - scale.left - scale.width / 2) < 1 &&
                label.left >= scale.left && label.right <= scale.right;
        }""")
        await expect(stale.get_by_text("前回の使用率の観測: 2026/11/27(金) 07:00 JST（1時間以上前）")).to_be_visible()
        await expect(stale.get_by_role("progressbar", name="週 · 全体の前回の使用率")).to_have_attribute("value", "32")
        await expect(expired.get_by_text("前回のリセット予定")).to_be_visible()
        await expect(expired.get_by_text("使用率不明（前回の観測はリセット前）")).to_be_visible()
        await expect(unknown.get_by_text("使用率・残りとも不明")).to_be_visible()
        for unavailable in (expired, unknown):
            await expect(unavailable.get_by_role("progressbar")).to_have_count(0)


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


@pytest.mark.cpu_limit(
    0.8,
    reason="実ブラウザーで提供元別取得の失敗・復旧と、既存の操作が並行できることを確かめる。",
)
@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.asyncio(loop_scope="session")
async def test_antigravity_display_failure_does_not_block_existing_usage(
    screen_harness: _ScreenHarness, monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    mode = "missing"
    blocked = threading.Event()
    release = threading.Event()

    def antigravity():
        if mode == "missing":
            return []
        if mode == "failed":
            blocked.set()
            if not release.wait(timeout=5):
                raise TimeoutError
            raise ValueError("provider fixture failed")
        return [
            {
                "group": "Gemini Models",
                "name": "Weekly Remaining",
                "remaining_fraction": 0.5,
                "resets_at": time.time() + 3600,
            }
        ]

    def refresh_for_display(*, force: bool = False):
        return {
            "refresh_context": usage_settings.context_id(),
            "last_refresh_at": time.time(),
            "refresh_failures": [],
            "force": force,
        }

    monkeypatch.setattr(usage_refresh, "read_antigravity_usage", antigravity)
    monkeypatch.setattr(usage_refresh, "refresh_for_display", refresh_for_display)
    page = screen_harness.page
    await page.set_viewport_size({"width": width, "height": 900})
    expect = playwright.async_api.expect
    async with page.expect_response("**/api/usage/antigravity"):
        await page.goto(screen_harness.base_url + "/usage")
    main = page.locator("#usage-workspace")
    card = main.get_by_role("region", name="Antigravity")
    refresh = main.get_by_role("button", name="再取得")
    scope = main.locator("#usage-antigravity-refresh-scope")
    headings = main.locator("#usage-content h3")
    await expect(main.get_by_role("heading", name="Codex", exact=True)).to_be_visible()
    await expect(card).to_have_count(0)

    mode = "available"
    await refresh.click()
    await expect(card).to_be_visible()
    await expect(headings).to_have_text(["Claude", "Codex", "Antigravity"])
    await expect(card.locator(".metric-row h4")).to_have_text("Weekly Remaining")
    await expect(card.locator(".metric-value")).to_have_text("50%")
    await expect(card.get_by_text("JST")).to_have_count(2)

    mode = "failed"
    try:
        await refresh.click()
        assert await asyncio.to_thread(blocked.wait, 5)
        await expect(refresh).to_be_enabled()
        status = card.get_by_role("status")
        await expect(status).to_contain_text("Antigravityを再取得中")
        await expect(status).to_contain_text("前回の観測")
        await expect(status).to_contain_text("ClaudeとCodexだけ")
        await expect(scope).to_be_visible()
        await expect(scope).to_be_in_viewport()
        await expect(refresh).to_have_accessible_description(
            "Antigravityを再取得中です。この間、再取得はClaudeとCodexだけに適用されます。"
        )
        await expect(main.get_by_role("heading", name="Codex", exact=True)).to_be_visible()
        await expect(main.get_by_role("checkbox", name="リセット権を自動で使う")).to_be_enabled()
        await expect(card.get_by_text("50%", exact=True)).to_be_visible()
    finally:
        release.set()
    await expect(scope).to_be_hidden()
    await expect(refresh).to_have_accessible_description("")
    await expect(card.get_by_role("status")).to_contain_text("最新の利用枠を取得できません")
    await expect(card.get_by_role("status")).to_contain_text("前回の観測")
    await expect(card.get_by_text("50%", exact=True)).to_be_visible()
    await expect(page.locator("#update-status")).to_be_hidden()

    mode = "available"
    await refresh.click()
    await expect(card.get_by_role("status")).to_contain_text("取得時刻")
    await expect(card.get_by_role("status")).not_to_contain_text("再取得中")

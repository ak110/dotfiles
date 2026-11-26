import asyncio
import datetime
import json
import time
from pathlib import Path

import pytest
from playwright.async_api import Locator, Route, expect

from agent_toolkit._atk import config, config_settings
from agent_toolkit._atk.serve import settings_routes
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _screen_harness_fixture,
    _ScreenHarness,
)
from agent_toolkit._common import usage_observation, usage_settings

pytestmark = [pytest.mark.browser]
_DAY = 86400
_ACCOUNT = "synthetic-claude"


def _iso(seconds: int) -> str:
    return datetime.datetime.fromtimestamp(seconds, datetime.UTC).isoformat()


@pytest.fixture(name="isolated_permissions")
def _isolated_permissions_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    return tmp_path


async def _open_add(screen_harness: _ScreenHarness) -> Locator:
    page = screen_harness.page
    await page.goto(screen_harness.base_url + "/settings")
    await page.get_by_role("navigation", name="設定の分類").get_by_text("利用枠", exact=True).click()
    section = page.locator('[data-setting="usage_reset_permissions"]')
    await section.get_by_text("許可を追加", exact=True).click()
    return section


@pytest.mark.usefixtures("isolated_permissions")
@pytest.mark.asyncio(loop_scope="session")
async def test_reset_permission_can_only_submit_when_scope_is_available_and_settings_are_idle(
    screen_harness: _ScreenHarness,
) -> None:
    page = screen_harness.page
    started = asyncio.Event()
    release = asyncio.Event()

    async def delay_save(route: Route) -> None:
        if route.request.method == "POST":
            started.set()
            await asyncio.wait_for(release.wait(), 10)
        await route.continue_()

    await page.route("**/api/settings", delay_save)
    try:
        permission = await _open_add(screen_harness)
        engine = permission.get_by_label("エンジン")
        add_permission = permission.get_by_role("button", name="追加", exact=True)
        await engine.select_option("claude")
        await expect(add_permission).to_be_disabled()
        navigation = page.get_by_role("navigation", name="設定の分類")
        await navigation.get_by_text("工程別モデル", exact=True).click()
        preset = page.locator('[data-editor="preset:claude-primary"]')
        await preset.locator("summary").click()
        await preset.get_by_role("button", name="適用", exact=True).click()
        await asyncio.wait_for(started.wait(), 5)
        await navigation.get_by_text("利用枠", exact=True).click()
        await engine.select_option("codex")
        await expect(add_permission).to_be_disabled()
        await engine.select_option("claude")
        release.set()
        await expect(page.get_by_label("設定対象ホスト")).to_be_enabled()
        await expect(add_permission).to_be_disabled()
        await engine.select_option("codex")
        await expect(add_permission).to_be_enabled()
    finally:
        release.set()
        await page.unroute("**/api/settings", delay_save)


@pytest.mark.usefixtures("isolated_permissions")
@pytest.mark.parametrize("case", ["rejected", "past", "unknown-reset", "inventory-expires", "other-account"])
@pytest.mark.asyncio(loop_scope="session")
async def test_claude_permission_uses_matching_inventory_and_weekly_observation(
    screen_harness: _ScreenHarness, case: str
) -> None:
    now = int(time.time())
    reset = now + 3 * _DAY
    weekly_reset = {"past": now - 1, "unknown-reset": None}.get(case, reset)
    usage_settings.record_metadata("claude", {"account": _ACCOUNT, "contract": "max"})
    usage_observation.observe(
        [
            usage_observation.QuotaWindow("claude", _ACCOUNT, "five_hour", 100, now + 3600, now, contract="max"),
            usage_observation.QuotaWindow(
                "claude",
                _ACCOUNT,
                "seven_day",
                90 if case == "inventory-expires" else None,
                weekly_reset,
                now,
                contract="max",
                rejected=case != "inventory-expires",
            ),
        ],
        setting_signature="synthetic",
        active_seconds=0,
    )
    inventory = [
        {
            "id": "synthetic-right",
            "engine": "claude",
            "account": "other-account" if case == "other-account" else _ACCOUNT,
            "scope": "all",
            "count": 1,
            "from": _iso(now - _DAY),
            "until": _iso(now + (3600 if case == "inventory-expires" else 5 * _DAY)),
        },
        {
            "id": "unrelated-right",
            "engine": "claude",
            "account": "other-account",
            "scope": "model:opus",
            "count": 1,
            "from": _iso(now - _DAY),
            "until": _iso(now + 5 * _DAY),
        },
    ]
    usage_settings.save_claude_reset_inventory(json.dumps(inventory))
    section = await _open_add(screen_harness)
    await section.get_by_label("エンジン").select_option("claude")
    apply = section.get_by_role("button", name="この提案を入力")
    weekly = section.get_by_role("button", name="次の週次リセットの1日前まで")
    if case == "other-account":
        await expect(section).to_contain_text("このアカウントに在庫がありません")
        await expect(section.get_by_role("button", name="追加", exact=True)).to_be_disabled()
        return
    await expect(section.get_by_label("アカウント", exact=True)).to_have_value(_ACCOUNT)
    scope = section.get_by_label("範囲")
    await expect(scope.locator("option")).to_have_text(["全体"])
    await expect(scope).to_have_value("all")
    if case == "rejected":
        await expect(section).to_contain_text("既に上限に到達")
        await apply.click()
        until = section.get_by_label("終了（このブラウザーの時刻）")
        assert await until.get_attribute("data-instant") == _iso(reset - _DAY).replace("+00:00", ".000Z")
        assert "usage_reset_permissions" not in config_settings.load_config()
    else:
        reason = (
            "在庫の期限が上限到達の見込みより前"
            if case == "inventory-expires"
            else "上限に到達していますがリセット日時が分からない"
        )
        await expect(section).to_contain_text(reason)
        await expect(apply).to_be_hidden()
        if case != "inventory-expires":
            await expect(weekly).to_be_disabled()


@pytest.mark.asyncio(loop_scope="session")
async def test_permission_draft_survives_host_switch_and_remote_unknown_has_no_candidate(
    screen_harness: _ScreenHarness, isolated_permissions: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_settings.save_config({"remote_hosts": '["synthetic-host"]'})
    now = int(time.time()) // 60 * 60 + 37
    usage_settings.record_metadata(
        "codex",
        {
            "permission_account": "synthetic-codex",
            "contract": "promax",
            "weekly_window": {"window_id": "codex:secondary", "resets_at": now + 3 * _DAY},
        },
    )
    remote = config.settings_snapshot(isolated_permissions)
    remote["reset_editor"]["accounts"]["codex"]["account"] = "unknown:synthetic"
    monkeypatch.setattr(settings_routes, "_remote_snapshot", lambda _host: (remote, None))

    section = await _open_add(screen_harness)
    account = section.get_by_label("アカウント", exact=True)
    until = section.get_by_label("終了（このブラウザーの時刻）")
    await expect(account).to_have_value("synthetic-codex")
    await expect(section.get_by_text("詳細", exact=True)).to_be_hidden()
    await section.get_by_label("リセット前の猶予").select_option("2")
    await section.get_by_role("button", name="次の週次リセットの2日前まで").click()
    await section.get_by_label("回数").fill("7")

    page = screen_harness.page
    host = page.get_by_label("設定対象ホスト")
    await host.select_option("synthetic-host")
    await expect(section.get_by_text("許可を追加", exact=True)).to_be_visible()
    await section.get_by_text("許可を追加", exact=True).click()
    assert await account.locator("option").count() == 0
    remote_account_visible = await account.is_visible()
    await expect(section).to_contain_text("atk config usage --refresh")
    await section.get_by_text("詳細", exact=True).click()
    await expect(section.get_by_label("アカウントを直接入力")).to_be_visible()
    await host.select_option("")
    await expect(account).to_have_value("synthetic-codex")
    await expect(section.get_by_label("回数")).to_have_value("7")
    await expect(section.get_by_label("リセット前の猶予")).to_have_value("2")
    await expect(until).to_have_attribute("data-instant", _iso(now + _DAY).replace("+00:00", ".000Z"))
    assert "usage_reset_permissions" not in config_settings.load_config()
    assert not remote_account_visible, "候補がないホストでは空のアカウント選択欄を表示しない"


@pytest.mark.usefixtures("isolated_permissions")
@pytest.mark.parametrize(
    ("case", "reason", "proposed"),
    [
        ("low-rate", "今週は許可不要", False),
        ("unobserved", "判断に使える観測が足りません", False),
        ("late-display", "猶予を取ると許可の期間が残りません", False),
        ("covered", "期間を覆う許可が設定済み", False),
        ("full", "既に上限に到達", True),
        ("multiple", "次の週次リセットの1日前まで", True),
    ],
)
@pytest.mark.asyncio(loop_scope="session")
async def test_codex_suggestion_uses_observation_not_display_time(
    screen_harness: _ScreenHarness, case: str, reason: str, proposed: bool
) -> None:
    now = int(time.time())
    reset = now + (_DAY // 2 if case == "late-display" else 7 * _DAY // 2)
    account = "synthetic-codex"
    usage_settings.record_metadata(
        "codex",
        {
            "account": account,
            "permission_account": account,
            "contract": "promax",
            "weekly_window": {"window_id": "codex:secondary", "resets_at": reset},
        },
    )
    if case == "covered":
        config_settings.save_config(
            {
                "usage_reset_permissions": json.dumps(
                    [
                        {
                            "id": "covers-window",
                            "engine": "codex",
                            "account": account,
                            "scope": "codex",
                            "count": 1,
                            "from": _iso(now - _DAY),
                            "until": _iso(reset),
                        }
                    ]
                )
            }
        )
    if case != "unobserved":
        used = {"low-rate": 40, "full": 100}.get(case, 80)
        windows = [
            usage_observation.QuotaWindow(
                "codex",
                account,
                "codex:secondary",
                used,
                reset,
                reset - 7 * _DAY // 2,
                contract="promax",
                duration_seconds=7 * _DAY,
            )
        ]
        if case == "multiple":
            windows.append(
                usage_observation.QuotaWindow(
                    "codex",
                    account,
                    "experimental:secondary",
                    100,
                    reset - _DAY,
                    now,
                    contract="promax",
                    duration_seconds=7 * _DAY,
                )
            )
        usage_observation.observe(windows, setting_signature="synthetic", active_seconds=0)
    section = await _open_add(screen_harness)
    editor = section.locator('details[data-editor="usage_reset_permissions:"]')
    await expect(editor).to_contain_text(reason)
    apply = editor.get_by_role("button", name="この提案を入力")
    if proposed:
        await apply.click()
        await expect(editor.get_by_label("終了（このブラウザーの時刻）")).to_have_attribute(
            "data-instant", _iso(reset - _DAY).replace("+00:00", ".000Z")
        )
    else:
        await expect(apply).to_be_hidden()


@pytest.mark.usefixtures("isolated_permissions")
@pytest.mark.asyncio(loop_scope="session")
async def test_existing_permission_keeps_legacy_scope_and_shows_period_states(screen_harness: _ScreenHarness) -> None:
    now = int(time.time())
    entries = [
        {
            "id": name,
            "engine": "codex",
            "account": "synthetic-codex",
            "scope": "model:opus",
            "count": count,
            "from": _iso(now + start * _DAY),
            "until": _iso(now + end * _DAY),
        }
        for name, count, start, end in (("active", 1, -1, 1), ("ahead", 1, 1, 2), ("past", 1, -2, -1), ("zero", 0, -1, 1))
    ]
    config_settings.save_config({"usage_reset_permissions": json.dumps(entries)})
    usage_settings.record_metadata("codex", {"permission_account": "synthetic-codex", "contract": "pro"})
    section = await _open_add(screen_harness)
    for name, label in (("active", "期間内"), ("ahead", "開始前"), ("past", "期限切れ"), ("zero", "回数0（使わない）")):
        await expect(section.locator(f'[data-editor="usage_reset_permissions:{name}"] > summary')).to_contain_text(label)
    active = section.locator('[data-editor="usage_reset_permissions:active"]')
    await expect(active.locator("summary").first).to_contain_text("Codex（pro）")
    await active.locator("summary").first.click()
    await expect(active).to_contain_text("自動のresetの対象外の範囲")
    await active.get_by_role("button", name="7日後").click()
    await active.get_by_role("button", name="保存", exact=True).click()
    await expect(section.get_by_text("設定を保存しました。", exact=True)).to_be_visible()
    saved = json.loads(config_settings.load_config()["usage_reset_permissions"])
    assert saved[0]["until"] != entries[0]["until"]
    assert [saved[0][field] for field in ("id", "account", "scope")] == [
        entries[0][field] for field in ("id", "account", "scope")
    ]

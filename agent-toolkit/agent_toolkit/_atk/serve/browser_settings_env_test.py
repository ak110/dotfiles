import json
import re

import playwright.async_api
import pytest

from agent_toolkit._atk import config, config_env, config_settings, usage_refresh
from agent_toolkit._atk.serve.browser_settings_test import _open_setting
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_fixture,
    _screen_harness_fixture,
    _ScreenHarness,
)
from agent_toolkit._common import env_values

pytestmark = [pytest.mark.browser]


@pytest.mark.asyncio(loop_scope="session")
async def test_shared_env_editor_masks_preserves_overwrites_and_deletes(screen_harness: _ScreenHarness, tmp_path, monkeypatch):
    monkeypatch.setattr(usage_refresh, "refresh", lambda **_kwargs: [])
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "shared"))
    config.save_mutable_settings(
        {
            "claude_payg_env": "ANTHROPIC_API_KEY=fixture-private\nANTHROPIC_BASE_URL=https://example.invalid\nAWS_REGION=us-east-1"
        }
    )
    page = screen_harness.page
    responses = []

    async def record(response):
        if "/api/settings" in response.url:
            responses.append(await response.text())

    page.on("response", record)
    await page.goto(screen_harness.base_url + "/settings")
    await page.get_by_role("navigation", name="設定の分類").get_by_role("button", name="接続", exact=True).click()
    row = page.locator('[data-scope="shared"] [data-setting="claude_payg_env"]')
    expect = playwright.async_api.expect
    await expect(row.locator(":scope > summary strong")).to_have_text("claude_payg_env")
    await _open_setting(row)
    field = row.get_by_label("保存値")
    await expect(field).to_have_value(
        "ANTHROPIC_API_KEY=[REDACTED]\nANTHROPIC_BASE_URL=https://example.invalid\nAWS_REGION=us-east-1"
    )
    await expect(field).to_have_accessible_description(re.compile("全ホストで共有"))
    await expect(row.get_by_text(str(config_env.path()), exact=False)).to_be_visible()
    await field.fill((await field.input_value()).replace("us-east-1", "eu-west-1"))
    await row.get_by_role("button", name="保存", exact=True).click()
    await expect(row.locator("[data-settings-result]")).to_contain_text("保存済み")
    assert env_values.parse(config.resolve_mutable_setting("claude_payg_env"))["ANTHROPIC_API_KEY"] == "fixture-private"
    await page.reload()
    await page.get_by_role("navigation", name="設定の分類").get_by_role("button", name="接続", exact=True).click()
    await _open_setting(row)
    await expect(field).to_have_value(
        "ANTHROPIC_API_KEY=[REDACTED]\nANTHROPIC_BASE_URL=https://example.invalid\nAWS_REGION=eu-west-1"
    )
    await field.fill("ANTHROPIC_API_KEY=fixture-replaced\nAWS_REGION=eu-west-1")
    await row.get_by_role("button", name="保存", exact=True).click()
    await expect(field).to_have_value("ANTHROPIC_API_KEY=[REDACTED]\nAWS_REGION=eu-west-1")
    assert env_values.parse(config.resolve_mutable_setting("claude_payg_env"))["ANTHROPIC_API_KEY"] == "fixture-replaced"
    await field.fill("AWS_REGION=eu-west-1")
    await row.get_by_role("button", name="保存", exact=True).click()
    await expect(row.locator("[data-settings-result]")).to_contain_text("保存済み")
    assert env_values.parse(config.resolve_mutable_setting("claude_payg_env")) == {"AWS_REGION": "eu-west-1"}
    assert "claude_payg_env" not in config_settings.load_config()
    assert "fixture-private" not in json.dumps(responses) and "fixture-replaced" not in json.dumps(responses)

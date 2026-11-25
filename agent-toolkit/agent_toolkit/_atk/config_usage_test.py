"""公開設定操作と実際の候補解決の一致、期限後の最新設定を検証する。"""

import json
import pathlib

import pytest

from agent_toolkit._atk import config, config_usage, usage_refresh
from agent_toolkit._common import state_paths, usage_observation, usage_settings
from agent_toolkit._testing.config_support import (
    _isolate_platformdirs,
    _isolate_serve_environment_fixture,
    _main,
    _saved,
    _write_config,
)

__all__ = ["_isolate_platformdirs", "_isolate_serve_environment_fixture"]


@pytest.fixture(autouse=True)
def isolated_usage(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path / "usage")
    monkeypatch.setattr(usage_observation, "state_dir", lambda: tmp_path / "usage")
    monkeypatch.setattr(config_usage.time, "time", lambda: 100.0)
    monkeypatch.setattr(usage_refresh, "ensure_fresh", lambda **_kwargs: None)


def test_temporary_expiry_uses_current_normal_setting(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = "codex:model-one/low"
    temporary = "claude:temporary-model/high"
    latest = "codex:model-two/medium"
    assert _main(tmp_path, "config", "set", "execute_model", first) == 0
    assert (
        _main(tmp_path, "config", "temporary", "set", "execute_model", temporary, "--until", "1970-01-01T00:03:20+00:00") == 0
    )
    assert config.resolve_mutable_setting("execute_model") == temporary
    assert _main(tmp_path, "config", "set", "execute_model", latest) != 0
    assert config.resolve_mutable_setting("execute_model") == temporary
    monkeypatch.setattr(config_usage.time, "time", lambda: 201.0)
    assert config.resolve_mutable_setting("execute_model") == latest


def test_environment_wins_and_clear_does_not_restore_snapshot(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _main(tmp_path, "config", "set", "execute_model", "codex:model-one/low") == 0
    assert _main(tmp_path, "config", "temporary", "set", "execute_model", "claude:temporary/high") == 0
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_EXECUTE_MODEL", "codex:explicit/high")
    assert config.resolve_mutable_setting("execute_model") == "codex:explicit/high"
    assert _main(tmp_path, "config", "temporary", "clear") == 0
    monkeypatch.delenv("AGENT_TOOLKIT_CONFIG_EXECUTE_MODEL")
    assert config.resolve_mutable_setting("execute_model") == "codex:model-one/low"


@pytest.mark.parametrize("stage", ["execute", "review", "review_fast", "chores", "chores_fast"])
@pytest.mark.parametrize("override", ["saved", "temporary", "environment"])
def test_quota_observations_do_not_change_candidate_order(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, stage: str, override: str
) -> None:
    key = f"{stage}_model"
    chosen = "claude:opus/high,codex:sol/low,claude:sonnet/medium"
    other = "codex:sol/medium,claude:opus/low"
    assert _main(tmp_path, "config", "set", key, chosen if override == "saved" else other) == 0
    if override == "temporary":
        assert _main(tmp_path, "config", "temporary", "set", key, chosen) == 0
    if override == "environment":
        assert _main(tmp_path, "config", "temporary", "set", key, other) == 0
        monkeypatch.setenv(f"AGENT_TOOLKIT_CONFIG_{key.upper()}", chosen)
    usage_settings.record_metadata("claude", {"account": "account", "contract": "plan"})
    usage_observation.observe(
        [
            usage_observation.QuotaWindow("claude", "account", "weekly", 100, 1000, 100, contract="plan"),
            usage_observation.QuotaWindow(
                "claude", "account", "seven_day_opus", None, 1000, 100, contract="plan", rejected=True
            ),
        ],
        setting_signature="unknown",
        active_seconds=0,
    )
    assert config.parse_unresolved_model_candidates(stage) == [
        ("claude", "opus", "high"),
        ("codex", "sol", "low"),
        ("claude", "sonnet", "medium"),
    ]


def test_usage_refresh_displays_observed_quota_and_reset_inventory(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refresh(*, force: bool = False) -> None:
        assert force
        usage_refresh.record_codex(
            {
                "accountId": "account",
                "rateLimits": {"primary": {"usedPercent": 20, "resetsAt": 1000}},
                "rateLimitResetCredits": {"availableCount": 2},
            },
            contract="plan",
        )

    monkeypatch.setattr(usage_refresh, "ensure_fresh", refresh)
    assert _main(tmp_path, "config", "usage", "--refresh") == 0
    lines = capsys.readouterr().out
    assert "観測残量=80%" in lines and "回復=" in lines and "最終観測=" in lines
    assert "Codex reset在庫: 2" in lines
    assert _main(tmp_path, "config", "usage", "--json") == 0
    details = json.loads(capsys.readouterr().out)
    assert "selection" not in details
    assert details["usage"]["windows"][0]["resets_at"] == 1000
    assert details["metadata"]["codex"]["reset_available_count"] == 2


@pytest.mark.parametrize(
    "key,value,extra",
    [
        ("execute_model", "codex:model/low", ["--until", "2030-01-01T09:00:00"]),
        ("claude_payg_env_file", "/not/allowed", []),
    ],
)
def test_invalid_temporary_input_does_not_save(tmp_path: pathlib.Path, key: str, value: str, extra: list[str]) -> None:
    assert _main(tmp_path, "config", "temporary", "set", key, value, *extra) == 2
    assert not config_usage.temporary_settings()


def test_legacy_inventory_migrates_without_granting_permission(tmp_path, capsys):
    inventory = [
        {
            "id": "right",
            "engine": "claude",
            "account": "account",
            "scope": "all",
            "count": 2,
            "from": "1970-01-01T00:00:00+00:00",
            "until": "1970-01-01T00:10:00+00:00",
        },
    ]
    _write_config(tmp_path, {"claude_reset_inventory": json.dumps(inventory)})
    assert _main(tmp_path, "config", "usage", "--json") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["claude_reset_inventory"] == inventory
    assert not result["claude_reset_advice"][0]["recommended"]
    assert _saved(tmp_path) == {}
    assert _main(tmp_path, "config", "usage", "--set-claude-reset-inventory", "[]") == 0
    capsys.readouterr()
    assert _main(tmp_path, "config", "usage", "--json") == 0
    assert json.loads(capsys.readouterr().out)["claude_reset_inventory"] == []


def test_set_with_temporary_value_requires_no_resave_after_clearing_one_key(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _main(tmp_path, "config", "temporary", "set", "execute_model", "claude:opus/medium") == 0
    assert _main(tmp_path, "config", "temporary", "set", "review_model", "claude:sonnet/medium") == 0
    capsys.readouterr()
    assert _main(tmp_path, "config", "set", "execute_model", "codex:sol/medium") != 0
    output = capsys.readouterr()
    assert "成功:" not in output.out
    assert "保存済み" in output.err and "temporary clear execute_model" in output.err
    assert _main(tmp_path, "config", "temporary", "clear", "execute_model") == 0
    assert config.resolve_mutable_setting("execute_model") == "codex:sol/medium"
    assert config.resolve_mutable_setting("review_model") == "claude:sonnet/medium"


def test_usage_and_snapshot_keep_existing_window_keys_but_editor_gets_extra_fields(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    usage_refresh.record_codex(
        {
            "accountId": "fixture-workspace",
            "rateLimits": {
                "limitId": "codex",
                "limitName": "fixture-plan",
                "primary": {"usedPercent": 20, "resetsAt": 96000, "windowDurationMins": 10080},
            },
            "ordinaryUsageAllowed": True,
            "rateLimitResetCredits": {"availableCount": 0, "credits": [{"id": "fixture-credit", "status": "active"}]},
        },
        contract="plan",
        permission_account="fixture-permission-account",
    )
    legacy_fields = {
        "engine",
        "account",
        "window_id",
        "used_percent",
        "resets_at",
        "observed_at",
        "scope",
        "contract",
        "duration_seconds",
        "rejected",
    }
    assert _main(tmp_path, "config", "usage", "--json") == 0
    usage = json.loads(capsys.readouterr().out)
    for key in ("windows", "current_windows"):
        assert set(usage["usage"][key][0]) == legacy_fields
    assert usage["metadata"]["codex"] == {
        "account": "fixture-workspace",
        "contract": "plan",
        "reset_available_count": 0,
        "permission_account": "fixture-permission-account",
        "observed_at": 100.0,
    }
    assert _main(tmp_path, "config", "snapshot") == 0
    snapshot = json.loads(capsys.readouterr().out)
    assert set(snapshot["usage"][0]) == legacy_fields
    assert snapshot["reset_editor"]["accounts"]["codex"]["account"] == "fixture-permission-account"
    assert snapshot["reset_editor"]["windows"][0]["limit_name"] == "fixture-plan"

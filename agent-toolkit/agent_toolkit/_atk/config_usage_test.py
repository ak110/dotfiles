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
    assert _main(tmp_path, "config", "set", "execute_model", latest) == 0
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


def test_public_usage_matches_launch_candidates(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _main(tmp_path, "config", "set", "execute_model", "claude:original/high,codex:new-model/low") == 0
    assert _main(tmp_path, "config", "set", "usage_auto_allocation", "true") == 0
    for engine in ("claude", "codex"):
        usage_settings.record_metadata(engine, {"account": "account", "contract": ""})
    usage_observation.observe(
        [
            usage_observation.QuotaWindow("claude", "account", "weekly", 97, 1000, 100),
            usage_observation.QuotaWindow("codex", "account", "primary", 10, 1000, 100),
        ],
        setting_signature="unknown",
        active_seconds=0,
    )
    assert config.parse_unresolved_model_candidates("execute") == [
        ("codex", "new-model", "low"),
        ("claude", "original", "high"),
    ]
    capsys.readouterr()
    assert _main(tmp_path, "config", "usage") == 0
    lines = capsys.readouterr().out.splitlines()
    assert all(detail in lines[0] for detail in ("codex,claude", "Claude", "ひっ迫", "weekly", "97%"))
    details = json.loads(lines[-1])
    assert details["settings"]["execute_model"]["effective"] == "claude:original/high,codex:new-model/low"
    assert details["usage"]["windows"][0]["used_percent"] is not None


def test_emergency_override_precedes_subscription_quota(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _main(tmp_path, "config", "set", "execute_model", "claude:chosen/high,codex:fallback/low") == 0
    assert _main(tmp_path, "config", "set", "usage_auto_allocation", "true") == 0
    usage_settings.record_metadata("claude", {"account": "account", "contract": "plan"})
    usage_observation.observe(
        [usage_observation.QuotaWindow("claude", "account", "weekly", 99, 1000, 100, contract="plan")],
        setting_signature="unknown",
        active_seconds=0,
    )
    assert config.parse_unresolved_model_candidates("execute") == [("codex", "fallback", "low"), ("claude", "chosen", "high")]
    assert _main(tmp_path, "config", "temporary", "set", "claude_worker_connections", "payg") == 0
    assert config.parse_unresolved_model_candidates("execute") == [("claude", "chosen", "high"), ("codex", "fallback", "low")]
    assert _main(tmp_path, "config", "temporary", "clear") == 0
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_EXECUTE_MODEL", "claude:explicit/high")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_WORKER_CONNECTIONS", "payg")
    assert config.parse_unresolved_model_candidates("execute") == [("claude", "explicit", "high")]


def test_temporary_model_keeps_automatic_engine_order(tmp_path: pathlib.Path) -> None:
    assert _main(tmp_path, "config", "set", "usage_auto_allocation", "true") == 0
    assert _main(tmp_path, "config", "temporary", "set", "execute_model", "claude:new-one/high,codex:new-two/low") == 0
    assert config.parse_unresolved_model_candidates("execute") == [("codex", "new-two", "low"), ("claude", "new-one", "high")]


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

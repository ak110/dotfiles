"""config migrationの契約を検証する。"""

from __future__ import annotations

import json

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config as config_module
from agent_toolkit._atk import config_settings as _config_settings
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._testing.config_support import (
    _DEFAULTS,
    _config_file,
    _isolate_platformdirs,
    _isolate_serve_environment_fixture,
    _main,
    _recovery_old_values,
    _saved,
    _write_config,
)

__all__ = ["_isolate_platformdirs", "_isolate_serve_environment_fixture"]


@pytest.mark.parametrize(
    "preset", ["codex-balanced", "codex-near-primary", "codex-primary", "claude-balanced", "claude-primary"]
)
def test_config_recovery_preset_repairs_all_missing_stages_atomically(tmp_path, capsys, monkeypatch, preset):
    old = _recovery_old_values(multiple=True)
    _write_config(tmp_path, old)
    original_write = config_module.atomic_write
    writes = []

    def write(path, content):
        writes.append(json.loads(content))
        original_write(path, content)

    monkeypatch.setattr(_config_settings, "atomic_write", write)
    assert _main(tmp_path, "config", "apply-preset", preset) == 0
    capsys.readouterr()
    saved = _saved(tmp_path)
    assert len(writes) == 1
    for key in ("write_model", "codex_fast_mode", "dotfiles_mode", "other_setting"):
        assert saved[key] == old[key]
    assert not any(key.endswith("_engine") or "tier_model" in key for key in saved)
    assert all(
        saved[f"{stage}_model"] for stage in ("execute", "review", "review_fast", "chores", "chores_fast", "orchestrate")
    )
    assert _main(tmp_path, "config", "show") == 0
    assert not capsys.readouterr().err


def test_config_recovery_set_repairs_missing_stage_without_replacing_other_candidates(tmp_path, capsys):
    old = _recovery_old_values()
    _write_config(tmp_path, old)
    value = "agy:gemini-4-argon/medium"
    assert _main(tmp_path, "config", "set", "review_fast_model", value) == 0
    assert not capsys.readouterr().err
    saved = _saved(tmp_path)
    assert saved["review_fast_model"] == value
    assert saved["review_model"] == "opencode:openai/gpt-4.1"
    assert saved["write_model"] == old["write_model"]
    assert saved["codex_fast_mode"] == old["codex_fast_mode"]
    assert saved["dotfiles_mode"] == old["dotfiles_mode"]
    assert saved["other_setting"] == old["other_setting"]
    assert "review_engine" not in saved
    assert _main(tmp_path, "config", "get", "review_fast_model") == 0
    assert capsys.readouterr().out == value + "\n"


@pytest.mark.parametrize("first_key,first_value", [("execute_model", "claude:opus/high"), ("codex_fast_mode", "false")])
def test_config_recovery_multiple_missing_stages_preserve_each_explicit_set(tmp_path, capsys, first_key, first_value):
    old = _recovery_old_values(multiple=True)
    _write_config(tmp_path, old)
    assert _main(tmp_path, "config", "set", first_key, first_value) == 0
    capsys.readouterr()
    saved = _saved(tmp_path)
    assert saved[first_key] == first_value
    assert saved["review_fast_model"] == ""
    assert saved["chores_fast_model"] == ""
    assert saved["review_model"] == saved["chores_model"] == "opencode:openai/gpt-4.1"
    repairs = [("review_fast_model", "agy:gemini-4-argon/medium"), ("chores_fast_model", "claude:haiku/high")]
    for key, value in repairs:
        assert _main(tmp_path, "config", "show") == 2
        diagnostic = capsys.readouterr().err
        assert f"atk config set {key} <VALUE>" in diagnostic
        assert _main(tmp_path, "config", "set", key, value) == 0
        capsys.readouterr()
        assert _saved(tmp_path)[first_key] == first_value
    assert _main(tmp_path, "config", "show") == 0
    assert not capsys.readouterr().err
    saved = _saved(tmp_path)
    assert all(saved[key] == value for key, value in repairs)
    assert saved["write_model"] == old["write_model"]
    assert saved["other_setting"] == old["other_setting"]


def test_config_recovery_read_failure_is_unchanged_and_guides_a_working_set(tmp_path, capsys):
    path = _write_config(tmp_path, _recovery_old_values())
    before = path.read_bytes()
    assert _main(tmp_path, "config", "get", "review_model") == 2
    diagnostic = capsys.readouterr().err
    assert "review_fast_model" in diagnostic and "apply-preset" in diagnostic
    assert path.read_bytes() == before
    assert _main(tmp_path, "config", "set", "review_fast_model", "claude:haiku") == 0
    capsys.readouterr()
    assert _main(tmp_path, "config", "get", "review_model") == 0
    assert capsys.readouterr().out == "opencode:openai/gpt-4.1\n"


@pytest.mark.parametrize("operation", ["set", "apply-preset"])
def test_config_recovery_failed_atomic_write_keeps_original_bytes(tmp_path, monkeypatch, operation):
    path = _write_config(tmp_path, _recovery_old_values(multiple=True))
    before = path.read_bytes()
    attempts = []

    def fail_write(_path, content, **_kwargs):
        attempts.append(json.loads(content))
        raise OSError("検証用の保存拒否")

    monkeypatch.setattr(_config_settings, "atomic_write", fail_write)
    arguments = ["set", "execute_model", "claude:opus/high"] if operation == "set" else ["apply-preset", "codex-balanced"]
    with pytest.raises((OSError, SystemExit)) as caught:
        atk.main(["config", *arguments], home=tmp_path)
    assert isinstance(caught.value, OSError) or (isinstance(caught.value, SystemExit) and caught.value.code != 0)
    assert len(attempts) == 1
    assert attempts[0]["other_setting"] == "keep"
    assert "review_engine" not in attempts[0]
    assert path.read_bytes() == before


def test_config_recovery_invalid_explicit_value_never_changes_legacy_file(tmp_path, capsys):
    path = _write_config(tmp_path, _recovery_old_values(multiple=True))
    before = path.read_bytes()
    assert _main(tmp_path, "config", "set", "review_fast_model", "opencode:invalid") == 2
    assert "受理可能書式" in capsys.readouterr().err
    assert path.read_bytes() == before


@pytest.mark.parametrize("entry", ["show", "get", "resolve"])
def test_migration_overwrites_stale_stage_values_and_removes_retired_keys(tmp_path, capsys, entry):
    old = {
        "base_engine": "claude,codex",
        "execute_engine": "claude,codex",
        "review_engine": "codex,claude",
        "chores_engine": "claude,codex",
        "orchestrate_engine": "codex,claude",
        "claude_high_tier_model": "opus/high",
        "claude_medium_tier_model": "sonnet/low",
        "claude_low_tier_model": "haiku",
        "codex_high_tier_model": "gpt-6-sol/high",
        "codex_medium_tier_model": "gpt-5.6-terra/medium",
        "codex_low_tier_model": "gpt-6-luna/low",
        "opencode_high_tier_model": "",
        "opencode_medium_tier_model": "",
        "opencode_low_tier_model": "",
        "execute_model": "claude:stale",
        "orchestrate_model": "claude:stale",
        "execute_fast_model": "claude:stale",
        "execute_review_model": "claude:stale",
        "explore_model": "claude:stale",
        "explore_fast_model": "claude:stale",
        "pick_wi_model": "claude:stale",
        "plan_model": "claude:stale",
        "plan_review_model": "claude:stale",
        "session_review_model": "claude:stale",
        "high_tier_model": "claude:stale",
        "medium_tier_model": "claude:stale",
        "low_tier_model": "claude:stale",
        "write_model": "agy:gemini-4-argon",
        "codex_fast_mode": "true",
    }
    path = _write_config(tmp_path, old)
    if entry == "resolve":
        assert config_module.parse_unresolved_model_candidates("execute")[0] == ("claude", "opus", "high")
    else:
        assert _main(tmp_path, "config", entry, *(["execute_model"] if entry == "get" else [])) == 0
        capsys.readouterr()
    expected = {
        "execute_model": "claude:opus/high,codex:gpt-6-sol/high",
        "review_model": "codex:gpt-5.6-terra/medium,claude:sonnet/low",
        "review_fast_model": "codex:gpt-6-luna/low,claude:haiku",
        "chores_model": "claude:sonnet/low,codex:gpt-5.6-terra/medium",
        "chores_fast_model": "claude:haiku,codex:gpt-6-luna/low",
        "orchestrate_model": "codex:gpt-6-sol/high,claude:opus/high",
        "write_model": "agy:gemini-4-argon",
        "codex_fast_mode": "true",
    }
    assert _saved(tmp_path) == expected
    migrated = path.read_bytes()
    assert _main(tmp_path, "config", "show") == 0
    assert path.read_bytes() == migrated


@pytest.mark.parametrize(
    "legacy",
    [
        "high_tier_model",
        "medium_tier_model",
        "low_tier_model",
        "claude_high_tier_model",
        "claude_medium_tier_model",
        "claude_low_tier_model",
        "codex_high_tier_model",
        "codex_medium_tier_model",
        "codex_low_tier_model",
        "opencode_high_tier_model",
        "opencode_medium_tier_model",
        "opencode_low_tier_model",
        "base_engine",
        "execute_engine",
        "review_engine",
        "chores_engine",
        "orchestrate_engine",
    ],
)
def test_retired_environment_diagnostic_and_keys_rejected(tmp_path, capsys, monkeypatch, legacy):
    for operation in ("get", "set"):
        assert _main(tmp_path, "config", operation, legacy, *(["claude:a"] if operation == "set" else [])) == 2
        assert "工程別" in capsys.readouterr().err
    variable = f"AGENT_TOOLKIT_CONFIG_{legacy.upper()}"
    monkeypatch.setenv(variable, "old-value")
    assert _main(tmp_path, "config", "get", "execute_model") == 2
    diagnostic = capsys.readouterr().err
    expected_stage = {
        "medium_tier_model": "review",
        "low_tier_model": "review_fast",
        "claude_medium_tier_model": "review",
        "claude_low_tier_model": "review_fast",
        "codex_medium_tier_model": "review",
        "codex_low_tier_model": "review_fast",
        "opencode_medium_tier_model": "review",
        "opencode_low_tier_model": "review_fast",
        "review_engine": "review",
        "chores_engine": "chores",
        "orchestrate_engine": "orchestrate",
    }.get(legacy, "execute")
    assert variable in diagnostic and f"AGENT_TOOLKIT_CONFIG_{expected_stage.upper()}_MODEL" in diagnostic
    with pytest.raises(_next_action.ActionableError):
        config_module.resolve_model_candidates("execute")
    assert not _config_file(tmp_path).exists()


def test_partial_legacy_configuration_uses_defaults_and_opencode_variant(tmp_path):
    _write_config(tmp_path, {"review_engine": "opencode,codex", "opencode_medium_tier_model": "local/org/model@high"})
    assert config_module.parse_unresolved_model_candidates("review") == [
        ("opencode", "local/org/model", "high"),
        ("codex", "sol", "medium"),
    ]
    assert config_module.parse_unresolved_model_candidates("review_fast") == [("codex", "luna", "medium")]
    assert set(_saved(tmp_path)) == set(_DEFAULTS) - {"write_model"}


def test_invalid_legacy_migration_keeps_file_unchanged(tmp_path, capsys):
    path = _write_config(tmp_path, {"review_engine": "opencode", "opencode_medium_tier_model": "local/model"})
    before = path.read_bytes()
    assert _main(tmp_path, "config", "get", "review_model") == 2
    assert "受理可能書式" in capsys.readouterr().err
    assert path.read_bytes() == before


@pytest.mark.parametrize("operation", ["set", "apply-preset"])
def test_explicit_save_after_legacy_configuration_keeps_user_choice(tmp_path, capsys, operation):
    _write_config(tmp_path, {"execute_engine": "codex", "codex_high_tier_model": "gpt-6-sol/high"})
    arguments = ["set", "execute_model", "claude:sonnet"] if operation == "set" else ["apply-preset", "claude-primary"]
    assert _main(tmp_path, "config", *arguments) == 0
    capsys.readouterr()
    assert _main(tmp_path, "config", "get", "execute_model") == 0
    expected = "claude:sonnet" if operation == "set" else _DEFAULTS["execute_model"]
    assert capsys.readouterr().out == expected + "\n"
    assert "execute_engine" not in _saved(tmp_path)


def test_empty_retired_environment_stops_without_migrating_file(tmp_path, monkeypatch, capsys):
    path = _write_config(tmp_path, {"review_engine": "claude"})
    before = path.read_bytes()
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REVIEW_ENGINE", "")
    assert _main(tmp_path, "config", "show") == 2
    assert "AGENT_TOOLKIT_CONFIG_REVIEW_FAST_MODEL" in capsys.readouterr().err
    assert path.read_bytes() == before


@pytest.mark.parametrize("explicit", [False, True])
def test_legacy_saved_base_inheritance_preserves_effective_execute_order(tmp_path, explicit):
    old = {"base_engine": "codex", "codex_high_tier_model": "gpt-6-sol/high"}
    if explicit:
        old["execute_engine"] = "claude"
    _write_config(tmp_path, old)
    expected = [("claude", "opus[1m]", "medium")] if explicit else [("codex", "gpt-6-sol", "high")]
    assert config_module.parse_unresolved_model_candidates("execute") == expected


def test_pre_tier_configuration_is_cleaned_without_reintroducing_old_migration(tmp_path):
    _write_config(
        tmp_path,
        {"high_tier_model": "agy:obsolete", "medium_tier_model": "claude:obsolete", "low_tier_model": "codex:obsolete"},
    )
    assert config_module.parse_unresolved_model_candidates("execute")[0] == ("claude", "opus[1m]", "low")
    assert not _saved(tmp_path)

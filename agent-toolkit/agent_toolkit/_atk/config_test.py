"""工程別モデル設定の公開CLI・候補解決・移行の回帰。"""

import json
import pathlib
from typing import cast

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import claude_connection
from agent_toolkit._atk import config as config_module
from agent_toolkit._common import codex_models, state_paths
from agent_toolkit._common import next_action as _next_action


def _recovery_old_values(*, multiple=False):
    values = {
        "review_engine": "opencode",
        "opencode_medium_tier_model": "openai/gpt-4.1",
        "write_model": "agy:gemini-4-argon/high",
        "codex_fast_mode": "true",
        "claude_api_mode": "api-only",
        "other_setting": "keep",
        "review_fast_model": "claude:stale",
    }
    if multiple:
        values["chores_engine"] = "opencode"
    return values


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

    monkeypatch.setattr(config_module, "atomic_write", write)
    assert _main(tmp_path, "config", "apply-preset", preset) == 0
    capsys.readouterr()
    saved = _saved(tmp_path)
    assert len(writes) == 1
    for key in ("write_model", "codex_fast_mode", "claude_api_mode", "other_setting"):
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
    assert saved["claude_api_mode"] == old["claude_api_mode"]
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

    monkeypatch.setattr(config_module, "atomic_write", fail_write)
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


_LEGACY_KEYS = ("high_tier_model", "medium_tier_model", "low_tier_model")

_DEFAULTS = {
    "execute_model": "claude:opus[1m]/medium,codex:sol/medium",
    "review_model": "codex:terra/medium,claude:sonnet[1m]/medium",
    "review_fast_model": "codex:luna/medium,claude:haiku/medium",
    "chores_model": "codex:terra/medium,claude:sonnet[1m]/medium",
    "chores_fast_model": "codex:luna/medium,claude:haiku/medium",
    "orchestrate_model": "claude:opus[1m]/medium,codex:sol/medium",
    "write_model": "agy:gemini-3.8-flash/medium,claude:claude-opus-5-5/medium",
}


@pytest.mark.parametrize("stage", ["execute", "review", "review_fast", "chores", "chores_fast", "orchestrate", "write"])
def test_public_model_round_trip_and_environment_precedence(tmp_path, monkeypatch, capsys, stage):
    key = f"{stage}_model"
    assert _main(tmp_path, "config", "get", key) == 0
    assert capsys.readouterr().out == _DEFAULTS[key] + "\n"
    assert not _config_file(tmp_path).exists()
    saved = "claude:opus/high,agy:gemini-4-argon/medium,codex:gpt-6-sol"
    assert _main(tmp_path, "config", "set", key, saved) == 0
    assert not capsys.readouterr().err
    assert config_module.parse_unresolved_model_candidates(stage) == [
        ("claude", "opus", "high"),
        ("agy", "gemini-4-argon", "medium"),
        ("codex", "gpt-6-sol", "medium"),
    ]
    monkeypatch.setenv(f"AGENT_TOOLKIT_CONFIG_{key.upper()}", "claude:sonnet/low")
    assert _main(tmp_path, "config", "get", key) == 0
    assert capsys.readouterr().out == "claude:sonnet/low\n"
    assert _main(tmp_path, "config", "show") == 0
    assert f"{key} claude:sonnet/low\n" in capsys.readouterr().out
    assert _main(tmp_path, "config", "set", key, saved) == 0
    assert "優先" in capsys.readouterr().err
    assert _saved(tmp_path)[key] == saved
    monkeypatch.delenv(f"AGENT_TOOLKIT_CONFIG_{key.upper()}")
    assert config_module.resolve_mutable_setting(key) == saved
    monkeypatch.setenv(f"AGENT_TOOLKIT_CONFIG_{key.upper()}", "")
    assert config_module.resolve_mutable_setting(key) == saved


@pytest.mark.parametrize(
    "preset", ["codex-balanced", "codex-near-primary", "codex-primary", "claude-balanced", "claude-primary"]
)
def test_presets_save_exact_six_stage_values_and_preserve_write(tmp_path, capsys, preset):
    expected = {key: value for key, value in _DEFAULTS.items() if key != "write_model"}
    if preset.startswith("codex-") and preset != "codex-balanced":
        expected["execute_model"] = "codex:sol/medium,claude:opus[1m]/medium"
    if preset == "codex-primary":
        expected["orchestrate_model"] = "codex:sol/medium,claude:opus[1m]/medium"
    if preset.startswith("claude-"):
        expected["chores_model"] = "claude:sonnet[1m]/medium,codex:terra/medium"
        expected["chores_fast_model"] = "claude:haiku/medium,codex:luna/medium"
    if preset == "claude-primary":
        expected["review_model"] = "claude:sonnet[1m]/medium,codex:terra/medium"
        expected["review_fast_model"] = "claude:haiku/medium,codex:luna/medium"
    kept = {"write_model": "agy:gemini-4-argon/high", "codex_fast_mode": "true", "other_setting": "keep"}
    _write_config(tmp_path, kept)
    assert _main(tmp_path, "config", "apply-preset", preset) == 0
    assert not capsys.readouterr().err
    assert _saved(tmp_path) == {**kept, **expected}
    assert _main(tmp_path, "config", "get", *expected) == 0
    assert capsys.readouterr().out.splitlines() == list(expected.values())


@pytest.mark.parametrize("arguments", [[], ["show"]])
@pytest.mark.parametrize("exists", [False, True])
def test_preset_listing_never_writes_settings(tmp_path, capsys, arguments, exists):
    if exists:
        _write_config(tmp_path, {"claude_low_tier_model": "haiku"})
    before = _config_file(tmp_path).read_bytes() if exists else None
    assert _main(tmp_path, "config", "apply-preset", *arguments) == 0
    shown = capsys.readouterr()
    assert not shown.err
    assert [line[:-1] for line in shown.out.splitlines() if not line.startswith(" ")] == [
        "codex-balanced",
        "codex-near-primary",
        "codex-primary",
        "claude-balanced",
        "claude-primary",
    ]
    assert (_config_file(tmp_path).read_bytes() if exists else None) == before


@pytest.mark.parametrize(
    "value",
    [
        "",
        "codex:",
        "other:model",
        "codex:a/b/c",
        "claude:model/",
        "claude:a,",
        "claude:a，codex:b",
        "claude:a, codex:b",
        "opencode:local",
        "opencode:local/a@",
    ],
)
def test_invalid_candidates_are_atomic_for_cli_and_environment(tmp_path, capsys, monkeypatch, value):
    path = _write_config(tmp_path, {"review_model": "claude:sonnet"})
    before = path.read_bytes()
    assert _main(tmp_path, "config", "set", "review_model", value) == 2
    assert "受理可能書式" in capsys.readouterr().err
    assert path.read_bytes() == before
    if value:
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REVIEW_MODEL", value)
        assert _main(tmp_path, "config", "get", "review_model") == 2
        assert "AGENT_TOOLKIT_CONFIG_REVIEW_MODEL" in capsys.readouterr().err
        assert path.read_bytes() == before


@pytest.mark.parametrize("stage", ["execute", "review", "review_fast", "chores", "chores_fast", "write"])
@pytest.mark.parametrize(
    ("value", "model", "effort"),
    [
        ("local/org/medium", "local/org/medium", "medium"),
        ("local/Qwen/model/high", "local/Qwen/model/high", "medium"),
        ("local/Qwen/model@thinking", "local/Qwen/model", "thinking"),
    ],
)
def test_opencode_paths_preserve_namespace_and_variant(tmp_path, capsys, stage, value, model, effort):
    candidate = f"opencode:{value}"
    assert _main(tmp_path, "config", "set", f"{stage}_model", candidate) == 0
    assert not capsys.readouterr().err
    assert config_module.resolve_model_candidates(stage) == [("opencode", model, effort)]
    assert config_module.resolve_model_candidates(candidate) == [("opencode", model, effort)]


def test_orchestrate_rejects_opencode_from_cli_and_environment(tmp_path, monkeypatch, capsys):
    assert _main(tmp_path, "config", "set", "orchestrate_model", "opencode:local/model") == 2
    assert "受理可能書式" in capsys.readouterr().err
    assert not _config_file(tmp_path).exists()
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_ORCHESTRATE_MODEL", "opencode:local/model")
    assert _main(tmp_path, "config", "get", "orchestrate_model") == 2
    assert "受理可能書式" in capsys.readouterr().err


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


def test_direct_candidates_preserve_order_and_bypass_saved_settings(tmp_path):
    path = _write_config(tmp_path, {"base_engine": "broken", "execute_model": "broken"})
    before = path.read_bytes()
    assert config_module.resolve_model_candidates("claude:sonnet/high,claude:sonnet/low,codex:gpt-6-sol") == [
        ("claude", "sonnet", "high"),
        ("claude", "sonnet", "low"),
        ("codex", "gpt-6-sol", "medium"),
    ]
    assert path.read_bytes() == before


def test_family_catalog_tracks_latest_without_rewriting_saved_value(tmp_path, monkeypatch, capsys):
    assert _main(tmp_path, "config", "set", "execute_model", "codex:sol/high") == 0
    capsys.readouterr()
    monkeypatch.setattr(
        codex_models,
        "list_models",
        lambda: [
            {"model": "gpt-6-sol", "supportedReasoningEfforts": [{"reasoningEffort": "high"}]},
            {"model": "gpt-6.1-sol", "supportedReasoningEfforts": [{"reasoningEffort": "high"}]},
        ],
    )
    assert config_module.resolve_model_candidates("execute") == [("codex", "gpt-6.1-sol", "high")]
    assert _saved(tmp_path) == {"execute_model": "codex:sol/high"}
    with pytest.raises(ValueError):
        config_module.resolve_model_candidates("codex:sol/max")


@pytest.mark.parametrize(
    "name", ["high_tier", "medium_tier", "low_tier", "execute_fast", "unknown", "codex:", "nosuch:model", "codex:a/b/c"]
)
def test_unknown_or_retired_model_types_are_rejected(name):
    with pytest.raises(ValueError, match="unknown model_type"):
        config_module.resolve_model_candidates(name)


def test_partial_legacy_configuration_uses_old_defaults_and_opencode_variant(tmp_path):
    _write_config(tmp_path, {"review_engine": "opencode,codex", "opencode_medium_tier_model": "local/org/model@high"})
    assert config_module.parse_unresolved_model_candidates("review") == [
        ("opencode", "local/org/model", "high"),
        ("codex", "terra", "medium"),
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


def test_show_and_guidance_preserve_engineering_key_order(tmp_path, capsys):
    assert _main(tmp_path, "config", "show") == 0
    output = capsys.readouterr().out
    keys = [line.partition(" ")[0] for line in output.splitlines()]
    assert keys[:11] == ["config_dir", "state_dir", "data_dir", "private_notes", *_DEFAULTS]
    assert not _config_file(tmp_path).exists()
    assert _main(tmp_path, "config", "set", "missing-key", "x") == 2
    error = capsys.readouterr().err
    positions = [error.index(key) for key in _DEFAULTS]
    assert positions == sorted(positions)


@pytest.mark.parametrize("preset", ["unknown", "opencode-balanced", "opencode-near-primary"])
def test_removed_and_unknown_presets_are_rejected_without_writing(tmp_path, capsys, preset):
    assert _main(tmp_path, "config", "apply-preset", preset) == 2
    assert not capsys.readouterr().out
    assert not _config_file(tmp_path).exists()


def test_unknown_model_and_effort_warn_but_persist(tmp_path, capsys):
    value = "codex:new-model/ultra"
    assert _main(tmp_path, "config", "set", "chores_model", value) == 0
    diagnostic = capsys.readouterr().err
    assert "モデル名`new-model`" in diagnostic and "effort`ultra`" in diagnostic
    assert diagnostic.count("警告: ") == 2 and diagnostic.count("次の操作: ") == 1
    assert _saved(tmp_path) == {"chores_model": value}


def test_catalog_failure_is_deferred_and_explicit_id_needs_no_catalog(tmp_path, monkeypatch, capsys):
    assert _main(tmp_path, "config", "set", "execute_model", "codex:sol/max") == 0
    capsys.readouterr()
    assert _main(tmp_path, "config", "get", "execute_model") == 0
    assert capsys.readouterr().out == "codex:sol/max\n"
    with pytest.raises(ValueError):
        config_module.resolve_model_candidates("execute")
    assert _main(tmp_path, "config", "set", "execute_model", "codex:gpt-6-sol/high") == 0

    def fail_catalog():
        raise AssertionError("完全IDはカタログ取得を必要としない")

    monkeypatch.setattr(codex_models, "list_models", fail_catalog)
    assert config_module.resolve_model_candidates("execute") == [("codex", "gpt-6-sol", "high")]


def test_pre_tier_configuration_is_cleaned_without_reintroducing_old_migration(tmp_path):
    _write_config(
        tmp_path,
        {"high_tier_model": "agy:obsolete", "medium_tier_model": "claude:obsolete", "low_tier_model": "codex:obsolete"},
    )
    assert config_module.parse_unresolved_model_candidates("execute")[0] == ("claude", "opus[1m]", "medium")
    assert not _saved(tmp_path)


def test_claude_api_mode_accepts_values_and_env_override(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """show・get・setと環境上書きで接続方針の保存と不正値拒否を観測する。"""
    assert _main(tmp_path, "config", "show") == 0
    assert "claude_orchestrator_api_only true" in capsys.readouterr().out
    for mode in ("api-credits", "pay-as-you-go", "api-only"):
        assert _main(tmp_path, "config", "set", "claude_api_mode", mode) == 0
        capsys.readouterr()
        assert _main(tmp_path, "config", "get", "claude_api_mode") == 0
        assert capsys.readouterr().out.strip() == mode
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_MODE", "api-only")
    assert _main(tmp_path, "config", "get", "claude_api_mode") == 0
    assert capsys.readouterr().out.strip() == "api-only"
    assert _main(tmp_path, "config", "set", "claude_api_mode", "invalid") == 2
    error = capsys.readouterr().err
    assert "pay-as-you-go" in error and "api-credits" in error
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_MODE", "invalid")
    assert _main(tmp_path, "config", "get", "claude_api_mode") == 2


def test_claude_api_env_file_accepts_missing_path(tmp_path: pathlib.Path) -> None:
    """将来作成するファイルのパスを設定CLIから保存できる。"""
    path = str(tmp_path / "future.env")
    assert _main(tmp_path, "config", "set", "claude_api_env_file", path) == 0
    assert config_module.resolve_mutable_setting("claude_api_env_file") == path


@pytest.mark.parametrize("mode,expected", [("pay-as-you-go", ("true", "false")), ("api-credits", ("false", "true"))])
def test_legacy_claude_mode_retains_source_after_get_and_partial_set(tmp_path, capsys, mode, expected):
    """公開getは旧入力の出所を保ち、新設定の片方だけを明示保存できる。"""
    _write_config(tmp_path, {"claude_api_mode": mode, "write_model": "claude:sonnet"})
    keys = ("claude_orchestrator_api_only", "claude_worker_api_first")
    assert _main(tmp_path, "config", "get", *keys) == 0
    assert capsys.readouterr().out.splitlines() == list(expected)
    assert _saved(tmp_path) == {"claude_api_mode": mode, "write_model": "claude:sonnet"}
    assert all(not config_module.claude_role_setting_is_explicit(key) for key in keys)
    assert claude_connection.candidates("orchestrator").require_first().kind == "inherit"
    assert claude_connection.candidates("delegate").require_first().kind == "inherit"
    assert _main(tmp_path, "config", "set", keys[0], "false") == 0
    capsys.readouterr()
    assert config_module.resolve_mutable_setting(keys[1]) == expected[1]
    assert _saved(tmp_path) == {"claude_api_mode": mode, "write_model": "claude:sonnet", keys[0]: "false"}
    assert config_module.claude_role_setting_is_explicit(keys[0])
    assert not config_module.claude_role_setting_is_explicit(keys[1])


def test_legacy_api_only_is_exclusive_until_new_setting_is_written(tmp_path, capsys):
    """新bool更新は指定した役割を変更し、他方の旧API固定を維持する。"""
    assert _main(tmp_path, "config", "set", "claude_api_mode", "api-only") == 0
    capsys.readouterr()
    assert _saved(tmp_path) == {"claude_api_mode": "api-only"}
    assert config_module.claude_legacy_api_only()
    assert _main(tmp_path, "config", "set", "claude_worker_api_first", "false") == 0
    capsys.readouterr()
    assert _saved(tmp_path) == {"claude_api_mode": "api-only", "claude_worker_api_first": "false"}
    assert not config_module.claude_legacy_api_only()
    assert config_module.claude_legacy_api_only("claude_orchestrator_api_only")


def test_new_claude_environment_overrides_legacy_mode(tmp_path, capsys, monkeypatch):
    """新しい環境設定を旧環境入力より優先し、保存設定を変更しない。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_MODE", "api-only")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_WORKER_API_FIRST", "false")
    assert _main(tmp_path, "config", "get", "claude_worker_api_first", "claude_orchestrator_api_only") == 0
    assert capsys.readouterr().out == "false\ntrue\n"
    assert not config_module.claude_legacy_api_only()
    assert config_module.claude_legacy_api_only("claude_orchestrator_api_only")
    assert not _config_file(tmp_path).exists()


@pytest.mark.parametrize("source", ["saved", "environment"])
@pytest.mark.parametrize("role", ["orchestrator", "delegate"])
@pytest.mark.parametrize("value", ["true", "false"])
@pytest.mark.parametrize("api_file", ["unset", "invalid", "valid"])
def test_explicit_claude_role_policy_with_optional_api_file(tmp_path, capsys, monkeypatch, source, role, value, api_file):
    """公開設定入力から候補と子環境まで、明示した役割の固定・順序と認証隔離を確認する。"""
    key = "claude_orchestrator_api_only" if role == "orchestrator" else "claude_worker_api_first"
    if source == "saved":
        assert _main(tmp_path, "config", "set", key, value) == 0
        capsys.readouterr()
    else:
        monkeypatch.setenv(f"AGENT_TOOLKIT_CONFIG_{key.upper()}", value)
    if api_file != "unset":
        path = tmp_path / "api.env"
        path.write_text("ANTHROPIC_API_KEY=selected-api\n" if api_file == "valid" else "invalid", encoding="utf-8")
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", str(path))
    parent = {"ANTHROPIC_API_KEY": "parent-api", "CLAUDE_CODE_OAUTH_TOKEN": "parent-oauth"}
    choices = claude_connection.candidates(role, env=parent)
    if role == "orchestrator" and value == "true" and api_file != "valid":
        assert not choices.connections
        with pytest.raises(ValueError, match="atk config set claude_api_env_file"):
            choices.require_first()
        return
    expected = ["subscription"]
    if api_file == "valid":
        expected = (
            (["api"] if value == "true" else ["subscription"])
            if role == "orchestrator"
            else (["api", "subscription"] if value == "true" else ["subscription", "api"])
        )
    assert [item.kind for item in choices.connections] == expected
    for selected in choices.connections:
        child = claude_connection.connection_environment(parent, selected)
        if selected.kind == "subscription":
            assert "ANTHROPIC_API_KEY" not in child
            assert child["CLAUDE_CODE_OAUTH_TOKEN"] == "parent-oauth"
        else:
            assert child["ANTHROPIC_API_KEY"] == "selected-api"
            assert "CLAUDE_CODE_OAUTH_TOKEN" not in child


@pytest.mark.parametrize("mode", [None, "pay-as-you-go", "api-credits"])
def test_unconfigured_claude_roles_preserve_inherit_after_public_get(tmp_path, capsys, mode):
    """未指定と旧2モードは、公開get後もAPI環境未設定の認証継承を保持する。"""
    if mode is not None:
        _write_config(tmp_path, {"claude_api_mode": mode})
    assert _main(tmp_path, "config", "get", "claude_orchestrator_api_only", "claude_worker_api_first") == 0
    capsys.readouterr()
    parent = {"ANTHROPIC_API_KEY": "parent-api", "CLAUDE_CODE_OAUTH_TOKEN": "parent-oauth"}
    for role in ("orchestrator", "delegate"):
        selected = claude_connection.candidates(role, env=parent).require_first()
        assert selected.kind == "inherit"
        assert claude_connection.connection_environment(parent, selected) == parent
    if mode is not None:
        assert _saved(tmp_path) == {"claude_api_mode": mode}
    else:
        assert not _config_file(tmp_path).exists()


@pytest.mark.parametrize("new_role", ["orchestrator", "delegate"])
@pytest.mark.parametrize("source", ["saved", "environment"])
def test_partial_new_role_keeps_other_legacy_api_only(tmp_path, capsys, monkeypatch, new_role, source):
    """片役割の明示変更後も、他役割の保存会話は旧API固定で再開する。"""
    assert _main(tmp_path, "config", "set", "claude_api_mode", "api-only") == 0
    capsys.readouterr()
    key = "claude_orchestrator_api_only" if new_role == "orchestrator" else "claude_worker_api_first"
    if source == "saved":
        assert _main(tmp_path, "config", "set", key, "false") == 0
        capsys.readouterr()
    else:
        monkeypatch.setenv(f"AGENT_TOOLKIT_CONFIG_{key.upper()}", "false")
    path = tmp_path / "api.env"
    path.write_text("ANTHROPIC_API_KEY=selected-api\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", str(path))
    other_role = "delegate" if new_role == "orchestrator" else "orchestrator"
    assert not claude_connection.api_only(new_role)
    assert claude_connection.api_only(other_role)
    selected = claude_connection.with_current_isolation(
        claude_connection.Connection("subscription"), role=other_role, env={"CLAUDE_CODE_OAUTH_TOKEN": "parent-oauth"}
    )
    assert selected.kind == "api"
    assert selected.api_env_file == str(path)


def test_legacy_environment_preserves_inherit_over_saved_new_role(tmp_path, capsys, monkeypatch):
    """保存済み新キーより優先する旧環境入力を、ユーザーの新キー指定として扱わない。"""
    assert _main(tmp_path, "config", "set", "claude_orchestrator_api_only", "true") == 0
    capsys.readouterr()
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_MODE", "pay-as-you-go")
    assert claude_connection.candidates("orchestrator").require_first().kind == "inherit"
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_API_ONLY", "false")
    selected = claude_connection.candidates("orchestrator", env={"CLAUDE_CODE_OAUTH_TOKEN": "oauth"}).require_first()
    assert selected.kind == "subscription"
    assert _saved(tmp_path) == {"claude_orchestrator_api_only": "true"}


def test_explicit_subscription_requires_oauth_without_api_file(tmp_path, capsys, monkeypatch):
    """親にAPI認証だけがある場合、公開保存したサブスク固定はログインの復旧操作を返す。"""
    assert _main(tmp_path, "config", "set", "claude_orchestrator_api_only", "false") == 0
    capsys.readouterr()
    available = claude_connection.subscription_available
    # 空の資格情報ディレクトリを使うファイル認証の分岐で、OSのKeychainと実CLIを呼ばずに確認する。
    monkeypatch.setattr(
        claude_connection,
        "subscription_available",
        lambda env, **kwargs: available(env, platform="linux", **kwargs),
    )
    with pytest.raises(ValueError, match="claude auth login"):
        claude_connection.candidates(
            "orchestrator", env={"ANTHROPIC_API_KEY": "parent-api", "CLAUDE_CONFIG_DIR": str(tmp_path / "empty-auth")}
        ).require_first()


@pytest.fixture(autouse=True)
def _isolate_platformdirs(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`platformdirs`の解決先を実環境から隔離する。"""
    monkeypatch.setattr(config_module.platformdirs, "user_config_dir", lambda _name, **_kwargs: str(tmp_path / "config"))
    monkeypatch.setattr(config_module.platformdirs, "user_state_dir", lambda _name, **_kwargs: str(tmp_path / "state"))
    monkeypatch.setattr(config_module.platformdirs, "user_data_dir", lambda _name, **_kwargs: str(tmp_path / "data"))
    for key in (*config_module._MUTABLE_KEY_DEFAULTS, "claude_api_mode", *_LEGACY_KEYS):  # pylint: disable=protected-access  # noqa: SLF001
        monkeypatch.delenv(f"AGENT_TOOLKIT_CONFIG_{key.upper()}", raising=False)
    monkeypatch.setattr(
        codex_models,
        "list_models",
        lambda: [
            {"model": "gpt-6-sol", "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]},
            {
                "model": "gpt-6-luna",
                "supportedReasoningEfforts": [{"reasoningEffort": effort} for effort in ("medium", "xhigh")],
            },
            {"model": "gpt-5.6-terra", "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]},
        ],
    )


def _main(tmp_path: pathlib.Path, *arguments: str) -> int:
    """`atk`を実行し、終了コードを返す。"""
    with pytest.raises(SystemExit) as exc_info:
        atk.main(list(arguments), home=tmp_path)
    return cast(int, exc_info.value.code)


def _config_file(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "config" / "config.json"


def _write_config(tmp_path: pathlib.Path, values: dict[str, str]) -> pathlib.Path:
    """隔離した設定保存先へ保存値を書き、そのパスを返す。"""
    path = _config_file(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values) + "\n", encoding="utf-8")
    return path


def _saved(tmp_path: pathlib.Path) -> dict[str, str]:
    return json.loads(_config_file(tmp_path).read_text(encoding="utf-8"))


def test_show_line_can_be_copied_to_set(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """showのキーを加工せずsetへ渡し、コロンを含む値をgetできる。"""
    value = "claude:opus/high,codex:sol/medium"
    assert _main(tmp_path, "config", "set", "write_model", value) == 0
    capsys.readouterr()
    for argv in (("config", "show"), ("config",)):
        assert _main(tmp_path, *argv) == 0
        lines = capsys.readouterr().out.splitlines()
        assert all(" " in line and not line.partition(" ")[0].endswith(":") for line in lines)
        key, copied = next(line for line in lines if line.startswith("write_model ")).split(" ", 1)
        assert _main(tmp_path, "config", "set", key, copied) == 0
        capsys.readouterr()
        assert _main(tmp_path, "config", "get", key) == 0
        assert capsys.readouterr().out.rstrip("\n") == value


@pytest.mark.parametrize(("value", "expected"), [("", ""), ("  ", ""), (" a, b,a ", "a,b")])
def test_codex_provider_config_cli(tmp_path, capsys, monkeypatch, value, expected):
    """provider列を公開CLIで正規化・保存・取得・解除し、モデルの警告を適用しない。"""
    key = "codex_model_providers"
    with pytest.raises(SystemExit, match="0"):
        atk.main(["config", "set", key, value], home=tmp_path)
    assert not capsys.readouterr().err
    assert json.loads((tmp_path / "config" / "config.json").read_text(encoding="utf-8"))[key] == expected
    with pytest.raises(SystemExit, match="0"):
        atk.main(["config", "get", key], home=tmp_path)
    assert capsys.readouterr().out == expected + "\n"
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_MODEL_PROVIDERS", " b, c,b ")
    with pytest.raises(SystemExit, match="0"):
        atk.main(["config", "get", key], home=tmp_path)
    assert capsys.readouterr().out == "b,c\n"
    monkeypatch.delenv("AGENT_TOOLKIT_CONFIG_CODEX_MODEL_PROVIDERS")
    with pytest.raises(SystemExit, match="0"):
        atk.main(["config", "set", key, ""], home=tmp_path)
    capsys.readouterr()
    with pytest.raises(SystemExit, match="0"):
        atk.main(["config", "get", key], home=tmp_path)
    assert capsys.readouterr().out == "\n"


@pytest.mark.parametrize("value", [",a", "a,", "a,,b", "a, ,b"])
def test_codex_provider_empty_element_rejected(tmp_path, capsys, monkeypatch, value):
    """保存値と環境変数の空候補を拒否し、設定ファイルの不在を保つ。"""
    key = "codex_model_providers"
    with pytest.raises(SystemExit, match="2"):
        atk.main(["config", "set", key, value], home=tmp_path)
    assert not (tmp_path / "config" / "config.json").exists()
    capsys.readouterr()
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_MODEL_PROVIDERS", value)
    with pytest.raises(SystemExit, match="2"):
        atk.main(["config", "get", key], home=tmp_path)
    assert not capsys.readouterr().out


def test_codex_provider_setting_clear_preserves_other_settings(tmp_path, capsys):
    """公開操作で接続先列を解除し、独立した速度設定を保持する。"""
    path = tmp_path / "config" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"codex_model_providers": "paid-a", "codex_fast_mode": "true"}), encoding="utf-8")
    with pytest.raises(SystemExit, match="0"):
        atk.main(["config", "get", "codex_model_providers"], home=tmp_path)
    initial = capsys.readouterr()
    assert initial.out == "paid-a\n"
    assert not initial.err
    with pytest.raises(SystemExit, match="0"):
        atk.main(["config", "set", "codex_model_providers", ""], home=tmp_path)
    assert not capsys.readouterr().err
    assert json.loads(path.read_text(encoding="utf-8")) == {"codex_model_providers": "", "codex_fast_mode": "true"}
    with pytest.raises(SystemExit, match="0"):
        atk.main(["config", "show"], home=tmp_path)
    assert not capsys.readouterr().err


def test_claude_api_env_config_cli(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """存在しないパスを保存・表示でき、環境上書きと解除を解釈する。"""
    for arguments, expected in [
        (["set", "claude_api_env_file", "~/missing.env"], ""),
        (["get", "claude_api_env_file"], str(pathlib.Path.home() / "missing.env")),
        (["show"], "claude_api_env_file"),
        (["set", "claude_api_env_file", ""], ""),
        (["get", "claude_api_env_file"], ""),
    ]:
        assert _main(tmp_path, "config", *arguments) == 0
        output = capsys.readouterr()
        if expected:
            assert expected in output.out
        elif arguments[0] == "get":
            assert output.out == "\n"
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", "~/override.env")
    assert config_module.resolve_mutable_setting("claude_api_env_file") == str(pathlib.Path.home() / "override.env")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", "")
    assert config_module.resolve_mutable_setting("claude_api_env_file") == ""


class TestCodexFastMode:
    """公開config操作で速度の実効値・保存値・環境変数と既存プリセットの独立性を確かめる。"""

    @pytest.mark.parametrize("value", ["true", "false"])
    def test_public_round_trip_and_preset_preservation(
        self, value: str, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "get", "codex_fast_mode"], home=tmp_path)
        assert capsys.readouterr().out == "false\n"
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "set", "codex_fast_mode", value], home=tmp_path)
        assert not capsys.readouterr().err
        path = tmp_path / "config" / "config.json"
        assert json.loads(path.read_text(encoding="utf-8"))["codex_fast_mode"] == value
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "apply-preset", "codex-primary"], home=tmp_path)
        capsys.readouterr()
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "get", "codex_fast_mode"], home=tmp_path)
        assert capsys.readouterr().out == f"{value}\n"
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "show"], home=tmp_path)
        shown = capsys.readouterr()
        assert f"codex_fast_mode {value}\n" in shown.out
        assert not shown.err

    @pytest.mark.parametrize("value", ["TRUE", "False", "1", "0", "yes", "", "codex:gpt-6-sol/medium"])
    def test_rejects_other_values_without_writing(
        self, value: str, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "config" / "config.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"codex_fast_mode": "true"}\n', encoding="utf-8")
        original = path.read_bytes()
        with pytest.raises(SystemExit, match="2"):
            atk.main(["config", "set", "codex_fast_mode", value], home=tmp_path)
        assert "true, false" in capsys.readouterr().err
        assert path.read_bytes() == original

    def test_environment_overrides_saved_value_and_warns_on_set(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_FAST_MODE", "true")
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "set", "codex_fast_mode", "false"], home=tmp_path)
        assert "AGENT_TOOLKIT_CONFIG_CODEX_FAST_MODEが優先" in capsys.readouterr().err
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "get", "codex_fast_mode"], home=tmp_path)
        assert capsys.readouterr().out == "true\n"
        monkeypatch.delenv("AGENT_TOOLKIT_CONFIG_CODEX_FAST_MODE")
        with pytest.raises(SystemExit, match="0"):
            atk.main(["config", "get", "codex_fast_mode"], home=tmp_path)
        assert capsys.readouterr().out == "false\n"


class TestConfigGet:
    def test_get_known_key(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """既知キーの値のみを1行で出力する。"""
        assert _main(tmp_path, "config", "get", "config_dir") == 0
        assert capsys.readouterr().out == f"{tmp_path / 'config'}\n"

    def test_get_state_dir_matches_public_helper(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """`state_dir()`の戻り値と`atk config get state_dir`の出力が一致する。

        フックは`state_dir()`経由で状態ファイルを配置する。
        両者が一致しない場合、ユーザーが`atk config get`で確認した位置と実際の配置先が異なる。
        """
        assert _main(tmp_path, "config", "get", "state_dir") == 0
        assert capsys.readouterr().out == f"{state_paths.state_dir()}\n"

    def test_get_state_dir_rejects_relative_xdg_state_home(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """相対XDG_STATE_HOMEはHOME配下へ退避し、状態読取側と同じ絶対パスを返す。"""
        monkeypatch.setenv("XDG_STATE_HOME", "relative-state")
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setattr(
            config_module.platformdirs,
            "user_state_dir",
            lambda _name, **_kwargs: "relative-state/agent-toolkit",
        )

        assert _main(tmp_path, "config", "get", "state_dir") == 0
        expected = tmp_path / "home" / ".local" / "state" / "agent-toolkit"
        assert capsys.readouterr().out == f"{expected}\n"

    def test_get_unknown_key_exits_2(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """未知キーはexit 2でエラー案内を出力する。"""
        assert _main(tmp_path, "config", "get", "no-such-key") == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert "execute_model" in captured.err
        assert "execute_fix_model" not in captured.err

    def test_get_known_and_unknown_keys_is_atomic(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """既知キーの間に未知キーがある場合は値を出力せずexit 2とする。"""
        assert _main(tmp_path, "config", "get", "config_dir", "no-such-key", "data_dir") == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert "review_model" in captured.err

    def test_immutable_environment_name_does_not_override_private_notes(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """変更可能キー以外を模した環境変数は既存の設定値の解決に影響しない。"""
        assert _main(tmp_path, "config", "get", "private_notes") == 0
        original = capsys.readouterr().out

        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PRIVATE_NOTES", "/unexpected")

        assert _main(tmp_path, "config", "get", "private_notes") == 0
        assert capsys.readouterr().out == original


class TestConfigSet:
    def test_legacy_execute_fix_model_get_is_rejected(self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
        """旧`execute_fix_model`は`get`でも未知キーとして拒否する。"""
        assert _main(tmp_path, "config", "get", "execute_fix_model") == 2
        captured = capsys.readouterr()
        assert not captured.out
        assert "失敗: 未知の設定キーを指定した: execute_fix_model" in captured.err

    def test_write_model_candidate_warnings_identify_only_unknown_candidate(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """write_modelは候補ごとに既知の一覧との一致を確かめ、一覧外の候補だけを警告する。"""
        known = "codex:gpt-6-astra/low"
        unknown = "claude:unknown-model/medium"

        assert _main(tmp_path, "config", "set", "write_model", f"{known},{unknown}") == 0
        captured = capsys.readouterr()
        assert f"候補`{unknown}`" in captured.err
        assert f"候補`{known}`" not in captured.err

    @pytest.mark.parametrize("value", ["gpt-5.6-sol", "other:model", "codex:", "claude:model/", "codex:a/b, claude:b"])
    def test_set_invalid_write_model_exits_2(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], value: str
    ) -> None:
        """write_modelの不正な候補列はexit 2で受理可能書式を案内する。"""
        assert _main(tmp_path, "config", "set", "write_model", value) == 2
        assert "<claude|codex|agy>:<model>[/<effort>]" in capsys.readouterr().err

    @pytest.mark.parametrize("key", ["merge_model", "codex_model", "config_dir"])
    def test_set_unknown_or_immutable_key_exits_2(
        self, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], key: str
    ) -> None:
        """廃止したキーと導出値キーは変更できずexit 2でエラー案内を出力する。"""
        assert _main(tmp_path, "config", "set", key, "codex:gpt-5.6-sol/medium") == 2
        assert f"失敗: 変更できない設定キーを指定した: {key}" in capsys.readouterr().err


@pytest.mark.parametrize("stage", ["execute", "review", "review_fast", "chores", "chores_fast", "orchestrate", "write"])
def test_show_keeps_unknown_values_without_repeating_set_warnings(tmp_path, monkeypatch, capsys, stage):
    """設定時の警告と、show/getの無警告の値表示を7工程で保持する。"""
    key = f"{stage}_model"
    unknown = "codex:unknown-model/ultra"
    assert _main(tmp_path, "config", "set", key, unknown) == 0
    diagnostic = capsys.readouterr().err
    assert "モデル名`unknown-model`" in diagnostic and "effort`ultra`" in diagnostic
    saved = _config_file(tmp_path).read_bytes()
    for arguments in [("show",), (), ("get", key)]:
        assert _main(tmp_path, "config", *arguments) == 0
        shown = capsys.readouterr()
        assert unknown in shown.out
        assert not shown.err
        assert _config_file(tmp_path).read_bytes() == saved
    known = "agy:gemini-4-argon/medium"
    assert _main(tmp_path, "config", "set", key, known) == 0
    assert not capsys.readouterr().err
    assert _main(tmp_path, "config", "show") == 0
    shown = capsys.readouterr()
    assert f"{key} {known}\n" in shown.out
    assert not shown.err
    monkeypatch.setenv(f"AGENT_TOOLKIT_CONFIG_{key.upper()}", "claude:unknown-model/low")
    assert _main(tmp_path, "config", "show") == 0
    shown = capsys.readouterr()
    assert f"{key} claude:unknown-model/low\n" in shown.out
    assert not shown.err
    assert _saved(tmp_path)[key] == known

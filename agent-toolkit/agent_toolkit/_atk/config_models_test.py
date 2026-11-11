"""config modelsの契約を検証する。"""

from __future__ import annotations

import pytest

from agent_toolkit._atk import config as config_module
from agent_toolkit._common import codex_models
from agent_toolkit._testing.config_support import (
    _DEFAULTS,
    _config_file,
    _isolate_platformdirs,
    _isolate_serve_environment_fixture,
    _main,
    _saved,
    _write_config,
)

__all__ = ["_isolate_platformdirs", "_isolate_serve_environment_fixture"]


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
        expected["chores_model"] = "claude:sonnet[1m]/medium,codex:sol/medium"
        expected["chores_fast_model"] = "claude:haiku/medium,codex:luna/medium"
    if preset == "claude-primary":
        expected["review_model"] = "claude:sonnet[1m]/medium,codex:sol/medium"
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

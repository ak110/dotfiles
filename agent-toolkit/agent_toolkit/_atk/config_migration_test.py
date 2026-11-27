"""config migrationの契約を検証する。"""

from __future__ import annotations

import json

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config as config_module
from agent_toolkit._atk import config_migration, research_subscriptions
from agent_toolkit._atk import config_settings as _config_settings
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._testing import git_repository
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


_PROJECTION_CASES = {
    "connection": {"claude_api_mode": "pay-as-you-go", "codex_model_providers": "openai,custom"},
    "process-loop-repositories": {},
    "research-repositories": {
        "weekly_research_repos": '["github.com/example/known","github.com/example/other"]',
        "repositories": '{"github.com/example/known":{"edit":"allow"}}',
        "weekly_research_x_accounts": '["Alice"]',
    },
    "model": {"base_engine": "claude", "execute_model": "invalid"},
    "remote-hosts": {"serve_plans_remote_hosts": '["one"]', "serve_sessions_remote_hosts": '["two"]'},
    "retired": {"codex_auto_reset": "true", "usage_auto_allocation": "true", "serve_sessions_codex_home": "/obsolete"},
    "fixed-state": {"weekly_research_x_max_posts": "3"},
    "research-subscriptions": {"weekly_research_x_accounts": '["Alice"]', "weekly_research_x_queries": '["example"]'},
    "serve-toml": {"serve_host": "saved-host"},
}


def test_projection_cases_cover_migration_groups():
    assert set(_PROJECTION_CASES) == {*config_migration.LEGACY_SETTING_GROUPS, "serve-toml"}


@pytest.mark.parametrize("group", _PROJECTION_CASES)
def test_listing_matches_persistent_migration_for_each_group(tmp_path, monkeypatch, capsys, group):
    config = dict(_PROJECTION_CASES[group])
    if group == "process-loop-repositories":
        repository = git_repository.init_repository(tmp_path / "repo", origin="https://example.test/team/repo.git")
        config["process_loop_repositories"] = json.dumps([str(repository)])
    source = tmp_path / "legacy-credits.env"
    if group == "fixed-state":
        source.write_text("ANTHROPIC_API_KEY=fixture\n", encoding="utf-8")
        config["claude_credits_env_file"] = str(source)
    path = _write_config(tmp_path, config)
    if group == "serve-toml":
        path.with_name("serve.toml").write_text('host = "old-host"\nport = 3412\n', encoding="utf-8")
    effects: list[research_subscriptions.Subscriptions] = []
    monkeypatch.setattr(research_subscriptions, "migrate", effects.append)
    original = {file.name: file.read_bytes() for file in path.parent.iterdir()}
    assert _main(tmp_path, "config", "show") == 0
    before = capsys.readouterr().out
    assert not effects
    if group == "fixed-state":
        assert source.is_file()
    assert {file.name: file.read_bytes() for file in path.parent.iterdir()} == original
    assert _main(tmp_path, "config", "get", "codex_fast_mode") == 0
    if "weekly_research_x_accounts" in config:
        assert effects and effects[0]["accounts"] == ("Alice",)
    if group == "fixed-state":
        assert not source.exists() and path.with_name("claude-credits.env.imported").is_file()
    capsys.readouterr()
    assert _main(tmp_path, "config", "show") == 0
    assert capsys.readouterr().out == before


@pytest.mark.parametrize("arguments", [(), ("show",), ("list",)])
@pytest.mark.parametrize("legacy", ['["github.com/example/other"]', "invalid"])
def test_research_repository_projection_without_subscription_effects(tmp_path, monkeypatch, capsys, arguments, legacy):
    path = _write_config(tmp_path, {"weekly_research_repos": legacy, "weekly_research_x_accounts": '["Alice"]'})
    before = path.read_bytes()

    def unexpected(_subscriptions):
        pytest.fail("設定一覧で旧購読を移行した")

    monkeypatch.setattr(research_subscriptions, "migrate", unexpected)
    assert _main(tmp_path, "config", *arguments) == (2 if legacy == "invalid" else 0)
    shown = capsys.readouterr()
    assert "codex_fast_mode auto\n" in shown.out
    if legacy == "invalid":
        assert "repositories " not in shown.out and "旧調査リポジトリを移行できない" in shown.err
    else:
        assert '"github.com/example/other"' in shown.out and '"edit": "awi"' in shown.out
    assert path.read_bytes() == before


def test_missing_legacy_x_file_does_not_block_config_use(tmp_path, capsys):
    key = "weekly_research_x_env_file"
    missing = str(tmp_path / "missing.env")
    _write_config(tmp_path, {key: missing, "weekly_research_x_api": "false"})

    assert _main(tmp_path, "config", "get", "codex_fast_mode") == 0
    assert _main(tmp_path, "config", "set", "codex_fast_mode", "true") == 0
    assert _saved(tmp_path)[key] == missing
    assert _saved(tmp_path)["codex_fast_mode"] == "true"
    assert "移行が未完了" in capsys.readouterr().err


@pytest.mark.parametrize("kind", ["credits", "payg"])
def test_missing_claude_file_already_at_fixed_path_preserves_repair_guidance(tmp_path, capsys, kind):
    key = f"claude_{kind}_env_file"
    path = _config_file(tmp_path).parent / f"claude-{kind}.env"
    _write_config(tmp_path, {key: str(path)})

    assert _main(tmp_path, "config", "get", "codex_fast_mode") == 2
    assert "旧ファイル" in capsys.readouterr().err
    assert key in _saved(tmp_path)


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


@pytest.mark.parametrize("entry", ["get", "resolve"])
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
    expected = "claude:sonnet" if operation == "set" else "claude:opus[1m]/medium,codex:sol/medium"
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
    assert config_module.parse_unresolved_model_candidates("execute")[0] == ("codex", "sol", "medium")
    assert not _saved(tmp_path)


@pytest.mark.parametrize(
    ("key", "destination"),
    [
        ("weekly_research_repos", "atk config repos"),
        ("weekly_research_x_accounts", "--add-account"),
        ("weekly_research_x_queries", "--add-query"),
    ],
)
def test_retired_research_env_guides_migration_without_saving(tmp_path, capsys, monkeypatch, key, destination):
    assert _main(tmp_path, "config", "get", key) == 2
    assert destination in capsys.readouterr().err

    variable = f"AGENT_TOOLKIT_CONFIG_{key.upper()}"
    assert variable in config_module.config_environment_variable_names()
    monkeypatch.setenv(variable, '["marker-not-to-be-saved"]')
    assert _main(tmp_path, "config", "get", "codex_fast_mode") == 2
    diagnostic = capsys.readouterr().err
    assert variable in diagnostic and destination in diagnostic
    assert "marker-not-to-be-saved" not in diagnostic
    assert not _config_file(tmp_path).exists()

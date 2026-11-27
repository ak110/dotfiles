import json
import os
import subprocess
import sys

import pytest

from agent_toolkit._atk import claude_connection, config, config_env, config_migration, config_settings, research_sources
from agent_toolkit._atk.config_sharing_test import _Hosts
from agent_toolkit._atk.serve import config as serve_config
from agent_toolkit._common import env_values, runtime_environment
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._testing.config_support import _main


@pytest.mark.windows
@pytest.mark.parametrize("key", ["claude_credits_env_file", "claude_payg_env_file", "weekly_research_x_env_file"])
@pytest.mark.parametrize("source_kind", ["saved", "environment"])
@pytest.mark.parametrize("target_kind", ["absent", "same", "different", "source-missing", "source-missing-target"])
def test_legacy_paths_import_or_diagnose_without_losing_auth(tmp_path, monkeypatch, key, source_kind, target_kind):
    root = config_settings.config_dir()
    root.mkdir(parents=True, exist_ok=True)
    filename = config_settings.RETIRED_FIXED_KEYS[key]
    target = root / filename
    source = tmp_path / "認証 with spaces.env"
    text = "ANTHROPIC_API_KEY=fixture-api\nANTHROPIC_BASE_URL=https://example.invalid\nX_BEARER_TOKEN=fixture-x"
    if not target_kind.startswith("source-missing"):
        source.write_text(text, encoding="utf-8")
    if target_kind in {"same", "different", "source-missing-target"}:
        target.write_text("ANTHROPIC_API_KEY=other" if target_kind == "different" else text, encoding="utf-8")
    if source_kind == "saved":
        config_settings.save_config({key: str(source)})
    else:
        monkeypatch.setenv(config_settings.config_env_name(key), str(source))
    if target_kind == "different":
        with pytest.raises(ActionableError, match="移行できない"):
            config_migration.migrate_legacy_settings()
        assert source.read_text(encoding="utf-8") == text
        assert target.read_text(encoding="utf-8") == "ANTHROPIC_API_KEY=other"
    elif target_kind == "source-missing":
        assert _main(tmp_path, "config", "show") == 2
        snapshot = config.settings_snapshot(tmp_path, read_only=True)
        item = snapshot["settings"][key.removesuffix("_file")]
        assert "次の操作" in item["error"] and str(source) in item["error"]
        assert serve_config.resolve_config().host == "127.0.0.1"
    else:
        config_migration.migrate_legacy_settings()
        env_key = key.removesuffix("_file")
        assert env_values.parse(config_settings.load_shared_settings(config_env.path())[env_key]) == env_values.parse(text)
        assert not source.exists() and not target.exists()
        assert target.with_name(filename + ".imported").read_text(encoding="utf-8") == text
        assert key not in config_settings.load_config()
        config_migration.migrate_legacy_settings()


@pytest.mark.windows
def test_fixed_files_are_imported_once_and_deletion_does_not_reimport():
    root = config_settings.config_dir()
    root.mkdir(parents=True, exist_ok=True)
    source = root / "x-api.env"
    source.write_text("X_BEARER_TOKEN=fixture-x", encoding="utf-8")
    assert research_sources.x_token() == "fixture-x"
    config.update_settings({"action": "set", "key": "weekly_research_x_env", "value": ""})
    assert research_sources.x_token() is None
    assert source.with_name("x-api.env.imported").exists()


def test_empty_import_keeps_parent_api_authentication_isolated(monkeypatch):
    root = config_settings.config_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "claude-credits.env").write_text("# not configured\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "fixture-oauth")
    parent = {"ANTHROPIC_API_KEY": "parent-api", "CLAUDE_CODE_OAUTH_TOKEN": "fixture-oauth"}
    selected = claude_connection.candidates("delegate", env=parent).require_first()
    assert selected.kind == "subscription"
    assert "ANTHROPIC_API_KEY" not in claude_connection.connection_environment(parent, selected)


@pytest.mark.cpu_limit(1.5, reason="2 cloneと実Git同期で接続値の共有・伏せ字・起動時の適用を検証する。")
def test_shared_environment_cli_masking_and_launch_isolation(tmp_path, monkeypatch, capsys):
    hosts = _Hosts(tmp_path, monkeypatch)
    hosts.use("a")
    text = (
        "ANTHROPIC_API_KEY=fixture-api\nANTHROPIC_BASE_URL=https://example.invalid\n"
        "AWS_REGION=us-east-1\nCUSTOM_CONNECTION=payg"
    )
    assert _main(tmp_path, "config", "set", "claude_payg_env", text) == 0
    assert "fixture-api" not in capsys.readouterr().out
    assert "claude_payg_env" not in hosts.local("a")
    config.save_mutable_settings({"claude_worker_connections": "payg", "claude_orchestrator_connections": "payg"})
    hosts.use("b")
    assert _main(tmp_path, "config", "get", "claude_payg_env") == 0
    public = capsys.readouterr().out
    assert "[REDACTED]" in public and "fixture-api" not in public
    assert "https://example.invalid" in public
    snapshot = config.settings_snapshot(tmp_path, read_only=True)
    assert "fixture-api" not in json.dumps(snapshot)
    for role in ("delegate", "orchestrator"):
        selected = claude_connection.candidates(role).require_first()
        assert selected.kind == "payg"
        child = claude_connection.connection_environment(
            {"CUSTOM_CONNECTION": "parent", "CLAUDE_CODE_OAUTH_TOKEN": "oauth"}, selected
        )
        assert child["ANTHROPIC_API_KEY"] == "fixture-api" and child["CUSTOM_CONNECTION"] == "payg"
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in child
    config.update_settings({"action": "set", "key": "claude_payg_env", "value": public.replace("us-east-1", "eu-west-1")})
    hosts.use("a")
    assert env_values.parse(config.resolve_mutable_setting("claude_payg_env"))["AWS_REGION"] == "eu-west-1"
    assert env_values.parse(config.resolve_mutable_setting("claude_payg_env"))["ANTHROPIC_API_KEY"] == "fixture-api"


@pytest.mark.windows
def test_caller_configuration_directory_uses_localappdata_or_xdg(tmp_path):
    home = tmp_path / "home"
    env = {
        "HOME": str(home),
        "USERPROFILE": str(home),
        "LOCALAPPDATA": str(tmp_path / "local"),
        "APPDATA": str(tmp_path / "roaming"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
    }
    with runtime_environment.bind(runtime_environment.CallerContext(env, tmp_path)):
        assert config_settings.config_dir() == tmp_path / ("local" if os.name == "nt" else "xdg") / "agent-toolkit"
    env = {"HOME": str(home), "USERPROFILE": str(home)}
    with runtime_environment.bind(runtime_environment.CallerContext(env, tmp_path)):
        expected = home / "AppData" / "Local" if os.name == "nt" else home / ".config"
        assert config_settings.config_dir() == expected / "agent-toolkit"


@pytest.mark.windows
@pytest.mark.parametrize("source", ["saved", "environment"])
def test_oldest_claude_path_also_reaches_shared_environment(tmp_path, monkeypatch, source):
    original = tmp_path / "old-api.env"
    original.write_text("ANTHROPIC_API_KEY=fixture", encoding="utf-8")
    if source == "saved":
        config_settings.save_config({"claude_api_env_file": str(original)})
    else:
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", str(original))
    config_migration.check_legacy_environment()
    config_migration.migrate_legacy_settings()
    assert env_values.parse(config.resolve_mutable_setting("claude_payg_env")) == {"ANTHROPIC_API_KEY": "fixture"}
    assert not original.exists()


def test_public_cli_process_saves_and_masks_shared_environment():
    value = "ANTHROPIC_API_KEY=fixture-cli\nANTHROPIC_BASE_URL=https://example.invalid"
    set_result = subprocess.run(
        [sys.executable, "-m", "agent_toolkit.atk", "config", "set", "claude_payg_env", value],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert set_result.returncode == 0, set_result.stderr
    get_result = subprocess.run(
        [sys.executable, "-m", "agent_toolkit.atk", "config", "get", "claude_payg_env"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert get_result.returncode == 0, get_result.stderr
    assert "fixture-cli" not in set_result.stdout + set_result.stderr + get_result.stdout + get_result.stderr
    assert "ANTHROPIC_API_KEY=[REDACTED]" in get_result.stdout
    assert "ANTHROPIC_BASE_URL=https://example.invalid" in get_result.stdout
    assert (
        env_values.parse(config_settings.load_shared_settings(config_env.path())["claude_payg_env"])["ANTHROPIC_API_KEY"]
        == "fixture-cli"
    )


@pytest.mark.parametrize("value", ["ANTHROPIC_API_KEY=fixture-saved", "ANTHROPIC_API_KEY=[REDACTED]"])
def test_masking_does_not_hide_environment_override_on_save(tmp_path, monkeypatch, capsys, value):
    config.save_mutable_settings({"claude_payg_env": "ANTHROPIC_API_KEY=fixture-saved"})
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_PAYG_ENV", "ANTHROPIC_API_KEY=fixture-override")
    assert _main(tmp_path, "config", "set", "claude_payg_env", value) == 2
    result = capsys.readouterr()
    assert "AGENT_TOOLKIT_CONFIG_CLAUDE_PAYG_ENV" in result.err
    assert "fixture-saved" not in result.out + result.err and "fixture-override" not in result.out + result.err

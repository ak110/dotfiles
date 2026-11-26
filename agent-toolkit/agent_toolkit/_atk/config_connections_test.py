"""config connectionsの契約を検証する。"""

from __future__ import annotations

import pytest

from agent_toolkit._atk import claude_connection
from agent_toolkit._atk import config as config_module
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._testing.config_support import (
    _CONNECTION_KEYS,
    _config_file,
    _isolate_platformdirs,
    _isolate_serve_environment_fixture,
    _main,
    _saved,
    _write_config,
)

__all__ = ["_isolate_platformdirs", "_isolate_serve_environment_fixture"]


def test_connection_keys_show_get_set_and_validate(tmp_path, capsys):
    assert _main(tmp_path, "config", "show") == 0
    shown = dict(line.split(" ", 1) for line in capsys.readouterr().out.splitlines())
    assert {key: shown[key] for key in _CONNECTION_KEYS} == {
        "claude_orchestrator_connections": "subscription,credits",
        "claude_worker_connections": "credits,subscription",
        "codex_credits_provider": "",
        "codex_payg_provider": "",
        "codex_orchestrator_connections": "subscription,credits",
        "codex_worker_connections": "credits,subscription",
    }
    assert not config_module.connections_configured("claude")
    assert not config_module.connections_configured("codex")
    for key, value, expected in (
        ("claude_orchestrator_connections", " subscription , payg ", "subscription,payg"),
        ("claude_worker_connections", "payg", "payg"),
        ("codex_credits_provider", "openai", "openai"),
        ("codex_payg_provider", "my-payg", "my-payg"),
        ("codex_orchestrator_connections", "credits,subscription,payg", "credits,subscription,payg"),
        ("codex_worker_connections", "subscription", "subscription"),
    ):
        assert _main(tmp_path, "config", "set", key, value) == 0
        assert not capsys.readouterr().err
        assert _main(tmp_path, "config", "get", key) == 0
        assert capsys.readouterr().out == expected + "\n"
    assert config_module.connections_configured("claude")
    assert config_module.connections_configured("codex")
    before = _config_file(tmp_path).read_bytes()
    for key, value in (
        ("claude_worker_connections", ""),
        ("claude_worker_connections", "api"),
        ("codex_worker_connections", "credits,credits"),
        ("codex_payg_provider", "my payg"),
        ("codex_credits_provider", "a,b"),
    ):
        assert _main(tmp_path, "config", "set", key, value) == 2
        assert "設定値の書式が不正" in capsys.readouterr().err
    assert _config_file(tmp_path).read_bytes() == before


@pytest.mark.parametrize(
    ("old", "expected"),
    [
        # この環境の保存値（統括担当サブスク固定・委譲先API優先）。
        (
            {
                "claude_api_env_file": "/home/user/claude-api.env",
                "claude_orchestrator_api_only": "false",
                "claude_worker_api_first": "true",
                "codex_model_providers": "",
            },
            {
                "claude_credits_env_file": "/home/user/claude-api.env",
                "claude_orchestrator_connections": "subscription",
                "claude_worker_connections": "credits,subscription",
            },
        ),
        (
            {"claude_api_env_file": "/api.env"},
            {
                "claude_payg_env_file": "/api.env",
                "claude_orchestrator_connections": "payg",
                "claude_worker_connections": "subscription,payg",
            },
        ),
        (
            {"claude_api_mode": "api-credits", "claude_api_env_file": "/api.env"},
            {
                "claude_credits_env_file": "/api.env",
                "claude_orchestrator_connections": "subscription",
                "claude_worker_connections": "credits,subscription",
            },
        ),
        (
            {"claude_api_mode": "api-only", "claude_api_env_file": "/api.env"},
            {
                "claude_payg_env_file": "/api.env",
                "claude_orchestrator_connections": "payg",
                "claude_worker_connections": "payg",
            },
        ),
        (
            {"claude_api_mode": "api-only", "claude_worker_api_first": "false", "claude_api_env_file": "/api.env"},
            {
                "claude_payg_env_file": "/api.env",
                "claude_orchestrator_connections": "payg",
                "claude_worker_connections": "subscription,payg",
            },
        ),
        ({"claude_api_mode": "api-only"}, {}),
        (
            {"claude_orchestrator_api_only": "false"},
            {"claude_orchestrator_connections": "subscription", "claude_worker_connections": "subscription"},
        ),
        (
            {"claude_api_env_file": "/api.env", "claude_worker_connections": "payg"},
            {
                "claude_payg_env_file": "/api.env",
                "claude_orchestrator_connections": "payg",
                "claude_worker_connections": "payg",
            },
        ),
        ({"codex_model_providers": ""}, {}),
        (
            {"codex_model_providers": "openai"},
            {"codex_orchestrator_connections": "subscription", "codex_worker_connections": "subscription"},
        ),
        (
            {"codex_model_providers": "openai,custom"},
            {
                "codex_payg_provider": "custom",
                "codex_orchestrator_connections": "subscription,payg",
                "codex_worker_connections": "subscription,payg",
            },
        ),
        (
            {"codex_model_providers": "custom,openai"},
            {
                "codex_payg_provider": "custom",
                "codex_orchestrator_connections": "payg,subscription",
                "codex_worker_connections": "payg,subscription",
            },
        ),
    ],
)
def test_retired_connection_settings_migrate_once_and_are_removed(tmp_path, capsys, old, expected):
    old = dict(old)
    expected = dict(expected)
    for kind in ("credits", "payg"):
        if expected.pop(f"claude_{kind}_env_file", None):
            path = tmp_path / "old-api.env"
            path.write_text("ANTHROPIC_API_KEY=fixture\n", encoding="utf-8")
            old["claude_api_env_file"] = str(path)
    _write_config(tmp_path, {**old, "codex_fast_mode": "true"})
    assert _main(tmp_path, "config", "show") == 2
    assert _saved(tmp_path) == {**old, "codex_fast_mode": "true"}
    capsys.readouterr()
    assert _main(tmp_path, "config", "get", "codex_fast_mode") == 0
    assert capsys.readouterr().out == "true\n"
    assert not capsys.readouterr().err
    assert _saved(tmp_path) == {**expected, "codex_fast_mode": "true"}
    assert _main(tmp_path, "config", "show") == 0
    assert _saved(tmp_path) == {**expected, "codex_fast_mode": "true"}


def test_codex_migration_with_multiple_api_providers_requires_new_keys(tmp_path, capsys):
    path = _write_config(tmp_path, {"codex_model_providers": "openai,paid-a,paid-b"})
    before = path.read_bytes()
    assert _main(tmp_path, "config", "get", "codex_fast_mode") == 2
    err = capsys.readouterr().err
    assert "自動移行できません" in err and "codex_payg_provider" in err
    assert path.read_bytes() == before
    assert _main(tmp_path, "config", "set", "codex_payg_provider", "paid-b") == 0
    capsys.readouterr()
    assert _saved(tmp_path) == {"codex_payg_provider": "paid-b"}


@pytest.mark.parametrize("key", list(config_module._RETIRED_CONNECTION_KEYS))  # pylint: disable=protected-access  # noqa: SLF001
def test_retired_connection_keys_are_rejected_with_targets(tmp_path, capsys, monkeypatch, key):
    targets = config_module._RETIRED_CONNECTION_KEYS[key]  # pylint: disable=protected-access  # noqa: SLF001
    for arguments in (("get", key), ("set", key, "value")):
        assert _main(tmp_path, "config", *arguments) == 2
        err = capsys.readouterr().err
        assert all(target in err for target in targets)
    assert not _config_file(tmp_path).exists()
    monkeypatch.setenv(f"AGENT_TOOLKIT_CONFIG_{key.upper()}", "value")
    assert _main(tmp_path, "config", "show") == 2
    err = capsys.readouterr().err
    assert all(
        (target if " " in target or "設定" in target else f"AGENT_TOOLKIT_CONFIG_{target.upper()}") in err for target in targets
    )
    with pytest.raises(_next_action.ActionableError, match=f"AGENT_TOOLKIT_CONFIG_{key.upper()}"):
        config_module.resolve_mutable_setting("claude_worker_connections")


def test_connection_order_resolution_per_engine_and_role(monkeypatch):
    assert config_module.connection_order("claude", "orchestrator") == ("subscription", "credits")
    assert config_module.connection_order("codex", "delegate") == ("credits", "subscription")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CODEX_WORKER_CONNECTIONS", "payg,subscription")
    assert config_module.connection_order("codex", "delegate") == ("payg", "subscription")
    assert config_module.connections_configured("codex")
    assert not config_module.connections_configured("claude")


def test_explicit_subscription_requires_oauth_without_api_file(tmp_path, capsys, monkeypatch):
    """親にAPI認証だけがある場合、公開保存したサブスクリプションだけの使用順はログインの復旧操作を返す。"""
    assert _main(tmp_path, "config", "set", "claude_orchestrator_connections", "subscription") == 0
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


def test_fixed_credentials_migrate_without_enabling_payg(tmp_path, capsys):
    credits_file = tmp_path / "original-credits.env"
    payg = tmp_path / "original-payg.env"
    credits_file.write_text("ANTHROPIC_API_KEY=credits-for-test\n", encoding="utf-8")
    payg.write_text("ANTHROPIC_API_KEY=payg-for-test\n", encoding="utf-8")
    _write_config(tmp_path, {"claude_credits_env_file": str(credits_file), "claude_payg_env_file": str(payg)})
    assert _main(tmp_path, "config", "show") == 2
    shown = capsys.readouterr()
    assert "claude_credits_env_file" not in shown.out
    assert "claude_payg_env_file" not in shown.out
    assert "for-test" not in shown.out + shown.err
    assert credits_file.exists() and payg.exists()
    assert _main(tmp_path, "config", "get", "codex_fast_mode") == 0
    assert _saved(tmp_path) == {}
    assert not credits_file.exists() and not payg.exists()
    choices = claude_connection.candidates("delegate", env={})
    assert [choice.kind for choice in choices.connections] == ["credits"]
    assert choices.connections[0].api_env_file == str(tmp_path / "config" / "claude-credits.env")


def test_credential_migration_does_not_replace_existing_fixed_env(tmp_path, capsys):
    source = tmp_path / "old.env"
    source.write_text("ANTHROPIC_API_KEY=old-test-value\n", encoding="utf-8")
    config = _write_config(tmp_path, {"claude_credits_env_file": str(source)})
    before = config.read_bytes()
    fixed = config.parent / "claude-credits.env"
    fixed.write_text("ANTHROPIC_API_KEY=new-test-value\n", encoding="utf-8")
    assert _main(tmp_path, "config", "get", "codex_fast_mode") == 2
    output = capsys.readouterr()
    assert "claude-credits.env" in output.err
    assert "test-value" not in output.out + output.err
    assert config.read_bytes() == before
    assert fixed.read_text(encoding="utf-8") == "ANTHROPIC_API_KEY=new-test-value\n"
    assert source.read_text(encoding="utf-8") == "ANTHROPIC_API_KEY=old-test-value\n"

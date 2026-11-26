import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config, config_settings
from agent_toolkit._common import mcp_environment, proxy_environment


def test_mcp_setting_names_follow_public_config_keys(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected_app_server(*_args, **_kwargs):
        pytest.fail("設定一覧でCodex app-serverを起動した")

    def saved_settings() -> dict[str, bytes]:
        directory = config_settings.config_dir()
        if not directory.is_dir():
            return {}
        return {path.name: path.read_bytes() for path in directory.iterdir() if path.is_file()}

    monkeypatch.setattr(config.codex_models, "with_app_server", unexpected_app_server)
    with pytest.raises(SystemExit) as exited:
        atk.main(["config", "show"])
    assert exited.value.code == 0
    baseline = capsys.readouterr().out.splitlines()
    paths = {"config_dir", "state_dir", "data_dir", "private_notes"}
    keys = {line.partition(" ")[0] for line in baseline} - paths
    setting_names = config.config_environment_variable_names()
    names = mcp_environment.environment_variable_names(setting_names)
    for key in keys:
        assert f"AGENT_TOOLKIT_CONFIG_{key.upper()}" in names
        config.resolve_mutable_setting(key)
    for name in setting_names:
        key = name.removeprefix("AGENT_TOOLKIT_CONFIG_").lower()
        if key in config_settings.RETIRED_CONNECTION_KEYS:
            assert name in names
            assert key not in keys
            before = saved_settings()
            with monkeypatch.context() as legacy:
                legacy.setenv(name, "legacy-value")
                with pytest.raises(SystemExit) as exited:
                    atk.main(["config", "show"])
            shown = capsys.readouterr()
            affected = keys.intersection(config_settings.RETIRED_CONNECTION_KEYS[key])
            if key == "claude_api_env_file":
                assert exited.value.code == 0
                assert shown.out.splitlines() == baseline
                assert name not in shown.err
            else:
                assert affected
                assert exited.value.code == 2
                assert {line.partition(" ")[0] for line in shown.out.splitlines()} - paths == keys - affected
                assert "旧接続設定" in shown.err and name in shown.err
            assert saved_settings() == before
        elif key in config_settings.RETIRED_FIXED_KEYS or key in config_settings.LEGACY_REMOTE_HOSTS_KEYS:
            assert name in names
            assert key not in keys
        elif key in config_settings.RETIRED_MODEL_KEYS:
            with pytest.raises(SystemExit) as exited:
                atk.main(["config", "get", key])
            assert exited.value.code == 2
            assert "旧モデル設定キー" in capsys.readouterr().err
            monkeypatch.setenv(name, "codex:gpt-6-sol/medium")
            with pytest.raises(ValueError, match=name):
                config.parse_unresolved_model_candidates("execute")
            monkeypatch.delenv(name)
        elif key == "process_loop_repositories":
            assert name in names
            with pytest.raises(SystemExit) as exited:
                atk.main(["config", "get", key])
            assert exited.value.code == 2
            diagnostic = capsys.readouterr().err
            assert key in diagnostic and "atk config repos" in diagnostic
            monkeypatch.setenv(name, "[]")
            with pytest.raises(ValueError, match=name):
                config.resolve_configured_repositories()
            monkeypatch.delenv(name)
        elif key in config_settings.RETIRED_RESEARCH_KEYS or key == "usage_auto_allocation":
            assert name in names
            assert key not in keys
            with monkeypatch.context() as legacy:
                legacy.setenv(name, "legacy-value")
                with pytest.raises(SystemExit) as shown:
                    atk.main(["config", "show"])
                assert shown.value.code == 0
                assert capsys.readouterr().out.splitlines() == baseline
                with pytest.raises(SystemExit) as rejected:
                    atk.main(["config", "get", "codex_fast_mode"])
                assert rejected.value.code == 2
                err = capsys.readouterr().err
                assert name in err
                if key in config_settings.RETIRED_RESEARCH_KEYS:
                    assert "旧調査設定" in err
        else:
            assert key in keys
    assert not {"AGENT_TOOLKIT_CONFIG_STATE_DIR", "AGENT_TOOLKIT_CONFIG_PRIVATE_NOTES"} & set(names)
    assert proxy_environment.PROXY_ENVIRONMENT_VARIABLES is mcp_environment.PROXY_ENVIRONMENT_VARIABLES


def test_environment_names_do_not_capture_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """環境変数名の列挙は環境変数の値に左右されず、接頭辞だけが一致する未公開の名前や認証用の名前を含めない。"""
    names = mcp_environment.environment_variable_names(config.config_environment_variable_names())
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_UNPUBLISHED", "diagnostic-value")
    monkeypatch.setenv("DIAGNOSTIC_TOKEN", "diagnostic-secret")
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "diagnostic-owner")
    assert mcp_environment.environment_variable_names(config.config_environment_variable_names()) == names
    assert not {"AGENT_TOOLKIT_CONFIG_UNPUBLISHED", "DIAGNOSTIC_TOKEN", "AGENT_TOOLKIT_OWNER_SESSION"} & set(names)

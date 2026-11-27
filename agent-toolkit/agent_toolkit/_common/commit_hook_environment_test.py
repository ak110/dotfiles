import pathlib
import tomllib

from agent_toolkit._common import commit_hook_environment


def test_merge_codex_config_preserves_other_settings_and_is_idempotent(tmp_path: pathlib.Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        'model = "example"\n'
        "[shell_environment_policy]\n"
        'include_only = ["PATH", "OTHER"]\n'
        'exclude = ["UNRELATED_*"]\n'
        "[shell_environment_policy.set]\n"
        'OTHER = "retained" # 既存の説明\n'
        "GIT_CONFIG_PARAMETERS = \"'color.ui'='never'\"\n",
        encoding="utf-8",
    )
    hooks = tmp_path / "hooks"
    assert commit_hook_environment.merge_codex_config(config, hooks)
    text = config.read_text(encoding="utf-8")
    settings = tomllib.loads(text)
    assert settings["model"] == "example"
    policy = settings["shell_environment_policy"]
    assert policy["exclude"] == ["UNRELATED_*"]
    assert policy["include_only"] == ["PATH", "OTHER", commit_hook_environment.HOOKS_ENV, "GIT_CONFIG_PARAMETERS"]
    assert policy["set"]["OTHER"] == "retained"
    assert policy["set"]["GIT_CONFIG_PARAMETERS"] == "'color.ui'='never' " + commit_hook_environment.config_parameter(hooks)
    assert "既存の説明" in text
    assert not commit_hook_environment.merge_codex_config(config, hooks)
    assert config.read_text(encoding="utf-8") == text
    assert commit_hook_environment.merge_codex_config(config, None)
    settings = tomllib.loads(config.read_text(encoding="utf-8"))
    assert settings["shell_environment_policy"]["set"] == {
        "OTHER": "retained",
        "GIT_CONFIG_PARAMETERS": "'color.ui'='never'",
    }
    assert not commit_hook_environment.merge_codex_config(config, None)


def test_merge_codex_config_in_empty_home(tmp_path: pathlib.Path) -> None:
    config = tmp_path / "other-home" / "config.toml"
    hooks = tmp_path / "hooks"
    assert commit_hook_environment.merge_codex_config(config, hooks)
    settings = tomllib.loads(config.read_text(encoding="utf-8"))["shell_environment_policy"]["set"]
    assert settings["GIT_CONFIG_PARAMETERS"] == commit_hook_environment.config_parameter(hooks)

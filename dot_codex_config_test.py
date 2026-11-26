"""chezmoiのCodex設定原本`modify_private_config.toml`の評価結果を検証する。

原本を置く`.chezmoi-source/`はpytestの通常の収集から外れる隠しディレクトリのため、リポジトリ直下へ置く。
"""

import collections.abc
import pathlib
import subprocess
import tomllib

import pytest

import _test_helpers

REPO_ROOT = pathlib.Path(__file__).resolve().parent


def _render_config(content: str, environment: dict[str, str]) -> str:
    return _test_helpers.render_chezmoi_modify_template(
        REPO_ROOT / ".chezmoi-source/dot_codex/modify_private_config.toml", content, environment
    )


def test_config_template_merges_commit_hook_with_existing_shell_environment(
    host_environ: collections.abc.Callable[[], dict[str, str]],
) -> None:
    original = (
        "[shell_environment_policy]\n"
        'include_only = ["PATH", "OTHER"]\n'
        'exclude = ["UNRELATED_*"]\n'
        "[shell_environment_policy.set]\n"
        'OTHER = "retained"\n'
        "GIT_CONFIG_PARAMETERS = \"'color.ui'='never'\"\n"
    )
    first = _render_config(original, host_environ())
    policy = tomllib.loads(first)["shell_environment_policy"]
    assert policy["exclude"] == ["UNRELATED_*"]
    assert policy["include_only"] == ["PATH", "OTHER", "AGENT_TOOLKIT_COMMIT_HOOKS", "GIT_CONFIG_PARAMETERS"]
    settings = policy["set"]
    assert settings["OTHER"] == "retained"
    assert settings["GIT_CONFIG_PARAMETERS"].startswith("'color.ui'='never' 'core.hooksPath'=")
    assert settings["GIT_CONFIG_PARAMETERS"].count("core.hooksPath") == 1
    assert tomllib.loads(_render_config(first, host_environ()))["shell_environment_policy"] == policy


@pytest.mark.parametrize("enabled", [False, True, None])
def test_config_template_preserves_explicit_plugin_enabled_state(
    enabled: bool | None, host_environ: collections.abc.Callable[[], dict[str, str]]
) -> None:
    plugin_id = "agent-toolkit@ak110-dotfiles"
    content = f'[plugins."{plugin_id}"]\ncustom = "keep"\n'
    if enabled is not None:
        content += f"enabled = {str(enabled).lower()}\n"
    for _ in range(2):
        content = _render_config(content, host_environ())
        plugin = tomllib.loads(content)["plugins"][plugin_id]
        assert plugin["custom"] == "keep"
        if enabled is None:
            assert "enabled" not in plugin
        else:
            assert plugin["enabled"] is enabled


@pytest.mark.parametrize("existing", ["", 'personality = "friendly"\n', 'personality = "pragmatic"\n'])
def test_config_template_disables_personality_and_reapplies(
    existing: str, host_environ: collections.abc.Callable[[], dict[str, str]]
) -> None:
    content = existing + 'model = "custom-model"\n[mcp_servers.custom]\ncommand = "custom-command"\n'
    for _ in range(2):
        content = _render_config(content, host_environ())
        rendered = tomllib.loads(content)
        assert rendered["personality"] == "none"
        assert rendered["model"] == "custom-model"
        assert rendered["mcp_servers"]["custom"]["command"] == "custom-command"


def test_config_template_applies_shared_limits_and_preserves_existing_values() -> None:
    """chezmoiの実行結果へ共有設定を反映し、無関係なユーザー設定を保持する。"""
    template = REPO_ROOT / ".chezmoi-source/dot_codex/modify_private_config.toml"
    result = subprocess.run(
        [
            "chezmoi",
            "execute-template",
            "--file",
            str(template),
            "--with-stdin",
            "--working-tree",
            str(REPO_ROOT),
        ],
        input=('model = "gpt-test"\nproject_doc_max_bytes = 1\ntool_output_token_limit = 2\n[features]\nuser_feature = true\n'),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    rendered = tomllib.loads(result.stdout)
    assert rendered["project_doc_max_bytes"] == 262144
    assert rendered["tool_output_token_limit"] == 20000
    assert rendered["suppress_unstable_features_warning"] is True
    assert rendered["model"] == "gpt-test"
    assert rendered["features"]["reasoning_effort_override"] is True
    assert rendered["features"]["user_feature"] is True


@pytest.mark.parametrize(
    "context_config",
    [
        "",
        "model_context_window = 1000000\nmodel_auto_compact_token_limit = 400000\n",
        "model_context_window = 1000000\nmodel_auto_compact_token_limit = 900000\n",
        "model_context_window = 200000\nmodel_auto_compact_token_limit = 180000\n",
    ],
    ids=["absent", "same", "previous", "different"],
)
def test_config_template_applies_context_overrides(context_config: str) -> None:
    """コンテキスト設定の有無と値によらず指定値を反映し、無関係な設定を保つ。

    固定値の目的と、値を変える前に観測する条件は`docs/development/concepts-codex-context.md`「Codexの文脈長設定」にある。
    このテストは生成値だけを確かめ、目的（圧縮の頻度と待ち時間）の達成は確かめない。
    """
    template = REPO_ROOT / ".chezmoi-source/dot_codex/modify_private_config.toml"
    result = subprocess.run(
        ["chezmoi", "execute-template", "--file", str(template), "--with-stdin", "--working-tree", str(REPO_ROOT)],
        input=context_config + 'model = "gpt-test"\n[features]\nuser_feature = true\n',
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    rendered = tomllib.loads(result.stdout)
    assert isinstance(rendered["model_context_window"], int)
    assert rendered["model_context_window"] == 1000000
    assert isinstance(rendered["model_auto_compact_token_limit"], int)
    assert rendered["model_auto_compact_token_limit"] == 400000
    assert rendered["model"] == "gpt-test"
    assert rendered["features"]["user_feature"] is True


@pytest.mark.parametrize(
    "reasoning_config",
    [
        "",
        'model_reasoning_summary = "auto"\nhide_agent_reasoning = false\n',
        'model_reasoning_summary = "none"\nhide_agent_reasoning = true\n',
    ],
)
def test_config_template_removes_reasoning_overrides(reasoning_config: str) -> None:
    template = REPO_ROOT / ".chezmoi-source/dot_codex/modify_private_config.toml"
    result = subprocess.run(
        ["chezmoi", "execute-template", "--file", str(template), "--with-stdin", "--working-tree", str(REPO_ROOT)],
        input=reasoning_config + 'model = "gpt-test"\n[features]\nuser_feature = true\n',
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    rendered = tomllib.loads(result.stdout)
    assert "model_reasoning_summary" not in rendered
    assert "hide_agent_reasoning" not in rendered
    assert rendered["model"] == "gpt-test"
    assert rendered["features"]["user_feature"] is True


@pytest.mark.parametrize("existing", ["", "[tui]\nmouse_scroll_speed = 1.0\nstatus_line_use_colors = true\n"])
def test_config_template_sets_mouse_scroll_speed_and_keeps_tui_keys(
    existing: str, host_environ: collections.abc.Callable[[], dict[str, str]]
) -> None:
    """マウスホイールの移動量を浮動小数点数の5.0にし、Codexが書いた他の`tui`キーを再適用後も保持する。

    codex 0.165.0では値が1イベントあたりの行数になり、正の有限数でない値は起動時に拒否される。
    """
    content = existing
    for _ in range(2):
        content = _render_config(content, host_environ())
        tui = tomllib.loads(content)["tui"]
        assert tui["mouse_scroll_speed"] == 5.0
        assert isinstance(tui["mouse_scroll_speed"], float)
        if existing:
            assert tui["status_line_use_colors"] is True


@pytest.mark.parametrize(
    "existing",
    [
        "",
        "[features.context_management]\nexperimental_mode = true\n",
        "[features.context_management]\nexperimental_mode = false\nother = 1\n",
    ],
    ids=["absent", "enabled", "disabled"],
)
def test_config_template_disables_experimental_context_management(
    existing: str, host_environ: collections.abc.Callable[[], dict[str, str]]
) -> None:
    content = existing if existing else existing + "[features]\nuser_feature = true\n"
    for _ in range(2):
        content = _render_config(content, host_environ())
        features = tomllib.loads(content)["features"]
        assert features["context_management"]["experimental_mode"] is False
        assert features["reasoning_effort_override"] is True
        if "other" in existing:
            assert features["context_management"]["other"] == 1
        if not existing:
            assert features["user_feature"] is True

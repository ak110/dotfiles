"""chezmoiのCodex設定原本`modify_private_config.toml`の評価結果を検証する。

原本を置く`.chezmoi-source/`はpytestの通常の収集から外れる隠しディレクトリのため、リポジトリ直下へ置く。
"""

import collections.abc
import pathlib
import subprocess
import tomllib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent


def _render_config(content: str, environment: dict[str, str]) -> str:
    """既存設定を実テンプレートで評価し、再適用できる本文を返す。"""
    template = REPO_ROOT / ".chezmoi-source/dot_codex/modify_private_config.toml"
    result = subprocess.run(
        ["chezmoi", "execute-template", "--file", str(template), "--with-stdin", "--working-tree", str(REPO_ROOT)],
        input=content,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


@pytest.mark.parametrize("enabled", [False, True, None])
def test_config_template_preserves_explicit_plugin_enabled_state(
    enabled: bool | None, host_environ: collections.abc.Callable[[], dict[str, str]]
) -> None:
    """pluginの明示状態と未知キーを再適用でも保持し、未設定の有効状態は新設しない。"""
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
    """未設定と既存有効値を無効値へ更新し、未知値と再適用後の値を保持する。"""
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
        "model_context_window = 1000000\nmodel_auto_compact_token_limit = 900000\n",
        "model_context_window = 200000\nmodel_auto_compact_token_limit = 180000\n",
    ],
    ids=["absent", "same", "different"],
)
def test_config_template_removes_context_overrides(context_config: str) -> None:
    """共通のコンテキスト固定値を除き、選択モデルと無関係な設定を保つ。"""
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
    assert "model_context_window" not in rendered
    assert "model_auto_compact_token_limit" not in rendered
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
    """既存設定の有無と値によらず固定を解除し、無関係なユーザー設定を保つ。"""
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

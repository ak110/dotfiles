"""chezmoiのCodex設定原本`modify_private_config.toml`の評価結果を検証する。

原本を置く`.chezmoi-source/`はpytestの通常の収集から外れる隠しディレクトリのため、リポジトリ直下へ置く。
"""

import pathlib
import subprocess
import tomllib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent


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
    ],
    ids=["absent", "present"],
)
def test_config_template_removes_context_overrides(context_config: str) -> None:
    """コンテキスト設定の有無によらず固定を解除し、無関係なユーザー設定を保つ。"""
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

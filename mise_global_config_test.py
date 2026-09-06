"""配布するglobal mise設定の契約を検証する。"""

import pathlib
import tomllib

REPO_ROOT = pathlib.Path(__file__).resolve().parent


def test_glab_cli_tokens_are_disabled() -> None:
    """公開プロジェクトの取得へglab CLIのトークンを付与しない。"""
    config_path = REPO_ROOT / ".chezmoi-source" / "dot_config" / "mise" / "config.toml"
    with config_path.open("rb") as config_file:
        config = tomllib.load(config_file)

    assert config["settings"]["gitlab"]["glab_cli_tokens"] is False

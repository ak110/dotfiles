"""update-dotfilesが子工程へ渡す環境の契約を検証する。"""

# pylint: disable=protected-access

from scripts import update_dotfiles


def test_child_env_removes_parent_virtual_environment(monkeypatch) -> None:
    """仮想環境の印だけを除き、他の環境とmise設定を保持する。"""
    monkeypatch.setenv("VIRTUAL_ENV", "/tmp/parent-venv")
    monkeypatch.setenv("VIRTUAL_ENV_PROMPT", "(parent)")
    monkeypatch.setenv("PRESERVED_VALUE", "kept")

    child_env = update_dotfiles._child_env()

    assert "VIRTUAL_ENV" not in child_env
    assert "VIRTUAL_ENV_PROMPT" not in child_env
    assert child_env["PRESERVED_VALUE"] == "kept"
    assert child_env["MISE_AUTO_INSTALL"] == "0"

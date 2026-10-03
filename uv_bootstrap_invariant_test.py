"""近くの実物と共有する契約の整合性を検査する。"""

import pathlib
import tomllib

import pytest

pytestmark = pytest.mark.repo_invariant

_ROOT = pathlib.Path(__file__).resolve().parent


def test_root_mise_files_do_not_manage_uv() -> None:
    """bootstrap基盤のuvがルートmise設定とlockへ再混入しない。"""
    with (_ROOT / "mise.toml").open("rb") as file:
        config = tomllib.load(file)
    with (_ROOT / "mise.lock").open("rb") as file:
        lock = tomllib.load(file)

    assert "uv" not in config["tools"]
    assert "uv" not in lock["tools"]

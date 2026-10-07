"""agent_toolkit/_atk/managed_temp/ のテストが共有するfixture。"""

import pathlib

import pytest

from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules


@pytest.fixture(autouse=True)
def isolated_state_root(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部真正性状態を各テストの専用領域へ分離する。"""
    cache_root = tmp_path / "managed-temp"
    cache_root.mkdir(mode=0o700)
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    setattr_in_managed_temp_modules(monkeypatch, "_temp_root", lambda: cache_root)

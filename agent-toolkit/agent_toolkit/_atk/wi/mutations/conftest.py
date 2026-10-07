"""agent_toolkit/_atk/wi/mutations/ のテストが共有するfixture。"""

import pathlib
import tempfile

import pytest

from agent_toolkit._testing.wi_mutations_support import _AGENT_ENVIRONMENT_VARIABLES


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """編集テストをホスト側のエージェント環境と一時rootから隔離する。"""
    for name in _AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp_root))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

"""agent_toolkit/_atk/serve/plans/ のテストが共有するfixture。"""

import pathlib

import pytest

from agent_toolkit._plan import creation_times as plan_creation_times


@pytest.fixture(name="index_path")
def _index_path(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """作成日時インデックスを一時ディレクトリへ隔離する。"""
    path = tmp_path / "cache" / "index.json"
    monkeypatch.setattr(plan_creation_times, "_CREATION_TIME_INDEX_PATH", path)
    monkeypatch.setattr(plan_creation_times, "observed_creation_epoch", lambda st: float(st.st_mtime))
    return path

"""agent_toolkit/_atk/serve/plans/ のテストが共有するfixture。"""

import pathlib

import pytest

from agent_toolkit._atk.serve.plans import ctime_index as plans_ctime_index
from agent_toolkit._atk.serve.plans import local_scan as plans_local_scan


@pytest.fixture(name="index_path")
def _index_path(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """作成日時インデックスを一時ディレクトリへ隔離する。"""
    path = tmp_path / "cache" / "index.json"
    monkeypatch.setattr(plans_ctime_index, "_CREATION_TIME_INDEX_PATH", path)
    monkeypatch.setattr(plans_local_scan, "_ctime_epoch", lambda st: float(st.st_mtime))
    return path

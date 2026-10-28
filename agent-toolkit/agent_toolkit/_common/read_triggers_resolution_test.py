"""宣言解析内の解決再利用と、要求境界での再取得を検証する。"""

import pathlib
from typing import Any

import pytest

from agent_toolkit._common import read_triggers


def test_resolution_cache_is_request_local(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同じ宣言内の再読を省き、次の宣言では削除・差し替え・新規作成を反映する。"""
    source = tmp_path / "source.md"
    source.write_text("# 本文\n", encoding="utf-8")
    original = pathlib.Path.read_text
    reads: list[pathlib.Path] = []

    def observe(path: pathlib.Path, *args: Any, **kwargs: Any) -> str:
        reads.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", observe)
    for _ in range(2):
        with read_triggers.resolution_scope():
            for _ in range(3):
                assert read_triggers.resolve_read_source(source, tmp_path) == source
    assert reads == [source, source]
    source.write_text(f"<!-- read-source: {'agent-toolkit'}:missing.md -->\n", encoding="utf-8")
    with read_triggers.resolution_scope(), pytest.raises(ValueError, match="実体が無い"):
        read_triggers.resolve_read_source(source, tmp_path)
    source.unlink()
    with read_triggers.resolution_scope():
        assert read_triggers.resolve_read_source(source, tmp_path) == source
    source.write_text("# 再作成\n", encoding="utf-8")
    with read_triggers.resolution_scope():
        assert read_triggers.resolve_read_source(source, tmp_path) == source
    assert reads[-1] == source


def test_cached_source_keeps_cycle_detection(tmp_path: pathlib.Path) -> None:
    """同じ資料を既に解決しても、訪問済み資料を別入力として循環を検査する。"""
    source = tmp_path / "source.md"
    source.write_text("# 本文\n", encoding="utf-8")
    with read_triggers.resolution_scope():
        assert read_triggers.resolve_read_source(source, tmp_path) == source
        with pytest.raises(ValueError, match="循環"):
            read_triggers.resolve_read_source(source, tmp_path, seen=(source,))

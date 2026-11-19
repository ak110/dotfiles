"""記録ごとの値の索引が、記録とコードの変化に追従しつつ、変わらない記録を解析し直さないことを確かめる。"""

from __future__ import annotations

import json
import os
import pathlib
import time

import pytest

from agent_toolkit._common import record_metadata_index, session_family
from agent_toolkit._testing.session_evidence_support import (
    codex_tool_result_entry,
    codex_tool_use_entry,
    timestamped_entry,
    write_jsonl,
)

_SETTLED = time.time() - 3600
_CHILD = "11111111-1111-4111-8111-111111111111"


def _settle(*paths: pathlib.Path, offset: int = 0) -> None:
    """書込から間もない記録は索引へ保存されないため、更新時刻を過去へ戻して保存の対象にする。"""
    for path in paths:
        os.utime(path, (_SETTLED + offset, _SETTLED + offset))


def _record(root: pathlib.Path, name: str, *, child: str | None = None) -> pathlib.Path:
    entries = [timestamped_entry("2026-10-06T00:00:00Z", name)]
    if child is not None:
        timestamp = "2026-10-06T00:01:00Z"
        entries.extend([codex_tool_use_entry(timestamp, "start", child), codex_tool_result_entry(timestamp, "start", child)])
    path = root / f"{name}.jsonl"
    write_jsonl(path, entries)
    return path


def _expected(path: pathlib.Path) -> record_metadata_index.RecordSummary:
    """記録を直接走査した値。索引はこれと同じ値を返す。"""
    meta = session_family._scan_record_metadata(path)  # pylint: disable=protected-access
    return record_metadata_index.RecordSummary(
        meta.runtime, meta.record_id, meta.thread_id, meta.start_value, meta.end_value, meta.child_references, meta.failure
    )


def _count_scans(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    scanned: list[str] = []
    scan = record_metadata_index._scan_record_metadata  # pylint: disable=protected-access

    def counting(path: pathlib.Path) -> session_family._RecordMetadata:  # pylint: disable=protected-access
        scanned.append(path.stem)
        return scan(path)

    monkeypatch.setattr(record_metadata_index, "_scan_record_metadata", counting)
    return scanned


def test_summaries_equal_the_record_scan(tmp_path: pathlib.Path) -> None:
    """子の参照・読込失敗・列挙の後に消えた記録を含め、索引の保存前後とも記録を直接走査した値を返す。"""
    root = tmp_path / "records"
    paths = [_record(root, "parent", child=_CHILD), _record(root, "plain")]
    broken = root / "broken.jsonl"
    broken.write_text('{"type": "user"}\n{"truncated', encoding="utf-8")
    paths.append(broken)
    _settle(*paths)
    paths.append(root / "vanished.jsonl")
    expected = {path: _expected(path) for path in paths}
    assert expected[paths[0]].child_references and expected[broken].failure and expected[paths[-1]].failure
    for _ in range(2):
        assert record_metadata_index.summaries(root, "claude", paths) == expected


@pytest.mark.parametrize("count", [5, 50])
def test_unchanged_records_are_not_scanned_again(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, count: int) -> None:
    """2回目以降に走査する記録は書き換えたものだけで、記録の件数に比例しない。書き換えた記録は新しい値を返す。"""
    root = tmp_path / "records"
    paths = [_record(root, f"session-{index}") for index in range(count)]
    _settle(*paths)
    scanned = _count_scans(monkeypatch)
    record_metadata_index.summaries(root, "claude", paths)
    assert len(scanned) == count
    scanned.clear()
    record_metadata_index.summaries(root, "claude", paths)
    assert not scanned
    _record(root, "session-0", child=_CHILD)
    _settle(paths[0], offset=1)
    summaries = record_metadata_index.summaries(root, "claude", paths)
    assert scanned == ["session-0"]
    assert summaries[paths[0]] == _expected(paths[0]) and summaries[paths[0]].child_references


def test_records_are_scanned_again_after_the_scanning_code_changes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """値を計算するコードが変わると（版を上げない開発中の編集を含む）、変わらない記録も走査し直す。"""
    root = tmp_path / "records"
    paths = [_record(root, f"session-{index}") for index in range(3)]
    _settle(*paths)
    scanned = _count_scans(monkeypatch)
    record_metadata_index.summaries(root, "claude", paths)
    monkeypatch.setattr(record_metadata_index.derived_index, "code_version", lambda _module: "edited")
    scanned.clear()
    record_metadata_index.summaries(root, "claude", paths)
    assert sorted(scanned) == ["session-0", "session-1", "session-2"]


@pytest.mark.parametrize("content", ["{", '{"version": 2, "code": "x", "entries": {}}', "short-values"])
def test_broken_index_is_ignored(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    """壊れた索引・別のコードの索引・形の合わない値は保存されていなかったものとして扱い、走査した値を返す。"""
    root = tmp_path / "records"
    paths = [_record(root, "parent", child=_CHILD)]
    _settle(*paths)
    record_metadata_index.summaries(root, "claude", paths)
    (index,) = (tmp_path / "home").rglob("record-index/*.json")
    if content == "short-values":
        saved = json.loads(index.read_text(encoding="utf-8"))
        content = json.dumps({**saved, "entries": {key: value[:5] for key, value in saved["entries"].items()}})
    index.write_text(content, encoding="utf-8")
    scanned = _count_scans(monkeypatch)
    assert record_metadata_index.summaries(root, "claude", paths) == {paths[0]: _expected(paths[0])}
    assert scanned == ["parent"]


def test_recently_written_record_is_scanned_until_it_settles(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """更新時刻の刻みの中で内容だけが変わり得る書込直後の記録は、保存せずに照会のたびに走査する。"""
    root = tmp_path / "records"
    paths = [_record(root, "fresh")]
    scanned = _count_scans(monkeypatch)
    for _ in range(2):
        record_metadata_index.summaries(root, "claude", paths)
    assert scanned == ["fresh", "fresh"]
    _settle(*paths)
    for _ in range(2):
        record_metadata_index.summaries(root, "claude", paths)
    assert scanned == ["fresh", "fresh", "fresh"]

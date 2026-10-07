"""agent-toolkit/agent_toolkit/_hooks/transcript_scan.py のテスト。"""

import pathlib

import pytest

from agent_toolkit._common import transcript as _transcript
from agent_toolkit._hooks import transcript_scan as _transcript_scan
from agent_toolkit._hooks.transcript_scan import read_transcript_entries_cached
from agent_toolkit._testing.helpers import _write_transcript


def _user_entry(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


class TestReadTranscriptEntriesCached:
    """`read_transcript_entries_cached`の待機・解析の重複回避を検証する。"""

    def test_second_call_with_same_path_skips_wait_and_read(self, tmp_path: pathlib.Path) -> None:
        """同じ`transcript_path`への2回目の呼び出しは待機と解析を再実行しない。"""
        _transcript_scan._TRANSCRIPT_ENTRIES_CACHE.clear()  # pylint: disable=protected-access
        transcript = _write_transcript(tmp_path, [_user_entry("hello")])
        calls = {"wait": 0, "read": 0}
        original_read = _transcript.read_transcript_entries

        def counting_wait(_path: str) -> None:
            calls["wait"] += 1

        def counting_read(path: str) -> list[dict]:
            calls["read"] += 1
            return original_read(path)

        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(_transcript_scan, "wait_for_end_turn", counting_wait)
            monkeypatch.setattr(_transcript, "read_transcript_entries", counting_read)

            first = read_transcript_entries_cached(str(transcript))
            second = read_transcript_entries_cached(str(transcript))

        assert first == second
        assert calls["wait"] == 1
        assert calls["read"] == 1

    def test_different_path_invalidates_cache(self, tmp_path: pathlib.Path) -> None:
        """異なる`transcript_path`への呼び出しはキャッシュを入れ替えて再解析する。"""
        _transcript_scan._TRANSCRIPT_ENTRIES_CACHE.clear()  # pylint: disable=protected-access
        first_dir, second_dir = tmp_path / "a", tmp_path / "b"
        first_dir.mkdir()
        second_dir.mkdir()
        first_transcript = _write_transcript(first_dir, [_user_entry("first")])
        second_transcript = _write_transcript(second_dir, [_user_entry("second")])

        first_entries = read_transcript_entries_cached(str(first_transcript))
        second_entries = read_transcript_entries_cached(str(second_transcript))

        assert first_entries != second_entries
        assert second_entries == read_transcript_entries_cached(str(second_transcript))

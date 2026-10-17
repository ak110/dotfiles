"""agent-toolkit/agent_toolkit/_hooks/transcript_scan.py のテスト。"""

import collections.abc
import json
import os
import pathlib
import time
import types

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


def _assistant(stop_reason: str, *, sidechain: bool = False) -> dict:
    entry: dict = {"type": "assistant", "message": {"role": "assistant", "stop_reason": stop_reason, "content": []}}
    if sidechain:
        entry["isSidechain"] = True
    return entry


def _jsonl(entries: list[dict | str]) -> str:
    return "".join((item if isinstance(item, str) else json.dumps(item)) + "\n" for item in entries)


class _ClockAndReads:
    """待機のpollごとに記録を書き換える操作を挿入し、全量の読取・デコード・行分割の回数を数える。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, steps: list[collections.abc.Callable[[], None]]) -> None:
        self.reads = 0
        self.polls = 0
        self._steps = steps
        original = _transcript.read_transcript_lines

        def counting_read(path: str) -> list[str] | None:
            self.reads += 1
            return original(path)

        def sleep(_seconds: float) -> None:
            if self.polls < len(self._steps):
                self._steps[self.polls]()
            self.polls += 1

        monkeypatch.setattr(_transcript, "read_transcript_lines", counting_read)
        monkeypatch.setattr(_transcript_scan, "time", types.SimpleNamespace(monotonic=time.monotonic, sleep=sleep))


@pytest.mark.usefixtures("real_end_turn_wait")
def test_wait_for_end_turn_reads_unchanged_transcript_once(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未変更の記録はpollを重ねても1回だけ読み、末尾にsidechainや不正JSONが続いても再変換しない。"""
    transcript = tmp_path / "unchanged.jsonl"
    transcript.write_text(_jsonl([_assistant("tool_use"), _assistant("end_turn", sidechain=True), "{broken"]), encoding="utf-8")
    counter = _ClockAndReads(monkeypatch, [])
    _transcript_scan.wait_for_end_turn(str(transcript), timeout=0.2)
    assert counter.polls >= 2
    assert counter.reads == 1


@pytest.mark.parametrize("change", ["append", "same-size", "replace", "shrink"])
@pytest.mark.usefixtures("real_end_turn_wait")
def test_wait_for_end_turn_rereads_after_change(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    """記録の追記・同サイズ更新・置換・縮小を観測した回は読み直し、end_turnの到着で戻る。"""
    transcript = tmp_path / "changed.jsonl"
    pending = (
        _jsonl([_assistant("tool_use"), _assistant("max_tokens")]) if change == "shrink" else _jsonl([_assistant("tool_use")])
    )
    transcript.write_text(pending, encoding="utf-8")

    def apply() -> None:
        if change == "append":
            with transcript.open("a", encoding="utf-8") as stream:
                stream.write(_jsonl([_assistant("end_turn")]))
        elif change == "same-size":
            # `tool_use`と`end_turn`は同じ長さのため、サイズを変えずに内容だけを更新する。
            transcript.write_text(pending.replace("tool_use", "end_turn"), encoding="utf-8")
            assert transcript.stat().st_size == len(pending)
        elif change == "replace":
            replacement = tmp_path / "replacement.jsonl"
            replacement.write_text(_jsonl([_assistant("end_turn")]), encoding="utf-8")
            os.replace(replacement, transcript)
        else:
            transcript.write_text(_jsonl([_assistant("end_turn")]), encoding="utf-8")

    counter = _ClockAndReads(monkeypatch, [lambda: None, apply])
    _transcript_scan.wait_for_end_turn(str(transcript), timeout=5)
    assert counter.reads == 2
    assert counter.polls == 2


@pytest.mark.usefixtures("real_end_turn_wait")
def test_wait_for_end_turn_late_arrival_and_timeout(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """即着のend_turnは読取1回で戻り、未到着は上限で戻り、読取失敗は待たずに戻る。"""
    arrived = tmp_path / "arrived.jsonl"
    arrived.write_text(_jsonl([_assistant("end_turn")]), encoding="utf-8")
    counter = _ClockAndReads(monkeypatch, [])
    _transcript_scan.wait_for_end_turn(str(arrived), timeout=5)
    assert (counter.reads, counter.polls) == (1, 0)

    missing = tmp_path / "missing.jsonl"
    _transcript_scan.wait_for_end_turn(str(missing), timeout=5)
    assert counter.polls == 0

    invalid = tmp_path / "invalid.jsonl"
    invalid.write_bytes(b"\xff\xfe")
    _transcript_scan.wait_for_end_turn(str(invalid), timeout=5)
    assert counter.polls == 0

    pending = tmp_path / "pending.jsonl"
    pending.write_text(_jsonl([_assistant("tool_use")]), encoding="utf-8")
    started = time.monotonic()
    _transcript_scan.wait_for_end_turn(str(pending), timeout=0.1)
    assert time.monotonic() - started >= 0.1

"""セッション記録の一覧判定と変更監視のテスト。"""

import json
import pathlib
import threading
import time
import typing

from agent_toolkit._atk.serve import session_watch


def _write(path: pathlib.Path, records: typing.Iterable[typing.Any], *, append: bool = False) -> pathlib.Path:
    """JSON Linesの記録を出力する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a" if append else "w", encoding="utf-8") as stream:
        stream.writelines(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    return path


class _Collector:
    """通知をまとめて受け取り、到着を待てるようにする。"""

    def __init__(self) -> None:
        self.flushes: list[tuple[bool, list[tuple[str, str]]]] = []
        self.event = threading.Event()

    def __call__(self, refresh: bool, records: list[tuple[str, str]]) -> None:
        self.flushes.append((refresh, sorted(records)))
        self.event.set()

    def wait(self, predicate: typing.Callable[[], bool], timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()

    @property
    def refreshed(self) -> bool:
        return any(refresh for refresh, _ in self.flushes)

    @property
    def records(self) -> set[tuple[str, str]]:
        return {record for _, records in self.flushes for record in records}


def _tracker(tmp_path: pathlib.Path, collector: _Collector) -> session_watch.RecordChangeTracker:
    return session_watch.RecordChangeTracker(
        [(tmp_path / "claude" / "projects", "claude"), (tmp_path / "codex" / "sessions", "codex")],
        collector,
        debounce_sec=0.01,
    )


def test_summary_fields_distinguishes_no_user_record_from_textless_user_message(tmp_path: pathlib.Path) -> None:
    """発話の有無は発話行の存在で判定し、最初の発話が本文を持たない記録は発話ありとする。"""
    operational = _write(tmp_path / "a.jsonl", [{"type": "mode", "cwd": "/w"}, {"type": "system", "subtype": "x"}])
    textless = _write(tmp_path / "b.jsonl", [{"type": "user", "message": {"content": [{"type": "tool_result"}]}}])
    codex_meta_only = _write(tmp_path / "c.jsonl", [{"type": "session_meta", "payload": {"cwd": "/w"}}])

    assert session_watch.summary_fields(operational, "claude")[3] is False
    assert session_watch.summary_fields(textless, "claude")[1:4:2] == (None, True)
    assert session_watch.summary_fields(codex_meta_only, "codex")[3] is False
    assert session_watch.summary_fields(tmp_path / "missing.jsonl", "claude")[3] is None
    assert session_watch.has_user_message(textless, "claude") is True
    assert session_watch.has_user_message(codex_meta_only, "codex") is False


def test_append_to_listed_record_notifies_only_the_record(tmp_path: pathlib.Path) -> None:
    """一覧に載っている記録への追記は記録1件の更新だけを通知し、一覧の再取得を促さない。"""
    collector = _Collector()
    tracker = _tracker(tmp_path, collector)
    path = _write(tmp_path / "claude" / "projects" / "p" / "s.jsonl", [{"type": "user", "message": {"content": "a"}}])
    tracker.prime(str(path), True)

    tracker.record_changed(path)
    tracker.record_changed(path)

    assert collector.wait(lambda: bool(collector.flushes))
    assert collector.flushes == [(False, [("claude", str(path))])]


def test_first_user_message_in_excluded_record_requests_list_refresh(tmp_path: pathlib.Path) -> None:
    """除外中の記録へ最初のユーザー発話が現れると、一覧の再取得を促す。"""
    collector = _Collector()
    tracker = _tracker(tmp_path, collector)
    path = _write(tmp_path / "codex" / "sessions" / "2026" / "09" / "26" / "rollout-x.jsonl", [{"type": "session_meta"}])
    tracker.prime(str(path), False)

    tracker.record_changed(path)
    assert collector.wait(lambda: bool(collector.flushes))
    assert not collector.refreshed

    _write(path, [{"type": "response_item", "payload": {"role": "user", "content": []}}], append=True)
    tracker.record_changed(path)

    assert collector.wait(lambda: collector.refreshed)


def test_created_record_without_user_message_does_not_request_list_refresh(tmp_path: pathlib.Path) -> None:
    """発話を持たない記録の作成は一覧を変えないため、一覧の再取得を促さない。"""
    collector = _Collector()
    tracker = _tracker(tmp_path, collector)
    empty = _write(tmp_path / "claude" / "projects" / "p" / "e.jsonl", [{"type": "mode"}])
    spoken = _write(tmp_path / "claude" / "projects" / "p" / "u.jsonl", [{"type": "user", "message": {"content": "a"}}])

    tracker.record_changed(empty)
    assert collector.wait(lambda: bool(collector.flushes))
    assert not collector.refreshed
    tracker.record_changed(spoken)

    assert collector.wait(lambda: collector.refreshed)


def test_removal_requests_refresh_only_for_records_that_may_be_listed(tmp_path: pathlib.Path) -> None:
    """除外中と分かっている記録の削除では通知せず、それ以外の削除では一覧の再取得を促す。"""
    collector = _Collector()
    tracker = _tracker(tmp_path, collector)
    excluded = tmp_path / "claude" / "projects" / "p" / "e.jsonl"
    listed = tmp_path / "claude" / "projects" / "p" / "l.jsonl"
    tracker.prime(str(excluded), False)
    tracker.prime(str(listed), True)

    tracker.record_removed(excluded)
    time.sleep(0.1)
    assert not collector.flushes
    tracker.record_removed(listed)

    assert collector.wait(lambda: collector.refreshed)


def test_paths_outside_the_record_roots_are_ignored(tmp_path: pathlib.Path) -> None:
    """記録のrootの外や記録でないファイルの変更は通知しない。"""
    collector = _Collector()
    tracker = _tracker(tmp_path, collector)
    outside = _write(tmp_path / "other" / "s.jsonl", [{"type": "user"}])
    not_rollout = _write(tmp_path / "codex" / "sessions" / "2026" / "note.jsonl", [{"payload": {"role": "user"}}])

    tracker.record_changed(outside)
    tracker.record_changed(not_rollout)
    time.sleep(0.1)

    assert not collector.flushes


def test_record_watch_detects_records_under_roots_created_after_start(tmp_path: pathlib.Path) -> None:
    """起動時に存在しないrootも、作成後の記録の追加と追記を検知する。"""
    collector = _Collector()
    tracker = _tracker(tmp_path, collector)
    (tmp_path / "claude").mkdir()
    watch = session_watch.RecordWatch(tracker)
    watch.start()
    try:
        path = _write(
            tmp_path / "claude" / "projects" / "new-project" / "s.jsonl", [{"type": "user", "message": {"content": "a"}}]
        )
        assert collector.wait(lambda: collector.refreshed)
        collector.flushes.clear()
        tracker.prime(str(path), True)

        _write(path, [{"type": "assistant", "message": {"content": "b"}}], append=True)

        assert collector.wait(lambda: ("claude", str(path)) in collector.records)
        assert not collector.refreshed
    finally:
        watch.stop()

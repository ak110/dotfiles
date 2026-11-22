import json
import os
import pathlib
import threading
import time
import typing

import pytest

from agent_toolkit._atk.serve import session_watch


def _write(path: pathlib.Path, records: typing.Iterable[typing.Any], *, append: bool = False) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a" if append else "w", encoding="utf-8") as stream:
        stream.writelines(json.dumps(record, ensure_ascii=False) + "\n" for record in records)
    return path


class _Collector:
    def __init__(self) -> None:
        self.flushes: list[tuple[bool, list[tuple[str, str]]]] = []
        self.event = threading.Event()

    def __call__(self, refresh: bool, records: list[tuple[str, str]]) -> None:
        self.flushes.append((refresh, sorted(records)))
        self.event.set()

    def wait(self, predicate: typing.Callable[[], bool], timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.event.clear()
            if predicate():
                return True
            self.event.wait(max(0, deadline - time.monotonic()))
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


def test_summary_skips_plugin_origin_and_keeps_human(tmp_path: pathlib.Path) -> None:
    records = [
        {"type": "user", "origin": {"kind": "plugin", "name": "別プラグイン"}, "message": {"content": "/status"}},
        {"type": "user", "origin": {"kind": "human"}, "message": {"content": "/status"}},
    ]
    plugin = _write(tmp_path / "plugin.jsonl", records[:1])
    human = _write(tmp_path / "human.jsonl", records)
    assert session_watch.first_user_message(plugin, "claude") is None
    assert session_watch.first_user_message(human, "claude") == "/status"


def test_summary_fields_distinguishes_no_user_record_from_textless_user_message(tmp_path: pathlib.Path) -> None:
    operational = _write(tmp_path / "a.jsonl", [{"type": "mode", "cwd": "/w"}, {"type": "system", "subtype": "x"}])
    textless = _write(tmp_path / "b.jsonl", [{"type": "user", "message": {"content": [{"type": "tool_result"}]}}])
    codex_meta_only = _write(tmp_path / "c.jsonl", [{"type": "session_meta", "payload": {"cwd": "/w"}}])

    assert session_watch.summary_fields(operational, "claude")[2] is False
    assert session_watch.summary_fields(textless, "claude")[2] is True
    assert session_watch.first_user_message(textless, "claude") is None
    assert session_watch.summary_fields(codex_meta_only, "codex")[2] is False
    assert session_watch.summary_fields(tmp_path / "missing.jsonl", "claude")[2] is None
    assert session_watch.has_user_message(textless, "claude") is True
    assert session_watch.has_user_message(codex_meta_only, "codex") is False


def test_summary_skips_runtime_inserted(tmp_path: pathlib.Path) -> None:
    claude_records = [
        {"type": "user", "isMeta": True, "cwd": "/work", "timestamp": "2026-09-01", "message": {"content": "メタ通知"}},
        {"type": "user", "message": {"content": "Base directory for this skill: /work"}},
        {"type": "user", "message": {"content": "本当の発話\n続き"}},
    ]
    codex_records = [
        {"type": "session_meta", "payload": {"cwd": "/work", "timestamp": "2026-09-01"}},
        {
            "type": "response_item",
            "payload": {"role": "user", "content": [{"text": "You are Codex, an agent based on GPT-6."}]},
        },
        {"type": "response_item", "payload": {"role": "user", "content": [{"text": "<skills_instructions>自動本文"}]}},
        {"type": "response_item", "payload": {"role": "user", "content": [{"text": "Codexの本当の発話"}]}},
    ]
    claude = _write(tmp_path / "claude.jsonl", claude_records)
    codex = _write(tmp_path / "codex.jsonl", codex_records)
    only_inserted = _write(tmp_path / "inserted.jsonl", claude_records[:2])

    assert session_watch.first_user_message(claude, "claude") == "本当の発話"
    assert session_watch.first_user_message(codex, "codex") == "Codexの本当の発話"
    assert session_watch.first_user_message(only_inserted, "claude") is None
    assert session_watch.summary_fields(only_inserted, "claude")[2] is True
    assert session_watch.has_user_message(only_inserted, "claude") is True


def _count_parsed_lines(monkeypatch: typing.Any) -> list[str]:
    parsed: list[str] = []
    original = json.loads

    def counting_loads(text: typing.Any, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        parsed.append(text)
        return original(text, *args, **kwargs)

    monkeypatch.setattr(json, "loads", counting_loads)
    return parsed


_FILLER = {"type": "event_msg", "payload": {"type": "token_count", "info": {"total": 1}}}


def test_scan_parses_only_relevant_lines(tmp_path: pathlib.Path, monkeypatch: typing.Any) -> None:
    """表示できる発話を持たないCodex記録の走査は、`"user"`も`agents_server`も含まない行を解析しない。

    最初のユーザー発話が挿入本文だけの記録（委譲先の記録）は終端まで読むため、全行を解析すると
    初回走査の費用の大半を占める。解析を省いても要約と子セッションIDの値は変わらない。
    """
    records = [
        {"type": "session_meta", "payload": {"cwd": "/work", "timestamp": "2026-09-01T00:00:00Z"}},
        {"type": "response_item", "payload": {"role": "user", "content": [{"text": "<skills_instructions>自動本文"}]}},
        *([_FILLER] * 5),
        {"type": "response_item", "payload": {"type": "function_call", "name": "mcp__agents_server__start", "call_id": "c1"}},
        {
            "type": "response_item",
            "payload": {"type": "function_call_output", "call_id": "c1", "output": '{"session_id": "child-1"}'},
        },
        *([_FILLER] * 5),
    ]
    path = _write(tmp_path / "codex.jsonl", records)
    filler_line = json.dumps(_FILLER, ensure_ascii=False) + "\n"
    parsed = _count_parsed_lines(monkeypatch)

    scan = session_watch.scan_record(path, "codex")

    assert scan.fields == ("/work", "2026-09-01T00:00:00Z", True, None)
    assert scan.delegated_ids == frozenset({"child-1"})
    assert filler_line not in parsed

    assert len(parsed) == 5


def test_scan_record_returns_same_values_as_separate_reads(tmp_path: pathlib.Path) -> None:
    claude = _write(
        tmp_path / "claude.jsonl",
        [
            {"type": "user", "cwd": "/w", "timestamp": "2026-09-01", "message": {"content": "親の発話\n続き"}},
            *([{"type": "assistant", "message": {"content": [{"type": "text", "text": "応答"}]}}] * 3),
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "s1", "name": "mcp__plugin_agent-toolkit_agents_server__start"}]
                },
            },
            {
                "type": "user",
                "toolUseResult": {"session_id": "child-a"},
                "mcpMeta": {"structuredContent": {"excluded_candidates": [{"session_id": "child-b"}]}},
                "message": {"content": [{"type": "tool_result", "tool_use_id": "s1"}]},
            },
        ],
    )
    codex = _write(
        tmp_path / "codex.jsonl",
        [
            {
                "type": "session_meta",
                "payload": {
                    "cwd": "/c",
                    "timestamp": "2026-09-02",
                    "source": {"subagent": {"thread_spawn": {"parent_thread_id": "p"}}},
                },
            },
            {
                "type": "session_meta",
                "payload": {"cwd": "/other", "source": {"subagent": {"thread_spawn": {"parent_thread_id": "q"}}}},
            },
            {"type": "response_item", "payload": {"role": "user", "content": [{"text": "Codexの発話"}]}},
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "McpToolCall",
                        "server": "agents_server",
                        "tool": "start",
                        "result": {"threadId": "child-c"},
                    },
                },
            },
        ],
    )

    claude_scan = session_watch.scan_record(claude, "claude")
    codex_scan = session_watch.scan_record(codex, "codex")

    assert claude_scan.fields == ("/w", "2026-09-01", True, None)
    assert claude_scan.fields == session_watch.summary_fields(claude, "claude")
    assert claude_scan.delegated_ids == frozenset({"child-a", "child-b"})
    assert codex_scan.fields == ("/c", "2026-09-02", True, "p")
    assert codex_scan.fields == session_watch.summary_fields(codex, "codex")
    assert codex_scan.delegated_ids == frozenset({"child-c"})
    missing = session_watch.scan_record(tmp_path / "missing.jsonl", "claude")
    assert (missing.fields[2], missing.delegated_ids) == (None, frozenset())


def test_has_user_message_skips_lines_without_user(tmp_path: pathlib.Path, monkeypatch: typing.Any) -> None:
    without_user = _write(tmp_path / "a.jsonl", [{"type": "session_meta", "payload": {"cwd": "/w"}}, _FILLER, _FILLER])
    with_user = _write(tmp_path / "b.jsonl", [_FILLER, {"type": "response_item", "payload": {"role": "user", "content": []}}])
    parsed = _count_parsed_lines(monkeypatch)

    assert session_watch.has_user_message(without_user, "codex") is False
    assert session_watch.has_user_message(with_user, "codex") is True
    assert len(parsed) == 1


def test_append_to_listed_record_notifies_only_the_record(tmp_path: pathlib.Path) -> None:
    collector = _Collector()
    tracker = _tracker(tmp_path, collector)
    path = _write(tmp_path / "claude" / "projects" / "p" / "s.jsonl", [{"type": "user", "message": {"content": "a"}}])
    tracker.prime(str(path), True)

    tracker.record_changed(path)
    tracker.record_changed(path)

    assert collector.wait(lambda: bool(collector.flushes))
    assert collector.flushes == [(False, [("claude", str(path))])]


def test_first_user_message_in_excluded_record_requests_list_refresh(tmp_path: pathlib.Path) -> None:
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
    collector = _Collector()
    tracker = _tracker(tmp_path, collector)
    excluded = tmp_path / "claude" / "projects" / "p" / "e.jsonl"
    listed = tmp_path / "claude" / "projects" / "p" / "l.jsonl"
    tracker.prime(str(excluded), False)
    tracker.prime(str(listed), True)

    tracker.record_removed(excluded)

    tracker.flush()
    assert not collector.flushes
    tracker.record_removed(listed)

    assert collector.wait(lambda: collector.refreshed)


def test_paths_outside_the_record_roots_are_ignored(tmp_path: pathlib.Path) -> None:
    collector = _Collector()
    tracker = _tracker(tmp_path, collector)
    outside = _write(tmp_path / "other" / "s.jsonl", [{"type": "user"}])
    not_rollout = _write(tmp_path / "codex" / "sessions" / "2026" / "note.jsonl", [{"payload": {"role": "user"}}])

    tracker.record_changed(outside)
    tracker.record_changed(not_rollout)
    tracker.flush()

    assert not collector.flushes


def test_record_watch_detects_records_under_roots_created_after_start(tmp_path: pathlib.Path) -> None:
    collector = _Collector()
    tracker = _tracker(tmp_path, collector)
    (tmp_path / "claude").mkdir()
    watch = session_watch.RecordWatch(tracker)
    watch.start()
    try:
        (tmp_path / "claude" / "projects").mkdir()
        assert collector.wait(lambda: collector.refreshed)
        collector.flushes.clear()

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


def test_record_watch_stops_refreshing_on_unrelated_directories_after_root_creation(tmp_path: pathlib.Path) -> None:
    collector = _Collector()
    tracker = _tracker(tmp_path, collector)
    (tmp_path / "claude").mkdir()
    watch = session_watch.RecordWatch(tracker)
    watch.start()
    try:
        (tmp_path / "claude" / "projects").mkdir()
        assert collector.wait(lambda: collector.refreshed)

        tracker.flush()
        collector.flushes.clear()

        (tmp_path / "claude" / "unrelated").mkdir()

        assert not collector.wait(lambda: collector.refreshed, timeout=0.5)
    finally:
        watch.stop()


def test_record_watch_keeps_shared_ancestor_while_another_root_is_missing(tmp_path: pathlib.Path) -> None:
    collector = _Collector()
    tracker = _tracker(tmp_path, collector)
    watch = session_watch.RecordWatch(tracker)
    watch.start()
    try:
        (tmp_path / "claude").mkdir()
        assert collector.wait(lambda: collector.refreshed)
        collector.flushes.clear()
        (tmp_path / "claude" / "projects").mkdir()
        assert collector.wait(lambda: collector.refreshed)
        tracker.flush()
        collector.flushes.clear()

        (tmp_path / "codex").mkdir()
        assert collector.wait(lambda: collector.refreshed)
        collector.flushes.clear()
        (tmp_path / "codex" / "sessions").mkdir()
        assert collector.wait(lambda: collector.refreshed)
        collector.flushes.clear()

        path = _write(
            tmp_path / "codex" / "sessions" / "2026" / "rollout-x.jsonl",
            [{"type": "response_item", "payload": {"role": "user", "content": []}}],
        )
        assert collector.wait(lambda: collector.refreshed)
        collector.flushes.clear()
        tracker.prime(str(path), True)

        _write(path, [{"type": "response_item", "payload": {"role": "assistant", "content": []}}], append=True)

        assert collector.wait(lambda: ("codex", str(path)) in collector.records)
    finally:
        watch.stop()


def test_summary_index_reuses_saved_results_after_restart(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_watch.RecordSummaryIndex, "_SAVE_INTERVAL_SEC", 0.0)
    cache = tmp_path / "state" / "session-summaries.json"
    stable = _write(tmp_path / "stable.jsonl", [{"type": "user", "cwd": "/a", "message": {"content": "最初"}}])
    changed = _write(tmp_path / "changed.jsonl", [{"type": "user", "cwd": "/b", "message": {"content": "前"}}])
    old_ns = time.time_ns() - 10_000_000_000
    for path in (stable, changed):
        os.utime(path, ns=(old_ns, old_ns))
    first = session_watch.RecordSummaryIndex(cache_path=cache)
    first.begin_scan()
    for path in (stable, changed):
        first.summary(path, "claude")
    first.finish_scan()
    first.display_messages([(stable, "claude"), (changed, "claude")])
    first.begin_scan()
    for path in (stable, changed):
        first.summary(path, "claude")
    first.finish_scan()
    assert cache.is_file()

    _write(changed, [{"type": "user", "cwd": "/c", "message": {"content": "後"}}])
    os.utime(changed, ns=(old_ns + 5_000_000_000, old_ns + 5_000_000_000))
    scanned: list[pathlib.Path] = []
    original = session_watch.scan_record

    def recording(path: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> session_watch.RecordScan:
        scanned.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(session_watch, "scan_record", recording)
    second = session_watch.RecordSummaryIndex(cache_path=cache)
    second.begin_scan()
    stable_fields, _ = second.summary(stable, "claude")
    changed_fields, _ = second.summary(changed, "claude")

    assert scanned == [changed]
    assert stable_fields[0] == "/a"
    assert changed_fields[0] == "/c"
    assert second.display_messages([(stable, "claude")])[str(stable)] == "最初"


def test_summary_index_ignores_broken_cache(tmp_path: pathlib.Path) -> None:
    cache = tmp_path / "session-summaries.json"
    cache.write_text("{壊れた", encoding="utf-8")
    record = _write(tmp_path / "r.jsonl", [{"type": "user", "cwd": "/a", "message": {"content": "x"}}])
    index = session_watch.RecordSummaryIndex(cache_path=cache)
    index.begin_scan()
    fields, _ = index.summary(record, "claude")
    assert fields[0] == "/a"


def test_list_changes_separate_appends_from_changes_to_the_listed_set(tmp_path: pathlib.Path) -> None:
    tracker = _tracker(tmp_path, _Collector())
    listed = _write(tmp_path / "claude" / "projects" / "p" / "l.jsonl", [{"type": "user", "message": {"content": "a"}}])
    excluded = _write(tmp_path / "claude" / "projects" / "p" / "e.jsonl", [{"type": "mode"}])
    tracker.prime(str(listed), True)
    tracker.prime(str(excluded), False)
    assert tracker.take_list_changes() == (True, frozenset())
    assert tracker.take_list_changes() == (False, frozenset())

    tracker.record_changed(listed)
    tracker.record_changed(excluded)
    assert tracker.take_list_changes() == (False, frozenset({str(listed)}))

    _write(excluded, [{"type": "user", "message": {"content": "b"}}], append=True)
    tracker.record_changed(excluded)
    assert tracker.take_list_changes()[0]

    tracker.record_removed(listed)
    assert tracker.take_list_changes()[0]

    tracker.structure_changed()
    assert tracker.take_list_changes()[0]
    tracker.stop()

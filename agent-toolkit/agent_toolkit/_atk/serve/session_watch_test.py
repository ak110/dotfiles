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


def test_summary_skips_runtime_inserted(tmp_path: pathlib.Path) -> None:
    """一覧は自動挿入の記録行を最初の発話に選ばない。実発話が無い記録も残す。"""
    claude_records = [
        {"type": "user", "isMeta": True, "cwd": "/work", "timestamp": "2026-09-01", "message": {"content": "メタ通知"}},
        {"type": "user", "message": {"content": "Base directory for this skill: /work"}},
        {"type": "user", "message": {"content": "本当の発話\n続き"}},
    ]
    codex_records = [
        {"type": "session_meta", "payload": {"cwd": "/work", "timestamp": "2026-09-01"}},
        {"type": "response_item", "payload": {"role": "user", "content": [{"text": "<skills_instructions>自動本文"}]}},
        {"type": "response_item", "payload": {"role": "user", "content": [{"text": "Codexの本当の発話"}]}},
    ]
    claude = _write(tmp_path / "claude.jsonl", claude_records)
    codex = _write(tmp_path / "codex.jsonl", codex_records)
    only_inserted = _write(tmp_path / "inserted.jsonl", claude_records[:2])

    assert session_watch.summary_fields(claude, "claude")[1:4:2] == ("本当の発話", True)
    assert session_watch.summary_fields(codex, "codex")[1:4:2] == ("Codexの本当の発話", True)
    assert session_watch.summary_fields(only_inserted, "claude")[1:4:2] == (None, True)
    assert session_watch.has_user_message(only_inserted, "claude") is True


def _count_parsed_lines(monkeypatch: typing.Any) -> list[str]:
    """JSONとして解析した文字列を順に記録する。"""
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

    assert scan.fields == ("/work", None, "2026-09-01T00:00:00Z", True, None)
    assert scan.delegated_ids == frozenset({"child-1"})
    assert filler_line not in parsed
    # session_meta・挿入本文の発話・起動の呼び出し・起動結果の4行と、起動結果の文字列だけを解析する。
    assert len(parsed) == 5


def test_scan_record_returns_same_values_as_separate_reads(tmp_path: pathlib.Path) -> None:
    """1回の走査で求める要約と子セッションIDは、要約だけの走査と起動結果の判定規則の値と一致する。"""
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

    assert claude_scan.fields == ("/w", "親の発話", "2026-09-01", True, None)
    assert claude_scan.fields == session_watch.summary_fields(claude, "claude")
    assert claude_scan.delegated_ids == frozenset({"child-a", "child-b"})
    assert codex_scan.fields == ("/c", "Codexの発話", "2026-09-02", True, "p")
    assert codex_scan.fields == session_watch.summary_fields(codex, "codex")
    assert codex_scan.delegated_ids == frozenset({"child-c"})
    missing = session_watch.scan_record(tmp_path / "missing.jsonl", "claude")
    assert (missing.fields[3], missing.delegated_ids) == (None, frozenset())


def test_has_user_message_skips_lines_without_user(tmp_path: pathlib.Path, monkeypatch: typing.Any) -> None:
    """`has_user_message`は`"user"`を含まない行を解析せず、判定は変わらない。"""
    without_user = _write(tmp_path / "a.jsonl", [{"type": "session_meta", "payload": {"cwd": "/w"}}, _FILLER, _FILLER])
    with_user = _write(tmp_path / "b.jsonl", [_FILLER, {"type": "response_item", "payload": {"role": "user", "content": []}}])
    parsed = _count_parsed_lines(monkeypatch)

    assert session_watch.has_user_message(without_user, "codex") is False
    assert session_watch.has_user_message(with_user, "codex") is True
    assert len(parsed) == 1


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
    """rootの作成後は、祖先直下の無関係なディレクトリ作成で一覧の再取得を通知しない。"""
    collector = _Collector()
    tracker = _tracker(tmp_path, collector)
    (tmp_path / "claude").mkdir()
    watch = session_watch.RecordWatch(tracker)
    watch.start()
    try:
        (tmp_path / "claude" / "projects").mkdir()
        assert collector.wait(lambda: collector.refreshed)
        # debounce中に保留した通知の送出を待ってから消去する。
        time.sleep(0.2)
        collector.flushes.clear()

        (tmp_path / "claude" / "unrelated").mkdir()

        # debounce（0.01秒）の数十倍待っても再取得の通知は届かない。
        assert not collector.wait(lambda: collector.refreshed, timeout=0.5)
    finally:
        watch.stop()


def test_record_watch_keeps_shared_ancestor_while_another_root_is_missing(tmp_path: pathlib.Path) -> None:
    """同じ祖先を共有する片方のrootだけを作成しても、もう片方のrootの作成と記録の追加を検知する。"""
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
        time.sleep(0.2)
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

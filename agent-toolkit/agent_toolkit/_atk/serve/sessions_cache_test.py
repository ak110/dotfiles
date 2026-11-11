"""serveのsessionsに関するcacheの試験。"""

# pylint: disable=protected-access

import asyncio
import json
import os
import pathlib
import threading
import time
import typing

import pytest

from agent_toolkit._atk.serve import sessions, sessions_local
from agent_toolkit._testing.serve_sessions_support import (
    _age,
    _claude_record,
    _codex_record,
    _context,
    _count_record_opens,
    _load_remote_helper,
    _retained_characters,
    _runner_returning,
    _write,
)


@pytest.mark.parametrize("engine", ["claude", "codex"])
@pytest.mark.parametrize("remote", [False, True])
def test_listing_retains_messages_only_for_current_display(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, engine: str, remote: bool
) -> None:
    """64件の選外本文長を変えても索引・全件結果は増えず、祖先と先頭行全体を返す。"""
    helper = _load_remote_helper()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(sessions_local, "MAX_LIST_ENTRIES", 1)
    monkeypatch.setattr(helper, "MAX_LIST_ENTRIES", 1)
    context = _context(tmp_path)
    index = context.state.record_index
    first_line = "表示" * 32768
    paths: list[pathlib.Path] = []

    def write_corpus(length: int) -> None:
        paths.clear()
        for number in range(64):
            identity = f"00000000-0000-4000-8000-{number:012d}"
            timestamp = f"2026-09-01T00:{number // 60:02d}:{number % 60:02d}Z"
            text = "祖先" if number == 0 else first_line + "\n続き" if number == 63 else "選外" + "x" * length
            if engine == "claude":
                path = tmp_path / "claude/projects/repo" / f"{identity}.jsonl"
                records: list[dict[str, typing.Any]] = [
                    {"type": "user", "cwd": "/work", "timestamp": timestamp, "message": {"content": text}}
                ]
                if number == 0:
                    records.extend(
                        [
                            {
                                "type": "assistant",
                                "message": {
                                    "content": [{"type": "tool_use", "id": "start", "name": "mcp__agents_server__start"}]
                                },
                            },
                            {
                                "type": "user",
                                "toolUseResult": {"session_id": "00000000-0000-4000-8000-000000000063"},
                                "message": {"content": [{"type": "tool_result", "tool_use_id": "start"}]},
                            },
                        ]
                    )
            else:
                path = tmp_path / "codex/sessions/2026/09/01" / f"rollout-{identity}.jsonl"
                payload: dict[str, typing.Any] = {"cwd": "/work", "timestamp": timestamp}
                if number == 63:
                    payload["source"] = {
                        "subagent": {"thread_spawn": {"parent_thread_id": "00000000-0000-4000-8000-000000000000"}}
                    }
                records = [
                    {"type": "session_meta", "payload": payload},
                    {"type": "response_item", "payload": {"role": "user", "content": [{"text": text}]}},
                ]
            paths.append(_write(path, records))
        _age(*paths)

    intermediate: list[int] = []
    original_limit = sessions.session_parents.limit_with_ancestors

    def observe_limit(entries: typing.Any, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        intermediate.append(_retained_characters(entries))
        assert "選外" not in repr(entries)
        return original_limit(entries, *args, **kwargs)

    monkeypatch.setattr(sessions.session_parents, "limit_with_ancestors", observe_limit)

    def listing() -> list[dict[str, typing.Any]]:
        if remote:
            return helper._list_payload(index=index)["entries"]
        return [entry.to_json() for entry in sessions.list_local_sessions(context)]

    retained: list[int] = []
    outputs: list[list[dict[str, typing.Any]]] = []
    for length in (65536, 262144):
        write_corpus(length)
        outputs.append(listing())
        retained.append(_retained_characters(index._records))
        assert "選外" not in repr(index._records)
        assert "選外" not in repr(index._messages)
    assert retained[0] == retained[1]
    assert intermediate[0] == intermediate[1]
    assert outputs[0] == outputs[1]
    assert [entry["first_user_message"] for entry in outputs[1]] == [first_line, "祖先"]
    assert outputs[1][0]["parent_path"] == str(paths[0])

    opened = _count_record_opens(monkeypatch)
    assert listing() == outputs[1]
    assert not opened
    monkeypatch.setattr(helper if remote else sessions_local, "MAX_LIST_ENTRIES", 0)
    assert not listing()
    assert not index._messages


def test_listing_reuses_unchanged_records_beyond_two_thousand(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """記録が2,049件以上でも、2回目の一覧は変更のない記録を開き直さず、変更は次の一覧へ反映する。

    固定上限のキャッシュは巡回する記録数が上限を超えると全件を順に破棄し、毎回の全件読み直しへ戻る。
    再利用が値まで固定すると、追記・新規作成の後も古い`updated_at`・`size`・親子関係が返る。
    """
    project = tmp_path / "claude" / "projects" / "proj"
    records = [
        _write(
            project / f"session-{index:05d}.jsonl",
            [
                {
                    "type": "user",
                    "timestamp": f"2026-08-01T00:{index // 60 % 60:02d}:{index % 60:02d}Z",
                    "message": {"content": f"会話{index}"},
                }
            ],
        )
        for index in range(2049)
    ]
    # 件数上限による切り詰めで外れないよう、親子関係を確かめる記録は開始日時を新しくする。
    parent = _write(
        project / "parent.jsonl", [{"type": "user", "timestamp": "2026-09-02T00:00:00Z", "message": {"content": "親"}}]
    )
    records.append(parent)
    child = _codex_record(tmp_path)
    _age(*records, child)
    context = _context(tmp_path)
    opened = _count_record_opens(monkeypatch)

    first = sessions.list_local_sessions(context)
    first_open_count = len(opened)
    opened.clear()
    second = sessions.list_local_sessions(context)

    assert first_open_count >= 2050
    assert not opened
    assert second == first

    _write(
        parent,
        [
            {"type": "user", "timestamp": "2026-09-02T00:00:00Z", "message": {"content": "親"}},
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "id": "start", "name": "mcp__agents_server__start", "input": {}}]},
            },
            {
                "type": "user",
                "toolUseResult": {"session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
                "message": {"content": [{"type": "tool_result", "tool_use_id": "start", "content": "起動"}]},
            },
        ],
    )
    added = _write(
        project / "added.jsonl", [{"type": "user", "timestamp": "2026-09-03T00:00:00Z", "message": {"content": "追加"}}]
    )
    opened.clear()
    third = {entry.path: entry for entry in sessions.list_local_sessions(context)}

    assert set(opened) == {parent, added}
    assert third[str(parent)].size == parent.stat().st_size
    assert third[str(parent)].updated_at == sessions._isoformat(parent.stat().st_mtime)
    assert third[str(child)].parent_path == str(parent)
    assert str(added) in third


def test_initial_list_scans_metadata_then_reads_displayed_messages(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """初回は全件のメタ情報を1回走査し、表示対象だけ本文を読み直す。"""
    claude = _claude_record(tmp_path)
    child = _codex_record(tmp_path)
    parent = _write(
        tmp_path / "claude" / "projects" / "proj" / "parent.jsonl",
        [
            {"type": "user", "timestamp": "2026-09-02T00:00:00Z", "message": {"content": "親"}},
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "id": "start", "name": "mcp__agents_server__start", "input": {}}]},
            },
            {
                "type": "user",
                "toolUseResult": {"session_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
                "message": {"content": [{"type": "tool_result", "tool_use_id": "start", "content": "起動"}]},
            },
        ],
    )
    context = _context(tmp_path)
    opened = _count_record_opens(monkeypatch)

    entries = {entry.path: entry for entry in sessions.list_local_sessions(context)}

    assert sorted(opened) == sorted([claude, child, parent] * 2)
    assert entries[str(child)].parent_path == str(parent)
    assert entries[str(claude)].first_user_message == "やあ"


@pytest.mark.asyncio
async def test_concurrent_list_requests_share_one_listing(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """同時に届いた一覧の要求は、進行中の1回の取得（ローカルの走査とリモートの取得）の結果を共有する。

    要求ごとに取得すると同じ重い走査がスレッドで重なり、GILを奪い合って他の画面の要求まで遅れる。
    リモートホストへの要求も常駐接続の上で順に処理され、後の要求ほど待たされる。
    """
    _claude_record(tmp_path)
    runner, remote_calls = _runner_returning(
        {"host": "remote", "entries": [{"engine": "claude", "session_id": "r", "path": "/remote/r.jsonl"}]}
    )
    context = _context(tmp_path, remote_hosts=["remote"], ssh_runner=runner)
    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []
    original = sessions.list_local_sessions

    def slow_scan(scan_context: sessions.SessionsContext) -> list[sessions.SessionSummary]:
        calls.append(1)
        started.set()
        release.wait(timeout=10)
        return original(scan_context)

    monkeypatch.setattr(sessions, "list_local_sessions", slow_scan)

    first = asyncio.ensure_future(sessions.list_sessions(context))
    await asyncio.to_thread(started.wait, 10)
    second = asyncio.ensure_future(sessions.list_sessions(context))
    await asyncio.sleep(0)
    release.set()
    (first_entries, _), (second_entries, _) = await asyncio.gather(first, second)

    assert calls == [1]
    assert len(remote_calls) == 1
    assert first_entries == second_entries
    assert {entry.host for entry in first_entries} == {"local-host", "remote"}

    await sessions.list_sessions(context)
    assert calls == [1, 1]
    assert len(remote_calls) == 2


def test_local_listing_reuses_previous_result_until_the_listed_set_may_change(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """変更監視が一覧の集合の変化を観測するまで前回の一覧を再利用し、追記した記録の大きさだけを取り直す。

    全件の走査は記録数千件で1秒前後かかり、一覧の要求ごとに走査すると表示が遅れる。
    監視の取りこぼしに備え、再利用の上限時間を過ぎると全件を背景で走査し直し、要求には前回の一覧を返す。
    """
    context = _context(tmp_path)
    record = _claude_record(tmp_path)
    tracker = sessions.session_watch.RecordChangeTracker(
        [(tmp_path / "claude" / "projects", "claude"), (tmp_path / "codex" / "sessions", "codex")],
        lambda _refresh, _records: None,
        debounce_sec=60.0,
    )
    context.state.record_watch = sessions.session_watch.RecordWatch(tracker)
    scans: list[int] = []
    original = sessions.session_record_format.claude_session_records

    def counting(claude_home: pathlib.Path) -> typing.Any:
        scans.append(1)
        return original(claude_home)

    monkeypatch.setattr(sessions.session_record_format, "claude_session_records", counting)
    try:
        first = sessions.list_local_sessions(context)
        assert [entry.path for entry in first] == [str(record)]
        assert len(scans) == 1

        with record.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "user", "message": {"content": "続き"}}, ensure_ascii=False) + "\n")
        tracker.record_changed(record)
        appended = sessions.list_local_sessions(context)
        assert len(scans) == 1
        assert appended[0].size == record.stat().st_size != first[0].size
        assert appended[0].first_user_message == first[0].first_user_message

        tracker.structure_changed()
        sessions.list_local_sessions(context)
        assert len(scans) == 2

        monkeypatch.setattr(sessions_local, "LOCAL_LIST_REUSE_SEC", 0.0)
        rescan_started = threading.Event()
        rescan_release = threading.Event()

        def held(claude_home: pathlib.Path) -> typing.Any:
            # 背景の走査を止めたまま、要求が走査を待たずに前回の一覧を返すことを確かめる。
            rescan_started.set()
            assert rescan_release.wait(10)
            return counting(claude_home)

        monkeypatch.setattr(sessions.session_record_format, "claude_session_records", held)
        expired = sessions.list_local_sessions(context)
        assert [entry.path for entry in expired] == [str(record)]
        assert rescan_started.wait(10)
        assert len(scans) == 2
        # 背景の走査中に届いた要求は、2つ目の走査を始めずに前回の一覧を返す。
        sessions.list_local_sessions(context)
        rescan_release.set()
        for thread in threading.enumerate():
            if thread.name == "atk-serve-session-rescan":
                thread.join(10)
        assert len(scans) == 3
        assert not context.state.local_rescan_running
    finally:
        tracker.stop()


@pytest.mark.asyncio
async def test_listed_set_change_returns_previous_list_and_refreshes_after_background_scan(
    tmp_path: pathlib.Path,
) -> None:
    """他プロセスが新しい記録を作っても要求の中で全件を走査せず、背景の走査の後に`refresh`で取り直させる。"""
    context = _context(tmp_path)
    record = _claude_record(tmp_path)
    tracker = sessions.session_watch.RecordChangeTracker(
        [(tmp_path / "claude" / "projects", "claude"), (tmp_path / "codex" / "sessions", "codex")],
        lambda _refresh, _records: None,
        debounce_sec=60.0,
    )
    context.state.record_watch = sessions.session_watch.RecordWatch(tracker)
    context.state.watch_loop = asyncio.get_running_loop()
    queue = await sessions.subscribe(context.state)
    try:
        first = await asyncio.to_thread(sessions.list_local_sessions, context)
        assert [entry.path for entry in first] == [str(record)]
        added = _claude_record(tmp_path, project="-home-aki-other")
        tracker.structure_changed()
        previous = await asyncio.to_thread(sessions.list_local_sessions, context)
        assert [entry.path for entry in previous] == [str(record)]
        assert json.loads(await asyncio.wait_for(queue.get(), timeout=10)) == {"type": "refresh"}
        refreshed = await asyncio.to_thread(sessions.list_local_sessions, context)
        assert sorted(entry.path for entry in refreshed) == sorted([str(record), str(added)])
    finally:
        await sessions.unsubscribe(context.state, queue)
        tracker.stop()


def test_first_list_after_restart_reuses_saved_display_messages(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """最初の全件の走査で求めた表示用の本文まで保存し、再起動後の最初の一覧は本文を読み直さない。

    表示用の本文を求める前に索引を保存すると、全件を読み直した直後にserveを止めた場合、次の起動の
    最初の一覧が表示件数分の本文を読み直し、記録数千件で数十秒かかる。
    """
    monkeypatch.setattr(sessions.session_watch.RecordSummaryIndex, "_SAVE_INTERVAL_SEC", 0.0)
    record = _claude_record(tmp_path)
    old_ns = time.time_ns() - 10_000_000_000
    os.utime(record, ns=(old_ns, old_ns))
    first = _context(tmp_path, state_dir=tmp_path / "state")
    assert [entry.first_user_message for entry in sessions.list_local_sessions(first)] == ["やあ"]
    read: list[pathlib.Path] = []
    original = sessions.session_watch.first_user_message

    def recording(path: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> str | None:
        read.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(sessions.session_watch, "first_user_message", recording)
    restarted = _context(tmp_path, state_dir=tmp_path / "state")
    assert [entry.first_user_message for entry in sessions.list_local_sessions(restarted)] == ["やあ"]
    assert not read


@pytest.mark.asyncio
async def test_first_list_after_restart_returns_saved_list_and_refreshes_after_background_scan(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """再起動後の最初の一覧は前回の一覧を要求の中で走査せずに返し、背景の走査の後に`refresh`で取り直させる。

    記録数千件の全件の走査は起動直後の負荷の下で数秒かかり、最初のセッション一覧の表示を待たせるためである。
    """
    record = _claude_record(tmp_path)
    first = _context(tmp_path, state_dir=tmp_path / "state")
    assert [entry.path for entry in sessions.list_local_sessions(first)] == [str(record)]
    added = _claude_record(tmp_path, project="-home-aki-other")
    restarted = _context(tmp_path, state_dir=tmp_path / "state")
    tracker = sessions.session_watch.RecordChangeTracker(
        [(tmp_path / "claude" / "projects", "claude"), (tmp_path / "codex" / "sessions", "codex")],
        lambda _refresh, _records: None,
        debounce_sec=60.0,
    )
    restarted.state.record_watch = sessions.session_watch.RecordWatch(tracker)
    restarted.state.watch_loop = asyncio.get_running_loop()
    scanned = threading.Event()
    release = threading.Event()
    original = sessions_local._scan_local_sessions  # pylint: disable=protected-access

    def gated(*args: typing.Any, **kwargs: typing.Any) -> list[sessions.SessionSummary]:
        scanned.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(sessions_local, "_scan_local_sessions", gated)
    queue = await sessions.subscribe(restarted.state)
    try:
        saved = await asyncio.to_thread(sessions.list_local_sessions, restarted)
        assert [entry.path for entry in saved] == [str(record)]
        assert await asyncio.to_thread(scanned.wait, 10)
        release.set()
        assert json.loads(await asyncio.wait_for(queue.get(), timeout=10)) == {"type": "refresh"}
        refreshed = await asyncio.to_thread(sessions.list_local_sessions, restarted)
        assert sorted(entry.path for entry in refreshed) == sorted([str(record), str(added)])
    finally:
        release.set()
        await sessions.unsubscribe(restarted.state, queue)
        tracker.stop()

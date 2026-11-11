"""serveのsessionsに関するlistingの試験。"""

# pylint: disable=protected-access

import json
import os
import pathlib

import pytest

from agent_toolkit._atk.serve import remote as _atk_serve_remote
from agent_toolkit._atk.serve import sessions, sessions_local
from agent_toolkit._testing import session_tree
from agent_toolkit._testing.serve_sessions_support import _claude_record, _codex_record, _context, _no_user_corpus, _write


def test_listing_identifies_engine_host_cwd_first_message_and_time(tmp_path: pathlib.Path) -> None:
    """一覧は実行系・ホスト・作業ディレクトリ・最初の発話・日時で記録を識別できる。"""
    claude_path = _claude_record(tmp_path)
    codex_path = _codex_record(tmp_path)
    # サブエージェント記録（深さ4）はセッション本体ではないため一覧へ含めない。
    _write(claude_path.with_suffix("") / "subagents" / "agent-1.jsonl", [{"type": "user", "message": {"content": "x"}}])

    entries = sessions.list_local_sessions(_context(tmp_path))

    by_engine = {entry.engine: entry for entry in entries}
    assert set(by_engine) == {"claude", "codex"}
    assert [entry.host for entry in entries] == ["local-host", "local-host"]
    assert by_engine["claude"].cwd == "/home/aki/proj"
    assert by_engine["claude"].first_user_message == "やあ"
    assert by_engine["claude"].session_id == "11111111-2222-3333-4444-555555555555"
    assert by_engine["claude"].path == str(claude_path)
    assert by_engine["codex"].session_id == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert by_engine["codex"].path == str(codex_path)
    assert by_engine["codex"].cwd == "/home/aki/other"
    assert by_engine["codex"].first_user_message == "やあ"
    assert by_engine["claude"].started_at == "2026-09-01T00:00:00Z"
    assert by_engine["codex"].started_at == "2026-09-01T00:00:00Z"
    for entry in entries:
        assert entry.updated_at is not None
        assert entry.size is not None


def test_listing_sorts_by_started_at_with_missing_values_last(tmp_path: pathlib.Path) -> None:
    """一覧は更新日時にかかわらず開始日時の降順とし、開始日時が無い記録を末尾へ置く。"""
    project = tmp_path / "claude" / "projects" / "proj"
    older = _write(
        project / "older.jsonl",
        [{"type": "user", "timestamp": "2026-09-01T00:00:00Z", "message": {"content": "古い記録"}}],
    )
    newer = _write(
        project / "newer.jsonl",
        [{"type": "user", "timestamp": "2026-09-02T00:00:00Z", "message": {"content": "新しい記録"}}],
    )
    missing = _write(project / "missing.jsonl", [{"type": "user", "message": {"content": "日時なし"}}])
    os.utime(older, (1_900_000_000, 1_900_000_000))
    os.utime(newer, (1_700_000_000, 1_700_000_000))
    os.utime(missing, (2_000_000_000, 2_000_000_000))

    entries = sessions.list_local_sessions(_context(tmp_path))

    assert [entry.session_id for entry in entries] == ["newer", "older", "missing"]


def test_listing_links_recorded_delegation_and_metadata_levels(tmp_path: pathlib.Path) -> None:
    """起動結果とmetadataで確定した親・子・孫を一覧へ返し、欠落した記録を除外する。"""
    project = tmp_path / "claude" / "projects" / "repo"
    parent = _write(
        project / "parent.jsonl",
        [
            {
                "type": "assistant",
                "timestamp": "2026-09-01T00:00:00Z",
                "message": {
                    "content": [{"type": "tool_use", "id": "call-1", "name": "mcp__agents_server__start", "input": {}}]
                },
            },
            {
                "type": "user",
                "timestamp": "2026-09-01T00:00:01Z",
                "mcpMeta": {"structuredContent": {"threadId": "child"}},
                "message": {"content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "起動"}]},
            },
        ],
    )
    child = _write(
        project / "child.jsonl", [{"type": "user", "timestamp": "2026-09-01T00:00:02Z", "message": {"content": "子"}}]
    )
    grandchild = _write(
        child.with_suffix("") / "subagents" / "agent-grandchild.jsonl",
        [{"type": "user", "timestamp": "2026-09-01T00:00:03Z", "message": {"content": "孫"}}],
    )
    grandchild.with_suffix(".meta.json").write_text(json.dumps({"spawnDepth": 1}), encoding="utf-8")
    _write(project / "unrelated.jsonl", [{"type": "user", "timestamp": "2026-09-01T00:00:04Z", "message": {"content": "別件"}}])

    entries = {entry.path: entry for entry in sessions.list_local_sessions(_context(tmp_path))}

    assert entries[str(child)].parent_path == str(parent)
    assert entries[str(grandchild)].parent_path == str(child)
    assert entries[str(project / "unrelated.jsonl")].parent_path is None
    grandchild.unlink()
    entries = {entry.path: entry for entry in sessions.list_local_sessions(_context(tmp_path))}
    assert str(grandchild) not in entries


@pytest.mark.parametrize("tool", ["start", "start_explore"])
def test_listing_links_codex_start_result_to_claude_child(tmp_path: pathlib.Path, tool: str) -> None:
    """Codex起動結果が指す実在するClaudeセッションだけを結ぶ。"""
    parent = _codex_record(tmp_path)
    records = [json.loads(line) for line in parent.read_text(encoding="utf-8").splitlines()]
    records.extend(
        [
            {
                "type": "response_item",
                "payload": {"type": "custom_tool_call", "call_id": "delegate", "name": f"mcp__agents_server__{tool}"},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "delegate",
                    "output": {"session_id": "11111111-2222-3333-4444-555555555555"},
                },
            },
        ]
    )
    _write(parent, records)
    child = _claude_record(tmp_path)

    entries = {entry.path: entry for entry in sessions.list_local_sessions(_context(tmp_path))}

    assert entries[str(child)].parent_path == str(parent)


@pytest.mark.parametrize("tool", ["start", "start_explore"])
def test_listing_links_codex_mcp_tool_call_and_excluded_candidate(tmp_path: pathlib.Path, tool: str) -> None:
    parent = _codex_record(tmp_path)
    chosen = _claude_record(tmp_path)
    excluded_id = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
    excluded = _write(
        tmp_path / "codex" / "sessions" / "2026" / "09" / "01" / f"rollout-excluded-{excluded_id}.jsonl",
        [
            {"type": "session_meta", "payload": {"cwd": "/home/aki/other"}},
            {"type": "response_item", "payload": {"role": "user", "content": [{"text": "候補"}]}},
        ],
    )
    records = [json.loads(line) for line in parent.read_text(encoding="utf-8").splitlines()]
    records.append(
        {
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "McpToolCall",
                    "server": "agents_server",
                    "tool": tool,
                    "result": json.dumps(
                        {
                            "structuredContent": {
                                "session_id": "11111111-2222-3333-4444-555555555555",
                                "excluded_candidates": [{"session_id": excluded_id}],
                            }
                        }
                    ),
                },
            },
        }
    )
    _write(parent, records)

    entries = {entry.path: entry for entry in sessions.list_local_sessions(_context(tmp_path))}

    assert entries[str(chosen)].parent_path == str(parent)
    assert entries[str(excluded)].parent_path == str(parent)


def test_listing_links_claude_start_result_and_excluded_candidate(tmp_path: pathlib.Path) -> None:
    parent = _claude_record(tmp_path)
    project = parent.parent
    chosen = _write(project / "chosen.jsonl", [{"type": "user", "message": {"content": "採用候補"}}])
    excluded = _write(project / "excluded.jsonl", [{"type": "user", "message": {"content": "除外候補"}}])
    records = [json.loads(line) for line in parent.read_text(encoding="utf-8").splitlines()]
    records.extend(
        [
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "start-call", "name": "mcp__agents_server__start", "input": {}}]
                },
            },
            {
                "type": "user",
                "toolUseResult": {
                    "structuredContent": {
                        "session_id": "chosen",
                        "excluded_candidates": [{"session_id": "excluded"}],
                    }
                },
                "message": {"content": [{"type": "tool_result", "tool_use_id": "start-call", "content": "起動"}]},
            },
        ]
    )
    _write(parent, records)

    entries = {entry.path: entry for entry in sessions.list_local_sessions(_context(tmp_path))}

    assert entries[str(chosen)].parent_path == str(parent)
    assert entries[str(excluded)].parent_path == str(parent)


def test_listing_uses_only_the_first_line_of_the_first_user_message(tmp_path: pathlib.Path) -> None:
    """一覧は最初のユーザーレコードの先頭1行だけを識別情報にする。"""
    path = _write(
        tmp_path / "claude" / "projects" / "encoded-path" / "first.jsonl",
        [
            {"type": "user", "cwd": "/actual/path", "message": {"content": "1行目\n2行目"}},
            {"type": "user", "message": {"content": "後続の発話"}},
        ],
    )

    entries = sessions.list_local_sessions(_context(tmp_path))

    assert len(entries) == 1
    entry = entries[0]
    assert entry.path == str(path)
    assert entry.cwd == "/actual/path"
    assert entry.first_user_message == "1行目"


def test_listing_keeps_missing_first_user_message_as_none(tmp_path: pathlib.Path) -> None:
    """最初のユーザーレコードに本文が無い場合は後続発話で補わない。"""
    _write(
        tmp_path / "claude" / "projects" / "encoded-path" / "missing.jsonl",
        [
            {"type": "user", "cwd": "/actual/path"},
            {"type": "user", "message": {"content": "後続の発話"}},
        ],
    )

    entries = sessions.list_local_sessions(_context(tmp_path))

    assert len(entries) == 1
    assert entries[0].first_user_message is None


def test_listing_restores_only_ancestors_dropped_by_the_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """件数上限を超えるのは戻した祖先の件数に限り、親を持たない古い記録は戻さない。"""
    state_dir = tmp_path / "state" / "agent-toolkit"
    tree = session_tree.write_session_tree(tmp_path / ".claude", tmp_path / "codex", state_dir)
    unrelated = _write(
        tmp_path / ".claude" / "projects" / "repo" / "old-unrelated.jsonl",
        [{"type": "user", "timestamp": "2026-07-01T00:00:00Z", "message": {"content": "古い無関係の記録"}}],
    )
    monkeypatch.setattr(sessions_local, "MAX_LIST_ENTRIES", session_tree.TOTAL_ENTRIES - 1)
    context = sessions.create_context(
        hostname="local-host", claude_home=tmp_path / ".claude", codex_home=tmp_path / "codex", state_dir=state_dir
    )

    paths = [entry.path for entry in sessions.list_local_sessions(context)]

    assert len(paths) == session_tree.TOTAL_ENTRIES
    assert str(tree.paths[session_tree.OLD_PARENT_ID]) in paths
    assert str(unrelated) not in paths


def test_records_without_user_message_are_excluded_until_the_first_message(tmp_path: pathlib.Path) -> None:
    """ユーザー発話の記録行を持たない記録は一覧から外し、発話が追記されると一覧へ載せる。"""
    corpus = _no_user_corpus(tmp_path)
    context = sessions.create_context(
        hostname="local-host", claude_home=tmp_path / ".claude", codex_home=tmp_path / "codex-home"
    )

    listed = {entry.path for entry in sessions.list_local_sessions(context)}
    assert listed == {str(corpus["spoken"]), str(corpus["codex_spoken"])}

    _write(corpus["silent"], [{"type": "permission-mode"}, {"type": "user", "message": {"content": "今から"}}])
    _write(corpus["subagent"], [{"type": "user", "message": {"content": "指示"}}])
    listed = {entry.path for entry in sessions.list_local_sessions(context)}
    assert {str(corpus["silent"]), str(corpus["subagent"])} <= listed


def test_exclusion_happens_before_the_listing_limit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """除外した記録で件数上限の枠を消費しない。"""
    projects = tmp_path / "claude" / "projects" / "repo"
    for index in range(3):
        _write(projects / f"silent-{index}.jsonl", [{"type": "mode", "timestamp": f"2026-09-26T00:00:0{index}Z"}])
    spoken = _write(projects / "spoken.jsonl", [{"type": "user", "timestamp": "2026-01-01T00:00:00Z"}])
    monkeypatch.setattr(sessions_local, "MAX_LIST_ENTRIES", 1)

    assert [entry.path for entry in sessions.list_local_sessions(_context(tmp_path))] == [str(spoken)]


def test_list_local_sessions_stops_on_shutdown_request(tmp_path: pathlib.Path) -> None:
    """停止要求が設定されると、スレッドで動くローカル走査は記録を読み進めずに打ち切る。

    要求をキャンセルしてもスレッドは止まらず、`asyncio.run`の終了処理がスレッドの終了を待つため、
    記録の件数に比例して停止を待たせる。
    """
    _claude_record(tmp_path)
    context = _context(tmp_path)
    assert sessions.list_local_sessions(context)

    context.state.stop_requested.set()

    with pytest.raises(_atk_serve_remote.ServeStopping):
        sessions.list_local_sessions(context)


def test_default_record_homes_follow_claude_config_dir_and_codex_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """記録の保存先を指定しない場合、`CLAUDE_CONFIG_DIR`と`CODEX_HOME`が示すディレクトリを使う。"""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    context = sessions.create_context(hostname="local", state_dir=tmp_path / "state")
    assert context.claude_home == tmp_path / "claude-config"
    assert context.codex_home == tmp_path / "codex-home"

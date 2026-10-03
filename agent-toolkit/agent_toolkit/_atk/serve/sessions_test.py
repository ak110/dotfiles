"""`atk serve`のセッション画面の処理のテスト。"""

# pylint: disable=protected-access

import asyncio
import base64
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import threading
import typing

import pytest

from agent_toolkit._atk.serve import remote as _atk_serve_remote
from agent_toolkit._atk.serve import sessions
from agent_toolkit._testing import session_tree


def _write(path: pathlib.Path, records: typing.Iterable[typing.Any]) -> pathlib.Path:
    """JSON Linesの記録を出力する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    return path


def _context(tmp_path: pathlib.Path, **kwargs: typing.Any) -> sessions.SessionsContext:
    """外部へ接続しないセッション画面のコンテキストを生成する。"""
    return sessions.create_context(
        hostname="local-host",
        claude_home=tmp_path / "claude",
        codex_home=tmp_path / "codex",
        **kwargs,
    )


def _claude_record(tmp_path: pathlib.Path, project: str = "-home-aki-proj") -> pathlib.Path:
    """Claude Codeのセッション記録1件を作成する。"""
    return _write(
        tmp_path / "claude" / "projects" / project / "11111111-2222-3333-4444-555555555555.jsonl",
        [
            {"type": "user", "timestamp": "2026-09-01T00:00:00Z", "cwd": "/home/aki/proj", "message": {"content": "やあ"}},
            {
                "type": "assistant",
                "timestamp": "2026-09-01T00:00:01Z",
                "message": {
                    "usage": {"input_tokens": 10, "output_tokens": 3},
                    "content": [
                        {"type": "thinking", "thinking": "考える"},
                        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                        {"type": "text", "text": "できました"},
                    ],
                },
            },
            {
                "type": "user",
                "timestamp": "2026-09-01T00:00:02Z",
                "message": {"content": [{"type": "tool_result", "content": "a.txt", "is_error": False}]},
            },
            {
                "type": "system",
                "subtype": "compact_boundary",
                "timestamp": "2026-09-01T00:00:03Z",
                "compactMetadata": {"trigger": "auto"},
            },
        ],
    )


def _codex_record(tmp_path: pathlib.Path) -> pathlib.Path:
    """Codexのロールアウト記録1件を作成する。"""
    return _write(
        tmp_path
        / "codex"
        / "sessions"
        / "2026"
        / "09"
        / "01"
        / "rollout-2026-09-01T00-00-00-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl",
        [
            {
                "type": "session_meta",
                "timestamp": "2026-09-01T00:00:00Z",
                "payload": {"cwd": "/home/aki/other", "timestamp": "2026-09-01T00:00:00Z"},
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-01T00:00:01Z",
                "payload": {"type": "message", "role": "user", "content": [{"text": "やあ"}]},
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-01T00:00:02Z",
                "payload": {"type": "reasoning", "summary": [{"text": "考える"}]},
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-01T00:00:03Z",
                "payload": {"type": "function_call", "name": "shell", "arguments": '{"cmd":"ls"}'},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-09-01T00:00:04Z",
                "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 7, "output_tokens": 2}}},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-09-01T00:00:05Z",
                "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 21, "output_tokens": 5}}},
            },
        ],
    )


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


# 統合前の起動ツール名を持つ保存済みの記録と、統合後の`start`の記録の双方を読む。
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


def test_detail_renders_claude_records_in_order(tmp_path: pathlib.Path) -> None:
    """Claude Codeの詳細は思考・ツール呼び出しと結果・圧縮境界・使用量を時系列に返す。"""
    path = _claude_record(tmp_path)
    meta = path.with_suffix("") / "subagents" / "agent-1.meta.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(
        json.dumps({"agentType": "Explore", "description": "探索", "spawnDepth": 1, "parentAgentId": None, "model": "opus"}),
        encoding="utf-8",
    )

    detail = sessions.read_local_detail(_context(tmp_path), "claude", str(path))

    assert [event["kind"] for event in detail["events"]] == [
        "user",
        "thinking",
        "tool_call",
        "assistant",
        "tool_result",
        "compact_boundary",
    ]
    assert detail["events"][2]["name"] == "Bash"
    assert detail["events"][5]["detail"] == {"trigger": "auto"}
    assert detail["usage"] == {"input_tokens": 10, "output_tokens": 3}
    # 記録本体が残っていないサブエージェントは、開く対象が無いことを`path`のnullで表す。
    assert detail["subagents"] == [
        {
            "agent_id": "agent-1",
            "agent_type": "Explore",
            "description": "探索",
            "spawn_depth": 1,
            "parent_agent_id": None,
            "model": "opus",
            "path": None,
        }
    ]
    assert detail["project"] == "/home/aki/proj"
    assert detail["started_at"] == "2026-09-01T00:00:00Z"


def test_subagent_records_are_reachable_from_the_parent_detail(tmp_path: pathlib.Path) -> None:
    """記録本体があるサブエージェントは絶対パスを返し、その詳細から下位の階層も辿れる。"""
    path = _claude_record(tmp_path)
    subagents = path.with_suffix("") / "subagents"
    for agent_id, depth in (("agent-parent", 1), ("agent-child", 2)):
        meta = subagents / f"{agent_id}.meta.json"
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(json.dumps({"agentType": "Explore", "spawnDepth": depth}), encoding="utf-8")
    parent_record = _write(subagents / "agent-parent.jsonl", [{"type": "user", "message": {"content": "親の発話"}}])
    nested = parent_record.with_suffix("") / "subagents" / "agent-nested.meta.json"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text(json.dumps({"agentType": "Plan", "spawnDepth": 2}), encoding="utf-8")
    context = _context(tmp_path)

    detail = sessions.read_local_detail(context, "claude", str(path))

    by_id = {item["agent_id"]: item for item in detail["subagents"]}
    assert by_id["agent-parent"]["path"] == str(parent_record)
    # 記録本体が無い項目は開けないため、深さだけを返す。
    assert by_id["agent-child"]["path"] is None
    assert by_id["agent-child"]["spawn_depth"] == 2

    nested_detail = sessions.read_local_detail(context, "claude", by_id["agent-parent"]["path"])

    assert [event["text"] for event in nested_detail["events"]] == ["親の発話"]
    assert [item["agent_id"] for item in nested_detail["subagents"]] == ["agent-nested"]


def test_detail_renders_codex_records_in_order(tmp_path: pathlib.Path) -> None:
    """Codexの詳細は発話・思考・ツール呼び出しを時系列に返し、使用量へ累計値を反映する。"""
    path = _codex_record(tmp_path)

    detail = sessions.read_local_detail(_context(tmp_path), "codex", str(path))

    assert [event["kind"] for event in detail["events"]] == ["user", "thinking", "tool_call"]
    assert detail["events"][2]["name"] == "shell"
    # Codexは累計値を通知するため、加算せず最後の観測値で置き換える。
    assert detail["usage"] == {"input_tokens": 21, "output_tokens": 5}
    assert detail["project"] == "/home/aki/other"


def test_codex_roles_and_compaction_are_preserved(tmp_path: pathlib.Path) -> None:
    """Codexの各発話ロールと圧縮境界を、表示用の種別と付随情報へ変換する。"""
    path = _write(
        tmp_path / "codex" / "sessions" / "2026" / "09" / "01" / "rollout-roles.jsonl",
        [
            {"type": "response_item", "payload": {"role": "user", "content": [{"text": "ユーザー"}]}},
            {"type": "response_item", "payload": {"role": "developer", "content": [{"text": "開発者"}]}},
            {"type": "response_item", "payload": {"role": "assistant", "content": [{"text": "応答"}]}},
            {"type": "response_item", "payload": {"role": "unknown", "content": [{"text": "不明"}]}},
            {"type": "response_item", "payload": {"content": [{"text": "欠落"}]}},
            {
                "type": "compacted",
                "timestamp": "2026-09-01T00:00:06Z",
                "payload": {"message": [{"text": "圧縮しました"}], "window_number": 2},
            },
            {"type": "compacted", "payload": {"message": "", "window_number": 3}},
        ],
    )

    detail = sessions.read_local_detail(_context(tmp_path), "codex", str(path))

    assert [event["kind"] for event in detail["events"]] == [
        "user",
        "developer",
        "assistant",
        "assistant",
        "assistant",
        "compact_boundary",
        "compact_boundary",
    ]
    assert detail["events"][5]["text"] == "圧縮しました"
    assert detail["events"][5]["timestamp"] == "2026-09-01T00:00:06Z"
    assert detail["events"][5]["detail"] == {"window_number": 2}
    assert detail["events"][6]["text"] is None
    assert detail["events"][6]["detail"] == {"window_number": 3}


def test_runtime_inserted_events(tmp_path: pathlib.Path) -> None:
    """挿入本文だけを表示種別へ分け、ユーザーとツールのイベントを保つ。"""
    claude = _write(
        tmp_path / "claude" / "projects" / "p" / "claude.jsonl",
        [
            {"type": "user", "isMeta": True, "message": {"content": "構造標識の本文"}},
            {"type": "user", "message": {"content": [{"type": "text", "text": "  Base directory for this skill: /p"}]}},
            {"type": "user", "message": {"content": [{"type": "text", "text": "実際の発話"}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "<skills_instructions>"}]}},
        ],
    )
    codex = _write(
        tmp_path / "codex" / "sessions" / "2026" / "09" / "01" / "rollout-injected.jsonl",
        [
            {"type": "response_item", "payload": {"role": "user", "content": [{"text": "<multi_agent_mode>自動本文"}]}},
            {
                "type": "response_item",
                "payload": {"role": "developer", "content": [{"text": "<permissions instructions>自動本文"}]},
            },
            {"type": "response_item", "payload": {"role": "developer", "content": [{"text": "通常の開発者本文"}]}},
            {"type": "response_item", "payload": {"role": "assistant", "content": [{"text": "<multi_agent_mode>引用"}]}},
            {"type": "response_item", "payload": {"type": "function_call_output", "output": "<multi_agent_mode>結果"}},
        ],
    )

    claude_events = sessions.read_local_detail(_context(tmp_path), "claude", str(claude))["events"]
    codex_events = sessions.read_local_detail(_context(tmp_path), "codex", str(codex))["events"]

    assert [event["kind"] for event in claude_events] == ["injected", "injected", "user", "tool_result"]
    assert [event["kind"] for event in codex_events] == ["injected", "injected", "developer", "assistant", "tool_result"]
    assert claude_events[1]["text"] == "  Base directory for this skill: /p"


def test_absent_fields_are_reported_as_unavailable(tmp_path: pathlib.Path) -> None:
    """記録が持たない情報は0や空文字列で補わず、取得不能として返す。"""
    path = _write(
        tmp_path / "claude" / "projects" / "proj" / "abc.jsonl",
        [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "やあ"}]}},
            # 本文を持たない形式のユーザー発話。発話は持つため一覧へ載り、最初の発話は取得不能となる。
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "結果"}]}},
        ],
    )

    detail = sessions.read_local_detail(_context(tmp_path), "claude", str(path))
    summaries = sessions.list_local_sessions(_context(tmp_path))

    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.cwd is None
    assert summary.first_user_message is None
    assert detail["started_at"] is None
    assert detail["usage"] == {"input_tokens": None, "output_tokens": None}
    # サブエージェント記録が無い場合は空配列ではなくnullとし、「0件」と区別する。
    assert detail["subagents"] is None
    # ローカルの記録は保存先を直接読むため、有無の判定自体は常に成立する。
    assert detail["subagents_unavailable"] is False
    assert detail["events"][0]["timestamp"] is None
    assert detail["events"][0]["name"] is None
    assert detail["events"][0]["usage"] is None


def test_broken_record_is_reported_per_entry(tmp_path: pathlib.Path) -> None:
    """書き込み途中の行があっても他の行を失わせず、該当件数を項目単位で返す。"""
    path = tmp_path / "claude" / "projects" / "proj" / "abc.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"type":"user","message":{"content":"1件目"}}\n'
        '{"type":"user","message":{"content":"途中で途絶\n'
        "[1, 2]\n"
        '{"type":"user","message":{"content":"3件目"}}\n',
        encoding="utf-8",
    )

    detail = sessions.read_local_detail(_context(tmp_path), "claude", str(path))

    assert [event["text"] for event in detail["events"]] == ["1件目", "3件目"]
    assert detail["broken_lines"] == 2
    # 破損した1件が一覧全体を失敗させない。
    assert [entry.session_id for entry in sessions.list_local_sessions(_context(tmp_path))] == ["abc"]


def test_local_record_path_outside_the_roots_is_rejected(tmp_path: pathlib.Path) -> None:
    """保存先の外を指す読み取り要求を拒否する。"""
    context = _context(tmp_path)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n", encoding="utf-8")

    assert not sessions.is_local_record_path(context, str(outside))
    assert not sessions.is_local_record_path(context, str(tmp_path / "claude" / "projects" / ".." / "x.jsonl"))
    assert not sessions.is_local_record_path(context, str(tmp_path / "claude" / "projects" / "p" / "x.txt"))
    with pytest.raises(sessions.SessionNotFoundError):
        sessions.read_local_detail(context, "claude", str(outside))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/home/aki/.claude/projects/p/a.jsonl", True),
        ("/home/aki/.claude/projects/../../etc/a.jsonl", False),
        ("/home/aki/.claude/projects/p/a.txt", False),
        ("", False),
        ("C:\\Users\\aki\\a.jsonl", True),
        ("C:\\Users\\aki\\..\\outside.jsonl", False),
        ("C:\\Users\\aki\\a.txt", False),
        ("relative\\a.jsonl", False),
    ],
)
def test_remote_record_path_is_validated_before_ssh(raw: str, expected: bool) -> None:
    """上位ディレクトリ参照と対象外の接尾辞は、SSH呼び出しの前に拒否する。"""
    assert sessions.is_safe_remote_record_path(raw) is expected


class _FakeRpcClient:
    """常駐RPCの接続状態と応答を差し替えるスタブ。"""

    def __init__(self, *, connected: bool, response: typing.Any) -> None:
        self._connected = connected
        self._response = response
        self.calls: list[tuple[str, dict[str, typing.Any]]] = []

    def is_connected(self) -> bool:
        """接続状態を返す。"""
        return self._connected

    async def request(self, op: str, args: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """RPC応答を返すか、設定された例外を送出する。"""
        self.calls.append((op, args))
        if isinstance(self._response, Exception):
            raise self._response
        assert isinstance(self._response, dict)
        return self._response


def _runner_returning(payload: typing.Any) -> tuple[typing.Any, list[tuple[str, str, list[str]]]]:
    """単発SSHの呼び出しを記録するrunnerと、その記録先を返す。"""
    calls: list[tuple[str, str, list[str]]] = []

    async def runner(host: str, op: str, args: list[str]) -> str:
        calls.append((host, op, args))
        if isinstance(payload, Exception):
            raise payload
        return json.dumps(payload)

    return runner, calls


@pytest.mark.asyncio
async def test_remote_entries_are_merged_into_the_listing(tmp_path: pathlib.Path) -> None:
    """設定済みリモートホストの記録を一覧へ含める。"""
    _claude_record(tmp_path)
    runner, calls = _runner_returning(
        {
            "ok": True,
            "entries": [
                {
                    "engine": "codex",
                    "cwd": "/srv/work",
                    "first_user_message": "リモートの最初の発話",
                    "session_id": "remote-session",
                    "path": "/home/aki/.codex/sessions/2026/09/01/rollout-x.jsonl",
                    "started_at": "2026-09-02T00:00:00Z",
                    "updated_at": 1_800_000_000,
                    "size": 12,
                }
            ],
        }
    )
    context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)

    entries, warnings = await sessions.list_sessions(context)

    assert warnings == []
    assert [entry.session_id for entry in entries] == [
        "remote-session",
        "11111111-2222-3333-4444-555555555555",
    ]
    assert {(entry.host, entry.session_id) for entry in entries} == {
        ("local-host", "11111111-2222-3333-4444-555555555555"),
        ("remote-host", "remote-session"),
    }
    remote = next(entry for entry in entries if entry.host == "remote-host")
    assert remote.cwd == "/srv/work"
    assert remote.first_user_message == "リモートの最初の発話"
    assert remote.started_at == "2026-09-02T00:00:00Z"
    assert calls == [("remote-host", "list", [])]


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


def test_remote_helper_listing_returns_cwd_and_first_user_message(tmp_path: pathlib.Path) -> None:
    """リモート補助の一覧もローカル側と同じ識別項目を返す。"""
    claude = _write(
        tmp_path / ".claude" / "projects" / "encoded-path" / "remote-claude.jsonl",
        [
            {
                "type": "user",
                "timestamp": "2026-09-05T00:00:00Z",
                "cwd": "/remote/claude",
                "message": {"content": "Claude先頭\n続き"},
            }
        ],
    )
    codex_home = tmp_path / "codex-home"
    codex = _write(
        codex_home / "sessions" / "2026" / "09" / "05" / "rollout-remote-codex.jsonl",
        [
            {
                "type": "session_meta",
                "payload": {"cwd": "/remote/codex", "timestamp": "2026-09-06T00:00:00Z"},
            },
            {"type": "response_item", "payload": {"role": "user", "content": [{"text": "Codex先頭\n続き"}]}},
        ],
    )
    os.utime(claude, (1_900_000_000, 1_900_000_000))
    os.utime(codex, (1_700_000_000, 1_700_000_000))
    environment = os.environ.copy()
    environment.update({"HOME": str(tmp_path), "CODEX_HOME": str(codex_home)})

    result = subprocess.run(
        [
            sys.executable,
            str(pathlib.Path(__file__).resolve().parents[3] / "scripts" / "atk_serve_sessions_remote_helper.py"),
            "list",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    entries = {entry["path"]: entry for entry in json.loads(result.stdout)["entries"]}

    assert entries[str(claude)]["cwd"] == "/remote/claude"
    assert entries[str(claude)]["first_user_message"] == "Claude先頭"
    assert entries[str(claude)]["started_at"] == "2026-09-05T00:00:00Z"
    assert entries[str(codex)]["cwd"] == "/remote/codex"
    assert entries[str(codex)]["first_user_message"] == "Codex先頭"
    assert entries[str(codex)]["started_at"] == "2026-09-06T00:00:00Z"
    assert [entry["path"] for entry in json.loads(result.stdout)["entries"]] == [str(codex), str(claude)]
    assert all("project" not in entry for entry in entries.values())


def test_remote_helper_listing_links_recorded_delegation(tmp_path: pathlib.Path) -> None:
    """リモート補助も起動結果に現れる実在する子を親へ結ぶ。"""
    project = tmp_path / ".claude" / "projects" / "repo"
    parent = _write(
        project / "parent.jsonl",
        [
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "mcp__agents_server__start", "id": "call"}]},
            },
            {
                "type": "user",
                "toolUseResult": {"session_id": "child"},
                "message": {"content": [{"type": "tool_result", "tool_use_id": "call", "content": "起動"}]},
            },
        ],
    )
    child = _write(project / "child.jsonl", [{"type": "user", "message": {"content": "子"}}])
    environment = os.environ.copy()
    environment.update({"HOME": str(tmp_path), "CODEX_HOME": str(tmp_path / "codex")})

    result = subprocess.run(
        [
            sys.executable,
            str(pathlib.Path(__file__).resolve().parents[3] / "scripts" / "atk_serve_sessions_remote_helper.py"),
            "list",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    entries = {entry["path"]: entry for entry in json.loads(result.stdout)["entries"]}

    assert entries[str(child)]["parent_path"] == str(parent)


def _load_remote_helper() -> typing.Any:
    """リモート補助を同じプロセスへ読み込む。件数上限を差し替えて`_list_payload`を呼ぶテストが使う。"""
    helper_path = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "atk_serve_sessions_remote_helper.py"
    spec = importlib.util.spec_from_file_location("_atk_serve_sessions_remote_helper_tree", helper_path)
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper


def test_local_and_remote_listing_link_children_from_every_launch_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """ローカルの一覧とリモート補助が、親子の全情報源と件数上限で外れた親について同じ親子を返す。

    実行系の異なる親子、Codexの親thread、登録簿の委譲元だけで親が決まる委譲先が親を持たないと、
    画面の第1階層へ並ぶ。親の記録に起動結果がある委譲先では、登録簿の委譲元より起動結果を優先する。
    件数上限で外れた親を戻さないと、その子が親を持たない項目として並ぶ。
    """
    state_dir = tmp_path / "state" / "agent-toolkit"
    tree = session_tree.write_session_tree(tmp_path / ".claude", tmp_path / "codex", state_dir)
    monkeypatch.setattr(sessions, "MAX_LIST_ENTRIES", session_tree.TOTAL_ENTRIES - 1)
    context = sessions.create_context(
        hostname="local-host", claude_home=tmp_path / ".claude", codex_home=tmp_path / "codex", state_dir=state_dir
    )

    local = sessions.list_local_sessions(context)

    local_parents = {entry.path: entry.parent_path for entry in local if entry.parent_path is not None}
    assert local_parents == tree.expected_parents
    assert len(local) == session_tree.TOTAL_ENTRIES
    assert str(tree.paths[session_tree.OLD_PARENT_ID]) in {entry.path for entry in local}

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_dir.parent))
    helper = _load_remote_helper()
    monkeypatch.setattr(helper, "MAX_LIST_ENTRIES", session_tree.TOTAL_ENTRIES - 1)
    remote = helper._list_payload()["entries"]
    remote_parents = {entry["path"]: entry["parent_path"] for entry in remote if entry.get("parent_path")}
    assert remote_parents == tree.expected_parents
    assert len(remote) == session_tree.TOTAL_ENTRIES
    assert all("codex_parent_thread_id" not in entry for entry in remote)


def test_listing_restores_only_ancestors_dropped_by_the_limit(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """件数上限を超えるのは戻した祖先の件数に限り、親を持たない古い記録は戻さない。"""
    state_dir = tmp_path / "state" / "agent-toolkit"
    tree = session_tree.write_session_tree(tmp_path / ".claude", tmp_path / "codex", state_dir)
    unrelated = _write(
        tmp_path / ".claude" / "projects" / "repo" / "old-unrelated.jsonl",
        [{"type": "user", "timestamp": "2026-07-01T00:00:00Z", "message": {"content": "古い無関係の記録"}}],
    )
    monkeypatch.setattr(sessions, "MAX_LIST_ENTRIES", session_tree.TOTAL_ENTRIES - 1)
    context = sessions.create_context(
        hostname="local-host", claude_home=tmp_path / ".claude", codex_home=tmp_path / "codex", state_dir=state_dir
    )

    paths = [entry.path for entry in sessions.list_local_sessions(context)]

    assert len(paths) == session_tree.TOTAL_ENTRIES
    assert str(tree.paths[session_tree.OLD_PARENT_ID]) in paths
    assert str(unrelated) not in paths


@pytest.mark.asyncio
async def test_unreachable_host_is_reported_and_others_are_returned(tmp_path: pathlib.Path) -> None:
    """1台へ到達できなくても、他のホストとローカルの一覧は返す。"""
    _claude_record(tmp_path)

    async def runner(host: str, op: str, args: list[str]) -> str:
        del op, args
        if host == "down-host":
            raise OSError("接続できません")
        return json.dumps({"ok": True, "entries": []})

    context = _context(tmp_path, remote_hosts=["down-host", "up-host"], ssh_runner=runner)

    entries, warnings = await sessions.list_sessions(context)

    assert [entry.host for entry in entries] == ["local-host"]
    assert [warning["host"] for warning in warnings] == ["down-host"]
    assert warnings[0]["reason"].startswith("記録を取得できません: ")
    assert "接続できません" in warnings[0]["reason"]


def _failed_ssh(returncode: int, stderr: bytes) -> typing.Callable[..., typing.Awaitable[tuple[int, bytes, bytes]]]:
    """指定した終了コードと標準エラー出力を返す単発SSH（`remote.run_ssh`）の代用を組み立てる。"""

    async def run(cmd: list[str], timeout: float) -> tuple[int, bytes, bytes]:
        del cmd, timeout
        return returncode, b"", stderr

    return run


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (b"helper not found\n", "helper not found"),
        (b"  \n ", "標準エラー出力はありません"),
        (b"\xff\xfe helper failed", "helper failed"),
    ],
    ids=["message", "empty", "undecodable"],
)
async def test_remote_failure_warning_carries_stderr(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    stderr: bytes,
    expected: str,
) -> None:
    """リモート実行が非0で終了した場合、終了コードと失敗元の標準エラー出力を警告本文へ引き継ぐ。"""
    monkeypatch.setattr(_atk_serve_remote, "run_ssh", _failed_ssh(2, stderr))
    context = _context(tmp_path, remote_hosts=["down-host"])

    _, warnings = await sessions.list_sessions(context)

    reason = warnings[0]["reason"]
    assert reason.startswith("記録を取得できません: ")
    assert "終了コード2" in reason
    assert expected in reason


@pytest.mark.asyncio
async def test_long_stderr_keeps_the_tail_in_the_warning(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """標準エラー出力が上限を超える場合、失敗の直接原因が現れる末尾側を残して切り詰める。"""
    head = "先頭の行" * sessions.STDERR_EXCERPT_MAX_CHARS
    stderr = f"{head}\n末尾の理由\n".encode()
    monkeypatch.setattr(_atk_serve_remote, "run_ssh", _failed_ssh(2, stderr))
    context = _context(tmp_path, remote_hosts=["down-host"])

    _, warnings = await sessions.list_sessions(context)

    reason = warnings[0]["reason"]
    assert "末尾の理由" in reason
    assert head not in reason
    assert len(reason) < len(head)


@pytest.mark.asyncio
async def test_host_status_reports_connection_state(tmp_path: pathlib.Path) -> None:
    """ホストごとの接続状態を返す。ローカルは常に接続済みとする。"""
    context = _context(tmp_path, remote_hosts=["remote-host"])

    assert await sessions.host_status(context) == {"local-host": "connected", "remote-host": "connecting"}

    async with context.state.lock:
        context.state.host_status["remote-host"] = "disconnected"
    assert (await sessions.host_status(context))["remote-host"] == "disconnected"


@pytest.mark.asyncio
async def test_remote_call_falls_back_to_single_ssh(tmp_path: pathlib.Path) -> None:
    """常駐RPCが未接続・失敗・エラー応答の場合は単発SSHへ切り替える。"""
    runner, calls = _runner_returning({"ok": True, "entries": []})
    context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)

    # 未接続。
    disconnected = _FakeRpcClient(connected=False, response={"ok": True, "entries": []})
    context.state.clients["remote-host"] = typing.cast(typing.Any, disconnected)
    assert await sessions._remote_call(context, "remote-host", "list", {}) == {"ok": True, "entries": []}
    # RPCが例外で失敗。
    context.state.clients["remote-host"] = typing.cast(
        typing.Any, _FakeRpcClient(connected=True, response=RuntimeError("切断"))
    )
    assert await sessions._remote_call(context, "remote-host", "list", {}) == {"ok": True, "entries": []}
    # RPCがエラー応答を返した。
    failing = _FakeRpcClient(connected=True, response={"ok": False, "error": "no such file"})
    context.state.clients["remote-host"] = typing.cast(typing.Any, failing)
    assert await sessions._remote_call(context, "remote-host", "list", {}) == {"ok": True, "entries": []}

    assert calls == [("remote-host", "list", []), ("remote-host", "list", []), ("remote-host", "list", [])]
    assert failing.calls == [("list", {})]


@pytest.mark.asyncio
async def test_remote_call_uses_rpc_when_connected(tmp_path: pathlib.Path) -> None:
    """常駐RPCが応答する場合は単発SSHを起動しない。"""
    runner, calls = _runner_returning({"ok": True, "entries": []})
    context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)
    client = _FakeRpcClient(connected=True, response={"ok": True, "entries": [{"path": "/x.jsonl"}]})
    context.state.clients["remote-host"] = typing.cast(typing.Any, client)

    payload = await sessions._remote_call(context, "remote-host", "list", {})

    assert payload["entries"] == [{"path": "/x.jsonl"}]
    assert not calls
    assert client.calls == [("list", {})]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record_path", ["/home/aki/.claude/projects/p/abc.jsonl", "C:\\Users\\aki\\.claude\\projects\\p\\abc.jsonl"]
)
async def test_remote_detail_is_normalized_like_local(tmp_path: pathlib.Path, record_path: str) -> None:
    """リモートの記録も同じ表示モデルへ正規化する。"""
    text = json.dumps({"type": "user", "timestamp": "2026-09-01T00:00:00Z", "message": {"content": "やあ"}}) + "\n"
    runner, calls = _runner_returning({"ok": True, "data": base64.b64encode(text.encode("utf-8")).decode("ascii")})
    context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)

    detail = await sessions.session_detail(context, "claude", "remote-host", record_path)

    assert detail["host"] == "remote-host"
    assert detail["session_id"] == "abc"
    assert detail["path"] == record_path
    assert [event["text"] for event in detail["events"]] == ["やあ"]
    assert calls[0][1] == "read"


@pytest.mark.asyncio
async def test_remote_subagents_are_listed_or_reported_as_unavailable(tmp_path: pathlib.Path) -> None:
    """リモートの記録もサブエージェント一覧を返し、一覧欄がない応答は判定不能として区別する。"""
    text = json.dumps({"type": "user", "timestamp": "2026-09-01T00:00:00Z", "message": {"content": "やあ"}}) + "\n"
    data = base64.b64encode(text.encode("utf-8")).decode("ascii")
    subagent = {
        "agent_id": "agent-1",
        "agent_type": "Explore",
        "description": "探索",
        "spawn_depth": 1,
        "parent_agent_id": None,
        "model": "opus",
        "path": "/home/aki/.claude/projects/p/abc/subagents/agent-1.jsonl",
    }

    async def detail_for(payload: dict[str, typing.Any]) -> dict[str, typing.Any]:
        runner, _ = _runner_returning(payload)
        context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)
        return await sessions.session_detail(context, "claude", "remote-host", "/home/aki/.claude/projects/p/abc.jsonl")

    listed = await detail_for({"ok": True, "data": data, "subagents": [subagent]})
    assert listed["subagents"] == [subagent]
    assert listed["subagents_unavailable"] is False

    # サブエージェント一覧を返さない版のヘルパーが動くホストでは、読み取り自体は成功するため欄の欠落で判別する。
    legacy = await detail_for({"ok": True, "data": data})
    assert legacy["subagents"] is None
    assert legacy["subagents_unavailable"] is True

    # 空配列を返すホストはサブエージェントが無いことを示すため、判定不能としない。
    empty = await detail_for({"ok": True, "data": data, "subagents": []})
    assert empty["subagents"] is None
    assert empty["subagents_unavailable"] is False


@pytest.mark.asyncio
async def test_unknown_engine_or_host_is_not_found(tmp_path: pathlib.Path) -> None:
    """未知の実行系・ホスト・危険なパスは詳細を返さない。"""
    runner, _ = _runner_returning({"ok": True, "data": ""})
    context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)

    for engine, host, path in (
        ("gemini", "remote-host", "/a.jsonl"),
        ("claude", "unknown", "/a.jsonl"),
        ("claude", "remote-host", "/home/aki/../etc/a.jsonl"),
    ):
        with pytest.raises(sessions.SessionNotFoundError):
            await sessions.session_detail(context, engine, host, path)


@pytest.mark.asyncio
async def test_refresh_notification_is_delivered_to_subscribers(tmp_path: pathlib.Path) -> None:
    """SSE購読者へ一覧の再取得を促す通知を配信する。"""
    context = _context(tmp_path)
    queue = await sessions.subscribe(context.state)
    try:
        await sessions.deliver_refresh(context.state)
        assert json.loads(await asyncio.wait_for(queue.get(), timeout=1)) == {"type": "refresh"}
        # 未配信の通知がある間に重ねて配信しても、一覧の再取得は1件にまとまり、配信で待たない。
        await sessions.deliver_refresh(context.state)
        await sessions.deliver_refresh(context.state)
        assert json.loads(await asyncio.wait_for(queue.get(), timeout=1)) == {"type": "refresh"}
        assert queue.empty()
    finally:
        await sessions.unsubscribe(context.state, queue)
    assert context.state.subscribers == set()


def test_local_hostname_must_not_collide_with_remote_hosts(tmp_path: pathlib.Path) -> None:
    """ローカルホスト名とリモートホスト名の重複は起動時に拒絶する。"""
    with pytest.raises(ValueError):
        _context(tmp_path, remote_hosts=["local-host"])


def _no_user_corpus(root: pathlib.Path) -> dict[str, pathlib.Path]:
    """発話を持つ記録と持たない記録（Claude Code本体・サブエージェント・Codex）を作成する。"""
    projects = root / ".claude" / "projects" / "repo"
    spoken = _write(projects / "spoken.jsonl", [{"type": "user", "message": {"content": "やあ"}}])
    silent = _write(projects / "silent.jsonl", [{"type": "permission-mode"}, {"type": "system", "subtype": "x"}])
    subagents = spoken.with_suffix("") / "subagents"
    _write(subagents / "agent-a.jsonl", [{"type": "assistant", "message": {"content": "作業"}}])
    (subagents / "agent-a.meta.json").write_text(json.dumps({"spawnDepth": 1}), encoding="utf-8")
    codex_day = root / "codex-home" / "sessions" / "2026" / "09" / "26"
    codex_silent = _write(codex_day / "rollout-silent.jsonl", [{"type": "session_meta", "payload": {"cwd": "/w"}}])
    codex_spoken = _write(
        codex_day / "rollout-spoken.jsonl",
        [{"type": "session_meta", "payload": {"cwd": "/w"}}, {"type": "response_item", "payload": {"role": "user"}}],
    )
    return {
        "spoken": spoken,
        "silent": silent,
        "subagent": subagents / "agent-a.jsonl",
        "codex_silent": codex_silent,
        "codex_spoken": codex_spoken,
    }


def _helper_list(root: pathlib.Path) -> list[dict[str, typing.Any]]:
    """リモート補助の一覧を、`root`を家ディレクトリとして取得する。"""
    environment = os.environ.copy()
    environment.update({"HOME": str(root), "CODEX_HOME": str(root / "codex-home")})
    result = subprocess.run(
        [
            sys.executable,
            str(pathlib.Path(__file__).resolve().parents[3] / "scripts" / "atk_serve_sessions_remote_helper.py"),
            "list",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return json.loads(result.stdout)["entries"]


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
    monkeypatch.setattr(sessions, "MAX_LIST_ENTRIES", 1)

    assert [entry.path for entry in sessions.list_local_sessions(_context(tmp_path))] == [str(spoken)]


def test_remote_helper_applies_the_same_exclusion_as_the_local_listing(tmp_path: pathlib.Path) -> None:
    """リモート補助の一覧も同じ基準で除外し、ローカルの一覧と同じ記録の集合を返す。"""
    corpus = _no_user_corpus(tmp_path)
    context = sessions.create_context(
        hostname="local-host", claude_home=tmp_path / ".claude", codex_home=tmp_path / "codex-home"
    )

    remote = {entry["path"] for entry in _helper_list(tmp_path)}
    local = {entry.path for entry in sessions.list_local_sessions(context)}

    assert remote == local == {str(corpus["spoken"]), str(corpus["codex_spoken"])}
    assert all("has_user_message" not in entry for entry in _helper_list(tmp_path))


@pytest.mark.asyncio
async def test_entries_from_an_old_remote_helper_are_not_excluded(tmp_path: pathlib.Path) -> None:
    """判定材料を返さない旧版のリモート補助の項目は、最初の発話がnullでも除外しない。"""
    runner, _ = _runner_returning(
        {"ok": True, "entries": [{"engine": "codex", "path": "/r/rollout-x.jsonl", "first_user_message": None}]}
    )
    context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)

    entries, _warnings = await sessions.list_sessions(context)

    assert [(entry.host, entry.path) for entry in entries] == [("remote-host", "/r/rollout-x.jsonl")]


@pytest.mark.asyncio
async def test_remote_change_notifications_are_relayed_to_subscribers(tmp_path: pathlib.Path) -> None:
    """リモート補助の変更通知を、ホスト名を付けてSSE購読者へ中継する。"""
    context = _context(tmp_path, remote_hosts=["remote-host"])
    client = sessions.RemoteSessionClient("remote-host", context.state)
    queue = await sessions.subscribe(context.state)
    stream = asyncio.StreamReader()
    stream.feed_data(
        b'{"type":"ready","host":"remote-host"}\n{"type":"record","engine":"claude","path":"/r/a.jsonl"}\n{"type":"unknown"}\n'
    )
    stream.feed_eof()

    await client._process_stream(stream)

    assert json.loads(queue.get_nowait()) == {
        "type": "record",
        "host": "remote-host",
        "engine": "claude",
        "path": "/r/a.jsonl",
    }
    assert queue.empty()
    stream = asyncio.StreamReader()
    stream.feed_data(b'{"type":"refresh"}\n')
    stream.feed_eof()
    await client._process_stream(stream)
    assert json.loads(queue.get_nowait()) == {"type": "refresh"}


@pytest.mark.asyncio
async def test_run_does_not_reconnect_when_cancelled_during_cleanup(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """常駐SSHが停止処理と同時に終了しても、停止処理のキャンセルで再接続せずに終わる。

    プロセスグループ全体へSIGTERMが届くと、子のSSHが先に終了して後始末へ入ったタスクへ停止処理のキャンセルが届く。
    後始末の待機がキャンセルを吸収したまま再接続すると、新しいSSHの出力を待ち続けてatk serveの停止が完了しない。
    """
    started: list[tuple[typing.Any, ...]] = []
    real_exec = asyncio.create_subprocess_exec

    async def fake_exec(*cmd: typing.Any, **kwargs: typing.Any) -> asyncio.subprocess.Process:
        # 標準出力を閉じて接続断を起こし、標準入力の終端を受けてから少し遅れて終了する子プロセスで代替する。
        started.append(cmd)
        script = "import os, sys, time; os.close(1); sys.stdin.read(); time.sleep(0.3)"
        return await real_exec(sys.executable, "-c", script, **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    context = _context(tmp_path, remote_hosts=["remote-host"])
    sessions.start_remote_clients(context)
    for _ in range(500):
        if context.state.host_status.get("remote-host") == "disconnected":
            break
        await asyncio.sleep(0.01)
    assert context.state.host_status.get("remote-host") == "disconnected"

    await asyncio.wait_for(sessions.stop_remote_clients(context), timeout=5)

    assert len(started) == 1


def test_remote_helper_is_started_with_watchdog_and_platformdirs() -> None:
    """リモート補助は変更監視に使うwatchdogと、登録簿の状態ディレクトリの解決に使うplatformdirsを伴って起動する。"""
    argv = sessions._build_remote_command_argv("serve", [])

    with_values = [argv[index + 1] for index, value in enumerate(argv) if value == "--with"]
    assert with_values == ['"watchdog>=6.0.0"', '"platformdirs>=4.0"']


@pytest.mark.asyncio
async def test_local_watch_notifies_new_records_and_appends(tmp_path: pathlib.Path) -> None:
    """ローカルの記録の追加で一覧の再取得を、一覧に載る記録への追記で記録1件の更新を通知する。"""
    context = _context(tmp_path)
    (tmp_path / "claude" / "projects").mkdir(parents=True)
    sessions.start_local_watch(context)
    queue = await sessions.subscribe(context.state)
    try:
        path = _write(tmp_path / "claude" / "projects" / "repo" / "new.jsonl", [{"type": "user", "message": {"content": "a"}}])
        assert json.loads(await asyncio.wait_for(queue.get(), timeout=5)) == {"type": "refresh"}
        await asyncio.to_thread(sessions.list_local_sessions, context)
        while not queue.empty():
            queue.get_nowait()

        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"type": "assistant", "message": {"content": "b"}}) + "\n")

        message = json.loads(await asyncio.wait_for(queue.get(), timeout=5))
        assert message == {"type": "record", "host": "local-host", "engine": "claude", "path": str(path)}
    finally:
        await sessions.unsubscribe(context.state, queue)
        sessions.stop_local_watch(context)
    assert context.state.record_watch is None


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


def _count_record_opens(monkeypatch: pytest.MonkeyPatch) -> list[pathlib.Path]:
    """記録（`.jsonl`）を開いた回数を数えるため、開いたパスを順に記録する。"""
    opened: list[pathlib.Path] = []
    original_open = pathlib.Path.open

    def counting_open(self: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        if self.suffix == ".jsonl":
            opened.append(self)
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", counting_open)
    return opened


def _age(*paths: pathlib.Path) -> None:
    """更新時刻を十分過去へ戻し、索引が解析結果を信用できる記録にする。"""
    for path in paths:
        os.utime(path, (1_700_000_000, 1_700_000_000))


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


def test_remote_helper_serve_mode_reuses_unchanged_records(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """リモートヘルパーの常駐モードは、同じプロセスの2回目の`list`要求で変更のない記録を開き直さない。"""
    home = tmp_path / "home"
    record = _write(
        home / ".claude" / "projects" / "proj" / "session.jsonl",
        [{"type": "user", "timestamp": "2026-09-01T00:00:00Z", "message": {"content": "やあ"}}],
    )
    _age(record)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    helper_path = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "atk_serve_sessions_remote_helper.py"
    spec = importlib.util.spec_from_file_location("_atk_serve_sessions_remote_helper_under_test", helper_path)
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)

    def no_watch() -> tuple[None, typing.Any]:
        return None, None

    monkeypatch.setattr(helper, "_start_watch", no_watch)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"id": 1, "op": "list"}\n{"id": 2, "op": "list"}\n'))
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    opened = _count_record_opens(monkeypatch)

    assert helper._serve() == 0

    responses = [json.loads(line) for line in output.getvalue().splitlines() if '"response"' in line]
    assert [response["id"] for response in responses] == [1, 2]
    assert responses[0]["entries"] == responses[1]["entries"]
    assert [entry["path"] for entry in responses[1]["entries"]] == [str(record)]
    # 1回目の要求が一覧の値と子セッションIDのために2回開き、2回目の要求は開かない。
    assert opened == [record, record]

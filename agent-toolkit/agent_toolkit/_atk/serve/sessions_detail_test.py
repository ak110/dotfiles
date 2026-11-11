"""serveのsessionsに関するdetailの試験。"""

# pylint: disable=protected-access

import base64
import json
import pathlib

import pytest

from agent_toolkit._atk.serve import sessions
from agent_toolkit._testing.serve_sessions_support import _claude_record, _codex_record, _context, _runner_returning, _write


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
    assert "project" not in detail
    assert detail["session_name"] is None
    assert detail["started_at"] == "2026-09-01T00:00:00Z"


def test_detail_returns_every_event_of_a_long_record(tmp_path: pathlib.Path) -> None:
    """5,000件を超えるイベントを持つ記録でも、詳細は全イベントを返し、切り詰めた件数の項目を持たない。

    件数で切り詰めると、右ペインはその後ろのイベントと以後の追記へどの操作でも到達できない。
    """
    count = 5001
    path = _write(
        tmp_path / "claude" / "projects" / "-home-aki-long" / "long.jsonl",
        [{"type": "user", "timestamp": "2026-09-01T00:00:00Z", "cwd": "/home/aki/long", "message": {"content": "開始"}}]
        + [
            {
                "type": "assistant",
                "timestamp": "2026-09-01T00:00:01Z",
                "message": {"content": [{"type": "text", "text": f"応答{index}"}]},
            }
            for index in range(count)
        ],
    )

    detail = sessions.read_local_detail(_context(tmp_path), "claude", str(path))

    assert len(detail["events"]) == count + 1
    assert detail["events"][-1]["text"] == f"応答{count - 1}"
    assert "truncated_events" not in detail


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
    assert "project" not in detail
    assert detail["session_name"] is None


@pytest.mark.parametrize(
    ("extra", "expected"),
    [
        (
            [
                {"type": "custom-title", "customTitle": "古い名前"},
                {"type": "ai-title", "aiTitle": "生成題名"},
                {"type": "custom-title", "customTitle": "付けた名前"},
                {"type": "agent-name", "agentName": "委譲先名"},
            ],
            "付けた名前",
        ),
        ([{"type": "ai-title", "aiTitle": "最初の題名"}, {"type": "ai-title", "aiTitle": "生成題名"}], "生成題名"),
        ([{"type": "custom-title", "customTitle": "  "}, {"type": "ai-title", "aiTitle": "生成題名"}], "生成題名"),
        ([], None),
    ],
)
def test_claude_detail_session_name_prefers_custom_title(
    tmp_path: pathlib.Path, extra: list[dict[str, str]], expected: str | None
) -> None:
    """詳細APIのセッション名は最後のcustom-title、無ければ最後のai-titleとし、名前の記録が無ければnullを返す。"""
    path = _claude_record(tmp_path)
    with path.open("a", encoding="utf-8") as stream:
        stream.writelines(json.dumps(record, ensure_ascii=False) + "\n" for record in extra)

    detail = sessions.read_local_detail(_context(tmp_path), "claude", str(path))

    assert detail["session_name"] == expected


def test_codex_detail_session_name_uses_last_matching_session_index_line(tmp_path: pathlib.Path) -> None:
    """Codexのセッション名は`<codex_home>/session_index.jsonl`のthread IDが一致する最後の行の`thread_name`とする。"""
    path = _codex_record(tmp_path)
    context = _context(tmp_path)
    thread_id = sessions.read_local_detail(context, "codex", str(path))["session_id"]
    (tmp_path / "codex" / "session_index.jsonl").write_text(
        "".join(
            json.dumps(record, ensure_ascii=False) + "\n"
            for record in [
                {"id": thread_id, "thread_name": "最初の名前", "updated_at": "2026-09-01T00:00:00Z"},
                {"id": "other-thread", "thread_name": "別のthread", "updated_at": "2026-09-01T00:00:01Z"},
                {"id": thread_id, "thread_name": "変更後の名前", "updated_at": "2026-09-01T00:00:02Z"},
            ]
        )
        + "{壊れた行\n",
        encoding="utf-8",
    )

    assert sessions.read_local_detail(context, "codex", str(path))["session_name"] == "変更後の名前"


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
            {
                "type": "response_item",
                "payload": {"role": "user", "content": [{"text": "You are Codex, an agent based on GPT-6."}]},
            },
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
    # サブエージェント記録が無い場合はnullとし、「0件」と区別する。
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


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["claude", "codex"])
async def test_detail_preserves_json_string_separators(tmp_path: pathlib.Path, engine: str) -> None:
    """ローカル・リモートの会話詳細取得で正常な本文を失わず解析不可0件を返す。"""
    texts = [f"前{separator}後" for separator in ("\u2028", "\u2029", "\u0085")]
    records = [
        {"type": "user", "message": {"content": text}}
        if engine == "claude"
        else {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": text}]}}
        for text in texts
    ]
    path = (
        tmp_path / "claude/projects/proj/unicode.jsonl"
        if engine == "claude"
        else (tmp_path / "codex/sessions/2026/11/09/rollout-unicode.jsonl")
    )
    _write(path, records)
    runner, _calls = _runner_returning({"ok": True, "data": base64.b64encode(path.read_bytes()).decode("ascii")})
    context = _context(tmp_path, remote_hosts=["remote-host"], ssh_runner=runner)

    local = await sessions.session_detail(context, engine, "local-host", str(path))
    remote = await sessions.session_detail(context, engine, "remote-host", str(path))

    for detail in (local, remote):
        assert [event["text"] for event in detail["events"]] == texts
        assert detail["broken_lines"] == 0


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


def test_runtime_outputs_and_codex_notifications_are_injected(tmp_path: pathlib.Path) -> None:
    """bashモードの出力とCodexの内部指示・hook出力・委譲先通知を挿入本文として折りたたみ、人間の入力は残す。

    各記録はClaude Code 2.1.282の実記録とCodexの2026年8月〜10月のrolloutから、本文の格納先の形を写した。
    """
    claude = _write(
        tmp_path / "claude" / "projects" / "p" / "claude.jsonl",
        [
            {"type": "user", "message": {"role": "user", "content": "<bash-input>! ls</bash-input>"}},
            {
                "type": "user",
                "message": {"role": "user", "content": "<bash-stdout>file</bash-stdout><bash-stderr></bash-stderr>"},
            },
        ],
    )
    codex = _write(
        tmp_path / "codex" / "sessions" / "2026" / "09" / "01" / "rollout-notifications.jsonl",
        [
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
            }
            for text in (
                '<codex_internal_context source="goal">\nContinue working toward the active thread goal.',
                '<hook_prompt hook_run_id="stop:8:hooks.codex.json">&lt;atk-auto kind="block"&gt;',
                '<subagent_notification>\n{"agent_path":"01a0280f"}',
                '<send_user_message_question_reply>\n[{"answer":"推奨"}]',
            )
        ],
    )

    claude_events = sessions.read_local_detail(_context(tmp_path), "claude", str(claude))["events"]
    codex_events = sessions.read_local_detail(_context(tmp_path), "codex", str(codex))["events"]

    assert [event["kind"] for event in claude_events] == ["user", "injected"]
    assert [event["kind"] for event in codex_events] == ["injected", "injected", "injected", "user"]

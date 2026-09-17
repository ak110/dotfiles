"""agents_server共有プロンプトと状態遷移の契約を検証する。"""

import logging

import pytest

from agent_toolkit._agents_server import state

_LAUNCH_DOCUMENTS: dict[state.LaunchKind, str] = {
    "delegate": "agents-server-delegate.md",
    "explore": "agents-server-explore.md",
    "shell": "agents-server-shell.md",
    "write": "agents-server-write.md",
}


def _shared_document_body(name: str) -> str:
    """共有文書から先頭のH1見出し行と直後の空行を除いた本文を返す。

    除去する行数は共有文書の書式（1行目がH1見出し、2行目が空行）から導く。
    書式が崩れた場合は照合の前に失敗させ、実装側の分割結果と偶然一致する事態を防ぐ。
    """
    lines = (state.SHARE_DIR / name).read_text(encoding="utf-8").rstrip("\n").split("\n")
    assert lines[0].startswith("# "), f"{name}の1行目がH1見出しではない"
    assert lines[1] == "", f"{name}の2行目が空行ではない"
    return "\n".join(lines[2:])


def test_launch_prompts_load_shared_documents() -> None:
    """起動区分ごとに対応する共有文書を読み、委譲先通知と組み合わせて実行時のシステム指示を組み立てる。

    共有文書の文面そのものは`state`モジュールの契約ではなく当該文書側の内容であるため、判定対象にしない。
    文面を検体へ書き写すと、実装の契約が変わらない改訂でも当該検体が失敗する。
    """
    notice = _shared_document_body("agents-server-delegate-notice.md")

    assert notice == state.DELEGATE_NOTICE
    for kind, document in _LAUNCH_DOCUMENTS.items():
        expected = f"{notice}\n{_shared_document_body(document)}"
        if kind == "delegate":
            expected = f"{expected}\n\n{state.SUBAGENT_RULES}"
        assert state.LAUNCH_SYSTEM_PROMPTS[kind] == expected, kind
    assert _shared_document_body("agents-server-auto-resume.md") == state.AUTO_RESUME_NOTICE
    assert not any(
        prompt.startswith("# ")
        for prompt in (state.DELEGATE_NOTICE, *state.LAUNCH_SYSTEM_PROMPTS.values(), state.AUTO_RESUME_NOTICE)
    )


def test_claude_delegate_adds_claude_specific_subagent_rules() -> None:
    """Claude通常起動だけがClaude固有の委譲先規範を追加する。"""
    assert state.CLAUDE_CODE_SUBAGENT_RULES not in state.DELEGATE_SYSTEM_PROMPT
    assert state.CLAUDE_DELEGATE_SYSTEM_PROMPT.endswith(state.CLAUDE_CODE_SUBAGENT_RULES)


@pytest.mark.parametrize("namespace", ["mcp__plugin_agent-toolkit_agents_server__", "mcp__agents_server__", ""])
def test_child_session_is_tracked_for_every_host_tool_name_form(namespace: str) -> None:
    """ホストが配送するいずれの修飾形式でも孫sessionを追跡対象へ登録する。"""
    session = state.SessionState("parent-1", "/tmp")

    state.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_1", "name": f"{namespace}start_explore", "input": {"prompt": "調査"}}]},
    )
    state.consume_claude_agents_server_message(
        session,
        {"content": [{"tool_use_id": "toolu_1", "content": {"session_id": "child-1", "status": "running"}}]},
    )

    assert session.live_child_session_ids == {"child-1"}
    assert state.has_pending_auto_resume_targets(session)


def test_pending_tool_uses_track_tools_outside_agents_server() -> None:
    """`agents_server`以外のツール呼び出しも未完了として記録し、結果の到着で除く。"""
    session = state.SessionState("parent-1", "/tmp")

    state.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_1", "name": "Bash", "input": {"command": "secret --token=abc"}}]},
    )

    assert [entry["name"] for entry in session.active_tool_uses()] == ["Bash"]

    state.consume_claude_agents_server_message(
        session,
        {"content": [{"tool_use_id": "toolu_1", "content": "done"}]},
    )

    assert not session.active_tool_uses()


def test_active_tool_uses_include_input_detail_and_sort_by_start() -> None:
    """未完了のツール呼び出しは開始時刻の昇順で返し、入力の1行要約を`detail`として載せる。"""
    session = state.SessionState("parent-1", "/tmp")
    session.pending_tool_uses.update(
        {
            "toolu_2": ("Read", "2026-09-15T00:00:02+00:00", "file_path=/tmp/a.py"),
            "toolu_1": ("Bash", "2026-09-15T00:00:01+00:00", "command=rg -n foo"),
            "toolu_3": ("TodoWrite", "2026-09-15T00:00:03+00:00", ""),
        }
    )

    assert session.active_tool_uses() == [
        {"name": "Bash", "started_at": "2026-09-15T00:00:01+00:00", "detail": "command=rg -n foo"},
        {"name": "Read", "started_at": "2026-09-15T00:00:02+00:00", "detail": "file_path=/tmp/a.py"},
        {"name": "TodoWrite", "started_at": "2026-09-15T00:00:03+00:00"},
    ]


def test_current_item_is_projected_as_active_tool_use() -> None:
    """Codex backendの進行中itemを、`type`・`id`・開始時刻と入力の要約として返す。"""
    session = state.SessionState("thread-1", "/tmp", engine="codex")

    session.record_current_item_start({"type": "commandExecution", "id": "item-1", "command": "rg -n foo"})
    entries = session.active_tool_uses()

    assert len(entries) == 1
    assert entries[0]["type"] == "commandExecution"
    assert entries[0]["id"] == "item-1"
    # `type`と`id`は別の項目として既に返すため、要約からは除く。
    assert entries[0]["detail"] == "command=rg -n foo"
    assert entries[0]["started_at"]
    assert session.last_action == "commandExecution: command=rg -n foo"

    session.record_current_item_start(None)

    assert not session.active_tool_uses()


def test_last_action_prefers_tool_use_issued_after_text() -> None:
    """同じメッセージがテキストとツール呼び出しを持つ場合は、後のツール呼び出しを最後の行動とする。"""
    session = state.SessionState("parent-1", "/tmp")

    session.set_progress("調査を続ける")
    state.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_1", "name": "Bash", "input": {}}]},
    )

    assert session.last_action == "Bash"


def test_last_action_changes_between_repeated_calls_of_the_same_tool() -> None:
    """同じツール名を繰り返す区間でも、入力が異なれば最後の行動の値が変わる。"""
    session = state.SessionState("parent-1", "/tmp")

    state.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_1", "name": "Bash", "input": {"command": "git status"}}]},
    )
    first = session.last_action
    state.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_2", "name": "Bash", "input": {"command": "git diff"}}]},
    )

    assert first == "Bash: command=git status"
    assert session.last_action == "Bash: command=git diff"


def test_action_detail_collapses_whitespace_and_truncates_at_limit() -> None:
    """入力の要約は改行と連続空白を1個へ畳み、上限を超えた分を省略記号で切り詰める。"""
    session = state.SessionState("parent-1", "/tmp")

    session.record_tool_use_start("toolu_1", "Write", {"path": "a.py", "content": "x\r\ny  z", "line": 12})

    assert session.last_action == "Write: path=a.py content=x y z line=12"

    session.record_tool_use_start("toolu_2", "Write", {"content": "y" * 300})
    detail = session.active_tool_uses()[-1]["detail"]

    assert detail == f"content={'y' * 192}…"


def test_activity_projection_decides_stall_by_activity_time() -> None:
    """停滞の印は活動時刻からの経過だけで決め、テキスト出力の停止では付けない。"""
    text_silent = state.activity_projection(
        updated_at="2099-01-01T00:00:00+00:00",
        output_updated_at="2000-01-01T00:00:00+00:00",
        started_at="2000-01-01T00:00:00+00:00",
    )
    inactive = state.activity_projection(
        updated_at="2000-01-01T00:00:00+00:00",
        output_updated_at="2099-01-01T00:00:00+00:00",
        started_at="2000-01-01T00:00:00+00:00",
    )
    unreadable = state.activity_projection(updated_at=None, output_updated_at=None, started_at="2026-09-15T00:00:00")

    assert text_silent["seconds_since_output"] >= state.STALL_NOTICE_SECONDS
    assert text_silent["seconds_since_activity"] == 0
    assert text_silent["seconds_since_activity"] < state.STALL_NOTICE_SECONDS
    assert inactive["seconds_since_activity"] >= state.STALL_NOTICE_SECONDS
    assert not unreadable


@pytest.mark.asyncio
async def test_terminal_transition_logs_safe_fields_once(caplog: pytest.LogCaptureFixture) -> None:
    """turn終端を一度だけ記録し、結果本文を含めない。"""
    session = state.SessionState("session-1", "/tmp")
    session.status = "completed"
    session.agent_message = "秘密の結果本文"
    session.turn_completed = True

    with caplog.at_level(logging.INFO, logger="agent-toolkit.agents-server.state"):
        session.touch()
        session.touch()

    assert caplog.text.count("session_transition event=terminal") == 1
    assert "session_id=session-1 writer=state status=completed turn_seq=0" in caplog.text
    assert "秘密の結果本文" not in caplog.text

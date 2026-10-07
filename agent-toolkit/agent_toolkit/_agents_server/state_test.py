"""agents_server共有プロンプトと状態遷移の契約を検証する。"""

import logging

import pytest

from agent_toolkit._agents_server import state, wait_output_tracking


def test_pending_tool_uses_track_tools_outside_agents_server() -> None:
    """`agents_server`以外のツール呼び出しも未完了として記録し、結果の到着で除く。"""
    session = state.SessionState("parent-1", "/tmp")

    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_1", "name": "Bash", "input": {"command": "secret --token=abc"}}]},
    )

    assert [entry["name"] for entry in session.active_tool_uses()] == ["Bash"]

    wait_output_tracking.consume_claude_agents_server_message(
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
    """Codex backendの進行中itemを、種別・開始時刻と入力の要約として返し、内部IDを保持だけに使う。"""
    session = state.SessionState("thread-1", "/tmp", engine="codex")

    session.record_current_item_start({"type": "commandExecution", "id": "item-1", "command": "rg -n foo"})
    entries = session.active_tool_uses()

    assert len(entries) == 1
    assert entries[0]["type"] == "commandExecution"
    assert "id" not in entries[0]
    assert session.current_item is not None and session.current_item["id"] == "item-1"
    # 種別は別の項目で返し、内部IDは入力の要約へ含めない。
    assert entries[0]["detail"] == "command=rg -n foo"
    assert entries[0]["started_at"]
    assert session.last_action == "commandExecution: command=rg -n foo"

    session.record_current_item_start(None)

    assert not session.active_tool_uses()


def test_last_action_prefers_tool_use_issued_after_text() -> None:
    """同じメッセージがテキストとツール呼び出しを持つ場合は、後のツール呼び出しを最後の行動とする。"""
    session = state.SessionState("parent-1", "/tmp")

    session.set_progress("調査を続ける")
    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_1", "name": "Bash", "input": {}}]},
    )

    assert session.last_action == "Bash"


def test_last_action_changes_between_repeated_calls_of_the_same_tool() -> None:
    """同じツール名を繰り返す区間でも、入力が異なれば最後の行動の値が変わる。"""
    session = state.SessionState("parent-1", "/tmp")

    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_1", "name": "Bash", "input": {"command": "git status"}}]},
    )
    first = session.last_action
    wait_output_tracking.consume_claude_agents_server_message(
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

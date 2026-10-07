"""`_agents_server/wait_output_tracking.py`の振る舞いを検証する。"""

from __future__ import annotations

import pathlib

import pytest

from agent_toolkit._agents_server import resume_waits, state, wait_output_tracking


@pytest.mark.parametrize("namespace", ["mcp__plugin_agent-toolkit_agents_server__", "mcp__agents_server__", ""])
@pytest.mark.parametrize(
    ("operation", "arguments"),
    [
        ("start", {"subagent_md_path": "/plugin/share/exec.subagent.md", "extra_params": {}}),
        ("start", {"mode": "delegate", "prompt": "調査", "model_type": "high_tier"}),
        ("start", {"mode": "explore", "prompt": "調査"}),
        ("start", {"mode": "write", "prompt": "起草"}),
        ("start", {"mode": "shell", "command": "make test", "summary_policy": "終了状態"}),
        ("start_explore", {"prompt": "調査"}),
        ("start_custom", {"prompt": "調査", "model_type": "high_tier"}),
    ],
)
def test_child_session_is_tracked_for_every_host_tool_name_form(
    namespace: str, operation: str, arguments: dict[str, object]
) -> None:
    """ホストが配送するいずれの修飾形式と`start`の各modeでも孫sessionを追跡対象へ登録する。

    統合前の起動ツール名で記録された呼び出しも同じく追跡する。
    """
    session = state.SessionState("parent-1", "/tmp")

    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_1", "name": f"{namespace}{operation}", "input": arguments}]},
    )
    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"tool_use_id": "toolu_1", "content": {"session_id": "child-1", "status": "running"}}]},
    )

    assert session.live_child_session_ids == {"child-1"}
    assert resume_waits.has_pending_auto_resume_targets(session)


def _start_child(session: state.SessionState, tool_use_id: str, child_id: str) -> None:
    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"id": tool_use_id, "name": "mcp__agents_server__start", "input": {"mode": "explore", "prompt": "調査"}}]},
    )
    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"tool_use_id": tool_use_id, "content": {"session_id": child_id, "status": "running"}}]},
    )


def test_child_session_collected_by_agents_wait_leaves_auto_resume_targets() -> None:
    """委譲先が`atk agents wait`で回収した孫sessionは、終端を理由とする自動再開の対象から外れる。"""
    session = state.SessionState("parent-1", "/tmp")
    _start_child(session, "toolu_1", "child-1")
    _start_child(session, "toolu_2", "child-2")

    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_3", "name": "Bash", "input": {"command": "atk agents wait"}}]},
    )
    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {
            "content": [
                {
                    "tool_use_id": "toolu_3",
                    "content": [
                        {
                            "type": "text",
                            "text": '{"session_id": "child-1", "status": "completed", "agent_message": "完了"}\n'
                            '{"session_id": "child-2", "status": "running", "notices": []}',
                        }
                    ],
                }
            ]
        },
    )

    # 実行中通知だけを返したsessionは回収済みではないため、追跡を続ける。
    assert session.live_child_session_ids == {"child-2"}
    assert resume_waits.has_pending_auto_resume_targets(session)


def test_child_session_collected_by_agents_wait_output_file(tmp_path: pathlib.Path) -> None:
    """生成側が自動保存した待機結果も回収の根拠として読む。"""
    session = state.SessionState("parent-1", "/tmp")
    _start_child(session, "toolu_1", "child-1")
    output = tmp_path / "wait.jsonl"
    output.write_text('{"session_id": "child-1", "status": "failed", "agent_message": ""}\n', encoding="utf-8")

    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_2", "name": "Bash", "input": {"command": "atk agents wait"}}]},
    )
    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"tool_use_id": "toolu_2", "content": f"保存先: {output}\n行数: 1\n終端: 1件"}]},
    )

    assert not session.live_child_session_ids
    assert not resume_waits.has_pending_auto_resume_targets(session)


def test_agents_wait_body_lines_are_not_collection_evidence(tmp_path: pathlib.Path) -> None:
    """要約が表示する本文の範囲にある終端行と保存先の行は、その行のsessionを回収済みにしない。

    委譲先の本文は自身の待機の出力を引用し得る。本文の行を根拠にすると、未回収の孫sessionを追跡から外し、
    その終端を理由とする自動再開が起きなくなる。本文の範囲の外にある保存先は従来どおり読む。
    """
    session = state.SessionState("parent-1", "/tmp")
    for index, child_id in enumerate(("child-1", "child-2", "child-3"), start=1):
        _start_child(session, f"toolu_{index}", child_id)
    saved = tmp_path / "saved.jsonl"
    saved.write_text('{"session_id": "child-1", "status": "completed", "agent_message_path": "/body.md"}\n', encoding="utf-8")
    quoted = tmp_path / "quoted.jsonl"
    quoted.write_text('{"session_id": "child-3", "status": "completed"}\n', encoding="utf-8")
    output = (
        f"保存先: {saved}\n行数: 1\n終端: 1件\n"
        "終端行: session_id=child-1 label=なし status=completed agent_message_path=/body.md\n"
        "本文開始: session_id=child-1\n"
        '{"session_id": "child-2", "status": "completed"}\n'
        f"保存先: {quoted}\n"
        "本文終了: session_id=child-1\n"
    )

    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_4", "name": "Bash", "input": {"command": "atk agents wait"}}]},
    )
    wait_output_tracking.consume_claude_agents_server_message(
        session, {"content": [{"tool_use_id": "toolu_4", "content": output}]}
    )

    assert session.live_child_session_ids == {"child-2", "child-3"}


def _background_agents_wait(session: state.SessionState, tool_use_id: str, output: pathlib.Path) -> None:
    """委譲先が`atk agents wait`を背景実行し、ホストが起動の通知だけを返した状態を再現する。"""
    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"id": tool_use_id, "name": "Bash", "input": {"command": "atk agents wait"}}]},
    )
    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {
            "content": [
                {
                    "tool_use_id": tool_use_id,
                    "content": f"Command running in background with ID: bqwu17av0. Output is being written to: {output}. "
                    "You will be notified when it completes.",
                }
            ]
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("output_text", "expected_unobserved"),
    [
        ('{"session_id": "child-1", "status": "completed", "agent_message": "完了"}\n', None),
        ('{"session_id": "child-1", "status": "running", "notices": []}\n', ["child-1"]),
        (None, ["child-1"]),
        ("saved-terminal", None),
    ],
    ids=["terminal", "running-only", "no-output", "saved-terminal"],
)
async def test_background_agents_wait_output_releases_collected_child_before_unobserved_record(
    tmp_path: pathlib.Path,
    output_text: str | None,
    expected_unobserved: list[str] | None,
) -> None:
    """背景実行の待機が出力ファイルへ書いた終端行の孫sessionは、未観測として記録しない。

    背景起動のツール結果は起動の通知だけで、結果本文は出力ファイルへ後から書かれる。
    出力ファイルに終端行が無い場合は、従来どおり未観測として記録する。
    """
    from agent_toolkit._agents_server import claude  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    session = state.SessionState("parent-1", "/tmp")
    _start_child(session, "toolu_1", "child-1")
    output = tmp_path / "tasks" / "bqwu17av0.output"
    _background_agents_wait(session, "toolu_2", output)
    # ツール結果の時点では出力ファイルが未完成のため、追跡を続ける。
    assert session.live_child_session_ids == {"child-1"}
    if output_text == "saved-terminal":
        # 長い結果は`atk`が別ファイルへ自動保存し、標準出力（出力ファイル）には保存先の行だけが残る。
        saved = tmp_path / "saved" / "output.txt"
        saved.parent.mkdir()
        saved.write_text('{"session_id": "child-1", "status": "completed", "agent_message": "長い本文"}\n', encoding="utf-8")
        output_text = f"保存先: {saved}\n行数: 1\n終端: 1件\n"
    if output_text is not None:
        output.parent.mkdir(parents=True)
        output.write_text(output_text, encoding="utf-8")
    session.pending_result = {"status": "completed", "agent_message": "待機表明", "error": None}
    session.awaiting_auto_resume = True

    claude.ClaudeServerManager._finalize_pending_result(session)  # pylint: disable=protected-access

    error = session.error if isinstance(session.error, dict) else {}
    assert error.get("unobservedSessions") == expected_unobserved


def test_unrelated_bash_output_does_not_release_child_session() -> None:
    """`atk agents wait`以外のBash出力に同じJSON行が現れても、追跡対象を変えない。"""
    session = state.SessionState("parent-1", "/tmp")
    _start_child(session, "toolu_1", "child-1")

    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_2", "name": "Bash", "input": {"command": "cat result.jsonl"}}]},
    )
    wait_output_tracking.consume_claude_agents_server_message(
        session,
        {"content": [{"tool_use_id": "toolu_2", "content": '{"session_id": "child-1", "status": "completed"}'}]},
    )

    assert session.live_child_session_ids == {"child-1"}


def test_stderr_saved_jsonl_is_not_wait_collection(tmp_path: pathlib.Path) -> None:
    """stderrの保存先が終端JSONを含んでも、stdoutの回収結果へ混ぜない。"""
    session = state.SessionState("parent-1", str(tmp_path))
    session.live_child_session_ids.add("child-1")
    output = tmp_path / "stderr.jsonl"
    output.write_text('{"session_id":"child-1","status":"completed"}\n', encoding="utf-8")
    wait_output_tracking.consume_agents_wait_output(session, f"標準エラー保存先: {output}\n標準エラー行数: 1\n")
    assert session.live_child_session_ids == {"child-1"}
    wait_output_tracking.consume_agents_wait_output(session, f"保存先: {output}\n行数: 1\n")
    assert not session.live_child_session_ids

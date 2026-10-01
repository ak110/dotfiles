"""agents_server共有プロンプトと状態遷移の契約を検証する。"""

import logging
import pathlib

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
    書式が不正な場合は比較の前に失敗させ、実装側の分割結果と偶然一致する事態を防ぐ。
    """
    lines = (state.SHARE_DIR / name).read_text(encoding="utf-8").rstrip("\n").split("\n")
    assert lines[0].startswith("# "), f"{name}の1行目がH1見出しではない"
    assert lines[1] == "", f"{name}の2行目が空行ではない"
    return "\n".join(lines[2:])


def test_launch_prompts_load_shared_documents() -> None:
    """起動区分ごとに対応する共有文書を読み、委譲先通知と組み合わせて実行時のシステム指示を組み立てる。

    共有文書の文面そのものは`state`モジュールの契約ではなくその文書側の内容であるため、判定対象にしない。
    文面をテストへ書き写すと、実装の契約が変わらない改訂でもこのテストが失敗する。
    """
    notice = _shared_document_body("agents-server-delegate-notice.md")

    assert notice == state.DELEGATE_NOTICE
    for kind, document in _LAUNCH_DOCUMENTS.items():
        prompt = state.LAUNCH_SYSTEM_PROMPTS[kind]
        assert f"{notice}\n{_shared_document_body(document)}" in prompt, kind
        assert (state.SUBAGENT_RULES in prompt) is (kind == "delegate"), kind
    assert _shared_document_body("agents-server-auto-resume.md") in state.AUTO_RESUME_NOTICE


def test_launch_prompts_carry_normative_boundaries() -> None:
    """system指示の各区分が、生成主体と種別を示す境界を持つこと。"""
    for kind, prompt in state.LAUNCH_SYSTEM_PROMPTS.items():
        assert f'<{state.NORMATIVE_ELEMENT} source="{state.NORMATIVE_SOURCE}" kind="{kind}"' in prompt, kind
        assert prompt.endswith(f"</{state.NORMATIVE_ELEMENT}>"), kind
    assert state.AUTO_RESUME_NOTICE.startswith(f"<{state.NORMATIVE_ELEMENT} ")
    assert 'kind="rules-subagent"' in state.DELEGATE_SYSTEM_PROMPT


def test_all_launch_system_prompts_include_language_condition() -> None:
    """全起動種別のシステム指示が完了報告の言語を定め、通常委譲は英語の挿入指示を引き継がない条件も持つ。

    起動文から言語の指定を外しても委譲先が日本語で返すことを、呼び出し元の記述に依存せず保証する。
    通常委譲の固定指示が条件を欠くと、Claude以外のbackendでは共有規範の言語条項も届かず、
    実行環境が英語で挿入した指示に引きずられた応答が応答言語のチェックで遮断される。
    """
    for kind, prompt in state.LAUNCH_SYSTEM_PROMPTS.items():
        assert "日本語" in prompt, kind
    for prompt in (state.DELEGATE_SYSTEM_PROMPT, state.CLAUDE_DELEGATE_SYSTEM_PROMPT):
        assert "英語で挿入した指示" in prompt
        assert "応答言語として引き継がない" in prompt


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


def _start_child(session: state.SessionState, tool_use_id: str, child_id: str) -> None:
    state.consume_claude_agents_server_message(
        session,
        {"content": [{"id": tool_use_id, "name": "mcp__agents_server__start_explore", "input": {"prompt": "調査"}}]},
    )
    state.consume_claude_agents_server_message(
        session,
        {"content": [{"tool_use_id": tool_use_id, "content": {"session_id": child_id, "status": "running"}}]},
    )


def test_child_session_collected_by_agents_wait_leaves_auto_resume_targets() -> None:
    """委譲先が`atk agents wait`で回収した孫sessionは、終端を理由とする自動再開の対象から外れる。"""
    session = state.SessionState("parent-1", "/tmp")
    _start_child(session, "toolu_1", "child-1")
    _start_child(session, "toolu_2", "child-2")

    state.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_3", "name": "Bash", "input": {"command": "atk agents wait"}}]},
    )
    state.consume_claude_agents_server_message(
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
    assert state.has_pending_auto_resume_targets(session)


def test_child_session_collected_by_agents_wait_output_file(tmp_path: pathlib.Path) -> None:
    """`--output-file`で保存した待機結果も回収の根拠として読む。"""
    session = state.SessionState("parent-1", "/tmp")
    _start_child(session, "toolu_1", "child-1")
    output = tmp_path / "wait.jsonl"
    output.write_text('{"session_id": "child-1", "status": "failed", "agent_message": ""}\n', encoding="utf-8")

    state.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_2", "name": "Bash", "input": {"command": f"atk agents wait --output-file {output}"}}]},
    )
    state.consume_claude_agents_server_message(
        session,
        {"content": [{"tool_use_id": "toolu_2", "content": f"保存先: {output}\n行数: 1\n終端: 1件"}]},
    )

    assert not session.live_child_session_ids
    assert not state.has_pending_auto_resume_targets(session)


def _background_agents_wait(session: state.SessionState, tool_use_id: str, output: pathlib.Path) -> None:
    """委譲先が`atk agents wait`を背景実行し、ホストが起動の通知だけを返した状態を再現する。"""
    state.consume_claude_agents_server_message(
        session,
        {"content": [{"id": tool_use_id, "name": "Bash", "input": {"command": "atk agents wait"}}]},
    )
    state.consume_claude_agents_server_message(
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

    claude.ClaudeServerManager._finalize_pending_result(session, record_unobserved=True)  # pylint: disable=protected-access

    error = session.error if isinstance(session.error, dict) else {}
    assert error.get("unobservedSessions") == expected_unobserved


def test_unrelated_bash_output_does_not_release_child_session() -> None:
    """`atk agents wait`以外のBash出力に同じJSON行が現れても、追跡対象を変えない。"""
    session = state.SessionState("parent-1", "/tmp")
    _start_child(session, "toolu_1", "child-1")

    state.consume_claude_agents_server_message(
        session,
        {"content": [{"id": "toolu_2", "name": "Bash", "input": {"command": "cat result.jsonl"}}]},
    )
    state.consume_claude_agents_server_message(
        session,
        {"content": [{"tool_use_id": "toolu_2", "content": '{"session_id": "child-1", "status": "completed"}'}]},
    )

    assert session.live_child_session_ids == {"child-1"}


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

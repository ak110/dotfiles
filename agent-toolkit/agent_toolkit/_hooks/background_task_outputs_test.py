"""背景タスク出力先の追跡と読取検出のテスト。"""

from __future__ import annotations

import json
import pathlib

from agent_toolkit._hooks import background_task_outputs as subject
from agent_toolkit._hooks import posttooluse, user_prompt_submit
from agent_toolkit._hooks.pretooluse import dispatch


def test_task_output_from_explicit_and_host_background_responses() -> None:
    """明示背景実行とホスト移行で共通の通知形式を解決する。"""
    response = {
        "stdout": "Command running in background with ID: bg-1.",
        "system": "Output is being written to: /tmp/bg-1.output",
    }

    assert subject.task_output_from_response(response) == ("bg-1", "/tmp/bg-1.output")
    timeout_response = (
        "Command timed out and is now running in the background. ID: timeout-1\n"
        "Output is being written to: /tmp/timeout-1.output"
    )
    assert subject.task_output_from_response(timeout_response) == ("timeout-1", "/tmp/timeout-1.output")


def test_structured_task_id_requires_an_actual_output_path() -> None:
    """構造化IDだけを含む実応答では、出力先の対応表を作成しない。"""
    response = {
        "backgroundTaskId": "bg-1",
        "interrupted": False,
        "isImage": False,
        "noOutputExpected": False,
        "stderr": "",
        "stdout": "",
    }

    assert subject.task_output_from_response(response) is None
    assert subject.task_output_from_response({**response, "stdout": "Output is being written to: /tmp/bg-1.output"}) == (
        "bg-1",
        "/tmp/bg-1.output",
    )


def test_posttooluse_records_task_output_mapping(monkeypatch) -> None:
    """PostToolUseは応答中のタスクIDと出力先を同じ状態キーへ保存する。"""
    state: dict = {}
    monkeypatch.setattr(posttooluse, "update_state", lambda _session_id, update: update(state))

    posttooluse._record_background_task_output(  # pylint: disable=protected-access
        "session",
        "Command running in background with ID: bg-1. Output is being written to: /tmp/bg-1.output",
    )

    assert state["background_task_output_paths"] == {"bg-1": "/tmp/bg-1.output"}


def test_command_reads_path_only_for_read_command_operand() -> None:
    """同じパス文字列でも読取コマンドのオペランドだけを検出する。"""
    paths = {"/tmp/bg-1.output"}

    assert subject.command_reads_path("tail -n 20 /tmp/bg-1.output", paths)
    assert subject.command_reads_path("echo ready; cat /tmp/bg-1.output", paths)
    assert not subject.command_reads_path("echo /tmp/bg-1.output", paths)
    assert not subject.command_reads_path("cat /tmp/other.output", paths)


def test_pending_bash_task_ids_excludes_completed(monkeypatch) -> None:
    """stop_gateの起動集合から完了集合を引いたタスクだけを返す。"""
    monkeypatch.setattr(subject.stop_gate, "read_transcript_entries_cached", lambda _path: [{"entry": 1}])
    monkeypatch.setattr(
        subject.stop_gate,
        "_describe_pending_background_entries",
        lambda *_args, **_kwargs: ({"toolu_pending", "toolu_done"}, {"toolu_done"}, set()),
    )
    monkeypatch.setattr(
        subject.stop_gate,
        "_collect_background_task_id_tool_use_ids",
        lambda _entries: {"bg-pending": {"toolu_pending"}, "bg-done": {"toolu_done"}},
    )

    assert subject.pending_bash_task_ids("/tmp/transcript.jsonl", "session") == {"bg-pending"}


def test_completion_notice_removes_mapping_and_reused_id_does_not_warn_for_old_path(
    monkeypatch, tmp_path: pathlib.Path
) -> None:
    """完了後のID再利用では旧世代の出力先を未完了と誤認しない。"""
    old_path = tmp_path / "old.output"
    old_path.write_text("old", encoding="utf-8")
    new_path = tmp_path / "new.output"
    state: dict = {"background_task_output_paths": {"bg-1": str(old_path)}}

    def update(_session_id: str, operation):
        return operation(state) is not None

    monkeypatch.setattr(subject, "update_state", update)
    monkeypatch.setattr(posttooluse, "update_state", update)
    monkeypatch.setattr(dispatch, "read_state", lambda _session_id: state)
    monkeypatch.setattr(subject, "pending_bash_task_ids", lambda _path, _session_id: {"bg-1"})

    result = user_prompt_submit.main(
        json.dumps(
            {
                "session_id": "session",
                "prompt": ("<task-notification><task-id>bg-1</task-id><status>completed</status></task-notification>"),
            }
        )
    )

    assert result == 0
    assert "background_task_output_paths" not in state

    posttooluse._record_background_task_output(  # pylint: disable=protected-access
        "session",
        f"Command running in background with ID: bg-1. Output is being written to: {new_path}",
    )
    emitted: list[dict] = []
    dispatch._handle_bash_tool(  # pylint: disable=protected-access
        {"cwd": str(tmp_path), "transcript_path": str(tmp_path / "transcript.jsonl")},
        {"command": f"cat {old_path}"},
        "session",
        emitted.append,
        lambda: None,
        is_codex=False,
    )

    assert state["background_task_output_paths"] == {"bg-1": str(new_path)}
    assert not emitted


def test_pretooluse_warns_when_reading_pending_task_output(monkeypatch) -> None:
    """PreToolUse(Bash)は未完了タスクの対応出力を読む直前に警告する。"""
    emitted: list[dict] = []
    monkeypatch.setattr(
        dispatch,
        "read_state",
        lambda _session_id: {"background_task_output_paths": {"bg-1": "/tmp/bg-1.output"}},
    )
    monkeypatch.setattr(subject, "pending_bash_task_ids", lambda _path, _session_id: {"bg-1"})

    result = dispatch._handle_bash_tool(  # pylint: disable=protected-access
        {"cwd": "/tmp", "transcript_path": "/tmp/transcript.jsonl"},
        {"command": "tail -n 20 /tmp/bg-1.output"},
        "session",
        emitted.append,
        lambda: None,
        is_codex=False,
    )

    assert result == 0
    context = emitted[0]["hookSpecificOutput"]["additionalContext"]
    assert "完了通知を唯一の再開契機" in context

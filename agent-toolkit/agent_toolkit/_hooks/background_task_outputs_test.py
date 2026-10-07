"""未完了の背景Bashの出力ファイルを読む操作への警告のテスト。

transcriptの起動記録は、Claude Code 2.1.292のtranscriptで観測した3形式（`run_in_background`、実行上限による移行、
手動の背景化）の`toolUseResult`と`tool_result`本文から形を写す。
PostToolUseの`tool_response`は構造化した`backgroundTaskId`だけを持つため、出力パスは`tool_result`本文から得る。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit._hooks import background_task_outputs as subject
from agent_toolkit._hooks import posttooluse
from agent_toolkit._hooks.pretooluse import dispatch

_WARNING = "未完了のバックグラウンドタスクが書き込む出力ファイルを読み取ろうとしている。"

# 形式名 → (`toolUseResult`の追加項目, `tool_result`本文の書式, 読取コマンド)
_BACKGROUND_FORMS: dict[str, tuple[dict, str, str]] = {
    "run_in_background": (
        {},
        "Command running in background with ID: {task_id}. Output is being written to: {path}. "
        "You will be notified when it completes. To check interim output, use Read on that file path.",
        "cat",
    ),
    "timeout": (
        {"timedOutAfterMs": 600000},
        "Command did not complete within its 600s timeout and was moved to the background (ID: {task_id}). "
        "Output is being written to: {path}. You will be notified when it completes.",
        "tail -n 20",
    ),
    "manual": (
        {"backgroundedByUser": True},
        "Command was manually backgrounded by user with ID: {task_id}. Output is being written to: {path}.",
        "head",
    ),
}


def _assistant_bash(tool_use_id: str, command: str) -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": tool_use_id, "name": "Bash", "input": {"command": command}}],
        },
    }


def _tool_result(tool_use_id: str, text: str, tool_use_result: object) -> dict:
    return {
        "type": "user",
        "toolUseResult": tool_use_result,
        "message": {
            "role": "user",
            "content": [{"tool_use_id": tool_use_id, "type": "tool_result", "content": text, "is_error": False}],
        },
    }


def _background_launch(form: str, task_id: str, tool_use_id: str, path: str) -> tuple[list[dict], dict]:
    """起動のtranscript記録と、同じ起動でPostToolUseが受け取る構造化応答を返す。"""
    extra, text_format, _read_command = _BACKGROUND_FORMS[form]
    response = {
        "stdout": "",
        "stderr": "",
        "interrupted": False,
        "isImage": False,
        "noOutputExpected": False,
        "backgroundTaskId": task_id,
        **extra,
    }
    entries = [
        _assistant_bash(tool_use_id, "make test"),
        _tool_result(tool_use_id, text_format.format(task_id=task_id, path=path), response),
    ]
    return entries, response


def _completion_entry(delivery: str, task_id: str, tool_use_id: str, path: str) -> dict:
    notification = (
        f"<task-notification>\n<task-id>{task_id}</task-id>\n<tool-use-id>{tool_use_id}</tool-use-id>\n"
        f"<output-file>{path}</output-file>\n<status>completed</status>\n"
        f'<summary>Background command "make test" completed (exit code 0)</summary>\n</task-notification>'
    )
    if delivery == "queue":
        return {"type": "queue-operation", "operation": "enqueue", "content": notification}
    return {"type": "user", "origin": {"kind": "task-notification"}, "message": {"role": "user", "content": notification}}


def _write_transcript(path: pathlib.Path, entries: list[dict]) -> str:
    path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    return str(path)


def _record_launch(session_id: str, response: dict, command: str = "make test") -> None:
    """PostToolUseのhookの`main`へ起動の応答を渡し、バックグラウンドタスクの所有記録を書かせる。"""
    payload = {
        "session_id": session_id,
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command, "run_in_background": True},
        "tool_response": response,
    }
    assert posttooluse.main(json.dumps(payload)) == 0


def _pretooluse_bash_context(
    capsys: pytest.CaptureFixture[str], session_id: str, transcript_path: str, command: str, cwd: pathlib.Path
) -> str:
    """PreToolUse(Bash)のhookの`main`を実行し、`additionalContext`を返す。"""
    capsys.readouterr()
    payload = {
        "session_id": session_id,
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "transcript_path": transcript_path,
        "cwd": str(cwd),
    }
    assert dispatch.main(json.dumps(payload)) == 0
    contexts = [
        json.loads(line)["hookSpecificOutput"].get("additionalContext", "")
        for line in capsys.readouterr().out.splitlines()
        if line.strip()
    ]
    return "\n".join(contexts)


@pytest.mark.parametrize("form", list(_BACKGROUND_FORMS))
def test_pretooluse_warns_reading_pending_output_of_each_background_form(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], form: str
) -> None:
    """3形式の背景移行とも、未完了の間に出力ファイルを読むと完了後の読み直しを示して警告する。"""
    session_id = f"pending-{form}"
    output_path = str(tmp_path / "tasks" / "bg-1.output")
    entries, response = _background_launch(form, "bg-1", "toolu_launch", output_path)
    _record_launch(session_id, response)
    read_command = _BACKGROUND_FORMS[form][2]
    entries.append(_assistant_bash("toolu_read", f"{read_command} {output_path}"))
    transcript = _write_transcript(tmp_path / "transcript.jsonl", entries)

    context = _pretooluse_bash_context(capsys, session_id, transcript, f"{read_command} {output_path}", tmp_path)

    assert _WARNING in context
    assert "完了通知を受けた後に同じ出力ファイルを読み直してから結果として使う" in context


@pytest.mark.parametrize("delivery", ["queue", "user"])
@pytest.mark.parametrize("form", list(_BACKGROUND_FORMS))
def test_pretooluse_does_not_warn_after_completion_notice(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], form: str, delivery: str
) -> None:
    """完了通知がキュー取り込みとユーザー発話のいずれで記録された後も、同じ読取を警告しない。"""
    output_path = str(tmp_path / "tasks" / "bg-1.output")
    entries, response = _background_launch(form, "bg-1", "toolu_launch", output_path)
    # 完了通知の前は同じ読取を警告し、通知の記録が警告の有無を分けることを示す。
    before = _write_transcript(tmp_path / "before.jsonl", entries)
    _record_launch(f"before-{form}-{delivery}", response)
    assert _WARNING in _pretooluse_bash_context(capsys, f"before-{form}-{delivery}", before, f"cat {output_path}", tmp_path)

    session_id = f"completed-{form}-{delivery}"
    _record_launch(session_id, response)
    entries.append(_completion_entry(delivery, "bg-1", "toolu_launch", output_path))
    transcript = _write_transcript(tmp_path / "transcript.jsonl", entries)

    context = _pretooluse_bash_context(capsys, session_id, transcript, f"cat {output_path}", tmp_path)

    assert _WARNING not in context


def test_pretooluse_ignores_output_path_in_foreground_result(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """前景Bashと他ツールの結果本文に現れた出力パスの文言は、未完了の出力として扱わない。"""
    pending_path = str(tmp_path / "tasks" / "bg-1.output")
    foreground_path = str(tmp_path / "quoted.output")
    mcp_path = str(tmp_path / "mcp.output")
    entries, response = _background_launch("run_in_background", "bg-1", "toolu_launch", pending_path)
    foreground_text = f"log line: Output is being written to: {foreground_path}"
    entries += [
        _assistant_bash("toolu_foreground", "cat notes.txt"),
        _tool_result(
            "toolu_foreground",
            foreground_text,
            {"stdout": foreground_text, "stderr": "", "interrupted": False, "isImage": False},
        ),
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "stop_reason": "tool_use",
                "content": [{"type": "tool_use", "id": "toolu_mcp", "name": "mcp__example__grep", "input": {}}],
            },
        },
        _tool_result("toolu_mcp", f"match: Output is being written to: {mcp_path}", [{"type": "text"}]),
    ]
    transcript = _write_transcript(tmp_path / "transcript.jsonl", entries)

    for index, path in enumerate((foreground_path, mcp_path)):
        session_id = f"foreground-{index}"
        _record_launch(session_id, response)
        assert _WARNING not in _pretooluse_bash_context(capsys, session_id, transcript, f"cat {path}", tmp_path)
    # 同じtranscriptで起動記録の出力パスは警告対象になり、前景出力の非対象が判定の空振りでないことを示す。
    _record_launch("foreground-pending", response)
    assert _WARNING in _pretooluse_bash_context(capsys, "foreground-pending", transcript, f"cat {pending_path}", tmp_path)


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

import json
import pathlib
from typing import Literal

from agent_toolkit._common import transcript as _transcript
from agent_toolkit._hooks import background_tasks as _background_tasks
from agent_toolkit._testing.helpers import _write_transcript


def _describe_pending_background_tasks(transcript_path: str, session_id: str) -> tuple[set[str], set[str]]:
    launched, completed, _host_reported = _background_tasks.describe_pending_background_entries(
        _transcript.read_transcript_entries(transcript_path),
        session_id,
        transcript_path=transcript_path,
    )
    return launched, completed


def _host_mcp_notice(task_id: str, tool_name: str = "mcp__agents_server__wait") -> str:
    """MCP呼び出しが背景へ移ったときにホストが返す移行通知の本文を返す。

    Claude Code 2.1.292のtranscriptで観測した形（テキストの先頭から始まる単独の本文）を写す。
    """
    return (
        f'MCP tool "{tool_name}" is still running after 120s. It was moved to the background as task {task_id}'
        " and keeps running; you will be notified when it completes."
    )


def _assistant_entry(content: list[dict], *, msg_id: str = "msg_test", stop_reason: str = "end_turn") -> dict:
    return {
        "type": "assistant",
        "message": {"id": msg_id, "role": "assistant", "content": content, "stop_reason": stop_reason},
    }


def _user_entry(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _user_async_launched_entry(
    tool_use_id: str,
    *,
    sidechain: bool = False,
    agent_id: str = "agent-x",
) -> dict:
    """Background Agent起動を記録するuserエントリを生成する。

    実transcriptフォーマットに合わせ、`toolUseResult.status == "async_launched"`と
    `message.content`配列内の`tool_result`ブロックを持たせる。
    """
    return {
        "type": "user",
        "isSidechain": sidechain,
        "toolUseResult": {"isAsync": True, "status": "async_launched", "agentId": agent_id},
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [{"type": "text", "text": "Async agent launched successfully"}],
                }
            ],
        },
    }


def _assistant_agent_entry(tool_use_id: str, *, sidechain: bool = True) -> dict:
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_use_id, "name": "Agent", "input": {}}],
            "stop_reason": "tool_use",
        },
    }


def _user_agent_launch_marker_entry(tool_use_id: str, *, status: str | None = None) -> dict:
    tool_use_result: dict[str, object] = {"isAsync": True, "agentId": "agent-real-shape"}
    if status is not None:
        tool_use_result["status"] = status
    return {
        "type": "user",
        "isSidechain": True,
        "toolUseResult": tool_use_result,
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [{"type": "text", "text": "Async agent launched successfully"}],
                }
            ],
        },
    }


def _user_background_bash_entry(tool_use_id: str, *, sidechain: bool = False) -> dict:
    """Background Bash起動を記録するuserエントリを生成する。

    実transcriptフォーマットに合わせ、`toolUseResult.backgroundTaskId`を持たせ、
    `message.content`配列内の`tool_result`ブロックに対応`tool_use_id`を含める。
    """
    return {
        "type": "user",
        "isSidechain": sidechain,
        "toolUseResult": {
            "stdout": "",
            "stderr": "",
            "backgroundTaskId": "bash-task-x",
        },
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [{"type": "text", "text": "Background command launched"}],
                }
            ],
        },
    }


def _user_task_stop_success_entry(stopped_task_id: str, *, tool_use_id: str = "toolu_stop") -> dict:
    return {
        "type": "user",
        "isSidechain": False,
        "toolUseResult": {
            "message": f"Successfully stopped task: {stopped_task_id} (sleep 60)",
            "task_id": stopped_task_id,
            "task_type": "local_bash",
            "command": "sleep 60",
        },
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": f"Successfully stopped task: {stopped_task_id}",
                    "is_error": False,
                }
            ],
        },
    }


def _assistant_sendmessage_entry(tool_use_id: str, *, sidechain: bool = False) -> dict:
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {
            "id": "msg_sm",
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": "SendMessage",
                    "input": {"message": "続きを再開してください"},
                }
            ],
            "stop_reason": "tool_use",
        },
    }


def _user_sendmessage_bg_resume_entry(
    tool_use_id: str, *, sidechain: bool = False, text_format: Literal["list", "str"] = "list"
) -> dict:
    """SendMessage背景再開を記録するuserエントリを生成する。

    `text_format="list"`のとき`content`はリスト形式のtextブロック、
    `text_format="str"`のとき`content`は文字列形式で返す。
    `_extract_sendmessage_bg_resume_id`が両形式を正しく処理することを検証するために用いる。
    """
    resume_text = "Agent aecb02dc74cf84e99 resumed from transcript in the background with your message."
    if text_format == "str":
        content: str | list[dict] = resume_text
    else:
        content = [{"type": "text", "text": resume_text}]
    return {
        "type": "user",
        "isSidechain": sidechain,
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": content,
                }
            ],
        },
    }


def _user_sendmessage_bg_resume_current_entry(
    tool_use_id: str,
    *,
    sidechain: bool = False,
    resumed_agent_id: object = "aecb02dc74cf84e99",
    resume_text: str | None = None,
) -> dict:
    body = resume_text if resume_text is not None else f"Resuming agent {resumed_agent_id}"
    return {
        "type": "user",
        "isSidechain": sidechain,
        "toolUseResult": {
            "success": True,
            "message": f"Resuming agent {resumed_agent_id}",
            "resumedAgentId": resumed_agent_id,
        },
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [{"type": "text", "text": body}],
                }
            ],
        },
    }


def _task_notification_body(tool_use_id: str | None, *, task_id: str | None = "task-x", status: str = "completed") -> str:
    parts = ["<task-notification>"]
    if task_id is not None:
        parts.append(f"<task-id>{task_id}</task-id>")
    if tool_use_id is not None:
        parts.append(f"<tool-use-id>{tool_use_id}</tool-use-id>")
    parts.append(f"<status>{status}</status><summary>sub agent finished</summary></task-notification>")
    return "".join(parts)


def _user_task_notification_entry(
    tool_use_id: str | None, *, task_id: str | None = "task-x", status: str = "completed"
) -> dict:
    notification = _task_notification_body(tool_use_id, task_id=task_id, status=status)
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": notification}],
        },
    }


def _monitor_launch_entries(tool_use_id: str, task_id: str) -> list[dict]:
    return [
        {
            "type": "assistant",
            "isSidechain": False,
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": tool_use_id, "name": "Monitor", "input": {}}],
            },
        },
        {
            "type": "user",
            "isSidechain": False,
            "toolUseResult": {"taskId": task_id, "timeoutMs": 3600000, "persistent": False},
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": "Monitor started"}],
            },
        },
    ]


def _user_non_monitor_task_id_entry(tool_use_id: str, task_id: str) -> dict:
    """Monitor以外のツールが`taskId`キーを返すuserエントリを生成する（実transcript調査で確認した衝突ケース）。

    `success`・`updatedFields`・`statusChange`キーを伴う形で観測された、
    Monitor以外の`toolUseResult.taskId`使用例を再現する。対応するassistant tool_useを
    `name == "Monitor"`以外にすることで、キーの存在だけで真とする誤判定が
    再発した場合にテストが失敗するようにする。
    """
    return {
        "type": "user",
        "isSidechain": False,
        "toolUseResult": {"success": True, "taskId": task_id, "updatedFields": [], "statusChange": None},
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": "updated"}],
        },
    }


def _attachment_task_notification_entry(
    tool_use_id: str | None,
    *,
    task_id: str | None = "task-x",
    status: str = "completed",
    command_mode: str = "task-notification",
    sidechain: bool = False,
) -> dict:
    """`<task-notification>`本文を持つattachmentエントリを生成する（Claude Code 2.1系以降の新形式）。

    `command_mode`を`task-notification`以外に上書きすると、走査対象から外れる否定ケースを再現できる。
    `tool_use_id`または`task_id`に`None`を渡すと該当要素を省略できる（`_task_notification_body`参照）。
    """
    notification = _task_notification_body(tool_use_id, task_id=task_id, status=status)
    return {
        "type": "attachment",
        "isSidechain": sidechain,
        "attachment": {
            "type": "queued_command",
            "prompt": notification,
            "commandMode": command_mode,
        },
    }


def _queue_operation_task_notification_entry(
    operation: str,
    *,
    tool_use_id: str | None = None,
    task_id: str | None = "task-x",
) -> dict:
    return {
        "type": "queue-operation",
        "operation": operation,
        "content": _task_notification_body(tool_use_id, task_id=task_id),
    }


def _write_nested_subagent_fixture(
    directory: pathlib.Path,
    entries: list[dict],
    child_entries: list[dict],
    *,
    child_id: str = "child-id",
    metadata: dict[str, object] | None = None,
) -> pathlib.Path:
    transcript = _write_transcript(directory, entries)
    subagents_dir = transcript.with_suffix("") / "subagents"
    subagents_dir.mkdir(parents=True)
    child_path = subagents_dir / f"agent-{child_id}.jsonl"
    child_path.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in child_entries) + "\n",
        encoding="utf-8",
    )
    metadata_path = child_path.with_name(f"{child_path.stem}.meta.json")
    metadata_path.write_text(
        json.dumps(metadata if metadata is not None else {"spawnDepth": 1}),
        encoding="utf-8",
    )
    return transcript


def _user_foreground_agent_entry(tool_use_id: str) -> dict:
    """Foreground Agent完了を記録するuserエントリを生成する。

    `toolUseResult.status`が`completed`の同期完了パスを再現する。
    `is_pending_async_work`がforeground Agentを未完了扱いしないことの確認に使う。
    """
    return {
        "type": "user",
        "isSidechain": False,
        "toolUseResult": {"status": "completed", "agentId": "agent-x"},
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [{"type": "text", "text": "Agent completed"}],
                }
            ],
        },
    }


def _bash_no_bg() -> dict:
    """非同期でないBash tool_useブロックを返す（最終ターンの末尾を構造的にtool_useで終了するため）。"""
    return {"type": "tool_use", "id": "x", "name": "Bash", "input": {"command": "echo done"}}

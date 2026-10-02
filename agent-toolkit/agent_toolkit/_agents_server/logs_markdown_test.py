"""旧エクスポーターから移したMarkdownの会話境界を検証する。"""

from __future__ import annotations

import pathlib
import typing

from agent_toolkit._agents_server import logs_markdown


def test_parent_filters_sidechain_and_meta_but_keeps_queued_user(tmp_path: pathlib.Path) -> None:
    """メイン記録から割り込み中の応答と実行環境の挿入を除き、後着の人間の指示を残す。"""
    records: list[dict[str, typing.Any]] = [
        {"type": "user", "timestamp": "2026-01-01T00:00:01Z", "message": {"content": "最初の質問"}},
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:02Z",
            "isSidechain": True,
            "message": {"content": [{"type": "text", "text": "中断された応答"}]},
        },
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:03Z",
            "isMeta": True,
            "message": {"content": "注入された指示"},
        },
        {"type": "queue-operation", "operation": "enqueue", "timestamp": "2026-01-01T00:00:04Z", "content": "追加の指示"},
        {
            "type": "queue-operation",
            "operation": "enqueue",
            "timestamp": "2026-01-01T00:00:05Z",
            "content": "<task-notification>内部通知</task-notification>",
        },
    ]

    markdown = logs_markdown.render_session("claude", records, "session-1", tmp_path / "session-1.jsonl")

    assert "最初の質問" in markdown
    assert "追加の指示" in markdown
    assert "中断された応答" not in markdown
    assert "注入された指示" not in markdown
    assert "内部通知" not in markdown


def test_separate_assistant_messages_keep_turn_boundary_and_string_result(tmp_path: pathlib.Path) -> None:
    """別のmessage.idは別ターンとし、文字列のツール結果を呼出しへ結び付ける。"""
    records = [
        {"type": "user", "timestamp": "2026-01-01T00:00:01Z", "message": {"content": "ファイルを読んで"}},
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:02Z",
            "message": {"id": "msg1", "content": [{"type": "text", "text": "読みます"}]},
        },
        {
            "type": "assistant",
            "timestamp": "2026-01-01T00:00:03Z",
            "message": {
                "id": "msg2",
                "content": [{"type": "tool_use", "id": "tool1", "name": "Read", "input": {"file_path": "/tmp/a"}}],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-01-01T00:00:04Z",
            "message": {"content": [{"type": "tool_result", "tool_use_id": "tool1", "content": "内容"}]},
        },
    ]

    markdown = logs_markdown.render_session("claude", records, "session-1", tmp_path / "session-1.jsonl")

    assert markdown.count("## Assistant") == 2
    assert "<summary>Tool: Read" in markdown
    assert "**Result:**\n\n```\n内容\n```" in markdown
    assert markdown.count("## Human") == 1

"""セッション振り返り用の証拠抽出を検証する。"""

from __future__ import annotations

import json
import pathlib

import _session_review_evidence as evidence


def _write_transcript(path: pathlib.Path, entries: list[dict]) -> pathlib.Path:
    transcript = path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n",
        encoding="utf-8",
    )
    return transcript


def test_extracts_selected_events_in_order(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "依頼"}},
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "作業中"}]},
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "tool-x", "is_error": True, "content": "失敗"}],
                },
            },
            {"type": "interrupt", "message": {"role": "user", "content": "中断"}},
            {
                "type": "user",
                "toolUseResult": {"status": "completed", "agentId": "agent-1", "summary": "完了報告"},
                "message": {"role": "user", "content": "<task-notification>内部通知</task-notification>"},
            },
            {
                "type": "assistant",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "最終結果"}]},
            },
        ],
    )

    events = evidence.load_and_extract(str(transcript))

    assert [event["kind"] for event in events] == [
        "user",
        "assistant",
        "failed-tool",
        "interrupt",
        "agent-completion",
        "final-result",
    ]
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert events[-1]["text"] == "最終結果"


def test_excludes_normal_tool_output_and_clips_failed_output(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "ok", "content": "通常出力" * 1000},
                        {
                            "type": "tool_result",
                            "tool_use_id": "bad",
                            "is_error": True,
                            "content": "エラー" * 1000,
                        },
                    ],
                },
            }
        ],
    )

    events = evidence.load_and_extract(str(transcript))

    assert len(events) == 1
    assert events[0]["kind"] == "failed-tool"
    assert "通常出力" not in events[0]["text"]
    assert events[0]["text"].endswith("…[省略]")


def test_missing_path_returns_fallback_instruction() -> None:
    events = evidence.load_and_extract(None)

    assert len(events) == 1
    assert events[0]["sequence"] == 1
    assert events[0]["kind"] == "fallback"
    assert "継承した会話履歴" in events[0]["text"]
    assert "未検証" in events[0]["text"]


def test_main_writes_jsonl_to_stdout(tmp_path: pathlib.Path, capsys) -> None:
    transcript = _write_transcript(
        tmp_path,
        [{"type": "user", "message": {"role": "user", "content": "入力"}}],
    )

    assert evidence.main([str(transcript)]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    lines = output.out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"kind": "user", "text": "入力", "sequence": 1}


def test_extracts_codex_rollout_events_and_ignores_unconfirmed_items(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "依頼"}]},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "途中結果"}],
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {"type": "CommandExecution", "status": "failed", "aggregated_output": "失敗出力"},
                },
            },
            {"type": "event_msg", "payload": {"type": "turn_aborted", "reason": "interrupted"}},
            {
                "type": "response_item",
                "payload": {"type": "agent_message", "message": "Message Type: MESSAGE\n通常連絡"},
            },
            {
                "type": "response_item",
                "payload": {"type": "agent_message", "message": "Message Type: FINAL_ANSWER\n完了報告"},
            },
            {
                "type": "event_msg",
                "payload": {"type": "item_completed", "item": {"type": "SubAgentActivity", "status": "interacted"}},
            },
        ],
    )

    events = evidence.load_and_extract(str(transcript))

    assert [event["kind"] for event in events] == [
        "user",
        "final-result",
        "failed-tool",
        "interrupt",
        "agent-completion",
    ]
    assert events[2]["tool"] == "CommandExecution"
    assert events[-1]["text"].endswith("完了報告")


def test_codex_failed_command_without_output_keeps_failure_event(tmp_path: pathlib.Path) -> None:
    """出力が空でも非0終了のCommandExecutionを証拠から欠落させない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "status": "failed",
                        "aggregated_output": "",
                        "exit_code": 1,
                    },
                },
            }
        ],
    )

    events = evidence.load_and_extract(str(transcript))

    assert len(events) == 1
    assert events[0]["kind"] == "failed-tool"
    assert events[0]["tool"] == "CommandExecution"
    assert '"exit_code": 1' in events[0]["text"]


def test_manual_review_invocation_excludes_following_codex_events(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "本来の最終結果"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "/agent-toolkit:session-review"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "振り返り中"}],
                },
            },
        ],
    )

    assert evidence.load_and_extract(str(transcript)) == [{"kind": "final-result", "text": "本来の最終結果", "sequence": 1}]


def test_manual_review_invocation_excludes_following_claude_events(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "assistant", "message": {"role": "assistant", "content": "本来の最終結果"}},
            {"type": "user", "message": {"role": "user", "content": "/session-review"}},
            {"type": "assistant", "message": {"role": "assistant", "content": "振り返り中"}},
        ],
    )

    assert evidence.load_and_extract(str(transcript)) == [{"kind": "final-result", "text": "本来の最終結果", "sequence": 1}]


def test_unsupported_nonempty_jsonl_returns_fallback(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(tmp_path, [{"type": "unknown", "payload": {"type": "unknown"}}])

    events = evidence.load_and_extract(str(transcript))

    assert [event["kind"] for event in events] == ["fallback"]

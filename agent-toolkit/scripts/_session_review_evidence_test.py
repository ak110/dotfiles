"""セッション振り返り用の証拠抽出を検証する。"""

from __future__ import annotations

import json
import pathlib

import _session_review_evidence as evidence
import pytest
from _test_helpers import _write_transcript


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


def test_claude_question_answers_become_one_user_event_in_insertion_order(tmp_path: pathlib.Path) -> None:
    """Claudeの質問回答だけを質問順の単一userイベントへ変換する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "AskUserQuestion", "id": "question"}],
                },
            },
            {
                "type": "user",
                "toolUseResult": {
                    "answers": {"最初の質問": "最初の回答", "次の質問": "次の回答"},
                    "questions": [{"question": "選択肢定義は証拠化しない"}],
                },
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "question", "content": "通常出力"}],
                },
            },
        ],
    )

    events = evidence.load_and_extract(str(transcript))

    assert events == [
        {
            "kind": "user",
            "text": "質問: 最初の質問\n回答: 最初の回答\n質問: 次の質問\n回答: 次の回答",
            "line": 1,
            "sequence": 1,
        }
    ]


@pytest.mark.parametrize(
    "answers",
    [
        {"質問": ["文字列ではない"]},
        {"質問": "回答", "不正な質問": 1},
        [],
    ],
)
def test_claude_ignores_non_string_answer_maps(tmp_path: pathlib.Path, answers: object) -> None:
    """文字列辞書ではないClaude answersから利用者判断を捏造しない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "AskUserQuestion", "id": "question"}],
                },
            },
            {
                "type": "user",
                "toolUseResult": {"answers": answers, "questions": []},
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "question", "content": "通常出力"}],
                },
            },
        ],
    )

    assert evidence.load_and_extract(str(transcript)) == []


@pytest.mark.parametrize(
    "tool_result",
    [
        {"answers": {"質問": "通常ツールの値"}},
        {"questions": [{"question": "回答がない質問"}]},
        {"questions": [], "answers": {"項目": "値"}},
    ],
)
def test_claude_ignores_unmatched_normal_tool_results(
    tmp_path: pathlib.Path,
    tool_result: dict[str, object],
) -> None:
    """payload形状にかかわらず未対応の通常tool resultを利用者判断へ変換しない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": tool_result,
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "normal", "content": "通常出力"}],
                },
            }
        ],
    )

    assert evidence.load_and_extract(str(transcript)) == []


def test_claude_matches_multiple_question_ids_and_ignores_repeated_result(tmp_path: pathlib.Path) -> None:
    """複数の保留IDを個別に対応し、対応済み・未知IDからイベントを捏造しない。"""
    answer_result = {"answers": {"質問": "回答"}, "questions": []}
    second_answer_result = {"answers": {"別の質問": "別の回答"}, "questions": []}
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "AskUserQuestion", "id": "known"},
                        {"type": "tool_use", "name": "AskUserQuestion", "id": "second"},
                        {"type": "tool_use", "name": "OtherTool", "id": "normal"},
                    ],
                },
            },
            {
                "type": "user",
                "toolUseResult": answer_result,
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "unknown", "content": "通常出力"}],
                },
            },
            {
                "type": "user",
                "toolUseResult": answer_result,
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "known", "content": "通常出力"}],
                },
            },
            {
                "type": "user",
                "toolUseResult": answer_result,
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "known", "content": "通常出力"}],
                },
            },
            {
                "type": "user",
                "toolUseResult": answer_result,
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "normal", "content": "通常出力"}],
                },
            },
            {
                "type": "user",
                "toolUseResult": second_answer_result,
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "second", "content": "通常出力"}],
                },
            },
        ],
    )

    assert evidence.load_and_extract(str(transcript)) == [
        {"kind": "user", "text": "質問: 質問\n回答: 回答", "line": 1, "sequence": 1},
        {"kind": "user", "text": "質問: 別の質問\n回答: 別の回答", "line": 1, "sequence": 2},
    ]


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
    assert json.loads(lines[0]) == {"kind": "user", "text": "入力", "line": 1, "sequence": 1}


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


def test_codex_question_output_becomes_user_event_at_output_position(tmp_path: pathlib.Path) -> None:
    """Codexの質問定義と回答をcall_idで対応付け、回答位置へuserイベントを置く。"""
    arguments = {
        "questions": [
            {"id": "first", "question": "最初の質問", "options": [{"label": "非出力の選択肢"}]},
            {"id": "second", "question": "次の質問"},
            "不正な質問定義",
        ]
    }
    output = {
        "answers": {
            "first": {"answers": ["最初の回答"]},
            "second": {"answers": ["次の回答1", "次の回答2"]},
            "unknown": {"answers": ["未知IDの回答"]},
        }
    }
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "request_user_input",
                    "call_id": "call-question",
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "回答待ち"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-question",
                    "output": json.dumps(output, ensure_ascii=False),
                },
            },
        ],
    )

    events = evidence.load_and_extract(str(transcript))

    assert events == [
        {"kind": "final-result", "text": "回答待ち", "line": 2, "sequence": 1},
        {
            "kind": "user",
            "text": "質問: 最初の質問\n回答: 最初の回答\n質問: 次の質問\n回答: 次の回答1\n次の回答2",
            "line": 1,
            "sequence": 2,
        },
    ]


def test_codex_question_call_ids_keep_local_question_identity(tmp_path: pathlib.Path) -> None:
    """同じ質問IDを持つ複数callを、各call_idの質問文へ対応付ける。"""
    entries: list[dict] = []
    for call_id, question in (("call-one", "一つ目"), ("call-two", "二つ目")):
        entries.append(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "request_user_input",
                    "call_id": call_id,
                    "arguments": json.dumps({"questions": [{"id": "shared", "question": question}]}),
                },
            }
        )
    for call_id, answer in (("call-two", "回答2"), ("call-one", "回答1")):
        entries.append(
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps({"answers": {"shared": {"answers": [answer]}}}),
                },
            }
        )
    transcript = _write_transcript(tmp_path, entries)

    events = evidence.load_and_extract(str(transcript))

    assert [event["text"] for event in events] == ["質問: 二つ目\n回答: 回答2", "質問: 一つ目\n回答: 回答1"]


@pytest.mark.parametrize(
    "entries",
    [
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "request_user_input",
                    "call_id": "broken-call",
                    "arguments": "{broken",
                },
            }
        ],
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "unknown-call",
                    "output": json.dumps({"answers": {"question": {"answers": ["回答"]}}}),
                },
            }
        ],
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "request_user_input",
                    "call_id": "known-call",
                    "arguments": json.dumps({"questions": [{"id": "known", "question": "質問"}]}),
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "known-call",
                    "output": "{broken",
                },
            },
        ],
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "request_user_input",
                    "call_id": "known-call",
                    "arguments": json.dumps({"questions": [{"id": "known", "question": "質問"}]}),
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "known-call",
                    "output": json.dumps({"answers": {"unknown": {"answers": ["回答"]}}}),
                },
            },
        ],
    ],
)
def test_codex_ignores_malformed_or_unmatched_question_payloads(
    tmp_path: pathlib.Path,
    entries: list[dict],
) -> None:
    """不正なJSON、未対応call、未知の質問IDから証拠を捏造しない。"""
    transcript = _write_transcript(tmp_path, entries)

    assert evidence.load_and_extract(str(transcript)) == []


def test_codex_agent_message_block_array_extracts_only_final_answer(tmp_path: pathlib.Path) -> None:
    """配列形式のFINAL_ANSWERだけを完了報告とし、通常MESSAGEは除外する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "agent_message",
                    "content": [{"type": "input_text", "text": "Message Type: MESSAGE\n通常連絡"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "agent_message",
                    "content": [
                        {"type": "input_text", "text": "Message Type: FINAL_ANSWER"},
                        {"type": "input_text", "text": "完了報告"},
                    ],
                },
            },
        ],
    )

    events = evidence.load_and_extract(str(transcript))

    assert events == [{"kind": "agent-completion", "text": "Message Type: FINAL_ANSWER\n完了報告", "line": 2, "sequence": 1}]


@pytest.mark.parametrize("key", ["message", "text", "content"])
def test_codex_agent_message_keeps_string_container_compatibility(tmp_path: pathlib.Path, key: str) -> None:
    """agent_messageの既存文字列containerを完了報告として保持する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {"type": "agent_message", key: "Message Type: FINAL_ANSWER\n文字列完了報告"},
            }
        ],
    )

    events = evidence.load_and_extract(str(transcript))

    assert events[0]["kind"] == "agent-completion"
    assert events[0]["text"].endswith("文字列完了報告")


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


@pytest.mark.parametrize("command", [["git", "status"], ["tool", "one", "two"], []])
def test_codex_failed_command_keeps_structured_command_and_exit_code(tmp_path: pathlib.Path, command: list[str]) -> None:
    """失敗CommandExecutionは配列構造と終了コードを本文とは別に保持する。"""
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
                        "command": command,
                        "exit_code": 17,
                        "stderr": "失敗",
                    },
                },
            }
        ],
    )

    event = evidence.load_and_extract(str(transcript))[0]

    assert event["command"] == json.dumps(command, ensure_ascii=False)
    assert event["exit_code"] == 17
    assert event["text"] == "失敗"


def test_codex_failed_command_clips_only_long_structured_command(tmp_path: pathlib.Path) -> None:
    """長大commandは既存の証拠上限と省略標識に従う。"""
    command = ["x" * 2100]
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
                        "command": command,
                        "aggregated_output": "失敗",
                    },
                },
            }
        ],
    )

    event = evidence.load_and_extract(str(transcript))[0]

    assert event["command"].endswith("…[省略]")
    assert len(event["command"]) == 2000 + len("…[省略]")


def test_claude_started_marker_excludes_automatic_review_events(tmp_path: pathlib.Path) -> None:
    """Claude形式ではStop注入だけを除き、起動後の人間介入を保持する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "assistant", "message": {"role": "assistant", "content": "本来の最終結果"}},
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Stop hook feedback:\n" + evidence.STOP_ADVISOR_PREFIX + " 誘導",
                        },
                        {"type": "text", "text": "同じ注入エントリの残余本文"},
                    ],
                },
            },
            {
                "type": "user",
                "toolUseResult": {"stdout": evidence.SESSION_REVIEW_STARTED_MARKER},
                "message": {"role": "user", "content": []},
            },
            {
                "type": "attachment",
                "attachment": {
                    "type": "queued_command",
                    "origin": {"kind": "human"},
                    "prompt": "開始後の介入",
                },
            },
        ],
    )

    assert evidence.load_and_extract(str(transcript)) == [
        {"kind": "final-result", "text": "本来の最終結果", "line": 1, "sequence": 1},
        {"kind": "user", "text": "開始後の介入", "line": 4, "sequence": 2},
    ]
    assert evidence.has_session_review_started(str(transcript)) is True


def test_claude_skill_injection_keeps_only_invocation_line(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "通常の依頼"}},
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Base directory for this skill: /plugin/skills/example\n# 長いスキル本文\n規範",
                        },
                        {"type": "text", "text": "同じ注入エントリの残余本文"},
                    ],
                },
            },
        ],
    )

    events = evidence.load_and_extract(str(transcript))

    assert [event["kind"] for event in events] == ["user", "skill-invocation"]
    assert events[1]["text"] == "Base directory for this skill: /plugin/skills/example"
    assert "長いスキル本文" not in events[1]["text"]


def test_claude_normal_user_entry_keeps_multiple_text_blocks_in_order(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "最初の入力"},
                        {"type": "text", "text": "次の入力"},
                    ],
                },
            },
        ],
    )

    events = evidence.load_and_extract(str(transcript))

    assert [event["text"] for event in events] == ["最初の入力", "次の入力"]


def test_claude_queued_commands_keep_only_human_prompts(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "依頼"}},
            {
                "type": "attachment",
                "attachment": {
                    "type": "queued_command",
                    "origin": {"kind": "human"},
                    "prompt": "人間の割り込み",
                },
            },
            {
                "type": "attachment",
                "attachment": {
                    "type": "queued_command",
                    "origin": {"kind": "peer"},
                    "prompt": "peer通知",
                },
            },
            {
                "type": "attachment",
                "attachment": {
                    "type": "queued_command",
                    "origin": {"kind": "human"},
                    "commandMode": "task-notification",
                    "prompt": "task通知",
                },
            },
            {"type": "queue-operation", "prompt": "重複記録"},
        ],
    )

    events = evidence.load_and_extract(str(transcript))

    assert [event["text"] for event in events] == ["依頼", "人間の割り込み"]


def test_queued_manual_review_command_sets_claude_boundary(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "assistant", "message": {"role": "assistant", "content": "本来の最終結果"}},
            {
                "type": "attachment",
                "attachment": {
                    "type": "queued_command",
                    "origin": {"kind": "human"},
                    "prompt": "/agent-toolkit:session-review",
                },
            },
            {"type": "assistant", "message": {"role": "assistant", "content": "振り返り中"}},
        ],
    )

    assert evidence.load_and_extract(str(transcript)) == [
        {"kind": "final-result", "text": "本来の最終結果", "line": 1, "sequence": 1}
    ]
    assert evidence.has_session_review_started(str(transcript)) is True


@pytest.mark.parametrize("command", ["/session-review", "/agent-toolkit:session-review"])
def test_claude_syntax_is_not_codex_manual_review_boundary(tmp_path: pathlib.Path, command: str) -> None:
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
                    "content": [{"type": "input_text", "text": command}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "slash後の本来の作業結果"}],
                },
            },
        ],
    )

    events = evidence.load_and_extract(str(transcript))

    assert [event["text"] for event in events] == [
        "本来の最終結果",
        command,
        "slash後の本来の作業結果",
    ]
    assert events[-1]["kind"] == "final-result"
    assert evidence.has_session_review_started(str(transcript)) is False


@pytest.mark.parametrize("command", ["$session-review", "$agent-toolkit:session-review"])
def test_dollar_manual_review_invocation_excludes_following_codex_events(tmp_path: pathlib.Path, command: str) -> None:
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
                    "content": [{"type": "input_text", "text": command}],
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

    assert evidence.load_and_extract(str(transcript)) == [
        {"kind": "final-result", "text": "本来の最終結果", "line": 1, "sequence": 1}
    ]
    assert evidence.has_session_review_started(str(transcript)) is True


def test_automatic_review_boundary_uses_last_stop_notice_before_started_marker(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "未完了結果"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": evidence.STOP_ADVISOR_PREFIX + " 最初の誘導"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "追加作業"}],
                },
            },
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
                    "content": [{"type": "input_text", "text": evidence.STOP_ADVISOR_PREFIX + " 完了後の誘導"}],
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "status": "completed",
                        "aggregated_output": evidence.SESSION_REVIEW_STARTED_MARKER + "\n",
                    },
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

    events = evidence.load_and_extract(str(transcript))

    assert events[-1] == {"kind": "final-result", "text": "本来の最終結果", "line": 4, "sequence": 4}
    assert any(event["text"] == "追加作業" for event in events)
    assert all(event["kind"] != "session-review-started" for event in events)
    assert evidence.has_session_review_started(str(transcript)) is True


def test_stop_notice_without_started_marker_does_not_mark_review_started(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "追加作業後の結果"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": evidence.STOP_ADVISOR_PREFIX + " 誘導"}],
                },
            },
        ],
    )

    events = evidence.load_and_extract(str(transcript))

    assert events[-1]["text"].startswith(evidence.STOP_ADVISOR_PREFIX)
    assert evidence.has_session_review_started(str(transcript)) is False


def test_unrelated_successful_codex_command_is_not_evidence(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "status": "completed",
                        "aggregated_output": "通常出力",
                    },
                },
            }
        ],
    )

    assert evidence.has_session_review_started(str(transcript)) is False
    assert evidence.load_and_extract(str(transcript)) == []


@pytest.mark.parametrize("command", ["/session-review", "/agent-toolkit:session-review"])
def test_manual_review_invocation_excludes_following_claude_events(tmp_path: pathlib.Path, command: str) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "assistant", "message": {"role": "assistant", "content": "本来の最終結果"}},
            {"type": "user", "message": {"role": "user", "content": command}},
            {"type": "assistant", "message": {"role": "assistant", "content": "振り返り中"}},
        ],
    )

    assert evidence.load_and_extract(str(transcript)) == [
        {"kind": "final-result", "text": "本来の最終結果", "line": 1, "sequence": 1}
    ]


@pytest.mark.parametrize("command", ["$session-review", "$agent-toolkit:session-review"])
def test_codex_syntax_is_not_claude_manual_review_boundary(tmp_path: pathlib.Path, command: str) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "assistant", "message": {"role": "assistant", "content": "本来の最終結果"}},
            {"type": "user", "message": {"role": "user", "content": command}},
            {"type": "assistant", "message": {"role": "assistant", "content": "dollar後の本来の作業結果"}},
        ],
    )

    events = evidence.load_and_extract(str(transcript))

    assert [event["text"] for event in events] == [
        "本来の最終結果",
        command,
        "dollar後の本来の作業結果",
    ]
    assert events[-1]["kind"] == "final-result"
    assert evidence.has_session_review_started(str(transcript)) is False


def test_unsupported_nonempty_jsonl_returns_fallback(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(tmp_path, [{"type": "unknown", "payload": {"type": "unknown"}}])

    events = evidence.load_and_extract(str(transcript))

    assert [event["kind"] for event in events] == ["fallback"]


def test_default_output_line_points_at_source_transcript_line(tmp_path: pathlib.Path) -> None:
    """既定出力の各イベントへ、由来したtranscript行の1始まり行番号を付ける。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "依頼"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "作業中"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "最終結果"}]}},
        ],
    )

    events = evidence.load_and_extract(str(transcript))

    assert [event["line"] for event in events] == [1, 2, 3]
    raw_lines = transcript.read_text(encoding="utf-8").splitlines()
    for event in events:
        assert event["text"] in raw_lines[event["line"] - 1]


def _read_jsonl(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    """標準出力のJSONLを辞書列として読む。"""
    captured = capsys.readouterr()
    assert captured.err == ""
    return [json.loads(line) for line in captured.out.splitlines()]


def test_warn_mode_reports_matching_entries_with_line_and_tool(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """警告に一致したエントリだけを、行番号と手掛かりのツール識別子付きで照会する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "依頼"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": "call-1", "input": {"command": "make test"}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "warning: 警告が出た"}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    events = _read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["warning"]
    assert events[0]["line"] == 3
    assert events[0]["tool"] == "call-1"
    assert events[0]["text"] == "warning: 警告が出た"


def test_warn_mode_ignores_identifier_only_management_values(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """警告形式でない管理用の値への一致を警告として報告しない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event",
                "uuid": "warn-0001",
                "sessionId": "warning-session",
                "timestamp": "2026-08-18T00:00:00.000Z",
                "cwd": "/tmp/warn",
                "message": {"role": "user", "content": "依頼"},
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert _read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]


@pytest.mark.parametrize(
    "warning_line",
    [
        "[warn] 実行時警告",
        "[warning] 実行時警告",
        "[auto-generated: agent-toolkit/pretooluse][warn] 実行時警告",
        "warning: 実行時警告",
        "warn: 実行時警告",
        "警告: 実行時警告",
        "⚠: 実行時警告",
        "⚠ 実行時警告",
        "⚠    実行時警告",
    ],
)
def test_warn_mode_accepts_real_line_start_markers_only(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    warning_line: str,
) -> None:
    """実在する行頭マーカーを受理し、本文途中の同語を警告へ昇格させない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "本文途中の warning と warn"}},
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "content": warning_line}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert _read_jsonl(capsys) == [{"kind": "warning", "line": 2, "text": warning_line}]


def test_warn_mode_accepts_structured_warning_fields_and_grep_keeps_arbitrary_search(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """構造化警告は受理し、任意本文の検索は`--grep`へ分離する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "警告という語の説明"}},
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call-1",
                            "content": json.dumps({"warning_message": "構造化された警告"}, ensure_ascii=False),
                        }
                    ],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0
    assert _read_jsonl(capsys) == [{"kind": "warning", "line": 2, "text": "構造化された警告", "tool": "call-1"}]

    assert evidence.main([str(transcript), "--grep", "警告"]) == 0
    matches = _read_jsonl(capsys)
    assert [event["line"] for event in matches[:-1]] == [1, 2]
    assert matches[-1] == {"kind": "summary", "count": 2}


def test_warn_mode_accepts_codex_custom_tool_call_output_structured_warning(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codexの実行結果出力を構造化警告として受理し、入力は`--grep`だけで検索する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "call-1",
                    "arguments": json.dumps({"warning_message": "引数内の警告"}, ensure_ascii=False),
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "call-1",
                    "output": json.dumps({"warning_message": "Codex実行結果の警告"}, ensure_ascii=False),
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0
    assert _read_jsonl(capsys) == [{"kind": "warning", "line": 2, "text": "Codex実行結果の警告"}]

    assert evidence.main([str(transcript), "--grep", "引数内の警告"]) == 0
    assert _read_jsonl(capsys) == [
        {"kind": "match", "line": 1, "text": '{"warning_message": "引数内の警告"}'},
        {"kind": "summary", "count": 1},
    ]


def test_warn_mode_accepts_case_variants_of_structured_warning_fields(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """構造化警告フィールドの大文字表記差を許容し、本文途中の語は拾わない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"WarningMessage": "構造化警告"},
                "message": {"role": "user", "content": []},
            },
            {
                "type": "user",
                "toolUseResult": {"message": "本文途中の WarningMessage"},
                "message": {"role": "user", "content": []},
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert _read_jsonl(capsys) == [{"kind": "warning", "line": 1, "text": "構造化警告"}]


def test_warn_mode_keeps_ordinary_siblings_out_of_structured_warning_text(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """警告キーの値又は直接警告辞書の本文だけを抽出し、兄弟の通常本文を除外する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"warning_message": "構造化警告", "message": "通常本文"},
                "message": {"role": "user", "content": []},
            },
            {
                "type": "user",
                "toolUseResult": {
                    "kind": "warning",
                    "text": "直接表す警告本文",
                    "message": "通常の兄弟本文",
                },
                "message": {"role": "user", "content": []},
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert _read_jsonl(capsys) == [
        {"kind": "warning", "line": 1, "text": "構造化警告"},
        {"kind": "warning", "line": 2, "text": "直接表す警告本文"},
    ]


@pytest.mark.parametrize(
    ("entry", "grep_pattern", "expected_text"),
    [
        (
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "SomeTool",
                            "id": "call-1",
                            "input": {"warning_message": "入力内JSONの警告"},
                        }
                    ],
                },
            },
            "入力内JSON",
            "入力内JSONの警告",
        ),
        (
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "SomeTool",
                            "id": "call-1",
                            "input": {"command": "warning: 入力内マーカー"},
                        }
                    ],
                },
            },
            "入力内マーカー",
            "warning: 入力内マーカー",
        ),
        (
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "SomeTool",
                            "id": "call-1",
                            "input": {"command": "⚠ 入力内マーカー"},
                        }
                    ],
                },
            },
            "入力内マーカー",
            "⚠ 入力内マーカー",
        ),
        (
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "call-1",
                    "arguments": json.dumps({"warning_message": "引数内JSONの警告"}, ensure_ascii=False),
                },
            },
            "引数内JSON",
            '{"warning_message": "引数内JSONの警告"}',
        ),
        (
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "call-1",
                    "arguments": json.dumps({"command": "warning: 引数内マーカー"}, ensure_ascii=False),
                },
            },
            "引数内マーカー",
            '{"command": "warning: 引数内マーカー"}',
        ),
    ],
)
def test_warn_mode_ignores_warning_markers_and_structured_json_in_inputs_but_grep_finds_it(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    entry: dict[str, object],
    grep_pattern: str,
    expected_text: str,
) -> None:
    """入力の行頭マーカーと構造化警告を`--warn`へ昇格させず、`--grep`では検索する。"""
    transcript = _write_transcript(tmp_path, [entry])

    assert evidence.main([str(transcript), "--warn"]) == 0
    assert _read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]

    assert evidence.main([str(transcript), "--grep", grep_pattern]) == 0
    assert _read_jsonl(capsys) == [
        {"kind": "match", "line": 1, "text": expected_text},
        {"kind": "summary", "count": 1},
    ]


def test_warn_mode_excludes_records_after_review_boundary(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """振り返り起動後に記録された警告を照会対象へ含めない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"stdout": "warning: 作業中の警告"},
                "message": {"role": "user", "content": []},
            },
            {"type": "user", "message": {"role": "user", "content": "/session-review"}},
            {
                "type": "user",
                "toolUseResult": {"stdout": "warning: 振り返り中の警告"},
                "message": {"role": "user", "content": []},
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert _read_jsonl(capsys) == [{"kind": "warning", "line": 1, "text": "warning: 作業中の警告"}]


def test_warn_mode_excludes_records_after_claude_automatic_review_marker(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Claude自動session-review開始マーカー後の同形式警告を照会対象へ含めない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"stdout": "warning: マーカー前の警告"},
                "message": {"role": "user", "content": []},
            },
            {
                "type": "user",
                "toolUseResult": {"stdout": evidence.SESSION_REVIEW_STARTED_MARKER},
                "message": {"role": "user", "content": []},
            },
            {
                "type": "user",
                "toolUseResult": {"stdout": "warning: マーカー後の警告"},
                "message": {"role": "user", "content": []},
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert _read_jsonl(capsys) == [{"kind": "warning", "line": 1, "text": "warning: マーカー前の警告"}]


def test_query_modes_search_hook_notice_stored_under_attachment(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """hook通知のようにattachment配下へ格納された警告行を`--warn`・`--grep`の双方で照会する。

    走査対象は既知フィールドの列挙ではなくエントリ内の全本文であり、
    hook通知の格納先（実行結果のJSON・追加コンテキストの配列）を問わず一致する。
    """
    notice = "[auto-generated: agent-toolkit/pretooluse][warn] 検証コマンドの出力を切り詰めている"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "attachment",
                "uuid": "hook-success",
                "attachment": {
                    "type": "hook_success",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "stdout": json.dumps({"hookSpecificOutput": {"additionalContext": notice}}, ensure_ascii=False),
                    "stderr": "",
                    "exitCode": 0,
                },
            },
            {
                "type": "attachment",
                "uuid": "hook-context",
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "content": [notice],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0
    warnings = _read_jsonl(capsys)
    assert [event["line"] for event in warnings] == [1]
    assert notice in warnings[0]["text"]

    assert evidence.main([str(transcript), "--grep", "切り詰めている"]) == 0
    matches = _read_jsonl(capsys)
    assert [event["line"] for event in matches[:2]] == [1, 2]
    assert matches[-1] == {"kind": "summary", "count": 2}


def test_warn_mode_keeps_hook_notices_with_distinct_or_missing_tool_use_ids(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """異なるツール呼び出しの通知と識別子を持たない通知は個別の警告として保持する。"""
    notice = "[auto-generated: agent-toolkit/pretooluse][warn] 検証コマンドの出力を切り詰めている"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "attachment",
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "content": [notice],
                },
            },
            {
                "type": "attachment",
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-2",
                    "content": [notice],
                },
            },
            {
                "type": "attachment",
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "content": [notice],
                },
            },
            {
                "type": "attachment",
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "content": [notice],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    warnings = _read_jsonl(capsys)
    assert [event["line"] for event in warnings] == [1, 2, 3, 4]
    assert [event["text"] for event in warnings] == [notice] * 4


def test_warn_mode_reports_matches_found_only_in_tool_use_result(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """本文が外部退避されたBash出力の警告を、ツール実行結果側から照会する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"stdout": "warning: 退避された警告", "stderr": "警告: 標準エラー"},
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "<persisted-output>"}],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    events = _read_jsonl(capsys)
    assert [event["text"] for event in events] == ["warning: 退避された警告", "警告: 標準エラー"]
    assert [event["line"] for event in events] == [1, 1]


def test_warn_mode_does_not_duplicate_tool_use_result_already_visible(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """可視テキストと同一の実行結果を重ねて照会しない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"stdout": "warning: 同じ警告\n"},
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "warning: 同じ警告\n"}],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert [event["text"] for event in _read_jsonl(capsys)] == ["warning: 同じ警告"]


@pytest.mark.parametrize(
    ("option", "pattern"),
    [("--warn", None), ("--grep", "warning")],
)
def test_query_modes_normalize_line_number_prefix_across_body_fields(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    option: str,
    pattern: str | None,
) -> None:
    """別本文間だけ行番号接頭辞を正規化し、最初に現れた原文を表示する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "content": "12\twarning: 同じ本文"},
                        {"type": "tool_result", "content": "warning: 同じ本文"},
                    ],
                },
            }
        ],
    )

    arguments = [str(transcript), option]
    if pattern is not None:
        arguments.append(pattern)
    assert evidence.main(arguments) == 0

    events = _read_jsonl(capsys)
    assert events[0] == {"kind": "warning" if option == "--warn" else "match", "line": 1, "text": "12\twarning: 同じ本文"}
    if option == "--grep":
        assert events[-1] == {"kind": "summary", "count": 1}
        assert len(events) == 2
    else:
        assert len(events) == 1


@pytest.mark.parametrize(
    ("option", "pattern"),
    [("--warn", None), ("--grep", "warning")],
)
def test_query_modes_keep_numbered_and_unnumbered_lines_in_one_body_distinct(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    option: str,
    pattern: str | None,
) -> None:
    """同一本文内の行番号接頭辞は、別行を表すため重複として除かない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "content": "12\twarning: 本文\nwarning: 本文"}],
                },
            }
        ],
    )

    arguments = [str(transcript), option]
    if pattern is not None:
        arguments.append(pattern)
    assert evidence.main(arguments) == 0

    events = _read_jsonl(capsys)
    assert [event["text"] for event in events if event["kind"] in {"warning", "match"}] == [
        "12\twarning: 本文",
        "warning: 本文",
    ]
    if option == "--grep":
        assert events[-1] == {"kind": "summary", "count": 1}


def test_grep_mode_searches_tool_use_result_output(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--grep`も`--warn`と同じくツール実行結果の生出力を走査対象に含める。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"stdout": "退避された本文の照合語"},
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "<persisted-output>"}],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--grep", "照合語"]) == 0

    events = _read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["match", "summary"]
    assert events[0] == {"kind": "match", "line": 1, "text": "退避された本文の照合語"}
    assert events[-1]["count"] == 1


def _self_invocation_entries(command: str) -> list[dict]:
    """本スクリプト自身を呼び出したBashコマンドと、その照会結果からなるエントリ列を構成する。"""
    return [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": command}}],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tu1",
                        "content": json.dumps({"kind": "warning", "text": "過去の照会結果"}, ensure_ascii=False),
                    }
                ],
            },
        },
    ]


@pytest.mark.parametrize(
    "command",
    [
        "python3 agent-toolkit/scripts/_session_review_evidence.py --warn /tmp/foo.jsonl",
        "uv run --no-project --script /plugin/scripts/_session_review_evidence.py /tmp/foo.jsonl",
        "./agent-toolkit/scripts/_session_review_evidence.py --grep 'warn' /tmp/foo.jsonl",
        "cd /repo && python3 agent-toolkit/scripts/_session_review_evidence.py --warn /tmp/foo.jsonl",
        "bash -lc 'python3 /plugin/scripts/_session_review_evidence.py --warn /tmp/foo.jsonl'",
    ],
)
def test_query_modes_ignore_own_invocation_and_its_result(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    """起動形式を問わず、本スクリプトを実行したコマンドとその照会結果を報告しない。"""
    transcript = _write_transcript(tmp_path, _self_invocation_entries(command))

    assert evidence.main([str(transcript), "--warn"]) == 0
    assert _read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]

    assert evidence.main([str(transcript), "--grep", "warning"]) == 0
    assert _read_jsonl(capsys) == [{"kind": "summary", "count": 0}]


def test_query_modes_ignore_own_invocation_recorded_as_codex_exec_command(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codexのコマンド実行記録（`arguments`が`cmd`キーを持つ形式）の自己呼び出しも報告しない。"""
    command = "python3 /plugin/scripts/_session_review_evidence.py --warn /tmp/foo.jsonl"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": "call-1",
                    "arguments": json.dumps({"cmd": command, "workdir": "/repo"}, ensure_ascii=False),
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": json.dumps({"kind": "warning", "text": "過去の照会結果"}, ensure_ascii=False),
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0
    assert _read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]

    assert evidence.main([str(transcript), "--grep", "warning"]) == 0
    assert _read_jsonl(capsys) == [{"kind": "summary", "count": 0}]


def test_query_modes_keep_warnings_outside_own_invocation(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """自己呼び出しの除外は当該記録に限り、無関係なエントリの警告は照会し続ける。"""
    command = "python3 agent-toolkit/scripts/_session_review_evidence.py --warn /tmp/foo.jsonl"
    entries = [
        *_self_invocation_entries(command),
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu2", "content": "warning: 実在の警告"}],
            },
        },
    ]
    transcript = _write_transcript(tmp_path, entries)

    assert evidence.main([str(transcript), "--warn"]) == 0

    events = _read_jsonl(capsys)
    assert [event["text"] for event in events] == ["warning: 実在の警告"]
    assert [event["line"] for event in events] == [3]


@pytest.mark.parametrize(
    "command",
    [
        "rg -n _session_review_evidence agent-toolkit/scripts",
        "grep -rn TODO agent-toolkit/scripts/_session_review_evidence.py",
        "cat agent-toolkit/scripts/_session_review_evidence.py",
        "sed -n '1,20p' agent-toolkit/scripts/_session_review_evidence.py",
        "head -n 5 agent-toolkit/scripts/_session_review_evidence_test.py",
        "tail -n 5 agent-toolkit/scripts/_session_review_evidence.py",
        "vim agent-toolkit/scripts/_session_review_evidence.py",
        "bash -lc 'rg -n _session_review_evidence agent-toolkit/scripts'",
    ],
)
def test_query_modes_keep_records_that_only_reference_the_script_file(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    """スクリプトを検索・閲覧・編集するだけのコマンドは実行と扱わず、その警告を照会し続ける。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "c1", "name": "Bash", "input": {"command": command}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "c1",
                            "content": [{"type": "text", "text": "warning: relevant output"}],
                        }
                    ],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    events = _read_jsonl(capsys)
    assert [event["text"] for event in events] == ["warning: relevant output"]
    assert [event["line"] for event in events] == [2]


def test_query_modes_ignore_structural_values_of_codex_envelopes(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codex形式の入れ子構造が持つ区分値・識別子は一致とせず、実行結果の本文は検索する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "id": "exec-1",
                        "command": ["echo", "ok"],
                        "status": "completed",
                        "stdout": "done",
                    },
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--grep", "completed"]) == 0
    assert _read_jsonl(capsys) == [{"kind": "summary", "count": 0}]

    assert evidence.main([str(transcript), "--grep", "done"]) == 0
    assert _read_jsonl(capsys) == [
        {"kind": "match", "line": 1, "text": "done"},
        {"kind": "summary", "count": 1},
    ]


def test_grep_mode_searches_nested_values_under_management_named_keys(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """入れ子の汎用語キーが持つtool_use入力を検索し、エントリ直下の管理用の値は除外し続ける。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "uuid": "needle-value-uuid",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "c1", "name": "SomeTool", "input": {"mode": "needle-value"}}],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--grep", "needle-value"]) == 0

    events = _read_jsonl(capsys)
    assert events == [
        {"kind": "match", "line": 1, "text": "needle-value"},
        {"kind": "summary", "count": 1},
    ]


def test_warn_mode_reports_absence_when_no_entry_matches(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """一致が無い場合も、事実を1行で照会結果として返す。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert _read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]


def test_detail_mode_keeps_tool_use_input_shapes_and_result_body(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """入力形態を問わずtool_useの`input`全体を保持し、tool_result本文も照会する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Bash", "id": "c1", "input": {"command": "atk mq list", "n": 1}},
                        {"type": "tool_use", "name": "Read", "id": "c2", "input": {"file_path": "/tmp/x.md"}},
                        {"type": "tool_use", "name": "Agent", "id": "c3", "input": {"prompt": "依頼本文"}},
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "結果本文"}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--detail", "1", "2"]) == 0

    events = _read_jsonl(capsys)
    assert [event["line"] for event in events] == [1, 1, 1, 2]
    assert [event.get("name") for event in events[:3]] == ["Bash", "Read", "Agent"]
    assert events[0]["input"] == {"command": "atk mq list", "n": 1}
    assert events[1]["input"] == {"file_path": "/tmp/x.md"}
    assert events[2]["input"] == {"prompt": "依頼本文"}
    assert events[3] == {"kind": "detail", "line": 2, "tool": "c1", "text": "結果本文"}


@pytest.mark.parametrize(
    "content",
    [
        "<persisted-output>",
        "<persisted-output>\nOutput too large (76.4KB). Full output saved to: /tmp/tool-results/x.txt",
        "",
    ],
)
def test_detail_mode_returns_persisted_body_from_tool_use_result(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    content: str,
) -> None:
    """本文が退避されたtool_resultでは、退避通知ではなく実行結果側の本文を詳細として返す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "c1", "name": "Bash", "input": {"command": "echo warning: real output"}}
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "c1", "content": content}],
                },
                "toolUseResult": {"stdout": "warning: real output", "stderr": ""},
            },
        ],
    )

    assert evidence.main([str(transcript), "--detail", "2"]) == 0

    assert _read_jsonl(capsys) == [{"kind": "detail", "line": 2, "tool": "c1", "text": "warning: real output"}]


def test_detail_mode_shares_one_clip_budget_across_entry_blocks(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """複数ブロックを持つエントリでも、詳細本文の合計を1エントリ分の上限内へ収める。

    省略標識は予算の上限へ達した本文だけへ付き、以降の本文は空文字列となる。
    省略が生じた事実はエントリの全イベントへ付く`omitted`が示す。
    """
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Agent", "id": "c1", "input": {"prompt": "あ" * 9000}},
                        {"type": "tool_use", "name": "Agent", "id": "c2", "input": {"prompt": "い" * 9000}},
                        {"type": "tool_result", "tool_use_id": "c3", "content": "う" * 9000},
                        {"type": "tool_result", "tool_use_id": "c4", "content": ""},
                    ],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--detail", "1"]) == 0

    events = _read_jsonl(capsys)
    total = sum(len(event["input"]["prompt"]) for event in events if "input" in event)
    total += sum(len(event["text"]) for event in events if "text" in event)
    assert total <= 8000
    assert events[0]["input"]["prompt"].endswith("…[省略]")
    assert events[1]["input"]["prompt"] == ""
    assert events[2]["text"] == ""
    assert events[3]["text"] == ""
    assert all(event["omitted"] is True for event in events)


def test_detail_mode_marks_omission_when_budget_ends_exactly_before_next_value(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """先行する本文の長さが上限と一致する場合も、後続の非空値の省略を判別できる。

    上限に達した後の本文は空文字列となるため、`omitted`が無ければ
    元から空の値と省略された値を区別できない。
    """
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Agent", "id": "c1", "input": {"prompt": "あ" * 8000}},
                        {"type": "tool_use", "name": "Read", "id": "c2", "input": {"file_path": "/tmp/x.md"}},
                        {"type": "tool_result", "tool_use_id": "c3", "content": "後続本文"},
                    ],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--detail", "1"]) == 0

    events = _read_jsonl(capsys)
    assert len(events[0]["input"]["prompt"]) == 8000
    assert events[1]["input"]["file_path"] == ""
    assert events[2]["text"] == ""
    assert all(event["omitted"] is True for event in events)


def test_detail_mode_keeps_total_within_limit_for_many_string_values(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """文字列値を多数持つ入力でも、詳細本文の合計が1エントリ分の上限を超えない。

    上限へ達した後も本文ごとに省略標識を付けると、合計が文字列値の個数に比例して増える。
    """
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "id": "c1",
                            "input": {f"key{index}": "あ" * 20 for index in range(5000)},
                        }
                    ],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--detail", "1"]) == 0

    events = _read_jsonl(capsys)
    assert sum(len(value) for value in events[0]["input"].values()) <= 8000
    assert events[0]["omitted"] is True


def test_detail_mode_formats_entry_without_tool_blocks(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """tool_useもtool_resultも持たないエントリはJSON整形本文で照会する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "依頼"}]},
            }
        ],
    )

    assert evidence.main([str(transcript), "--detail", "1"]) == 0

    events = _read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["detail"]
    assert "依頼" in events[0]["text"]


def test_detail_mode_rejects_line_number_outside_transcript(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """範囲外の行番号は照会不能として終了コード2で返す。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])

    assert evidence.main([str(transcript), "--detail", "9"]) == 2

    assert _read_jsonl(capsys) == [{"kind": "error", "text": "行番号9は範囲外"}]


def test_grep_mode_reports_matching_lines_and_entry_count(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """tool_use入力とtool_result本文を含む可視テキストから一致行と一致エントリ数を照会する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "依頼"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Bash", "id": "c1", "input": {"command": "atk mq list"}},
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "失敗: atk mq list"}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--grep", "atk mq"]) == 0

    events = _read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["match", "match", "summary"]
    assert [event["line"] for event in events[:2]] == [2, 3]
    assert events[-1]["count"] == 2


def test_grep_mode_rejects_invalid_regular_expression(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """不正な正規表現は照会不能として終了コード2で返す。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])

    assert evidence.main([str(transcript), "--grep", "["]) == 2

    events = _read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["error"]
    assert "正規表現が不正" in events[0]["text"]


def test_query_modes_are_mutually_exclusive(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """照会モードの併用は引数誤用として終了コード2で返す。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])

    assert evidence.main([str(transcript), "--warn", "--grep", "依頼"]) == 2

    events = _read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["error"]
    assert "併用できない" in events[0]["text"]


def _events_by_kind(events: list[dict], kind: str) -> list[dict]:
    return [event for event in events if event.get("kind") == kind]


def test_stats_deduplicates_claude_usage_and_reports_tool_breakdown(tmp_path: pathlib.Path, capsys) -> None:
    """Claudeの重複messageとツール所要時間を集計し、呼び出し行を保持する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "timestamp": "2026-08-19T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
            {
                "type": "assistant",
                "timestamp": "2026-08-19T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "id": "message-1",
                    "usage": {
                        "input_tokens": 3,
                        "output_tokens": 4,
                        "cache_creation_input_tokens": 5,
                        "cache_read_input_tokens": 6,
                    },
                    "content": [
                        {"type": "tool_use", "name": "Bash", "id": "call-1", "input": {"command": "make test"}},
                        {"type": "tool_use", "name": "Read", "id": "call-2", "input": {"file_path": "/tmp/target.py"}},
                    ],
                },
            },
            {
                "type": "user",
                "timestamp": "2026-08-19T00:00:04Z",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "完了"}]},
            },
            {
                "type": "user",
                "timestamp": "2026-08-19T00:00:11Z",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-2", "content": "本文"}]},
            },
            {
                "type": "assistant",
                "timestamp": "2026-08-19T00:01:10Z",
                "message": {
                    "role": "assistant",
                    "id": "message-1",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 20,
                        "cache_creation_input_tokens": 30,
                        "cache_read_input_tokens": 40,
                    },
                    "content": [{"type": "text", "text": "結果"}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    summary = _events_by_kind(events, "stats-summary")[0]
    assert summary["tokens"] == {
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_creation_input_tokens": 30,
        "cache_read_input_tokens": 40,
    }
    assert summary["api_messages"] == 1
    assert summary["elapsed_seconds"] == 70
    assert _events_by_kind(events, "stats-tool") == [
        {"kind": "stats-tool", "tool": "Read", "count": 1, "total_seconds": 10.0},
        {"kind": "stats-tool", "tool": "Bash", "count": 1, "total_seconds": 3.0},
    ]
    assert _events_by_kind(events, "stats-slow-call") == [
        {"kind": "stats-slow-call", "tool": "Read", "seconds": 10.0, "line": 2, "hint": "/tmp/target.py"},
        {"kind": "stats-slow-call", "tool": "Bash", "seconds": 3.0, "line": 2, "hint": "make test"},
    ]
    assert _events_by_kind(events, "stats-token-peak")[0]["line"] == 5


def test_stats_reports_gap_repeat_and_token_peak_union(tmp_path: pathlib.Path, capsys) -> None:
    """空白区間、同一入力の反復、単一順位では除外されるトークン極値を出力する。"""
    entries: list[dict] = [
        {"type": "user", "timestamp": "2026-08-19T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
    ]
    for index in range(11):
        timestamp = f"2026-08-19T00:{2 + index:02d}:00Z"
        entries.append(
            {
                "type": "assistant",
                "timestamp": timestamp,
                "message": {
                    "role": "assistant",
                    "id": f"message-{index}",
                    # 先行10件はキャッシュ読取が支配し全成分合計が大きい。
                    # 末尾1件は生成が支配し、全成分合計では11位となる。
                    "usage": {
                        "input_tokens": 0,
                        "output_tokens": 1 if index < 10 else 100,
                        "cache_creation_input_tokens": 0 if index < 10 else 50,
                        "cache_read_input_tokens": 1000 if index < 10 else 0,
                    },
                    "content": [
                        {"type": "tool_use", "name": "Read", "id": f"read-{index}", "input": {"command": "same input"}}
                    ],
                },
            }
        )
        entries.append(
            {
                "type": "user",
                "timestamp": f"2026-08-19T00:{2 + index:02d}:01Z",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": f"read-{index}", "content": "完了"}],
                },
            }
        )
    transcript = _write_transcript(tmp_path, entries)

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    gaps = _events_by_kind(events, "stats-gap")
    # 60秒以上の空白は先頭の依頼から最初のassistantまでの1件だけで、59秒の空白は出力されない。
    assert gaps == [{"kind": "stats-gap", "seconds": 120.0, "before_line": 1, "after_line": 2}]
    assert all(event["seconds"] >= 60 for event in gaps)
    repeats = _events_by_kind(events, "stats-repeat")
    assert repeats and repeats[0]["tool"] == "Read" and repeats[0]["count"] == 11
    peaks = _events_by_kind(events, "stats-token-peak")
    generative = next(event for event in peaks if event["line"] == 22)
    assert generative["total_tokens"] == 150
    assert all(event["total_tokens"] > generative["total_tokens"] for event in peaks if event["line"] != 22)


def test_stats_sums_codex_last_token_usage_and_pairs_tool_calls(tmp_path: pathlib.Path, capsys) -> None:
    """Codexのトークンは各`token_count`の実消費の加算とし、call_idで所要時間を対応付ける。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:00Z",
                "payload": {"type": "message", "role": "user", "content": "依頼"},
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:01Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                        "last_token_usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                    },
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:02Z",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec_command",
                    "call_id": "call-1",
                    "arguments": json.dumps({"command": ["make", "test"]}),
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:04Z",
                "payload": {"type": "custom_tool_call_output", "call_id": "call-1", "output": "完了"},
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:05Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {"input_tokens": 22, "output_tokens": 33, "total_tokens": 55},
                        "last_token_usage": {"input_tokens": 20, "output_tokens": 30, "total_tokens": 50},
                    },
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    assert _events_by_kind(events, "stats-summary")[0]["tokens"]["total_tokens"] == 55
    assert _events_by_kind(events, "stats-tool")[0]["total_seconds"] == 2.0
    assert _events_by_kind(events, "stats-slow-call")[0]["line"] == 3


def _codex_token_count_entry(timestamp: str, usage: dict[str, int], cumulative: dict[str, int] | None = None) -> dict:
    """Codexの`token_count`エントリを作成する。

    `usage`は当該リクエストの実消費（`last_token_usage`）、`cumulative`はセッション累積
    （`total_token_usage`）とする。`cumulative`を省略した場合は同じ値を与える。
    """
    return {
        "type": "response_item",
        "timestamp": timestamp,
        "payload": {
            "type": "token_count",
            "info": {"total_token_usage": usage if cumulative is None else cumulative, "last_token_usage": usage},
        },
    }


def _codex_usage(input_tokens: int, cached: int, cache_write: int, output_tokens: int, reasoning: int) -> dict[str, int]:
    """Codex形式の6成分`token_usage`を作成する。`total_tokens`は入力と出力の合計とする。"""
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": cache_write,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": reasoning,
        "total_tokens": input_tokens + output_tokens,
    }


def test_stats_sums_codex_last_token_usage_across_rewind(tmp_path: pathlib.Path, capsys) -> None:
    """累積値が巻き戻る記録でも、各リクエストの実消費の単純加算として集計する。

    Codexは過去のチェックポイントへ戻ると`total_token_usage`を巻き戻し先の値へ戻して再累積するため、
    累積値の減少を区間境界とみなして減少前の値を加算すると、巻き戻し先までの消費を二重計上する。
    本フィクスチャでは減少前の累積（入力150）を加算すると入力が280となり、実消費の合計180と一致しない。
    """
    transcript = _write_transcript(
        tmp_path,
        [
            _codex_token_count_entry(
                "2026-08-19T00:00:00Z", _codex_usage(100, 80, 5, 20, 10), _codex_usage(100, 80, 5, 20, 10)
            ),
            _codex_token_count_entry("2026-08-19T00:00:01Z", _codex_usage(50, 40, 2, 10, 4), _codex_usage(150, 120, 7, 30, 14)),
            _codex_token_count_entry("2026-08-19T00:00:02Z", _codex_usage(30, 20, 1, 5, 2), _codex_usage(130, 100, 6, 25, 12)),
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    summary = _events_by_kind(events, "stats-summary")[0]
    assert summary["tokens"] == _codex_usage(180, 140, 8, 35, 16)
    assert summary["api_messages"] == 3
    assert _events_by_kind(events, "stats-total")[0]["tokens"] == {
        "input_tokens": 40,
        "output_tokens": 35,
        "cache_creation_input_tokens": 8,
        "cache_read_input_tokens": 140,
    }


def test_stats_skips_codex_duplicate_token_count_records(tmp_path: pathlib.Path, capsys) -> None:
    """同一リクエストを再送した`token_count`は合計へ加算しない。

    Codexはターン終了時に直前と同一の`total_token_usage`・`last_token_usage`を持つレコードを
    再記録する。無条件加算では入力が200となり、実際のリクエスト2件分の150と一致しない。
    """
    duplicated = _codex_usage(150, 120, 7, 30, 14)
    transcript = _write_transcript(
        tmp_path,
        [
            _codex_token_count_entry(
                "2026-08-19T00:00:00Z", _codex_usage(100, 80, 5, 20, 10), _codex_usage(100, 80, 5, 20, 10)
            ),
            _codex_token_count_entry("2026-08-19T00:00:01Z", _codex_usage(50, 40, 2, 10, 4), duplicated),
            _codex_token_count_entry("2026-08-19T00:00:02Z", _codex_usage(50, 40, 2, 10, 4), duplicated),
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    summary = _events_by_kind(_read_jsonl(capsys), "stats-summary")[0]
    assert summary["tokens"] == duplicated
    assert summary["api_messages"] == 2


def test_stats_skips_codex_zero_usage_record_after_compact(tmp_path: pathlib.Path, capsys) -> None:
    """compact直後の実消費0のレコードは合計へ影響しない。

    当該レコードは`last_token_usage`の6成分が全て0でありながら`total_token_usage`は直前と同一のため、
    加算対象へ含めると`api_messages`が実際のリクエスト数を上回る。
    """
    cumulative = _codex_usage(100, 80, 5, 20, 10)
    transcript = _write_transcript(
        tmp_path,
        [
            _codex_token_count_entry("2026-08-19T00:00:00Z", _codex_usage(100, 80, 5, 20, 10), cumulative),
            _codex_token_count_entry("2026-08-19T00:00:01Z", _codex_usage(0, 0, 0, 0, 0), cumulative),
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    summary = _events_by_kind(_read_jsonl(capsys), "stats-summary")[0]
    assert summary["tokens"] == cumulative
    assert summary["api_messages"] == 1


def test_stats_token_peak_normalizes_codex_cache_components(tmp_path: pathlib.Path, capsys) -> None:
    """Codexの`stats-token-peak`はキャッシュ成分をClaude形式へ変換して出力する。"""
    transcript = _write_transcript(
        tmp_path,
        [_codex_token_count_entry("2026-08-19T00:00:00Z", _codex_usage(300, 250, 7, 40, 20))],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    peak = _events_by_kind(_read_jsonl(capsys), "stats-token-peak")[0]
    assert peak["total_tokens"] == 347
    assert peak["input_tokens"] == 50
    assert peak["cache_read_input_tokens"] == 250
    assert peak["cache_creation_input_tokens"] == 7
    assert peak["output_tokens"] == 40


def test_stats_collects_subagents_and_excludes_review_descendants(tmp_path: pathlib.Path, capsys) -> None:
    """通常のサブエージェントだけを主体別集計へ含め、振り返り系と子孫を除外する。"""
    transcript = _write_transcript(
        tmp_path,
        [{"type": "user", "timestamp": "2026-08-19T00:00:00Z", "message": {"role": "user", "content": "依頼"}}],
    )
    subagents = transcript.with_suffix("") / "subagents"
    subagents.mkdir(parents=True)
    normal = subagents / "agent-normal.jsonl"
    normal.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-08-19T00:00:01Z",
                "message": {"role": "assistant", "id": "n", "usage": {"input_tokens": 2, "output_tokens": 3}},
            }
        )
        + "\n"
    )
    (subagents / "agent-normal.meta.json").write_text(json.dumps({"agentType": "Explore"}))
    review = subagents / "agent-review.jsonl"
    review.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-08-19T00:00:01Z",
                "message": {"role": "assistant", "id": "r", "usage": {"input_tokens": 100, "output_tokens": 100}},
            }
        )
        + "\n"
    )
    (subagents / "agent-review.meta.json").write_text(json.dumps({"agentType": "session-review-advisor"}))
    child = subagents / "agent-child.jsonl"
    child.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-08-19T00:00:01Z",
                "message": {"role": "assistant", "id": "c", "usage": {"input_tokens": 200, "output_tokens": 200}},
            }
        )
        + "\n"
    )
    (subagents / "agent-child.meta.json").write_text(json.dumps({"parentAgentId": "review", "agentType": "Explore"}))

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    assert [event["agent"] for event in _events_by_kind(events, "stats-subagent")] == ["agent-normal"]
    total = _events_by_kind(events, "stats-subagent-total")[0]
    assert total["count"] == 1 and total["excluded_review_agents"] == 2
    assert total["tokens"]["input_tokens"] == 2


def test_stats_omits_subagent_events_without_subagents_directory(tmp_path: pathlib.Path, capsys) -> None:
    """`subagents/`が無い場合は主体別集計のイベントを出力しない。"""
    transcript = _write_transcript(
        tmp_path,
        [_assistant_usage_entry("2026-08-19T00:00:00Z", "main", _usage(2, 3))],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    assert _events_by_kind(events, "stats-subagent") == []
    assert _events_by_kind(events, "stats-subagent-total") == []
    assert _events_by_kind(events, "stats-total")[0]["subagent_count"] == 0


def test_stats_reports_zero_subagent_total_when_all_records_excluded(tmp_path: pathlib.Path, capsys) -> None:
    """記録が振り返り系だけの場合も合算イベントを出力し、除外件数を観測可能にする。

    サブエージェントを使わなかったセッションと、活動が振り返り自身だけだったセッションを
    出力から区別できる状態を保証する。
    """
    transcript = _write_transcript(
        tmp_path,
        [_assistant_usage_entry("2026-08-19T00:00:00Z", "main", _usage(2, 3))],
    )
    _write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-review",
        [_assistant_usage_entry("2026-08-19T00:00:01Z", "review", _usage(100, 200))],
        meta={"agentType": "agent-toolkit:session-review-advisor"},
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    assert _events_by_kind(events, "stats-subagent") == []
    total = _events_by_kind(events, "stats-subagent-total")[0]
    assert total["count"] == 0
    assert total["tokens"] == _usage(0)
    assert total["excluded_review_agents"] == 1
    assert _events_by_kind(events, "stats-total")[0]["subagent_count"] == 0


def test_stats_discovers_codex_threads_from_structured_shapes(tmp_path: pathlib.Path, monkeypatch, capsys) -> None:
    """Codex委譲の入力形態表の全形状からthreadIdを収集し、引用本文を収集せずrollout欠落をスキップする。

    `tool_use`入力・`mcpMeta.structuredContent`・JSON文字列型`toolUseResult`は同一threadIdへ重複排除し、
    タスク通知の`<result>`要素だけで到達するthreadIdも収集する。
    引用UUIDにも対応するrolloutを配置するため、誤って収集した場合は当該スレッドの
    `stats-agent-thread`が出力され、本テストが失敗する。
    """
    thread_id = "11111111-1111-4111-8111-111111111111"
    notified_id = "33333333-3333-4333-8333-333333333333"
    quoted_id = "22222222-2222-4222-8222-222222222222"
    missing_id = "44444444-4444-4444-8444-444444444444"
    sent_id = "55555555-5555-4555-8555-555555555555"
    codex_home = tmp_path / "codex"
    rollout_dir = codex_home / "sessions" / "2026" / "08" / "19"
    rollout_dir.mkdir(parents=True)
    rollout = rollout_dir / f"rollout-test-{thread_id}.jsonl"
    rollout.write_text(
        json.dumps(
            {
                "timestamp": "2026-08-19T00:00:00Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {"input_tokens": 4, "output_tokens": 5, "total_tokens": 9},
                        "last_token_usage": {"input_tokens": 4, "output_tokens": 5, "total_tokens": 9},
                    },
                },
            }
        )
        + "\n"
    )
    _write_rollout(
        codex_home, quoted_id, [("2026-08-19T00:00:00Z", {"input_tokens": 6, "output_tokens": 7, "total_tokens": 13})]
    )
    _write_rollout(
        codex_home, notified_id, [("2026-08-19T00:00:00Z", {"input_tokens": 8, "output_tokens": 9, "total_tokens": 17})]
    )
    _write_rollout(
        codex_home, sent_id, [("2026-08-19T00:00:00Z", {"input_tokens": 10, "output_tokens": 11, "total_tokens": 21})]
    )
    notification = (
        "<task-notification>\n"
        "<source>codex/codex</source>\n"
        "<status>completed</status>\n"
        f"<result>{json.dumps({'engine': 'codex', 'threadId': notified_id})}</result>\n"
        "</task-notification>"
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "timestamp": "2026-08-19T00:00:00Z",
                "message": {"role": "user", "content": "引用本文にはthreadId: " + quoted_id},
            },
            _codex_tool_use_entry("2026-08-19T00:00:01Z", "call-missing", missing_id),
            _codex_tool_use_entry(
                "2026-08-19T00:00:01Z",
                "call-send",
                sent_id,
                tool_name="mcp__agents_server__send_message",
            ),
            {
                "type": "assistant",
                "timestamp": "2026-08-19T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "mcp__agents_server__start",
                            "id": "a",
                            "input": {"engine": "codex", "threadId": thread_id},
                        }
                    ],
                },
            },
            {
                "type": "user",
                "timestamp": "2026-08-19T00:00:02Z",
                "mcpMeta": {"structuredContent": {"engine": "codex", "threadId": thread_id}},
                "toolUseResult": json.dumps({"engine": "codex", "conversationId": thread_id}),
                "message": {"role": "user", "content": "完了"},
            },
            {
                "type": "user",
                "timestamp": "2026-08-19T00:00:03Z",
                "message": {"role": "user", "content": [{"type": "text", "text": notification}]},
            },
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    threads = _events_by_kind(events, "stats-agent-thread")
    assert [event["thread"] for event in threads] == [sent_id, notified_id, thread_id]
    assert [event["tokens"]["total_tokens"] for event in threads] == [21, 17, 9]
    assert quoted_id not in {event["thread"] for event in threads}
    assert missing_id not in {event["thread"] for event in threads}


def test_stats_resolves_claude_session_from_codex_rollout_tool_call(
    tmp_path: pathlib.Path,
    monkeypatch,
    capsys,
) -> None:
    """Codex rolloutのcustom tool callからClaude sessionを解決し、エンジン別に集計する。"""
    session_id = "claude-session-11111111"
    claude_home = tmp_path / "home"
    claude_transcript = claude_home / ".claude" / "projects" / "repo" / f"{session_id}.jsonl"
    claude_transcript.parent.mkdir(parents=True)
    claude_transcript.write_text(
        json.dumps(_assistant_usage_entry("2026-08-19T00:00:02Z", "claude-message", _usage(4, 5))) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(claude_home))
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:00Z",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "mcp__agents_server__start",
                    "call_id": "call-claude",
                    "arguments": json.dumps({"engine": "claude", "session_id": session_id}),
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    thread = _events_by_kind(events, "stats-agent-thread")[0]
    assert thread["engine"] == "claude"
    assert thread["session_id"] == session_id
    assert thread["tokens"] == _usage(4, 5)
    assert _events_by_kind(events, "stats-total")[0]["agent_thread_counts"] == {"claude": 1}


def test_stats_recursively_discovers_native_subagent_activity_without_cycles(
    tmp_path: pathlib.Path,
    monkeypatch,
    capsys,
) -> None:
    """native `SubAgentActivity`の子孫を重複なく再帰集計し、循環参照で停止しない。"""
    root_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    child_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    grandchild_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    codex_home = tmp_path / "codex"
    rollout_dir = codex_home / "sessions" / "2026" / "08" / "19"
    rollout_dir.mkdir(parents=True)

    def write_rollout(thread_id: str, entries: list[dict]) -> None:
        (rollout_dir / f"rollout-test-{thread_id}.jsonl").write_text(
            "\n".join(json.dumps(entry) for entry in entries) + "\n",
            encoding="utf-8",
        )

    def activity(thread_id: str) -> dict:
        return {"type": "SubAgentActivity", "agent_thread_id": thread_id}

    write_rollout(
        root_id,
        [
            _codex_token_count_entry("2026-08-19T00:00:01Z", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
            {"timestamp": "2026-08-19T00:00:02Z", "payload": {"type": "message", "activity": activity(child_id)}},
            {"timestamp": "2026-08-19T00:00:03Z", "payload": {"type": "message", "activity": activity(child_id)}},
        ],
    )
    write_rollout(
        child_id,
        [
            _codex_token_count_entry("2026-08-19T00:00:04Z", {"input_tokens": 2, "output_tokens": 2, "total_tokens": 4}),
            {"timestamp": "2026-08-19T00:00:05Z", "payload": {"type": "message", "activity": activity(grandchild_id)}},
            {"timestamp": "2026-08-19T00:00:06Z", "payload": {"type": "message", "activity": activity(root_id)}},
        ],
    )
    write_rollout(
        grandchild_id,
        [_codex_token_count_entry("2026-08-19T00:00:07Z", {"input_tokens": 3, "output_tokens": 3, "total_tokens": 6})],
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:00Z",
                "payload": {"type": "message", "role": "assistant", "activity": activity(root_id)},
            }
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    threads = _events_by_kind(events, "stats-agent-thread")
    assert [event["thread"] for event in threads] == [grandchild_id, child_id, root_id]
    assert [event["tokens"]["total_tokens"] for event in threads] == [6, 4, 2]
    assert _events_by_kind(events, "stats-total")[0]["tokens"] == {
        "input_tokens": 6,
        "output_tokens": 6,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }


def test_stats_drops_native_subagent_activity_started_after_review_boundary(
    tmp_path: pathlib.Path,
    monkeypatch,
    capsys,
) -> None:
    """振り返り境界後に起動したnative子孫threadを全体集計から除外する。"""
    root_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    after_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    codex_home = tmp_path / "codex"
    rollout_dir = codex_home / "sessions" / "2026" / "08" / "19"
    rollout_dir.mkdir(parents=True)
    (rollout_dir / f"rollout-test-{root_id}.jsonl").write_text(
        "\n".join(
            json.dumps(entry)
            for entry in [
                _codex_token_count_entry("2026-08-19T00:00:01Z", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
                {
                    "timestamp": "2026-08-19T00:00:02Z",
                    "payload": {"activity": {"type": "SubAgentActivity", "agent_thread_id": after_id}},
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_rollout(
        codex_home,
        after_id,
        [("2026-08-19T00:00:03Z", {"input_tokens": 90, "output_tokens": 90, "total_tokens": 180})],
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:00Z",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "activity": {"type": "SubAgentActivity", "agent_thread_id": root_id},
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:02Z",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "$session-review"}]},
            },
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    assert [event["thread"] for event in _events_by_kind(events, "stats-agent-thread")] == [root_id]


def test_stats_excluded_thread_does_not_return_when_rediscovered_from_sibling(
    tmp_path: pathlib.Path,
    monkeypatch,
    capsys,
) -> None:
    """境界超過で除外したthreadが、後から処理される兄弟rolloutから再発見されても集計へ戻らない。

    `root_id`のrolloutが`shared_id`と`sibling_id`をこの順で子として発見し、`shared_id`は
    境界後に起動したため除外される。`sibling_id`は境界前に起動し、自身のrollout内で
    `shared_id`を再び子として発見するが、既に除外済みのため復帰してはならない。
    """
    root_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    shared_id = "11111111-2222-4111-8222-111111111111"
    sibling_id = "33333333-4444-4333-8444-333333333333"
    codex_home = tmp_path / "codex"
    rollout_dir = codex_home / "sessions" / "2026" / "08" / "19"
    rollout_dir.mkdir(parents=True)

    def activity(thread_id: str) -> dict:
        return {"type": "SubAgentActivity", "agent_thread_id": thread_id}

    (rollout_dir / f"rollout-test-{root_id}.jsonl").write_text(
        "\n".join(
            json.dumps(entry)
            for entry in [
                _codex_token_count_entry("2026-08-19T00:00:01Z", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
                {"timestamp": "2026-08-19T00:00:02Z", "payload": {"activity": activity(shared_id)}},
                {"timestamp": "2026-08-19T00:00:03Z", "payload": {"activity": activity(sibling_id)}},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (rollout_dir / f"rollout-test-{sibling_id}.jsonl").write_text(
        "\n".join(
            json.dumps(entry)
            for entry in [
                _codex_token_count_entry("2026-08-19T00:00:04Z", {"input_tokens": 3, "output_tokens": 3, "total_tokens": 6}),
                {"timestamp": "2026-08-19T00:00:04Z", "payload": {"activity": activity(shared_id)}},
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    _write_rollout(
        codex_home,
        shared_id,
        [("2026-08-19T00:00:10Z", {"input_tokens": 90, "output_tokens": 90, "total_tokens": 180})],
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:00Z",
                "payload": {"type": "message", "role": "assistant", "activity": activity(root_id)},
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:05Z",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "$session-review"}]},
            },
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    threads = {event["thread"] for event in _events_by_kind(events, "stats-agent-thread")}
    assert threads == {root_id, sibling_id}


def test_stats_boundary_excludes_manual_review_and_rejects_combination(tmp_path: pathlib.Path, capsys) -> None:
    """手動起動境界以降を集計せず、statsと既存照会モードの併用を拒否する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "timestamp": "2026-08-19T00:00:00Z", "message": {"role": "user", "content": "作業"}},
            {
                "type": "assistant",
                "timestamp": "2026-08-19T00:00:01Z",
                "message": {"role": "assistant", "id": "before", "usage": {"input_tokens": 2, "output_tokens": 3}},
            },
            {"type": "user", "timestamp": "2026-08-19T00:00:02Z", "message": {"role": "user", "content": "/session-review"}},
            {
                "type": "assistant",
                "timestamp": "2026-08-19T00:00:03Z",
                "message": {"role": "assistant", "id": "after", "usage": {"input_tokens": 100, "output_tokens": 100}},
            },
        ],
    )
    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    assert _events_by_kind(events, "stats-summary")[0]["tokens"]["input_tokens"] == 2
    assert evidence.main([str(transcript), "--stats", "--warn"]) == 2
    assert _read_jsonl(capsys)[0]["kind"] == "error"


def _usage(input_tokens: int, output_tokens: int = 0) -> dict[str, int]:
    """Claude形式の4成分usageを作成する。"""
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }


def _assistant_usage_entry(timestamp: str, message_id: str, usage: dict[str, int]) -> dict:
    """usageだけを持つassistantエントリを作成する。"""
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "message": {"role": "assistant", "id": message_id, "usage": usage},
    }


def _write_subagent(directory: pathlib.Path, agent_id: str, entries: list[dict], meta: dict | None = None) -> None:
    """`subagents/`配下へサブエージェント記録と付随metaを書き込む。"""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{agent_id}.jsonl").write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n",
        encoding="utf-8",
    )
    if meta is not None:
        (directory / f"{agent_id}.meta.json").write_text(json.dumps(meta), encoding="utf-8")


def _write_rollout(codex_home: pathlib.Path, thread_id: str, usages: list[tuple[str, dict[str, int]]]) -> None:
    """`CODEX_HOME`配下へthreadIdに対応するrolloutを書き込む。

    `usages`の各要素は当該リクエストの実消費（`last_token_usage`）とし、
    `total_token_usage`にはそこまでの走行合計を与える。
    """
    rollout_dir = codex_home / "sessions" / "2026" / "08" / "19"
    rollout_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    cumulative: dict[str, int] = {}
    for timestamp, usage in usages:
        for key, value in usage.items():
            cumulative[key] = cumulative.get(key, 0) + value
        entries.append(
            {
                "timestamp": timestamp,
                "payload": {
                    "type": "token_count",
                    "info": {"total_token_usage": dict(cumulative), "last_token_usage": usage},
                },
            }
        )
    (rollout_dir / f"rollout-test-{thread_id}.jsonl").write_text(
        "\n".join(json.dumps(entry) for entry in entries) + "\n",
        encoding="utf-8",
    )


def _codex_tool_use_entry(
    timestamp: str,
    call_id: str,
    thread_id: str,
    *,
    tool_name: str = "mcp__agents_server__start",
) -> dict:
    """Codex委譲のtool_useを持つassistantエントリを作成する。"""
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": tool_name,
                    "id": call_id,
                    "input": {"engine": "codex", "threadId": thread_id},
                }
            ],
        },
    }


def test_stats_outputs_every_subagent_without_limit(tmp_path: pathlib.Path, capsys) -> None:
    """21件以上のサブエージェント記録を件数制限なく全成分合計降順で出力する。"""
    transcript = _write_transcript(
        tmp_path,
        [{"type": "user", "timestamp": "2026-08-19T00:00:00Z", "message": {"role": "user", "content": "依頼"}}],
    )
    subagents = transcript.with_suffix("") / "subagents"
    for index in range(21):
        _write_subagent(
            subagents,
            f"agent-{index:02d}",
            [_assistant_usage_entry("2026-08-19T00:00:01Z", f"message-{index}", _usage(index + 1))],
        )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    rows = _events_by_kind(events, "stats-subagent")
    assert [row["agent"] for row in rows] == [f"agent-{index:02d}" for index in range(20, -1, -1)]
    total = _events_by_kind(events, "stats-subagent-total")[0]
    assert total["count"] == 21
    assert total["tokens"] == _usage(sum(range(1, 22)))


def test_stats_outputs_every_codex_thread_without_limit(tmp_path: pathlib.Path, monkeypatch, capsys) -> None:
    """21件以上のCodexスレッドを件数制限なく`total_tokens`降順で出力する。"""
    codex_home = tmp_path / "codex"
    thread_ids = [f"{index:08d}-0000-4000-8000-000000000000" for index in range(21)]
    entries: list[dict] = [
        {"type": "user", "timestamp": "2026-08-19T00:00:00Z", "message": {"role": "user", "content": "依頼"}}
    ]
    for index, thread_id in enumerate(thread_ids):
        _write_rollout(
            codex_home,
            thread_id,
            [("2026-08-19T00:00:01Z", {"input_tokens": index + 1, "output_tokens": 1, "total_tokens": index + 2})],
        )
        entries.append(_codex_tool_use_entry("2026-08-19T00:00:01Z", f"call-{index}", thread_id))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(tmp_path, entries)

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    threads = _events_by_kind(events, "stats-agent-thread")
    assert [event["thread"] for event in threads] == list(reversed(thread_ids))
    assert [event["tokens"]["total_tokens"] for event in threads] == list(range(22, 1, -1))


def test_stats_excludes_auxiliary_records_started_after_boundary(tmp_path: pathlib.Path, monkeypatch, capsys) -> None:
    """手動起動境界以降に起動した補助記録を主体別集計と全体合算から除く。"""
    thread_id = "33333333-3333-4333-8333-333333333333"
    codex_home = tmp_path / "codex"
    _write_rollout(
        codex_home,
        thread_id,
        [("2026-08-19T00:10:00Z", {"input_tokens": 500, "output_tokens": 500, "total_tokens": 1000})],
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "timestamp": "2026-08-19T00:00:00Z", "message": {"role": "user", "content": "作業"}},
            _assistant_usage_entry("2026-08-19T00:00:01Z", "before", _usage(2, 3)),
            {"type": "user", "timestamp": "2026-08-19T00:05:00Z", "message": {"role": "user", "content": "/session-review"}},
        ],
    )
    subagents = transcript.with_suffix("") / "subagents"
    _write_subagent(
        subagents,
        "agent-before",
        [_assistant_usage_entry("2026-08-19T00:00:02Z", "sub-before", _usage(7))],
    )
    _write_subagent(
        subagents,
        "agent-after",
        [
            _assistant_usage_entry("2026-08-19T00:06:00Z", "sub-after", _usage(900)),
            _codex_tool_use_entry("2026-08-19T00:06:01Z", "call-after", thread_id),
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    assert [row["agent"] for row in _events_by_kind(events, "stats-subagent")] == ["agent-before"]
    assert _events_by_kind(events, "stats-agent-thread") == []
    total = _events_by_kind(events, "stats-total")[0]
    assert total["tokens"] == _usage(9, 3)
    assert total["subagent_count"] == 1
    assert total["agent_thread_count"] == 0


def test_stats_attributes_whole_record_started_before_boundary(tmp_path: pathlib.Path, monkeypatch, capsys) -> None:
    """境界前に起動した補助記録は、境界後のtimestamp・トークンも含めて全体を集計する。"""
    thread_id = "44444444-4444-4444-8444-444444444444"
    codex_home = tmp_path / "codex"
    _write_rollout(
        codex_home,
        thread_id,
        [
            ("2026-08-19T00:00:03Z", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
            ("2026-08-19T00:07:00Z", {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}),
        ],
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "timestamp": "2026-08-19T00:00:00Z", "message": {"role": "user", "content": "作業"}},
            _assistant_usage_entry("2026-08-19T00:00:01Z", "before", _usage(2)),
            {"type": "user", "timestamp": "2026-08-19T00:05:00Z", "message": {"role": "user", "content": "/session-review"}},
        ],
    )
    _write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-span",
        [
            _codex_tool_use_entry("2026-08-19T00:00:02Z", "call-span", thread_id),
            _assistant_usage_entry("2026-08-19T00:00:03Z", "span-1", _usage(5)),
            _assistant_usage_entry("2026-08-19T00:06:00Z", "span-2", _usage(11)),
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    subagent = _events_by_kind(events, "stats-subagent")[0]
    assert subagent["agent"] == "agent-span"
    assert subagent["tokens"] == _usage(16)
    thread = _events_by_kind(events, "stats-agent-thread")[0]
    assert thread["thread"] == thread_id
    assert thread["tokens"]["total_tokens"] == 32


def test_stats_collects_thread_ids_only_from_included_subagents(tmp_path: pathlib.Path, monkeypatch, capsys) -> None:
    """通常サブエージェント発の委譲は`agent`キー付きで出力し、振り返り系発の委譲は出力しない。"""
    normal_thread = "55555555-5555-4555-8555-555555555555"
    review_thread = "66666666-6666-4666-8666-666666666666"
    codex_home = tmp_path / "codex"
    _write_rollout(
        codex_home,
        normal_thread,
        [("2026-08-19T00:00:02Z", {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7})],
    )
    _write_rollout(
        codex_home,
        review_thread,
        [("2026-08-19T00:00:02Z", {"input_tokens": 300, "output_tokens": 400, "total_tokens": 700})],
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [{"type": "user", "timestamp": "2026-08-19T00:00:00Z", "message": {"role": "user", "content": "依頼"}}],
    )
    subagents = transcript.with_suffix("") / "subagents"
    _write_subagent(
        subagents,
        "agent-normal",
        [_codex_tool_use_entry("2026-08-19T00:00:01Z", "call-normal", normal_thread)],
        {"agentType": "Explore"},
    )
    _write_subagent(
        subagents,
        "agent-review",
        [_codex_tool_use_entry("2026-08-19T00:00:01Z", "call-review", review_thread)],
        {"agentType": "session-review-advisor"},
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    threads = _events_by_kind(events, "stats-agent-thread")
    assert [event["thread"] for event in threads] == [normal_thread]
    assert threads[0]["agent"] == "agent-normal"


def test_stats_thread_line_only_for_main_transcript_threads(tmp_path: pathlib.Path, monkeypatch, capsys) -> None:
    """`line`はメイン記録発の委譲だけに付け、サブエージェント記録発の委譲には付けない。

    `line`は`--detail`が解決するメインtranscriptの行番号であり、サブエージェント記録の行番号を
    同じキーで出力すると`--detail`が無関係なエントリを返すため。
    """
    main_thread = "88888888-8888-4888-8888-888888888888"
    sub_thread = "99999999-9999-4999-8999-999999999999"
    codex_home = tmp_path / "codex"
    _write_rollout(
        codex_home, main_thread, [("2026-08-19T00:00:02Z", {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20})]
    )
    _write_rollout(
        codex_home, sub_thread, [("2026-08-19T00:00:02Z", {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7})]
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "timestamp": "2026-08-19T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
            _codex_tool_use_entry("2026-08-19T00:00:01Z", "call-main", main_thread),
        ],
    )
    _write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-sub",
        [
            {"type": "user", "timestamp": "2026-08-19T00:00:01Z", "message": {"role": "user", "content": "委譲"}},
            {"type": "user", "timestamp": "2026-08-19T00:00:01Z", "message": {"role": "user", "content": "追記"}},
            _codex_tool_use_entry("2026-08-19T00:00:01Z", "call-sub", sub_thread),
        ],
        {"agentType": "Explore"},
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    threads = {event["thread"]: event for event in _events_by_kind(events, "stats-agent-thread")}
    assert threads[main_thread]["line"] == 2
    assert "agent" not in threads[main_thread]
    assert threads[sub_thread]["agent"] == "agent-sub"
    assert "line" not in threads[sub_thread]

    assert evidence.main([str(transcript), "--detail", "2"]) == 0
    detail = "".join(json.dumps(event, ensure_ascii=False) for event in _read_jsonl(capsys))
    assert main_thread in detail


def test_stats_total_sums_main_subagent_and_normalized_codex(tmp_path: pathlib.Path, monkeypatch, capsys) -> None:
    """`stats-total`はCodex分をClaude形式の4成分へ変換してから3区分を合算する。"""
    thread_id = "77777777-7777-4777-8777-777777777777"
    codex_home = tmp_path / "codex"
    _write_rollout(
        codex_home,
        thread_id,
        [
            (
                "2026-08-19T00:00:03Z",
                {
                    "input_tokens": 100,
                    "cached_input_tokens": 90,
                    "cache_write_input_tokens": 7,
                    "output_tokens": 40,
                    "reasoning_output_tokens": 30,
                    "total_tokens": 140,
                },
            )
        ],
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "timestamp": "2026-08-19T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
            {
                "type": "assistant",
                "timestamp": "2026-08-19T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "id": "main",
                    "usage": {
                        "input_tokens": 2,
                        "output_tokens": 3,
                        "cache_creation_input_tokens": 4,
                        "cache_read_input_tokens": 5,
                    },
                },
            },
            _codex_tool_use_entry("2026-08-19T00:00:02Z", "call-1", thread_id),
        ],
    )
    _write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-normal",
        [_assistant_usage_entry("2026-08-19T00:00:02Z", "sub", _usage(10, 20))],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    total = _events_by_kind(events, "stats-total")[0]
    assert total["tokens"] == {
        "input_tokens": 22,
        "output_tokens": 63,
        "cache_creation_input_tokens": 11,
        "cache_read_input_tokens": 95,
    }
    assert total["subagent_count"] == 1
    assert total["agent_thread_count"] == 1
    assert total["agent_thread_counts"] == {"codex": 1}
    thread_tokens = _events_by_kind(events, "stats-agent-thread")[0]["tokens"]
    assert thread_tokens["total_tokens"] == 140
    assert thread_tokens["cached_input_tokens"] == 90


def test_stats_total_normalizes_codex_main_record(tmp_path: pathlib.Path, capsys) -> None:
    """メイン記録がCodex形式でも`stats-total`はClaude形式の4成分だけを持つ。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:00Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 1000,
                            "cached_input_tokens": 900,
                            "cache_write_input_tokens": 8,
                            "output_tokens": 60,
                            "reasoning_output_tokens": 20,
                            "total_tokens": 1060,
                        },
                        "last_token_usage": {
                            "input_tokens": 1000,
                            "cached_input_tokens": 900,
                            "cache_write_input_tokens": 8,
                            "output_tokens": 60,
                            "reasoning_output_tokens": 20,
                            "total_tokens": 1060,
                        },
                    },
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    total = _events_by_kind(events, "stats-total")[0]
    assert total["tokens"] == {
        "input_tokens": 100,
        "output_tokens": 60,
        "cache_creation_input_tokens": 8,
        "cache_read_input_tokens": 900,
    }
    assert _events_by_kind(events, "stats-summary")[0]["tokens"]["total_tokens"] == 1060


def test_stats_claude_automatic_start_keeps_records_after_marker(tmp_path: pathlib.Path, capsys) -> None:
    """Claude形式の自動起動では境界を設けず、開始マーカー後の記録も集計する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            _assistant_usage_entry("2026-08-19T00:00:00Z", "before", _usage(2)),
            {
                "type": "user",
                "timestamp": "2026-08-19T00:00:01Z",
                "toolUseResult": {"stdout": evidence.SESSION_REVIEW_STARTED_MARKER},
                "message": {"role": "user", "content": []},
            },
            _assistant_usage_entry("2026-08-19T00:00:02Z", "after", _usage(100)),
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    assert _events_by_kind(events, "stats-summary")[0]["tokens"] == _usage(102)


def test_stats_codex_automatic_boundary_excludes_review_records(tmp_path: pathlib.Path, capsys) -> None:
    """Codex形式の自動起動ではstop通知起点の境界を適用し、境界後の記録を集計しない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:00Z",
                "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "結果"}]},
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:01Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                        "last_token_usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                    },
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:02Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": evidence.STOP_ADVISOR_PREFIX + " 誘導"}],
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:03Z",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {"input_tokens": 502, "output_tokens": 503, "total_tokens": 1005},
                        "last_token_usage": {"input_tokens": 500, "output_tokens": 500, "total_tokens": 1000},
                    },
                },
            },
            {
                "type": "event_msg",
                "timestamp": "2026-08-19T00:00:04Z",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "status": "completed",
                        "aggregated_output": evidence.SESSION_REVIEW_STARTED_MARKER + "\n",
                    },
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    assert _events_by_kind(events, "stats-summary")[0]["tokens"]["total_tokens"] == 5


def test_stats_reports_no_target_without_timestamp_and_tokens(tmp_path: pathlib.Path, capsys) -> None:
    """timestampもトークン情報も無い入力では集計対象なしを返し、終了コード0で終わる。"""
    transcript = _write_transcript(
        tmp_path,
        [{"type": "user", "message": {"role": "user", "content": "依頼"}}],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    assert _events_by_kind(events, "stats-summary") == [{"kind": "stats-summary", "text": "集計対象なし"}]


def test_stats_repeat_limits_to_ten_groups_and_requires_hint(tmp_path: pathlib.Path, capsys) -> None:
    """反復呼び出しは入力ヒントを持つ組だけを回数降順で最大10件出力する。"""
    entries: list[dict] = []
    for index in range(12):
        for repetition in range(2):
            call_id = f"bash-{index}-{repetition}"
            entries.append(
                {
                    "type": "assistant",
                    "timestamp": f"2026-08-19T00:{index:02d}:{repetition:02d}Z",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "tool_use", "name": "Bash", "id": call_id, "input": {"command": f"command-{index}"}}
                        ],
                    },
                }
            )
            entries.append(
                {
                    "type": "user",
                    "timestamp": f"2026-08-19T00:{index:02d}:{repetition:02d}Z",
                    "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": "済"}]},
                }
            )
    for index in range(3):
        call_id = f"todo-{index}"
        entries.append(
            {
                "type": "assistant",
                "timestamp": f"2026-08-19T00:30:{index:02d}Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "TodoWrite", "id": call_id, "input": {"todos": []}}],
                },
            }
        )
        entries.append(
            {
                "type": "user",
                "timestamp": f"2026-08-19T00:30:{index:02d}Z",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": "済"}]},
            }
        )
    transcript = _write_transcript(tmp_path, entries)

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    repeats = _events_by_kind(events, "stats-repeat")
    assert len(repeats) == 10
    assert {event["tool"] for event in repeats} == {"Bash"}
    assert all(event["hint"].startswith("command-") for event in repeats)


def test_stats_repeat_uses_target_input_keys_of_tools_without_command(tmp_path: pathlib.Path, capsys) -> None:
    """`command`を持たないツールでも対象を表す入力キーをヒントとし、反復を集計する。"""
    entries: list[dict] = []
    inputs = [{"file_path": "/tmp/same.py"}] * 3 + [{"pattern": "同じ検索語"}] * 2 + [{"file_path": "/tmp/other.py"}]
    names = ["Read"] * 3 + ["Grep"] * 2 + ["Read"]
    for index, (name, block_input) in enumerate(zip(names, inputs, strict=True)):
        call_id = f"call-{index}"
        entries.append(
            {
                "type": "assistant",
                "timestamp": f"2026-08-19T00:00:{index:02d}Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": name, "id": call_id, "input": block_input}],
                },
            }
        )
        entries.append(
            {
                "type": "user",
                "timestamp": f"2026-08-19T00:00:{index:02d}Z",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": "済"}]},
            }
        )
    transcript = _write_transcript(tmp_path, entries)

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    repeats = _events_by_kind(events, "stats-repeat")
    assert [(event["tool"], event["hint"], event["count"]) for event in repeats] == [
        ("Read", "/tmp/same.py", 3),
        ("Grep", "同じ検索語", 2),
    ]
    assert repeats[0]["lines"] == [1, 3, 5]


def test_stats_repeat_distinguishes_multiline_commands_sharing_first_line(tmp_path: pathlib.Path, capsys) -> None:
    """先頭行が同一の複数行コマンドは、本体が異なれば同じ反復組へ集約しない。"""
    commands = ["cd /repo\nmake test", "cd /repo\nmake lint", "cd /repo\nmake test"]
    entries: list[dict] = []
    for index, command in enumerate(commands):
        call_id = f"call-{index}"
        entries.append(
            {
                "type": "assistant",
                "timestamp": f"2026-08-19T00:00:{index * 2:02d}Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": call_id, "input": {"command": command}}],
                },
            }
        )
        entries.append(
            {
                "type": "user",
                "timestamp": f"2026-08-19T00:00:{index * 2 + 1:02d}Z",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": "済"}]},
            }
        )
    transcript = _write_transcript(tmp_path, entries)

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    assert _events_by_kind(events, "stats-repeat") == [
        {"kind": "stats-repeat", "tool": "Bash", "hint": "cd /repo\nmake test", "count": 2, "lines": [1, 5]}
    ]


def test_stats_repeat_distinguishes_long_commands_sharing_clipped_prefix(tmp_path: pathlib.Path, capsys) -> None:
    """表示上の切り詰め長を超えて前方一致するだけの呼び出しは、同じ反復組へ集約しない。"""
    shared_prefix = "echo " + "a" * 2100
    commands = [f"{shared_prefix} first", f"{shared_prefix} second", f"{shared_prefix} first"]
    entries: list[dict] = []
    for index, command in enumerate(commands):
        call_id = f"call-{index}"
        entries.append(
            {
                "type": "assistant",
                "timestamp": f"2026-08-19T00:00:{index * 2:02d}Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": call_id, "input": {"command": command}}],
                },
            }
        )
        entries.append(
            {
                "type": "user",
                "timestamp": f"2026-08-19T00:00:{index * 2 + 1:02d}Z",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": "済"}]},
            }
        )
    transcript = _write_transcript(tmp_path, entries)

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    repeats = _events_by_kind(events, "stats-repeat")
    assert [(event["tool"], event["count"], event["lines"]) for event in repeats] == [("Bash", 2, [1, 5])]
    assert repeats[0]["hint"].endswith("…[省略]")
    assert len(repeats[0]["hint"]) == 2000 + len("…[省略]")


def test_stats_takes_codex_hint_from_input_without_arguments(tmp_path: pathlib.Path, capsys) -> None:
    """`arguments`を持たないCodexの呼び出しでは`input`の本文をヒントとする。"""
    command_input = 'const out = await sh({cmd: "rg -n \'foo\' src", workdir: "/repo"});'
    entries: list[dict] = [
        {
            "type": "response_item",
            "timestamp": "2026-08-19T00:00:00Z",
            "payload": {"type": "message", "role": "user", "content": "依頼"},
        }
    ]
    for index, seconds in enumerate((2, 6)):
        call_id = f"call-{index}"
        started = index * 10 + 1
        entries.append(
            {
                "type": "response_item",
                "timestamp": f"2026-08-19T00:00:{started:02d}Z",
                "payload": {
                    "type": "custom_tool_call",
                    "status": "completed",
                    "call_id": call_id,
                    "name": "exec",
                    "input": command_input,
                },
            }
        )
        entries.append(
            {
                "type": "response_item",
                "timestamp": f"2026-08-19T00:00:{started + seconds:02d}Z",
                "payload": {"type": "custom_tool_call_output", "call_id": call_id, "output": "完了"},
            }
        )
    transcript = _write_transcript(tmp_path, entries)

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    assert _events_by_kind(events, "stats-repeat") == [
        {"kind": "stats-repeat", "tool": "exec", "hint": command_input, "count": 2, "lines": [2, 4]}
    ]
    assert _events_by_kind(events, "stats-slow-call") == [
        {"kind": "stats-slow-call", "tool": "exec", "seconds": 6.0, "line": 4, "hint": command_input},
        {"kind": "stats-slow-call", "tool": "exec", "seconds": 2.0, "line": 2, "hint": command_input},
    ]


def test_hook_notices_mode_is_exclusive_with_other_query_modes(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """構造化集計モードも他の照会モードとの併用を引数誤用として拒否する。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])

    assert evidence.main([str(transcript), "--hook-notices", "--warn"]) == 2

    events = _read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["error"]
    assert "--hook-notices" in events[0]["text"]


def _hook_attachment(attachment: dict) -> dict:
    """hook実行の記録をtranscriptのエントリ形式へ包む。"""
    return {"type": "attachment", "attachment": attachment}


def test_hook_notices_mode_counts_notices_by_hook_origin_tag_and_kind(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """hook実行の記録4種の通知だけを、hook識別子・発動元・タグ・種別ごとに数える。

    同一ツール呼び出しの標準出力と追加コンテキストへ重複して格納された通知は1件へ集約し、
    追加コンテキストを伴わない標準エラー出力の通知と、標識を持たないシステムメッセージも計上する。
    hookの記録でない本文（会話中の引用）は同じ文字列でも母集団へ含めない。
    """
    truncation = "[auto-generated: agent-toolkit/pretooluse][warn] warn: 出力を切り詰めている"
    fixed_wait = "[auto-generated: agent-toolkit/pretooluse][block] block: 固定待機を検出した"
    stop_notice = "[auto-generated: dotfiles/claude_hook_stop] 応答の完了可否を明示すること"
    system_message = "[agent-toolkit] auto-inserted --decorate into git log."
    transcript = _write_transcript(
        tmp_path,
        [
            _hook_attachment(
                {
                    "type": "hook_success",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "stdout": json.dumps({"hookSpecificOutput": {"additionalContext": truncation}}, ensure_ascii=False),
                    "stderr": "",
                }
            ),
            _hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "content": [truncation],
                }
            ),
            _hook_attachment(
                {
                    "type": "hook_success",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-2",
                    "stdout": "{}",
                    "stderr": fixed_wait,
                }
            ),
            _hook_attachment(
                {
                    "type": "hook_success",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-3",
                    "stdout": "{}",
                    "stderr": fixed_wait,
                }
            ),
            _hook_attachment(
                {
                    "type": "hook_system_message",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-4",
                    "content": system_message,
                }
            ),
            _hook_attachment(
                {
                    "type": "hook_blocking_error",
                    "hookName": "Stop",
                    "toolUseID": "call-5",
                    "blockingError": {"blockingError": stop_notice},
                }
            ),
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": truncation}]}},
        ],
    )

    assert evidence.main([str(transcript), "--hook-notices"]) == 0

    events = _read_jsonl(capsys)
    assert events[-1] == {"kind": "summary", "count": 5}
    assert events[0] == {
        "kind": "hook-notice",
        "hook": "agent-toolkit/pretooluse",
        "hook_name": "PreToolUse:Bash",
        "tag": "block",
        "kind_text": "block: 固定待機を検出した",
        "count": 2,
    }
    assert sorted(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in events[1:-1]) == sorted(
        json.dumps(event, ensure_ascii=False, sort_keys=True)
        for event in [
            {
                "kind": "hook-notice",
                "hook": "agent-toolkit/pretooluse",
                "hook_name": "PreToolUse:Bash",
                "tag": "warn",
                "kind_text": "warn: 出力を切り詰めている",
                "count": 1,
            },
            {
                "kind": "hook-notice",
                "hook": "dotfiles/claude_hook_stop",
                "hook_name": "Stop",
                "tag": None,
                "kind_text": "応答の完了可否を明示すること",
                "count": 1,
            },
            {
                "kind": "hook-notice",
                "hook": None,
                "hook_name": "PreToolUse:Bash",
                "tag": None,
                "kind_text": system_message,
                "count": 1,
            },
        ]
    )


def test_hook_notices_mode_separates_kinds_by_leading_body_and_skips_empty_bodies(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """先頭一定長が異なる通知を別種別として数え、本文が空の記録は数えない。"""
    prefix = "[auto-generated: agent-toolkit/pretooluse][warn] "
    head = "a" * 79  # 種別キーの長さ80文字の直前まで同一とし、80文字目だけを違えて別種別とする
    transcript = _write_transcript(
        tmp_path,
        [
            _hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "content": [f"{prefix}{head}x 対象A", f"{prefix}{head}y 対象B"],
                }
            ),
            _hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-2",
                    "content": [f"{prefix}{head}x 対象C"],
                }
            ),
            _hook_attachment(
                {"type": "hook_success", "hookName": "Stop", "toolUseID": "call-3", "stdout": "{}", "stderr": "   "}
            ),
        ],
    )

    assert evidence.main([str(transcript), "--hook-notices"]) == 0

    events = _read_jsonl(capsys)
    assert [(event["kind_text"], event["count"]) for event in events[:-1]] == [
        (f"{head}x", 2),
        (f"{head}y", 1),
    ]
    assert events[-1] == {"kind": "summary", "count": 3}


def test_hook_notices_mode_merges_kinds_differing_only_by_variable_parts(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """パスや識別子だけが異なる同種の通知を1つの種別へ集約する。"""
    prefix = "[auto-generated: agent-toolkit/posttooluse][notice] "
    bodies = [
        f"{prefix}plan file /home/aki/.claude/plans/alpha-1.md was written.",
        f"{prefix}plan file /home/aki/.claude/plans/beta-2.md was written.",
        f"{prefix}plan file ~/.claude/plans/gamma-3.md was written.",
    ]
    transcript = _write_transcript(
        tmp_path,
        [
            _hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PostToolUse:Write",
                    "toolUseID": f"call-{index}",
                    "content": [body],
                }
            )
            for index, body in enumerate(bodies)
        ],
    )

    assert evidence.main([str(transcript), "--hook-notices"]) == 0

    events = _read_jsonl(capsys)
    assert [(event["kind_text"], event["count"]) for event in events[:-1]] == [
        ("plan file <var> was written.", 3),
    ]
    assert events[-1] == {"kind": "summary", "count": 3}

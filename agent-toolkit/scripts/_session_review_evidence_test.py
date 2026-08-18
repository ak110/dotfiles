"""セッション振り返り用の証拠抽出を検証する。"""

from __future__ import annotations

import json
import pathlib

import _session_review_evidence as evidence
import pytest
from conftest import _write_transcript


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
    """識別子・時刻・形式名・実行環境といった管理用の値への一致を警告として報告しない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "warning",
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
                    "content": [notice],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0
    warnings = _read_jsonl(capsys)
    assert [event["line"] for event in warnings] == [1, 2]
    assert notice in warnings[0]["text"]
    assert warnings[1]["text"] == notice

    assert evidence.main([str(transcript), "--grep", "切り詰めている"]) == 0
    matches = _read_jsonl(capsys)
    assert [event["line"] for event in matches[:2]] == [1, 2]
    assert matches[-1] == {"kind": "summary", "count": 2}


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


def test_detail_mode_keeps_total_within_limit_for_many_string_values(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """文字列値を多数持つ入力でも、詳細本文の合計が1エントリ分の上限を超えない。

    予算が尽きた後も本文ごとに省略標識を付けると、合計が文字列値の個数に比例して増える。
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

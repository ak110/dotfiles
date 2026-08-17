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
        {"kind": "user", "text": "質問: 質問\n回答: 回答", "sequence": 1},
        {"kind": "user", "text": "質問: 別の質問\n回答: 別の回答", "sequence": 2},
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
        {"kind": "final-result", "text": "回答待ち", "sequence": 1},
        {
            "kind": "user",
            "text": "質問: 最初の質問\n回答: 最初の回答\n質問: 次の質問\n回答: 次の回答1\n次の回答2",
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

    assert events == [{"kind": "agent-completion", "text": "Message Type: FINAL_ANSWER\n完了報告", "sequence": 1}]


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
        {"kind": "final-result", "text": "本来の最終結果", "sequence": 1},
        {"kind": "user", "text": "開始後の介入", "sequence": 2},
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

    assert evidence.load_and_extract(str(transcript)) == [{"kind": "final-result", "text": "本来の最終結果", "sequence": 1}]
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

    assert evidence.load_and_extract(str(transcript)) == [{"kind": "final-result", "text": "本来の最終結果", "sequence": 1}]
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

    assert events[-1] == {"kind": "final-result", "text": "本来の最終結果", "sequence": 4}
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

    assert evidence.load_and_extract(str(transcript)) == [{"kind": "final-result", "text": "本来の最終結果", "sequence": 1}]


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

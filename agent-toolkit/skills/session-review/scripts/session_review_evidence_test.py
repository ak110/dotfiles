"""セッション振り返り用の証拠抽出の起動スクリプトと、照会モードを指定しない時系列の抽出を検証する。

照会モードごとの検証は`session_evidence_<照会モード>_test.py`、複数のテストが使う記録の組み立ては
`agent_toolkit/_testing/session_evidence_support.py`に置く。
"""

from __future__ import annotations

import json
import pathlib
import shlex

import pytest
import session_evidence_candidates as evidence_candidates
import session_evidence_extract as evidence_extract
import session_review_evidence as evidence

from agent_toolkit._agents_server import backends
from agent_toolkit._atk import outcome
from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import (
    hook_attachment,
    local_time_transcript,
    read_jsonl,
    timestamped_entry,
)


def test_direct_cli_returns_events_for_stream_redirection(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "入力"}}])
    assert evidence.main([str(transcript)]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "kind": "user",
        "runtime_inserted": False,
        "text": "入力",
        "line": 1,
        "timestamp": None,
        "sequence": 1,
        "record": "claude:transcript",
    }


@pytest.mark.parametrize("schema_locators", [["main:1"], ["main:2"], ["main:1", "main:2"]])
def test_combined_locator_queries(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], schema_locators: list[str]
) -> None:
    """本文と構造を同じ収集記録から取得し、反復指定した結果も単独照会と一致する。"""
    transcript = _write_transcript(
        tmp_path,
        [{"type": "user", "message": {"role": "user", "content": text}} for text in ("最初の入力", "追加の入力")],
    )
    detail_args = ["--detail", "main:1", "--detail", "main:2"]
    schema_args = [argument for locator in schema_locators for argument in ("--record-schema", locator)]
    assert evidence.main([str(transcript), *detail_args]) == 0
    details = read_jsonl(capsys)
    assert evidence.main([str(transcript), *schema_args]) == 0
    schemas = read_jsonl(capsys)
    assert evidence.main([str(transcript), *detail_args, *schema_args]) == 0
    combined = read_jsonl(capsys)
    assert combined == [*details, *schemas]
    assert {event["kind"] for event in combined} == {"detail", "record-schema"}


def test_combined_locator_query_errors(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """片方の解決失敗を成功で隠さず、成功した側の結果も返す。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])
    assert evidence.main([str(transcript), "--detail", "main:1", "--record-schema", "main:999"]) == 2
    events = read_jsonl(capsys, raw=True)
    assert any(event["kind"] == "detail" for event in events)
    errors = [event for event in events if event["kind"] == "error"]
    assert errors and all(event.get("next_action") for event in errors)
    assert evidence.main([str(transcript), "--detail", "main:1", "--record-schema", "main:1", "--stats"]) == 2
    assert any(event["kind"] == "error" for event in read_jsonl(capsys))


def test_output_file_option_is_removed(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="2"):
        evidence.main(["unused.jsonl", "--output-file", "relative.jsonl"])
    assert "--output-file" in capsys.readouterr().err


def test_send_to_user_message_is_assistant_event(tmp_path: pathlib.Path) -> None:
    """send_to_userの`message`はユーザーへ届いた本文として、assistantの出来事にする。"""
    call = {"type": "tool_use", "id": "toolu_1", "name": "mcp__agent-toolkit__send_to_user", "input": {"message": "途中の報告"}}
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "依頼"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [call]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "最終結果"}]}},
        ],
    )

    events = evidence_extract.load_and_extract(str(transcript))

    assert [(event["kind"], event["text"]) for event in events] == [
        ("user", "依頼"),
        ("assistant", "途中の報告"),
        ("final-result", "最終結果"),
    ]


@pytest.mark.parametrize("work_position", ["none", "before", "after", "next-entry"])
def test_sent_final_result_uses_work_order(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], work_position: str
) -> None:
    """送信を報告として扱い、後続の作業だけで途中発話へ戻す。"""
    sent = {"type": "tool_use", "id": "sent", "name": "mcp__agent-toolkit__send_to_user", "input": {"message": "結果の報告"}}
    work = {"type": "tool_use", "id": "work", "name": "Bash", "input": {"command": "pwd"}}
    blocks = [work, sent] if work_position == "before" else [sent, work] if work_position == "after" else [sent]
    entries = [{"type": "assistant", "message": {"role": "assistant", "content": blocks}}]
    if work_position == "next-entry":
        entries.append({"type": "assistant", "message": {"role": "assistant", "content": [work]}})
    entries.append(
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "sent", "content": "ユーザーの画面へ表示した。"},
                ],
            },
        }
    )
    transcript = _write_transcript(tmp_path, entries)
    assert evidence.main([str(transcript)]) == 0
    events = read_jsonl(capsys)
    report = next(event for event in events if event.get("text") == "結果の報告")
    assert report["kind"] == ("assistant" if work_position in {"after", "next-entry"} else "final-result")
    assert (report.get("phase") == "commentary") is (work_position in {"after", "next-entry"})


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

    events = evidence_extract.load_and_extract(str(transcript))

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

    events = evidence_extract.load_and_extract(str(transcript))

    assert len(events) == 1
    assert events[0]["kind"] == "failed-tool"
    assert "通常出力" not in events[0]["text"]
    assert events[0]["text"].endswith("…[省略]")


def test_claude_failed_tool_keeps_corresponding_operation(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "call-1", "name": "Bash", "input": {"command": "rg foo"}},
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call-1", "is_error": True, "content": "Exit code 1\n原因A"},
                    ],
                },
            },
        ],
    )

    failed = [event for event in evidence_extract.load_and_extract(str(transcript)) if event["kind"] == "failed-tool"]

    assert len(failed) == 1
    assert failed[0]["tool_name"] == "Bash"
    assert json.loads(failed[0]["operation"]) == {"command": "rg foo"}


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

    events = evidence_extract.load_and_extract(str(transcript))

    assert events == [
        {
            "kind": "user",
            "text": "最初の回答\n次の回答",
            "runtime_inserted": False,
            "assistant_context": [
                {"question": "最初の質問", "options": []},
                {"question": "次の質問", "options": []},
            ],
            "user_response": [{"answers": ["最初の回答"]}, {"answers": ["次の回答"]}],
            "line": 1,
            "timestamp": None,
            "sequence": 1,
        }
    ]


def _claude_answer_event_with_options(
    tmp_path: pathlib.Path,
    options: list[dict[str, str]],
    answer: str,
    notes: str | None,
) -> dict[str, object]:
    """選択肢を持つAskUserQuestionの回答記録から抽出した単一イベントを返す。"""
    annotations: dict[str, object] = {"方針": {"notes": notes}} if notes is not None else {}
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
                            "name": "AskUserQuestion",
                            "id": "question",
                            "input": {"questions": [{"question": "方針", "options": options}]},
                        }
                    ],
                },
            },
            {
                "type": "user",
                "toolUseResult": {"answers": {"方針": answer}, "annotations": annotations},
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "question", "content": "通常出力"}],
                },
            },
        ],
    )
    return evidence_extract.load_and_extract(str(transcript))[0]


@pytest.mark.parametrize(
    ("options", "answer", "notes", "expected"),
    [
        ([{"label": "既存機構へ統合"}, {"label": "新機構を追加"}], "既存機構へ統合", None, False),
        ([{"label": "既存機構へ統合"}, {"label": "新機構を追加"}], "既存機構へ統合, 新機構を追加", None, False),
        ([{"label": "既存機構へ統合"}], "対象範囲を広げて全件を対象にする", None, True),
        ([{"label": "既存機構へ統合"}], "既存機構へ統合", "ただし対象範囲は全件とする", True),
        ([], "(notes only)", "全件を対象にする", True),
    ],
)
def test_claude_answer_intervention_marks_answers_outside_offered_choices(
    tmp_path: pathlib.Path,
    options: list[dict[str, str]],
    answer: str,
    notes: str | None,
    expected: bool,
) -> None:
    """提示した選択肢のlabelと一致しない回答と自由記述を伴う回答へ介入の標識を付ける。"""
    event = _claude_answer_event_with_options(tmp_path, options, answer, notes)

    assert (event.get("answer_intervention") is True) is expected


def _claude_answer_event(tmp_path: pathlib.Path, result: dict[str, object]) -> dict[str, object]:
    """AskUserQuestionの回答記録から抽出した単一イベントを返す。"""
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
                "toolUseResult": result,
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "question", "content": "通常出力"}],
                },
            },
        ],
    )
    event = evidence_extract.load_and_extract(str(transcript))[0]
    assert isinstance(event["text"], str)
    return event


def test_claude_answer_includes_annotation_notes(tmp_path: pathlib.Path) -> None:
    """annotationsの文字列notesを自由記述として証拠化する。"""
    event = _claude_answer_event(
        tmp_path,
        {"answers": {"質問": "回答"}, "annotations": {"質問": {"notes": "自由記述"}}},
    )

    assert event["text"] == "回答\n自由記述"
    assert event["user_response"] == [{"answers": ["回答"], "notes": "自由記述"}]


def test_claude_answer_with_notes_only_keeps_recorded_answer(tmp_path: pathlib.Path) -> None:
    """選択肢なしの自由記述でも記録済み回答を保持する。"""
    event = _claude_answer_event(
        tmp_path,
        {"answers": {"質問": "(notes only)"}, "annotations": {"質問": {"notes": "自由記述"}}},
    )

    assert event["text"] == "(notes only)\n自由記述"


@pytest.mark.parametrize("annotations", [{"質問": {"preview": "選択肢"}}, None])
def test_claude_answer_without_notes_is_unchanged(tmp_path: pathlib.Path, annotations: object) -> None:
    """自由記述が無い回答の本文を変更しない。"""
    result: dict[str, object] = {"answers": {"質問": "回答"}}
    if annotations is not None:
        result["annotations"] = annotations

    event = _claude_answer_event(tmp_path, result)

    assert event["text"] == "回答"
    assert isinstance(event["text"], str)
    assert "自由記述" not in event["text"]


@pytest.mark.parametrize("annotations", [[], {"質問": "不正"}, {"質問": {"notes": ["不正"]}}])
def test_claude_answer_ignores_non_string_annotation_notes(tmp_path: pathlib.Path, annotations: object) -> None:
    """辞書以外または文字列以外のnotesを自由記述として出力しない。"""
    event = _claude_answer_event(tmp_path, {"answers": {"質問": "回答"}, "annotations": annotations})

    assert isinstance(event["text"], str)
    assert "自由記述" not in event["text"]


@pytest.mark.parametrize(
    "answers",
    [
        {"質問": ["文字列ではない"]},
        {"質問": "回答", "不正な質問": 1},
        [],
    ],
)
def test_claude_ignores_non_string_answer_maps(tmp_path: pathlib.Path, answers: object) -> None:
    """文字列辞書ではないClaude answersからユーザー判断を捏造しない。"""
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

    assert not evidence_extract.load_and_extract(str(transcript))


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
    """payload形状にかかわらず未対応の通常tool resultをユーザー判断へ変換しない。"""
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

    assert not evidence_extract.load_and_extract(str(transcript))


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

    assert evidence_extract.load_and_extract(str(transcript)) == [
        {
            "kind": "user",
            "text": "回答",
            "runtime_inserted": False,
            "assistant_context": [{"question": "質問", "options": []}],
            "user_response": [{"answers": ["回答"]}],
            "line": 1,
            "timestamp": None,
            "sequence": 1,
        },
        {
            "kind": "user",
            "text": "別の回答",
            "runtime_inserted": False,
            "assistant_context": [{"question": "別の質問", "options": []}],
            "user_response": [{"answers": ["別の回答"]}],
            "line": 1,
            "timestamp": None,
            "sequence": 2,
        },
    ]


@pytest.mark.parametrize("transcript_path", ["/not-found/session.jsonl", "relative/session.jsonl"])
def test_main_rejects_unreadable_transcript_path(
    transcript_path: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """対象記録を解決できない呼び出しはerrorイベントと終了コード2を返す。"""
    assert evidence.main([transcript_path]) == 2

    assert read_jsonl(capsys) == [{"kind": "error", "text": f"対象記録を読み込めない: {transcript_path}"}]


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
    assert json.loads(lines[0]) == {
        "kind": "user",
        "runtime_inserted": False,
        "text": "入力",
        "line": 1,
        "timestamp": None,
        "sequence": 1,
        "record": "claude:transcript",
    }


def test_without_observation_boundary_output_is_unchanged(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """観測境界を指定しない呼び出しは境界後相当の記録も従来どおり返す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            timestamped_entry("2026-09-01T00:00:01Z", "先行記録"),
            timestamped_entry("2026-09-01T00:00:03Z", "後続記録"),
        ],
    )

    assert evidence.main([str(transcript)]) == 0

    assert [event["text"] for event in read_jsonl(capsys)] == ["先行記録", "後続記録"]


def test_invalid_observation_boundary_returns_exit_code_two(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """解析できない観測境界はエラーイベントと終了コード2を返す。"""
    transcript = _write_transcript(tmp_path, [timestamped_entry("2026-09-01T00:00:01Z", "記録")])

    assert evidence.main([str(transcript), "--observation-boundary", "not-a-timestamp"]) == 2

    assert read_jsonl(capsys) == [{"kind": "error", "text": "観測境界が不正: not-a-timestamp"}]


def test_elapsed_until_returns_seconds_from_first_record(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """指定時刻までの経過秒数と入力の時刻表現を返す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            timestamped_entry("2026-09-01T00:00:01Z", "開始"),
            timestamped_entry("2026-09-01T00:00:03Z", "終了"),
        ],
    )

    assert evidence.main([str(transcript), "--elapsed-until", "2026-09-01T00:01:01Z"]) == 0

    assert read_jsonl(capsys) == [
        {
            "kind": "session-elapsed",
            "start": "2026-09-01T00:00:01Z",
            "until": "2026-09-01T00:01:01Z",
            "elapsed_seconds": 60,
        }
    ]


def test_elapsed_until_with_observation_boundary_keeps_same_result(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """観測境界を併用しても経過時間の起点と終端を変えない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            timestamped_entry("2026-09-01T00:00:01Z", "開始"),
            timestamped_entry("2026-09-01T00:00:03Z", "境界後"),
        ],
    )
    elapsed_args = [str(transcript), "--elapsed-until", "2026-09-01T00:01:01Z"]

    assert evidence.main(elapsed_args) == 0
    without_boundary = read_jsonl(capsys)
    assert evidence.main([*elapsed_args, "--observation-boundary", "2026-09-01T00:00:02Z"]) == 0

    assert read_jsonl(capsys) == without_boundary


def test_elapsed_until_rejects_invalid_observation_boundary(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """経過時間照会と併用した解析不能な観測境界を拒否する。"""
    transcript = _write_transcript(tmp_path, [timestamped_entry("2026-09-01T00:00:01Z", "記録")])

    assert (
        evidence.main(
            [
                str(transcript),
                "--elapsed-until",
                "2026-09-01T00:00:02Z",
                "--observation-boundary",
                "not-a-timestamp",
            ]
        )
        == 2
    )

    assert read_jsonl(capsys) == [{"kind": "error", "text": "観測境界が不正: not-a-timestamp"}]


def test_elapsed_until_rejects_invalid_timestamp(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """解析できない経過時間の終端を拒否する。"""
    transcript = _write_transcript(tmp_path, [timestamped_entry("2026-09-01T00:00:01Z", "記録")])

    assert evidence.main([str(transcript), "--elapsed-until", "not-a-timestamp"]) == 2

    assert read_jsonl(capsys) == [{"kind": "error", "text": "経過時間の終端が不正: not-a-timestamp"}]


def test_elapsed_until_rejects_records_without_timestamp(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """起点になる時刻を持たない記録を拒否する。"""
    transcript = _write_transcript(tmp_path, [timestamped_entry(None, "記録")])

    assert evidence.main([str(transcript), "--elapsed-until", "2026-09-01T00:00:01Z"]) == 2

    assert read_jsonl(capsys) == [{"kind": "error", "text": f"経過時間を算出できる記録が無い: {transcript}"}]


def test_elapsed_until_rejects_time_before_first_record(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """最初の記録より前の経過時間の終端を拒否する。"""
    transcript = _write_transcript(tmp_path, [timestamped_entry("2026-09-01T00:00:02Z", "記録")])

    assert evidence.main([str(transcript), "--elapsed-until", "2026-09-01T00:00:01Z"]) == 2

    assert read_jsonl(capsys) == [
        {
            "kind": "error",
            "text": "経過時間の終端が最初のレコードより前: 2026-09-01T00:00:01Z",
        }
    ]


@pytest.mark.usefixtures("local_time_jst")
def test_elapsed_until_without_timezone_is_local_time(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """タイムゾーンを省いた`--elapsed-until`は、ローカルタイムゾーンのオフセットを付けた値と同じ経過秒数を返す。"""
    transcript = local_time_transcript(tmp_path)

    assert evidence.main([str(transcript), "--elapsed-until", "2026-10-07T00:01:00"]) == 0
    (naive,) = read_jsonl(capsys)
    assert evidence.main([str(transcript), "--elapsed-until", "2026-10-07T00:01:00+09:00"]) == 0
    (offset,) = read_jsonl(capsys)

    assert naive["elapsed_seconds"] == offset["elapsed_seconds"] == 120


def test_elapsed_until_after_reconciliation_includes_finalization_time(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """再比較後の成果確定時刻までを経過時間へ含める。"""
    transcript = _write_transcript(
        tmp_path,
        [
            timestamped_entry("2026-09-01T00:00:01Z", "開始"),
            timestamped_entry("2026-09-01T00:00:07Z", "再取得中の入力"),
        ],
    )
    final_reconciliation_boundary = "2026-09-01T00:00:09Z"
    finalized_at = "2026-09-01T00:00:12Z"

    assert evidence.main([str(transcript), "--observation-boundary", final_reconciliation_boundary]) == 0
    read_jsonl(capsys, raw=True)
    assert evidence.main([str(transcript), "--elapsed-until", finalized_at]) == 0

    assert read_jsonl(capsys) == [
        {
            "kind": "session-elapsed",
            "start": "2026-09-01T00:00:01Z",
            "until": finalized_at,
            "elapsed_seconds": 11,
        }
    ]


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

    events = evidence_extract.load_and_extract(str(transcript))

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
            {"id": "first", "question": "最初の質問", "options": [{"label": "提示した選択肢"}]},
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

    events = evidence_extract.load_and_extract(str(transcript))

    assert events == [
        {"kind": "final-result", "text": "回答待ち", "line": 2, "timestamp": None, "sequence": 1},
        {
            "kind": "user",
            "text": "最初の回答\n次の回答1\n次の回答2",
            "runtime_inserted": False,
            "assistant_context": [
                {
                    "question": "最初の質問",
                    "options": [{"label": "提示した選択肢", "description": ""}],
                },
                {"question": "次の質問", "options": []},
            ],
            "user_response": [
                {"answers": ["最初の回答"]},
                {"answers": ["次の回答1", "次の回答2"]},
            ],
            # 最初の回答は提示した選択肢のlabelと一致しないため、Claude Codeの回答と同じく介入として残す。
            "answer_intervention": True,
            "line": 1,
            "timestamp": None,
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

    events = evidence_extract.load_and_extract(str(transcript))

    assert [event["text"] for event in events] == ["回答2", "回答1"]


def _known_question_with_output(output: str) -> list[dict]:
    """`request_user_input`の呼び出しと、指定した出力を持つ結果のCodexの記録を返す。"""
    return [
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
            "payload": {"type": "function_call_output", "call_id": "known-call", "output": output},
        },
    ]


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
        _known_question_with_output("{broken"),
        _known_question_with_output(json.dumps({"answers": {"unknown": {"answers": ["回答"]}}})),
    ],
)
def test_codex_ignores_malformed_or_unmatched_question_payloads(
    tmp_path: pathlib.Path,
    entries: list[dict],
) -> None:
    """不正なJSON、未対応call、未知の質問IDから証拠を捏造しない。"""
    transcript = _write_transcript(tmp_path, entries)

    assert not evidence_extract.load_and_extract(str(transcript))


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

    events = evidence_extract.load_and_extract(str(transcript))

    assert events == [
        {
            "kind": "agent-completion",
            "text": "Message Type: FINAL_ANSWER\n完了報告",
            "line": 2,
            "timestamp": None,
            "sequence": 1,
        }
    ]


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

    events = evidence_extract.load_and_extract(str(transcript))

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

    events = evidence_extract.load_and_extract(str(transcript))

    assert len(events) == 1
    assert events[0]["kind"] == "failed-tool"
    assert events[0]["tool"] == "CommandExecution"
    assert events[0]["text"] == "CommandExecution failed"
    assert events[0]["diagnostic"] == ""


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

    event = evidence_extract.load_and_extract(str(transcript))[0]

    assert event["command"] == json.dumps(command, ensure_ascii=False)
    assert event["exit_code"] == 17
    assert event["text"] == "失敗"
    assert event["executable"] == (command[0] if command else "")
    assert event["diagnostic"] == "失敗"


def _bundle_failed_codex_commands(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    cases: list[tuple[list[str], int, str]],
) -> list[dict]:
    """CommandExecutionの失敗列を公開されたbundleコマンドへ渡し、候補レコードを返す。"""
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
                        "exit_code": code,
                        "stderr": diagnostic,
                    },
                },
            }
            for command, code, diagnostic in cases
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    read_jsonl(capsys)
    return [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]


def test_bundle_groups_wrapped_commands_by_failure_signature(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """包装とスクリプト名が異なる同じ原因を1署名へまとめ、別の終了理由は分ける。"""
    cases = [
        (["/bin/bash", "-lc", "timeout 900s uv run --frozen python a.py"], 127, "python: command not found"),
        (["python", "b.py"], 127, "python: command not found"),
        (["git", "push", "--dry-run", "--porcelain"], 128, "fatal: push destination differs"),
    ]
    records = _bundle_failed_codex_commands(tmp_path, capsys, cases)
    candidates = [item for item in records if item["kind"] == "candidate"]

    assert sorted(item["count"] for item in candidates) == [1, 2]
    repeated = next(item for item in candidates if item["count"] == 2)
    assert repeated["candidate_kind"] == "command-failure"
    assert "python a.py" in repeated["failure_summary"]
    assert "127" in repeated["failure_signature"]
    assert len({item["failure_signature"] for item in candidates}) == 2


def test_bundle_excludes_negative_results_and_checks_without_hiding_argument_error(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """正常な否定結果と自動チェックによる検出を除外し、終了コード2の入力誤りは候補へ残す。"""
    cases = [
        (["/bin/bash", "-lc", "git -c color.ui=false grep -n -F needle -- docs"], 1, ""),
        (["git", "config", "--get", "unset.key"], 1, ""),
        (["/bin/bash", "-lc", "git grep needle && test -e /missing"], 1, ""),
        (["pyfltr", "run"], 1, "test failed"),
        (["python", "scripts/sync.py", "--check"], 1, "generated files differ"),
        (["atk", "review-table", "validate"], 2, "usage: invalid arguments"),
    ]
    records = _bundle_failed_codex_commands(tmp_path, capsys, cases)

    assert [item["locators"] for item in records if item["kind"] == "candidate"] == [
        [{"record": "codex:transcript", "line": 6}]
    ]
    assert records[-1]["excluded"]["normal-negative-result"] == 3
    assert records[-1]["excluded"]["check-detected"] == 2


def test_bundle_treats_quoted_operator_shaped_search_terms_as_data(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """引用・エスケープした演算子形の検索語の一致0件を除外し、実際の演算子・診断・終了2は候補へ残す。"""
    cases = [
        (["/bin/bash", "-lc", "rg -n -F '<<<<<<<' docs"], 1, ""),
        (["/bin/bash", "-lc", "rg -n -F ';' docs"], 1, ""),
        (["/bin/bash", "-lc", "rg -n -F '&&' docs && git grep -n -F '<<<<<<<' -- docs"], 1, ""),
        (["/bin/bash", "-lc", r"rg -n -F \| docs"], 1, ""),
        (["/bin/bash", "-lc", "git ls-files | rg -F '&&'"], 1, ""),
        (["/bin/bash", "-lc", "git ls-files -z | xargs -0 rg -n -F ';'"], 123, ""),
        (["/bin/bash", "-lc", "rg -n -F '<<<<<<<' docs && echo found"], 1, ""),
        (["/bin/bash", "-lc", "rg -n -F '<<<<<<<' docs > found.txt"], 1, ""),
        (["/bin/bash", "-lc", "rg -n -F '<<<<<<<' docs"], 2, ""),
        (["/bin/bash", "-lc", "rg -n -F ';' docs"], 1, "rg: docs: No such file or directory"),
    ]
    records = _bundle_failed_codex_commands(tmp_path, capsys, cases)
    candidates = [item for item in records if item["kind"] == "candidate"]
    assert {locator["line"] for item in candidates for locator in item["locators"]} == {7, 8, 9, 10}
    assert records[-1]["excluded"]["normal-negative-result"] == 6


def test_bundle_excludes_wait_continuations_but_keeps_real_failures(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """終了3の待機継続を出力の表記によらず除外し、別の契約の失敗は残す。"""
    cases = [
        (["atk", "agents", "wait"], 3, '{"status": "running"}'),
        (["/repo/agent-toolkit/bin/atk", "agents", "wait"], 3, "待機継続"),
        (["/bin/bash", "-lc", "/repo/agent-toolkit/bin/atk agents wait"], 3, "別の表示"),
        (["sh", "-c", "atk agents wait"], 3, ""),
        (["atk", "agents", "wait"], 2, "引数が不正"),
        (["atk", "agents", "show"], 3, "失敗"),
        (["other", "agents", "wait"], 3, "失敗"),
        (["bash", "-lc", "atk agents wait; false"], 3, "連結全体の失敗"),
        (["bash", "-lc", "echo 'atk agents wait'"], 3, "引用だけ"),
        (["rg", "missing", "docs"], 1, ""),
    ]
    records = _bundle_failed_codex_commands(tmp_path, capsys, cases)
    candidates = [item for item in records if item["kind"] == "candidate"]
    assert {locator["line"] for item in candidates for locator in item["locators"]} == {5, 6, 7, 8, 9}
    assert records[-1]["excluded"]["normal-nonterminal-result"] == 4
    assert records[-1]["excluded"]["normal-negative-result"] == 1


def test_candidates_exclude_explicit_help_failure_but_keep_usage_errors() -> None:
    """明示helpのusageだけを除外し、通常操作とhelp以外の診断は候補へ残す。"""
    timeline = [
        {
            "kind": "failed-tool",
            "tool": "CommandExecution",
            "record": "main",
            "line": 1,
            "text": "usage: git commit",
            "diagnostic": "usage: git commit",
            "command": json.dumps(["git", "commit", "-h"]),
        },
        {
            "kind": "failed-tool",
            "tool": "CommandExecution",
            "record": "main",
            "line": 2,
            "text": "usage: git commit",
            "diagnostic": "usage: git commit",
            "command": json.dumps(["git", "commit"]),
        },
        {
            "kind": "failed-tool",
            "tool": "CommandExecution",
            "record": "main",
            "line": 3,
            "text": "fatal: repository unavailable",
            "diagnostic": "fatal: repository unavailable",
            "command": json.dumps(["git", "commit", "--help"]),
        },
    ]

    records = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    candidates = [record for record in records if record["kind"] == "candidate"]
    assert {locator["line"] for candidate in candidates for locator in candidate["locators"]} == {2, 3}
    assert records[-1]["excluded"] == {"command-help": 1}


def test_candidates_exclude_normal_negative_results_but_keep_diagnostics() -> None:
    """述語の偽を件数へ分け、受理形式や対象の異常は候補へ残す。"""
    commands = [
        (["git", "grep", "missing"], ""),
        (["rg", "missing", "."], ""),
        (["bash", "-lc", "test -e /missing"], ""),
        (["git", "merge-base", "--is-ancestor", "A", "B"], ""),
        (["git", "grep", "missing"], "fatal: repository unavailable"),
        (["cp", "source", "target"], ""),
    ]
    timeline = [
        {
            "kind": "failed-tool",
            "tool": "CommandExecution",
            "record": "main",
            "line": index,
            "text": diagnostic or "CommandExecution failed",
            "diagnostic": diagnostic,
            "exit_code": 1,
            "command": json.dumps(command),
        }
        for index, (command, diagnostic) in enumerate(commands, start=1)
    ]

    records = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    candidates = [record for record in records if record["kind"] == "candidate"]
    assert {locator["line"] for candidate in candidates for locator in candidate["locators"]} == {5, 6}
    assert records[-1]["excluded"] == {"normal-negative-result": 4}


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

    event = evidence_extract.load_and_extract(str(transcript))[0]

    assert event["command"].endswith("…[省略]")
    assert len(event["command"]) == 2000 + len("…[省略]")


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

    events = evidence_extract.load_and_extract(str(transcript))

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

    events = evidence_extract.load_and_extract(str(transcript))

    assert [event["text"] for event in events] == ["最初の入力", "次の入力"]


@pytest.mark.parametrize("field", ["isMeta", "turnCompanion"])
def test_claude_runtime_generated_user_entry_is_marked(field: str, tmp_path: pathlib.Path) -> None:
    """実行環境が生成したエントリのユーザーイベントへ標識を付ける。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "依頼"}},
            {"type": "user", field: True, "message": {"role": "user", "content": "[Image: original 2938x1682]"}},
        ],
    )

    events = evidence_extract.load_and_extract(str(transcript))

    assert [event.get("runtime_generated") for event in events] == [None, True]


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

    events = evidence_extract.load_and_extract(str(transcript))

    assert [event["text"] for event in events] == ["依頼", "人間の割り込み"]


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

    assert not evidence_extract.load_and_extract(str(transcript))


def test_unsupported_nonempty_jsonl_returns_fallback(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(tmp_path, [{"type": "unknown", "payload": {"type": "unknown"}}])

    events = evidence_extract.load_and_extract(str(transcript))

    assert events == [
        {
            "sequence": 1,
            "kind": "fallback",
            "text": (
                "記録は読み込めたが形式を判定できないため抽出証拠を生成できない。"
                "継承した会話履歴を評価し、取得できない範囲を未検証と明記すること。"
            ),
        }
    ]


def test_default_output_line_points_at_source_transcript_line(tmp_path: pathlib.Path) -> None:
    """オプションを指定しない場合の出力の各イベントへ、由来したtranscript行の1始まり行番号を付ける。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "依頼"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "作業中"}]}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "最終結果"}]}},
        ],
    )

    events = evidence_extract.load_and_extract(str(transcript))

    assert [event["line"] for event in events] == [1, 2, 3]
    raw_lines = transcript.read_text(encoding="utf-8").splitlines()
    for event in events:
        assert event["text"] in raw_lines[event["line"] - 1]


def test_final_result_skips_commentary_phase(tmp_path: pathlib.Path) -> None:
    """Codexの中間報告をassistantのまま保ち、最終回答だけを最終結果へ分類する。"""
    commentary_only = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "作業中"}],
                },
            }
        ],
    )

    assert evidence_extract.load_and_extract(str(commentary_only)) == [
        {"kind": "assistant", "text": "作業中", "phase": "commentary", "line": 1, "timestamp": None, "sequence": 1}
    ]

    with_final_answer = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [{"type": "output_text", "text": "完了"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "後続の中間報告"}],
                },
            },
        ],
    )

    assert [event["kind"] for event in evidence_extract.load_and_extract(str(with_final_answer))] == [
        "final-result",
        "assistant",
    ]


def _assert_boundary_question_events(
    transcript: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """開始前の質問に対する区間内回答を回答時刻順で確認する。"""
    assert (
        evidence.main(
            [
                str(transcript),
                "--user-events",
                "--since",
                "2026-09-01T12:00:01Z",
                "--observation-boundary",
                "2026-09-01T12:00:02Z",
            ]
        )
        == 0
    )
    events = read_jsonl(capsys, raw=True)
    assert [(event["line"], event["text"]) for event in events[:-1]] == [
        (4, "回答2"),
        (5, "回答3"),
        (3, "回答1"),
    ]
    assert events[-1] == {"kind": "summary", "count": 3}


def test_user_events_keeps_claude_question_state_across_start_boundary(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """開始境界以前のClaude質問を保持し、回答が成立した時刻で区間を判定する。"""
    entries: list[dict] = []

    def add_question(timestamp: str, call_id: str) -> None:
        entries.append(
            {
                "type": "assistant",
                "timestamp": timestamp,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "AskUserQuestion", "id": call_id}],
                },
            }
        )

    def add_answer(timestamp: str, call_id: str, question: str, answer: str) -> None:
        entries.append(
            {
                "type": "user",
                "timestamp": timestamp,
                "toolUseResult": {"answers": {question: answer}, "questions": []},
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": call_id, "content": "通常出力"}],
                },
            }
        )

    add_question("2026-09-01T12:00:00.100Z", "old")
    add_answer("2026-09-01T12:00:00.800Z", "old", "区間前", "対象外")
    add_question("2026-09-01T12:00:00.900Z", "cross-one")
    add_question("2026-09-01T12:00:00.950Z", "cross-two")
    add_question("2026-09-01T12:00:01.200Z", "within")
    add_answer("2026-09-01T12:00:01.300Z", "cross-two", "境界越え2", "回答2")
    add_answer("2026-09-01T12:00:01.400Z", "within", "区間内", "回答3")
    add_answer("2026-09-01T12:00:01.500Z", "cross-one", "境界越え1", "回答1")
    add_question("2026-09-01T12:00:01.600Z", "future")
    add_answer("2026-09-01T12:00:02.100Z", "future", "終了後", "対象外")
    transcript = _write_transcript(tmp_path, entries)
    _assert_boundary_question_events(transcript, capsys)


def test_user_events_keeps_codex_question_state_across_start_boundary(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """開始境界以前のCodex質問を保持し、回答が成立した時刻で区間を判定する。"""
    entries: list[dict] = []

    def add_question(timestamp: str, call_id: str, question_id: str, question: str) -> None:
        entries.append(
            {
                "type": "response_item",
                "timestamp": timestamp,
                "payload": {
                    "type": "function_call",
                    "name": "request_user_input",
                    "call_id": call_id,
                    "arguments": json.dumps({"questions": [{"id": question_id, "question": question}]}),
                },
            }
        )

    def add_answer(timestamp: str, call_id: str, question_id: str, answer: str) -> None:
        entries.append(
            {
                "type": "response_item",
                "timestamp": timestamp,
                "payload": {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps({"answers": {question_id: {"answers": [answer]}}}),
                },
            }
        )

    add_question("2026-09-01T12:00:00.100Z", "old", "old-question", "区間前")
    add_answer("2026-09-01T12:00:00.800Z", "old", "old-question", "対象外")
    add_question("2026-09-01T12:00:00.900Z", "cross-one", "shared", "境界越え1")
    add_question("2026-09-01T12:00:00.950Z", "cross-two", "shared", "境界越え2")
    add_question("2026-09-01T12:00:01.200Z", "within", "within-question", "区間内")
    add_answer("2026-09-01T12:00:01.300Z", "cross-two", "shared", "回答2")
    add_answer("2026-09-01T12:00:01.400Z", "within", "within-question", "回答3")
    add_answer("2026-09-01T12:00:01.500Z", "cross-one", "shared", "回答1")
    add_question("2026-09-01T12:00:01.600Z", "future", "future-question", "終了後")
    add_answer("2026-09-01T12:00:02.100Z", "future", "future-question", "対象外")
    transcript = _write_transcript(tmp_path, entries)
    _assert_boundary_question_events(transcript, capsys)


def _user_events(argv: list[str], capsys: pytest.CaptureFixture[str]) -> list[dict]:
    """`--user-events`を実行し、終了コード0を確かめて全イベントを返す。"""
    assert evidence.main([*argv, "--user-events", "--since", "2026-09-01T00:00:00Z"]) == 0
    return read_jsonl(capsys, raw=True)


def test_user_events_keeps_long_text(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """2000字を超えるユーザー発話も切り詰めずに返す。逐語引用と文字列比較する原文として使うためである。"""
    long_text = "長" * 2500 + "末尾"
    transcript = _write_transcript(tmp_path, [timestamped_entry("2026-09-01T00:00:01Z", long_text)])

    events = _user_events([str(transcript)], capsys)

    assert [event["text"] for event in events if event["kind"] == "user"] == [long_text]


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_user_events_excludes_generated_inputs_without_losing_human_text(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], host: str
) -> None:
    """原文と構造標識で生成本文を除き、人間の長文・手動起動・是正の位置を保持する。"""
    texts = [
        "<skill>" + "本文" * 1600 + "</skill>",
        "実行環境の挿入",
        "会話に添えた生成本文",
        "最初の依頼",
        "/agent-toolkit:plan-mode" if host == "claude" else "$agent-toolkit:plan-mode",
        "人間の長文" * 600,
        '<atk-auto source="runtime" kind="rules">規範</atk-auto>',
        "# AGENTS.md instructions\n環境と規範",
        "後続の訂正",
    ]
    entries: list[dict] = []
    for index, text in enumerate(texts):
        timestamp = f"2026-09-01T00:00:{index + 1:02d}Z"
        entry = (
            timestamped_entry(timestamp, text)
            if host == "claude"
            else {
                "type": "response_item",
                "timestamp": timestamp,
                "payload": {"type": "message", "role": "user", "content": text},
            }
        )
        if index == 1:
            entry["isMeta"] = True
        elif index == 2:
            entry["turnCompanion"] = True
        entries.append(entry)
    transcript = _write_transcript(tmp_path, entries)
    events = _user_events([str(transcript)], capsys)
    assert [(event["line"], event["text"]) for event in events if event["kind"] == "user"] == [
        (4, texts[3]),
        (5, texts[4]),
        (6, texts[5]),
        (9, texts[8]),
    ]
    assert events[-1] == {"kind": "summary", "count": 4}


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_long_injection_is_classified_before_display_shortening(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], host: str
) -> None:
    """2000文字より後の閉タグが表示から消えても、初期要求と介入候補を変えない。"""
    texts = ["<skill>" + "規範" * 1600 + "</skill>", "最初の依頼", "後続の訂正"]
    entries = [
        timestamped_entry("2026-09-01T00:00:01Z", text)
        if host == "claude"
        else {
            "type": "response_item",
            "timestamp": "2026-09-01T00:00:01Z",
            "payload": {"type": "message", "role": "user", "content": text},
        }
        for text in texts
    ]
    transcript = _write_transcript(tmp_path, entries)
    displayed = evidence_extract.load_and_extract(str(transcript))
    first = next(event for event in displayed if event["kind"] == "user")
    assert "</skill>" not in first["text"]
    assert first["runtime_inserted"] is True
    records, _ = _bundle_candidates_and_evidence(tmp_path, capsys, entries)
    assert [locator["line"] for item in records if item["kind"] == "candidate" for locator in item["locators"]] == [3]
    assert records[-1]["excluded"] == {"initial-request": 1, "runtime-inserted": 1}
    conversation = [
        json.loads(line) for line in (tmp_path / "bundle/conversation.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [event["text"] for event in conversation if event.get("role") == "user"] == texts[1:]


def test_user_events_includes_offered_options_claude(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """AskUserQuestionの回答イベントは、提示した全選択肢のlabelとdescriptionを質問文の直後に持つ。

    確認回答に依存する対象・除外・認可は、選ばれなかった選択肢との対比で決まるため、回答だけでは判定できない。
    """
    question = "反映先をどうしますか"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "timestamp": "2026-09-01T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "AskUserQuestion",
                            "id": "ask-1",
                            "input": {
                                "questions": [
                                    {
                                        "question": question,
                                        "options": [
                                            {"label": "既存節へ追記", "description": "既存の節の末尾へ加える"},
                                            {"label": "新しい節", "description": "独立した節を設ける"},
                                            {"label": "説明なし"},
                                        ],
                                    }
                                ]
                            },
                        }
                    ],
                },
            },
            {
                "type": "user",
                "timestamp": "2026-09-01T00:00:02Z",
                "toolUseResult": {"answers": {question: "新しい節"}, "questions": []},
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "ask-1", "content": "回答"}]},
            },
        ],
    )

    events = _user_events([str(transcript)], capsys)

    user_event = next(event for event in events if event["kind"] == "user")
    assert user_event["text"] == "新しい節"
    assert user_event["assistant_context"] == [
        {
            "question": question,
            "options": [
                {"label": "既存節へ追記", "description": "既存の節の末尾へ加える"},
                {"label": "新しい節", "description": "独立した節を設ける"},
                {"label": "説明なし", "description": ""},
            ],
        }
    ]


def test_user_events_includes_offered_options_codex(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Codexのrequest_user_inputの回答イベントも、提示した全選択肢のlabelとdescriptionを質問文の直後に持つ。"""
    arguments = {
        "questions": [
            {
                "id": "scope",
                "question": "対象範囲",
                "options": [
                    {"label": "全体", "description": "リポジトリ全体を対象にする"},
                    {"label": "一部", "description": "指定したディレクトリだけにする"},
                ],
            }
        ]
    }
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "timestamp": "2026-09-01T00:00:01Z",
                "payload": {
                    "type": "function_call",
                    "name": "request_user_input",
                    "call_id": "call-1",
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-01T00:00:02Z",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": json.dumps({"answers": {"scope": {"answers": ["全体"]}}}, ensure_ascii=False),
                },
            },
        ],
    )

    events = _user_events([str(transcript)], capsys)

    user_event = next(event for event in events if event["kind"] == "user")
    assert user_event["text"] == "全体"
    assert user_event["user_response"] == [{"answers": ["全体"]}]


def test_extractor_runtimes_match_supported_engines() -> None:
    """本スクリプトが時系列へ変換できる実行系は、agents_serverが起動できる実行系と一致する。

    実行系を追加して変換を追随させないと、その実行系の委譲先の記録は候補と集計へ現れない。
    """
    assert set(evidence_extract.RUNTIME_EXTRACTORS) == backends.SUPPORTED_ENGINES


def test_candidates_bound_hook_notice_variants_and_keep_each_emitter() -> None:
    """block/warn通知は発生源ごとに上位5変種の代表位置へ限定し、発生総数を保持する。"""
    notices: list[dict] = []
    line = 1
    for variant, count in enumerate(range(7, 0, -1)):
        for _ in range(count):
            notices.append(
                {
                    "kind": "hook-notice",
                    "record": "main",
                    "line": line,
                    "text": f"warn: variant-{variant}",
                    "hook": "agent-toolkit/pretooluse",
                    "hook_name": "PreToolUse:Bash" if variant % 2 == 0 else "PostToolUse:Bash",
                    "tag": "warn",
                }
            )
            line += 1
    notices.append(
        {
            "kind": "hook-notice",
            "record": "main",
            "line": line,
            "text": "block: another-emitter",
            "hook": "dotfiles/pretooluse",
            "hook_name": "PreToolUse:Write",
            "tag": "block",
        }
    )

    events = evidence_candidates._candidate_events([], [], notices)  # pylint: disable=protected-access
    candidates = [event for event in events if event["kind"] == "candidate"]
    first_emitter = [candidate for candidate in candidates if candidate["event_key"][0] == "agent-toolkit/pretooluse"]

    assert [candidate["occurrence_count"] for candidate in first_emitter] == [7, 6, 5, 4, 3]
    assert all(candidate["count"] == 1 and len(candidate["locators"]) == 1 for candidate in candidates)
    assert any(candidate["event_key"][0] == "dotfiles/pretooluse" for candidate in candidates)
    assert events[-1]["included_locator_count"] == 6
    assert events[-1]["excluded"] == {"hook-notice-detail-budget": 23}


def _bundle_delegate_return_texts(tmp_path: pathlib.Path, delegate_entries: list[dict[str, object]]) -> list[str]:
    """委譲先記録を1件持つtranscriptから`--bundle`で候補を生成し、`delegate-return`候補と`escalation`候補の本文を返す。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "最初の依頼"}}])
    subagents = transcript.with_suffix("") / "subagents"
    subagents.mkdir(parents=True)
    (subagents / "agent-delegate.jsonl").write_text(
        "\n".join(json.dumps({**entry, "isSidechain": True}, ensure_ascii=False) for entry in delegate_entries) + "\n",
        encoding="utf-8",
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0

    records = [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    return [
        record["text"]
        for record in records
        if record["kind"] == "candidate" and record["candidate_kind"] in {"delegate-return", "escalation"}
    ]


def test_transcript_alias_selects_the_single_record_source(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--transcript`を位置引数と同じ単一記録を指定する手段として扱う。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "入力"}}])

    assert evidence.main(["--transcript", str(transcript)]) == 0
    assert read_jsonl(capsys)[0]["text"] == "入力"
    assert evidence.main([str(transcript), "--transcript", str(transcript)]) == 2


def test_candidates_exclude_runtime_inputs_before_selecting_initial_request() -> None:
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": "環境情報", "runtime_generated": True},
        {"kind": "user", "record": "main", "line": 2, "text": "<skill>\n本文\n</skill>", "runtime_inserted": True},
        {"kind": "user", "record": "main", "line": 3, "text": "最初の依頼"},
        {"kind": "user", "record": "main", "line": 4, "text": "後続の訂正"},
        {"kind": "user", "record": "main", "line": 5, "text": "通常文中の <skill> という表記"},
    ]

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    items = [item for item in candidates if item["kind"] == "candidate"]
    assert sorted(item["locators"][0]["line"] for item in items) == [4, 5]
    assert candidates[-1]["excluded"] == {"initial-request": 1, "runtime-inserted": 1, "runtime-meta": 1}


def test_candidates_exclude_boundary_marked_injections() -> None:
    """属性を伴う境界の要素で囲んだ自動注入本文を、実行環境の挿入として除外する。"""
    normative = '<normative-context source="agent-toolkit" kind="rules-main">\n条文\n</normative-context>'
    hook_notice = (
        '<agent-toolkit-hook-message source="agent-toolkit/rules_context" kind="notice">\n注記\n</agent-toolkit-hook-message>'
    )
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": normative, "runtime_inserted": True},
        {"kind": "user", "record": "main", "line": 2, "text": hook_notice, "runtime_inserted": True},
        {"kind": "user", "record": "main", "line": 3, "text": "最初の依頼"},
        {"kind": "user", "record": "main", "line": 4, "text": "後続の訂正"},
    ]

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    items = [item for item in candidates if item["kind"] == "candidate"]
    assert sorted(item["locators"][0]["line"] for item in items) == [4]
    assert candidates[-1]["excluded"]["runtime-inserted"] == 2
    assert candidates[-1]["excluded"]["initial-request"] == 1


def test_candidates_exclude_initial_codex_skill_pair_without_hiding_later_intervention() -> None:
    """先頭スキル要求と対応本文を別区分で除外し、後続のユーザー介入を保持する。"""
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": "環境情報", "runtime_generated": True},
        {"kind": "user", "record": "main", "line": 2, "text": "$agent-toolkit:process-wi"},
        {
            "kind": "user",
            "record": "main",
            "line": 3,
            "text": "<skill>\n<name>agent-toolkit:process-wi</name>\n本文は後段で省略",
        },
        {"kind": "user", "record": "main", "line": 4, "text": "途中で方針を変更する"},
    ]

    records = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    candidates = [record for record in records if record["kind"] == "candidate"]
    assert [candidate["locators"] for candidate in candidates] == [[{"record": "main", "line": 4}]]
    assert records[-1]["excluded"] == {
        "initial-skill-body": 1,
        "initial-skill-request": 1,
        "runtime-meta": 1,
    }


def _bundle_candidates_and_evidence(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], entries: list[dict]
) -> tuple[list[dict], dict[str, dict]]:
    """transcriptから`--bundle`を実行し、候補の行と候補ID別の個別証拠を返す。"""
    transcript = _write_transcript(tmp_path, entries)
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    capsys.readouterr()
    candidates = [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    items = {
        item["candidate_id"]: json.loads(
            (bundle_dir / "candidate-evidence" / f"{item['candidate_id']}.json").read_text("utf-8")
        )
        for item in candidates
        if item["kind"] == "candidate"
    }
    return candidates, items


@pytest.mark.parametrize(
    "injection", ["# AGENTS.md instructions\n規範", '<atk-auto source="runtime" kind="notice">注入</atk-auto>']
)
def test_bundle_selects_initial_request_after_runtime_injections(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], injection: str
) -> None:
    """Codexの先行注入と最初の依頼を候補から除き、後続是正と通常文の標識を保持する。"""
    texts = [injection, "最初の依頼", "後続の訂正", "通常文中の <skill> という表記"]
    records, _ = _bundle_candidates_and_evidence(
        tmp_path,
        capsys,
        [{"type": "response_item", "payload": {"type": "message", "role": "user", "content": text}} for text in texts],
    )

    assert sorted(locator["line"] for item in records if item["kind"] == "candidate" for locator in item["locators"]) == [3, 4]
    assert records[-1]["excluded"] == {"initial-request": 1, "runtime-inserted": 1}


@pytest.mark.parametrize("closing", ["", "\n</skill>"])
def test_bundle_selects_initial_skill_after_runtime_injections(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], closing: str
) -> None:
    """注入後の初期スキルと対応本文は元の区分で除き、後続の是正を保持する。"""
    texts = [
        "# AGENTS.md instructions\n規範",
        '<atk-auto source="runtime" kind="notice">注入</atk-auto>',
        "$agent-toolkit:process-wi",
        f"<skill>\n<name>agent-toolkit:process-wi</name>\n本文{closing}",
        "後続の訂正",
    ]
    records, _ = _bundle_candidates_and_evidence(
        tmp_path,
        capsys,
        [{"type": "response_item", "payload": {"type": "message", "role": "user", "content": text}} for text in texts],
    )

    assert [item["locators"] for item in records if item["kind"] == "candidate"] == [
        [{"record": "codex:transcript", "line": 5}]
    ]
    assert records[-1]["excluded"] == {"initial-skill-body": 1, "initial-skill-request": 1, "runtime-inserted": 2}


def test_hook_notice_evidence_includes_tool_use_input(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """PreToolUseのwarn候補の個別証拠が、同じ呼び出し識別子のコマンドと記録位置を持つ。

    hook実行記録だけでは警告の対象を特定できず、分析主体が元記録を検索し直すことになる。
    """
    candidates, items = _bundle_candidates_and_evidence(
        tmp_path,
        capsys,
        [
            {"type": "user", "message": {"role": "user", "content": "初期要求"}},
            {
                "type": "assistant",
                "timestamp": "2026-09-25T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "id": "toolu_target",
                            "input": {"command": "find . -name '*.md'", "description": "Markdownを探す"},
                        }
                    ],
                },
            },
            hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "toolu_target",
                    "content": ["[auto-generated: agent-toolkit/pretooluse][warn] warn: 検索対象を限定する"],
                }
            ),
        ],
    )

    hook_candidates = [item for item in candidates if item.get("candidate_kind") == "hook-notice"]
    assert len(hook_candidates) == 1
    tool_uses = [event for event in items[hook_candidates[0]["candidate_id"]]["events"] if event["kind"] == "tool-use"]
    assert tool_uses == [
        {
            "record": "claude:transcript",
            "kind": "tool-use",
            "line": 2,
            "timestamp": "2026-09-25T00:00:01Z",
            "name": "Bash",
            "input": {"command": "find . -name '*.md'", "description": "Markdownを探す"},
        }
    ]


def test_context_hook_output_is_excluded(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """blockまたはwarn以外の区分を持つhook出力は候補へ残らず、区分の種類によらず除外件数へ計上される。"""
    candidates, _ = _bundle_candidates_and_evidence(
        tmp_path,
        capsys,
        [
            {"type": "user", "message": {"role": "user", "content": "初期要求"}},
            hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "SessionStart",
                    "content": [
                        '<agent-toolkit-auto-inserted source="agent-toolkit" kind="rules-main">\n# 規範\n'
                        "</agent-toolkit-auto-inserted>"
                    ],
                }
            ),
            hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "SubagentStart",
                    "content": [
                        '<agent-toolkit-auto-inserted source="agent-toolkit" kind="auto-resume">\n再開\n'
                        "</agent-toolkit-auto-inserted>"
                    ],
                }
            ),
            hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "content": [
                        '<agent-toolkit-auto-inserted source="agent-toolkit/pretooluse" kind="block">\nblock: 停止\n'
                        "</agent-toolkit-auto-inserted>"
                    ],
                }
            ),
        ],
    )

    hook_candidates = [item for item in candidates if item.get("candidate_kind") == "hook-notice"]
    assert [item["locators"] for item in hook_candidates] == [[{"record": "claude:transcript", "line": 4}]]
    assert candidates[-1]["excluded"]["hook-notice-context"] == 2


def test_bundle_excludes_atk_no_match_without_hiding_real_failures(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """結果行の契約を持つ終了1を除き、警告・失敗・連結・別コード・切り詰めは候補へ残す。"""
    no_match = outcome.NO_MATCH_PREFIX + "検索は正常に完了した"
    cases = [
        (["atk", "wi", "grep", "needle"], 1, no_match),
        (["atk", "managed-temp", "list"], 1, no_match),
        (
            ["/repo/agent-toolkit/bin/atk", "wi", "grep", "needle", "--output-file=/file"],
            1,
            no_match + "\n保存先: /file\n行数: 0",
        ),
        (["timeout", "60", "env", "KEY=VALUE", "uv", "run", "--frozen", "bash", "-lc", "atk wi grep 'a|b'"], 1, no_match),
        (["atk", "wi", "grep", "|"], 1, no_match),
        (["atk", "wi", "grep", "needle"], 1, no_match + "\n" + outcome.WARNING_PREFIX + "要確認"),
        (["atk", "wi", "grep", "needle"], 1, no_match + "\n" + outcome.FAILURE_PREFIX + "入力が不正"),
        (["atk", "wi", "grep", "needle"], 2, no_match),
        (["other", "wi", "grep", "needle"], 1, no_match),
        (["bash", "-lc", "atk wi grep needle; false"], 1, no_match),
        (["bash", "-lc", "bash -lc 'atk wi grep needle' && false"], 1, no_match),
        (["bash", "-lc", "atk wi grep needle\nfalse"], 1, no_match),
        (["atk", "wi", "grep", "needle"], 1, no_match + "\n" + "出力" * 1500 + "\n" + outcome.WARNING_PREFIX + "末尾警告"),
    ]
    records = _bundle_failed_codex_commands(tmp_path, capsys, cases)
    lines = {locator["line"] for item in records if item["kind"] == "candidate" for locator in item["locators"]}
    assert lines == set(range(6, 14))
    assert records[-1]["excluded"]["normal-negative-result"] == 5


def test_bundle_excludes_atk_no_match_of_claude_bash(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """引用した検索語と包装を保ち、Bashの終了1と結果行を公開bundleで同じ区分へ数える。"""
    no_match = outcome.NO_MATCH_PREFIX + "検索は正常に完了した"
    commands = [
        "atk wi grep 'a|b'",
        "atk managed-temp list",
        "timeout 60 env KEY=VALUE uv run --frozen atk wi grep ';' --output-file=/file",
        "bash -lc \"atk wi grep '&&'\"",
        "atk wi grep needle && false",
        "atk wi grep needle | cat",
        "bash -lc 'atk wi grep needle' && false",
        "atk wi grep needle\nfalse",
    ]
    records, lines = _bundle_failed_commands_of_runtime(
        tmp_path, capsys, "claude", [(command, 1, no_match + "\n保存先: /file\n行数: 0") for command in commands]
    )
    candidate_lines = {locator["line"] for item in records if item["kind"] == "candidate" for locator in item["locators"]}
    assert candidate_lines == set(lines[4:])
    assert records[-1]["excluded"]["normal-negative-result"] == 4


def _bundle_failed_commands_of_runtime(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    runtime: str,
    cases: list[tuple[str, int, str]],
) -> tuple[list[dict], list[int]]:
    """シェルのコマンド文字列の失敗列を、Claude CodeのBashかCodexの`CommandExecution`の記録として公開bundleへ渡す。

    候補レコードと、各ケースの失敗を記録した行番号を返す。
    """
    if runtime == "codex":
        records = _bundle_failed_codex_commands(
            tmp_path, capsys, [(["bash", "-lc", command], code, output) for command, code, output in cases]
        )
        return records, list(range(1, len(cases) + 1))
    entries: list[dict] = []
    for index, (command, code, output) in enumerate(cases):
        call_id = f"call-{index}"
        entries += [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": call_id, "input": {"command": command}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": call_id,
                            "is_error": True,
                            "content": f"Exit code {code}" + (f"\n{output}" if output else ""),
                        }
                    ],
                },
            },
        ]
    transcript = _write_transcript(tmp_path, entries)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(transcript), "--bundle", str(bundle)]) == 0
    read_jsonl(capsys)
    records = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    return records, [2 * index + 2 for index in range(len(cases))]


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_bundle_excludes_cd_prefixed_commands_like_standalone(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], runtime: str
) -> None:
    """先頭の`cd <パス>;`と`cd <パス> &&`を付けた4区分の正常な結果を、前置の無いコマンドと同じ区分へ除外する。"""
    no_match = outcome.NO_MATCH_PREFIX + "検索は正常に完了した"
    cases = [
        (f"{prefix}{command}", code, output)
        for prefix in ("cd /repo; ", "cd /repo && ")
        for command, code, output in [
            ("atk wi grep needle", 1, no_match),
            ("atk agents wait", 3, '{"status": "running"}'),
            ("rg missing docs", 1, ""),
            ("make test", 1, "FAILED test_example.py::test_case"),
        ]
    ]
    records, _lines = _bundle_failed_commands_of_runtime(tmp_path, capsys, runtime, cases)

    assert not [item for item in records if item["kind"] == "candidate"]
    excluded = records[-1]["excluded"]
    assert excluded["normal-negative-result"] == 4
    assert excluded["normal-nonterminal-result"] == 2
    assert excluded["check-detected"] == 2


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_bundle_keeps_cd_failures_and_unsupported_prefixes(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], runtime: str
) -> None:
    """`cd`の失敗を示す結果と、外す対象にしない前置の形は候補に残す。"""
    no_match = outcome.NO_MATCH_PREFIX + "検索は正常に完了した"
    cd_failure = "bash: line 1: cd: /missing: No such file or directory"
    cases = [
        ("cd /missing; atk wi grep needle", 1, f"{cd_failure}\n{no_match}"),
        ("cd /missing && make test", 1, cd_failure),
        ("cd /missing; rg missing docs", 1, cd_failure),
        ("cd -P /repo && make test", 1, "FAILED test_example.py::test_case"),
        ("cd; atk wi grep needle", 1, no_match),
        ("cd /a; cd /b; atk wi grep needle", 1, no_match),
        ("cd /repo | atk wi grep needle", 1, no_match),
        ("cd /repo\natk wi grep needle", 1, no_match),
        ("cd /repo; atk wi grep needle; false", 1, no_match),
        ("cd /repo && atk agents wait; false", 3, '{"status": "running"}'),
        # 対照: 同じ記録の中で、外す対象の前置だけを持つ正常な結果は除外される。
        ("cd /repo; atk wi grep needle", 1, no_match),
    ]
    records, lines = _bundle_failed_commands_of_runtime(tmp_path, capsys, runtime, cases)

    candidate_lines = {locator["line"] for item in records if item["kind"] == "candidate" for locator in item["locators"]}
    assert candidate_lines == set(lines[:-1])
    excluded = records[-1]["excluded"]
    assert excluded["normal-negative-result"] == 1
    assert excluded.get("normal-nonterminal-result", 0) == 0
    assert excluded.get("check-detected", 0) == 0


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_bundle_powershell_classification_preserves_unsupported_failures(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], runtime: str
) -> None:
    """PSの引用・パス・cd前置を共通分類へ渡し、診断・式・連結・対応外モードは元位置へ残す。"""
    normal = [
        ["pwsh", "-NoProfile", "-Command", "rg 'a''b' 'C:\\work\\docs'"],
        [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-COMMAND", 'test -e "C:\\work\\file"'],
        ["powershell", "-Command", "git grep -F -- 'a|b'"],
        ["pwsh.exe", "-Command", "rg a`|b docs"],
        ["pwsh", "-Command", "cd 'C:\\work'; cmp -s one two"],
        ["pwsh", "-Command", "cd 'C:\\work' && test -f absent"],
    ]
    kept = [
        (["pwsh", "-Command", "rg missing docs"], 2, "不正な入力"),
        (["pwsh", "-Command", "rg missing docs"], 1, "アクセス拒否"),
        (["pwsh", "-Command", "rg missing docs | cat"], 1, ""),
        (["pwsh", "-Command", "rg missing docs; false"], 1, ""),
        (["pwsh", "-Command", "rg $pattern docs"], 1, ""),
        (["pwsh", "-Command", "rg $(Get-Item .) docs"], 1, ""),
        (["pwsh", "-Command", "rg 'unclosed docs"], 1, ""),
        (["pwsh", "-Command", "cd /a; cd /b; pytest"], 1, "FAILED test_case"),
        (["pwsh", "-Command", "cd /missing; pytest"], 1, "Set-Location: パスが存在しない"),
        (["pwsh", "-File", "check.ps1"], 1, ""),
        (["pwsh", "-EncodedCommand", "encoded"], 1, ""),
        (["pwsh", "-Command", "-"], 1, ""),
        (["pwsh", "-Command", "rg missing docs > result"], 1, ""),
    ]
    cases = [(shlex.join(args), 1, "") for args in normal]
    cases += [(shlex.join(args), code, output) for args, code, output in kept]
    cases += [(shlex.join(["pwsh", "-Command", "cd /repo; uv run --frozen pytest"]), 1, "FAILED test_case")]
    records, lines = _bundle_failed_commands_of_runtime(tmp_path, capsys, runtime, cases)
    candidates = [item for item in records if item["kind"] == "candidate"]
    assert {locator["line"] for item in candidates for locator in item["locators"]} == set(lines[len(normal) : -1])
    assert records[-1]["excluded"]["normal-negative-result"] == len(normal)
    assert records[-1]["excluded"]["check-detected"] == 1
    _assert_bundle_original_commands(tmp_path, candidates, runtime, cases, lines)
    assert any(json.loads(item["failure_signature"])[1:4] == ["rg", "missing", 2] for item in candidates)


@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_bundle_run_command_uses_complete_child_result_only(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], runtime: str
) -> None:
    """公開run-commandの保存JSONで子へ帰属させ、包装の異常と子の診断を候補へ残す。"""

    def case(child: list[str], **changes: object) -> tuple[str, int, str]:
        result = {
            "argv": child,
            "child_exit_code": 1,
            "timed_out": False,
            "signal": None,
            "record_path": "/records/record.json",
            "stdout_bytes": 0,
            "stderr_bytes": 0,
        }
        result.update(changes)
        output = json.dumps(result) + "\n失敗: 外部コマンドが終了コード1で終了した\n次の操作: 保存先を診断する"
        return shlex.join(["atk", "run-command", "--timeout", "30", "--", *child]), 1, output

    cases = [
        case(child)
        for child in [["rg", "missing", "docs"], ["git", "grep", "needle"], ["cmp", "-s", "a", "b"], ["test", "-e", "absent"]]
    ]
    cases += [case(["pytest"], stdout_bytes=120), case(["uv", "run", "--frozen", "pytest"], stderr_bytes=120)]
    kept = [
        case(["rg", "missing", "docs"], stderr_bytes=20),
        case(["test", "-e", "absent"], timed_out=True),
        case(["pytest"], signal=15),
        case(["pytest"], record_path=None),
        case(["pytest"], child_exit_code=2),
        case(["pytest"], stdout_bytes=-1),
        case(["pytest"], stdout_bytes=True),
    ]
    command, code, output = case(["test", "-e", "absent"])
    kept += [
        (command, code, "{invalid JSON}"),
        (command, code, '{"argv": ["test"], "child_exit_code": 1}'),
        (command + "; false", code, output),
    ]
    cases += kept
    records, lines = _bundle_failed_commands_of_runtime(tmp_path, capsys, runtime, cases)
    candidates = [item for item in records if item["kind"] == "candidate"]
    assert {locator["line"] for item in candidates for locator in item["locators"]} == set(lines[6:])
    assert records[-1]["excluded"]["normal-negative-result"] == 4
    assert records[-1]["excluded"]["check-detected"] == 2
    _assert_bundle_original_commands(tmp_path, candidates, runtime, cases, lines)


def _assert_bundle_original_commands(
    tmp_path: pathlib.Path, candidates: list[dict], runtime: str, cases: list[tuple[str, int, str]], lines: list[int]
) -> None:
    """個別証拠が元の包装コマンドと位置を保持することを公開bundleで確かめる。"""
    for candidate in candidates:
        saved = json.loads(
            (tmp_path / "bundle" / "candidate-evidence" / f"{candidate['candidate_id']}.json").read_text(encoding="utf-8")
        )
        for event in saved["events"]:
            if runtime == "codex":
                original = cases[lines.index(event["line"])][0]
                assert json.loads(event["text"])["payload"]["item"]["command"][-1] == original
            elif event["kind"] == "tool-use":
                original = cases[lines.index(event["line"] + 1)][0]
                assert event["input"]["command"] == original


def test_assistant_clipping_without_improvement_and_main_record_separation() -> None:
    """標識の無い本文は元の短縮を使い、メインの標識は保持しても委譲返却候補にはしない。"""
    body = "本文" * 1500
    event = evidence_extract._event("assistant", body)  # pylint: disable=protected-access
    assert event is not None
    assert event["text"] == body[:2000] + "…[省略]"
    note = "気付いた改善点: メインの改善点。"
    event = evidence_extract._event("assistant", body + "\n" + note)  # pylint: disable=protected-access
    assert event is not None and note in event["text"]
    event.update(kind="final-result", record="main", line=1)
    candidates = evidence_candidates._candidate_events([event], [], [])  # pylint: disable=protected-access
    assert not candidates[:-1]

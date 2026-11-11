"""セッション振り返りの試験（session_review_evidence_claude_answers_test）。"""

from __future__ import annotations

import pathlib

import pytest
import session_evidence_extract as evidence_extract

from agent_toolkit._testing.helpers import _write_transcript


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

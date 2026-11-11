"""セッション振り返りの試験（session_review_evidence_user_events_test）。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_evidence_extract as evidence_extract
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import read_jsonl, timestamped_entry
from agent_toolkit._testing.session_review_evidence_support import _bundle_candidates_and_evidence, _user_events


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
        "/agent-toolkit:write-plan" if host == "claude" else "$agent-toolkit:write-plan",
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

    確認回答で決まる対象・除外・認可は、選ばれなかった選択肢との対比で決まるため、回答だけでは判定できない。
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

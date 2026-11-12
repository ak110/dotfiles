"""セッション振り返りの試験（session_review_evidence_test）。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_evidence_candidates as evidence_candidates
import session_evidence_extract as evidence_extract
import session_review_evidence as evidence

from agent_toolkit._agents_server import backends
from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import read_jsonl


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
    assert evidence.main([str(transcript), "--detail", "main:1", "--record-schema", "main:1", "--stats"]) == 0
    assert {event["kind"] for event in read_jsonl(capsys)} >= {"detail", "record-schema", "stats-total"}


def test_output_file_option_is_removed(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="2"):
        evidence.main(["unused.jsonl", "--output-file", "relative.jsonl"])
    assert "--output-file" in capsys.readouterr().err


def test_send_to_user_message_is_assistant_event(tmp_path: pathlib.Path) -> None:
    """send_to_userの`message`はユーザーへ届いた本文として、assistantのイベントにする。"""
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
    assert len(lines) == 2
    assert json.loads(lines[1])["kind"] == "record-provenance"
    assert json.loads(lines[0]) == {
        "kind": "user",
        "runtime_inserted": False,
        "text": "入力",
        "line": 1,
        "timestamp": None,
        "sequence": 1,
        "record": "claude:transcript",
    }


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
                "自身のコンテキストにある会話履歴から評価し、記録から取得できない範囲を未検証と明記すること。"
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


# test-copy-contract: session-review-evidenceの解析登録を起動側の全engineと比べ、記録の未対応を検出する。
def test_extractor_runtimes_match_supported_engines() -> None:
    """本スクリプトが時系列へ変換できる実行系は、agents_serverが起動できる実行系と一致する。

    実行系を追加して変換を追随させないと、その実行系の委譲先の記録は候補と集計へ現れない。
    """
    assert set(evidence_extract.RUNTIME_EXTRACTORS) == backends.SUPPORTED_ENGINES


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

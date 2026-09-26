"""セッション振り返り用の証拠抽出を検証する。"""

from __future__ import annotations

import json
import pathlib
from typing import Literal

import pytest
import session_review_evidence as evidence  # noqa: E402  # pylint: disable=wrong-import-position,import-error

from agent_toolkit import agents_server_mcp  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from agent_toolkit._testing.helpers import _write_transcript  # noqa: E402  # pylint: disable=wrong-import-position,import-error


def _execution_tool_use(tool_id: str, name: str = "Bash") -> dict[str, object]:
    """実行ツールのClaude呼び出し記録を返す。"""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": name, "id": tool_id, "input": {}}],
        },
    }


def _execution_result_transcript(tmp_path: pathlib.Path, *contents: str) -> pathlib.Path:
    """実行ツールの呼び出しと結果を持つ記録を書く。"""
    return _write_transcript(
        tmp_path,
        [
            _execution_tool_use("call-1"),
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": content} for content in contents],
                },
            },
        ],
    )


def test_output_file_saves_events_and_prints_path_and_line_count(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "入力"}}])
    output_path = tmp_path / "events.jsonl"

    assert evidence.main([str(transcript), "--output-file", str(output_path)]) == 0

    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "kind": "user",
        "text": "入力",
        "line": 1,
        "timestamp": None,
        "sequence": 1,
        "record": "main",
    }
    assert capsys.readouterr().out == f"保存先: {output_path.resolve()}\n行数: 1\n"


def test_output_file_rejects_relative_path(capsys: pytest.CaptureFixture[str]) -> None:
    assert evidence.main(["unused.jsonl", "--output-file", "relative.jsonl"]) == 2
    assert json.loads(capsys.readouterr().out) == {
        "kind": "error",
        "text": "--output-fileには絶対パスを指定してください。",
    }


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

    failed = [event for event in evidence.load_and_extract(str(transcript)) if event["kind"] == "failed-tool"]

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

    events = evidence.load_and_extract(str(transcript))

    assert events == [
        {
            "kind": "user",
            "text": "質問: 最初の質問\n回答: 最初の回答\n質問: 次の質問\n回答: 次の回答",
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
    return evidence.load_and_extract(str(transcript))[0]


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


def _claude_answer_event(tmp_path: pathlib.Path, result: dict[str, object]) -> dict[str, str | int]:
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
    event = evidence.load_and_extract(str(transcript))[0]
    assert isinstance(event["text"], str)
    return event


def test_claude_answer_includes_annotation_notes(tmp_path: pathlib.Path) -> None:
    """annotationsの文字列notesを自由記述として証拠化する。"""
    event = _claude_answer_event(
        tmp_path,
        {"answers": {"質問": "回答"}, "annotations": {"質問": {"notes": "自由記述"}}},
    )

    assert event["text"] == "質問: 質問\n回答: 回答\n自由記述: 自由記述"


def test_claude_answer_with_notes_only_keeps_recorded_answer(tmp_path: pathlib.Path) -> None:
    """選択肢なしの自由記述でも記録済み回答を保持する。"""
    event = _claude_answer_event(
        tmp_path,
        {"answers": {"質問": "(notes only)"}, "annotations": {"質問": {"notes": "自由記述"}}},
    )

    assert event["text"] == "質問: 質問\n回答: (notes only)\n自由記述: 自由記述"


@pytest.mark.parametrize("annotations", [{"質問": {"preview": "選択肢"}}, None])
def test_claude_answer_without_notes_is_unchanged(tmp_path: pathlib.Path, annotations: object) -> None:
    """自由記述が無い回答の本文を変更しない。"""
    result: dict[str, object] = {"answers": {"質問": "回答"}}
    if annotations is not None:
        result["annotations"] = annotations

    event = _claude_answer_event(tmp_path, result)

    assert event["text"] == "質問: 質問\n回答: 回答"
    assert isinstance(event["text"], str)
    assert "自由記述" not in event["text"]


@pytest.mark.parametrize("annotations", [[], {"質問": "不正"}, {"質問": {"notes": ["不正"]}}])
def test_claude_answer_ignores_non_string_annotation_notes(tmp_path: pathlib.Path, annotations: object) -> None:
    """辞書以外又は文字列以外のnotesを自由記述として出力しない。"""
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

    assert not evidence.load_and_extract(str(transcript))


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

    assert not evidence.load_and_extract(str(transcript))


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
        {"kind": "user", "text": "質問: 質問\n回答: 回答", "line": 1, "timestamp": None, "sequence": 1},
        {"kind": "user", "text": "質問: 別の質問\n回答: 別の回答", "line": 1, "timestamp": None, "sequence": 2},
    ]


@pytest.mark.parametrize("transcript_path", ["/not-found/session.jsonl", "relative/session.jsonl"])
def test_main_rejects_unreadable_transcript_path(
    transcript_path: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """対象記録を解決できない呼び出しはerrorイベントと終了コード2を返す。"""
    assert evidence.main([transcript_path]) == 2

    assert _read_jsonl(capsys) == [{"kind": "error", "text": f"対象記録を読み込めない: {transcript_path}"}]


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
        "text": "入力",
        "line": 1,
        "timestamp": None,
        "sequence": 1,
        "record": "main",
    }


def test_main_resolves_codex_transcript_from_thread_id(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """thread IDだけで一意な親rolloutを解決して抽出する。"""
    thread_id = "11111111-1111-4111-8111-111111111111"
    codex_home = tmp_path / "codex"
    _write_jsonl(
        codex_home / "sessions" / "2026" / "09" / "01" / f"rollout-parent-{thread_id}.jsonl",
        [
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": "thread IDから解決した記録"},
            }
        ],
    )

    assert evidence.main(["--codex-thread-id", thread_id, "--codex-home", str(codex_home)]) == 0

    assert _read_jsonl(capsys) == [
        {"kind": "user", "text": "thread IDから解決した記録", "line": 1, "timestamp": None, "sequence": 1}
    ]


def test_main_rejects_ambiguous_or_missing_codex_thread_id(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """親rolloutが0件又は複数件なら証拠不足として終了コード2を返す。"""
    thread_id = "22222222-2222-4222-8222-222222222222"
    codex_home = tmp_path / "codex"

    assert evidence.main(["--codex-thread-id", thread_id, "--codex-home", str(codex_home)]) == 2
    missing = _read_jsonl(capsys)
    assert missing == [
        {
            "kind": "error",
            "text": f"対象記録を解決できない: Codex thread ID {thread_id}に一致するrolloutが"
            f"{codex_home / 'sessions'}配下に無い",
        }
    ]

    candidates = [codex_home / "sessions" / "2026" / "09" / day / f"rollout-parent-{thread_id}.jsonl" for day in ("01", "02")]
    for day, path in zip(("01", "02"), candidates, strict=True):
        _write_jsonl(
            path,
            [{"type": "response_item", "payload": {"type": "message", "role": "user", "content": day}}],
        )

    assert evidence.main(["--codex-thread-id", thread_id, "--codex-home", str(codex_home)]) == 2
    ambiguous = _read_jsonl(capsys)
    assert ambiguous == [
        {
            "kind": "error",
            "text": f"対象記録を解決できない: Codex thread ID {thread_id}に一致するrolloutが複数ある: "
            + ", ".join(str(path) for path in sorted(candidates)),
        }
    ]


def test_codex_home_resolution_order(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """明示引数、空でない環境変数、ホーム既定値の順に保存先を解決する。"""
    thread_id = "33333333-3333-4333-8333-333333333333"
    home = tmp_path / "home"
    roots = {
        "default": home / ".codex",
        "environment": tmp_path / "environment-codex",
        "explicit": tmp_path / "explicit-codex",
    }
    for label, root in roots.items():
        _write_jsonl(
            root / "sessions" / "2026" / "09" / "01" / f"rollout-parent-{thread_id}.jsonl",
            [
                {
                    "type": "response_item",
                    "payload": {"type": "message", "role": "user", "content": label},
                }
            ],
        )
    monkeypatch.setenv("HOME", str(home))

    monkeypatch.delenv("CODEX_HOME", raising=False)
    assert evidence.main(["--codex-thread-id", thread_id]) == 0
    assert _read_jsonl(capsys)[0]["text"] == "default"

    monkeypatch.setenv("CODEX_HOME", "")
    assert evidence.main(["--codex-thread-id", thread_id]) == 0
    assert _read_jsonl(capsys)[0]["text"] == "default"

    monkeypatch.setenv("CODEX_HOME", str(roots["environment"]))
    assert evidence.main(["--codex-thread-id", thread_id]) == 0
    assert _read_jsonl(capsys)[0]["text"] == "environment"

    assert evidence.main(["--codex-thread-id", thread_id, "--codex-home", str(roots["explicit"])]) == 0
    assert _read_jsonl(capsys)[0]["text"] == "explicit"


def test_main_requires_exactly_one_transcript_source(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """親記録のパスとthread IDは同時指定も同時省略も拒否する。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "入力"}}])
    thread_id = "44444444-4444-4444-8444-444444444444"

    assert evidence.main([str(transcript), "--codex-thread-id", thread_id]) == 2
    assert _read_jsonl(capsys) == [
        {
            "kind": "error",
            "text": "transcript_path・--transcript・--codex-thread-id・カタログ走査はいずれか一つだけを指定する",
        }
    ]

    assert evidence.main([]) == 2
    assert _read_jsonl(capsys) == [
        {
            "kind": "error",
            "text": "transcript_path・--transcript・--codex-thread-id・カタログ走査はいずれか一つだけを指定する",
        }
    ]


def _timestamped_entry(timestamp: str | None, text: str) -> dict:
    """任意の時刻を持つClaude利用者エントリを作成する。"""
    entry: dict = {"type": "user", "message": {"role": "user", "content": text}}
    if timestamp is not None:
        entry["timestamp"] = timestamp
    return entry


def test_observation_boundary_excludes_later_main_records_in_all_modes(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """観測境界より後の親記録を全照会モードから除外する。"""
    before_notice = "[auto-generated: test/before][warn] warning: before needle"
    after_notice = "[auto-generated: test/after][warn] warning: after needle"
    transcript = _write_transcript(
        tmp_path,
        [
            _timestamped_entry(None, "時刻なしの管理相当レコード"),
            _timestamped_entry("2026-09-01T00:00:00Z", "before needle"),
            _hook_attachment(
                {
                    "type": "hook_system_message",
                    "hookName": "Before",
                    "toolUseID": "before",
                    "content": before_notice,
                }
            )
            | {"timestamp": "2026-09-01T00:00:01Z"},
            _timestamped_entry("2026-09-01T00:00:02Z", "boundary needle"),
            _timestamped_entry("2026-09-01T00:00:03Z", "after needle"),
            _hook_attachment(
                {
                    "type": "hook_system_message",
                    "hookName": "After",
                    "toolUseID": "after",
                    "content": after_notice,
                }
            )
            | {"timestamp": "2026-09-01T00:00:04Z"},
        ],
    )
    base = [str(transcript), "--observation-boundary", "2026-09-01T00:00:02Z"]

    assert evidence.main(base) == 0
    default_events = _read_jsonl(capsys)
    assert "時刻なしの管理相当レコード" in json.dumps(default_events, ensure_ascii=False)
    assert "boundary needle" in json.dumps(default_events, ensure_ascii=False)
    assert "after needle" not in json.dumps(default_events, ensure_ascii=False)

    assert evidence.main([*base, "--warn"]) == 0
    assert [event["text"] for event in _read_jsonl(capsys)] == [before_notice]

    assert evidence.main([*base, "--grep", "needle"]) == 0
    grep_events = _read_jsonl(capsys)
    assert [event["line"] for event in grep_events if event["kind"] == "match"] == [2, 3, 4]
    assert grep_events[-1] == {"kind": "summary", "count": 3}

    assert evidence.main([*base, "--detail", "5"]) == 2
    assert _read_jsonl(capsys) == [{"kind": "error", "text": "行番号5は範囲外"}]

    assert evidence.main([*base, "--stats"]) == 0
    summary = _events_by_kind(_read_jsonl(capsys), "stats-summary")[0]
    assert summary["end"] == "2026-09-01T00:00:02Z"

    assert evidence.main([*base, "--hook-notices"]) == 0
    hook_events = _read_jsonl(capsys)
    assert [event["hook"] for event in hook_events if event["kind"] == "hook-notice"] == ["test/before"]


def test_observation_boundary_keeps_original_line_numbers(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """境界適用後も詳細位置は元ファイルの行番号を指す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            _timestamped_entry("2026-09-01T00:00:03Z", "除外する先頭行"),
            _timestamped_entry("2026-09-01T00:00:01Z", "保持する元の2行目"),
        ],
    )

    assert evidence.main([str(transcript), "--observation-boundary", "2026-09-01T00:00:02Z", "--detail", "2"]) == 0

    assert _read_jsonl(capsys) == [
        {
            "kind": "detail",
            "line": 2,
            "timestamp": "2026-09-01T00:00:01Z",
            "role": "user",
            "text": "保持する元の2行目",
        }
    ]


def test_observation_boundary_does_not_apply_to_delegate_records(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """境界前に始まった委譲先の終了記録を保持し、後発の委譲先を除く。"""
    transcript = _write_transcript(
        tmp_path,
        [_timestamped_entry("2026-09-01T00:00:01Z", "親記録")],
    )
    _write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-child",
        [
            _timestamped_entry("2026-09-01T00:00:01Z", "境界前の委譲先記録"),
            _timestamped_entry("2026-09-01T00:00:03Z", "境界後の委譲先結果"),
        ],
    )
    _write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-review",
        [
            _timestamped_entry("2026-09-01T00:00:04Z", "後発の振り返り失敗"),
            _hook_attachment(
                {
                    "type": "hook_system_message",
                    "hookName": "After",
                    "toolUseID": "review",
                    "content": "[auto-generated: test/review][warn] 後発の振り返り失敗",
                }
            )
            | {"timestamp": "2026-09-01T00:00:05Z"},
        ],
    )

    base = [str(transcript), "--observation-boundary", "2026-09-01T00:00:02Z"]
    assert evidence.main(base) == 0

    events = _read_jsonl(capsys, raw=True)
    assert [event["text"] for event in events if event["record"] == "agent-child"] == [
        "境界前の委譲先記録",
        "境界後の委譲先結果",
    ]
    assert all(event["record"] != "agent-review" for event in events)

    assert evidence.main([*base, "--warn"]) == 0
    assert _read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]
    assert evidence.main([*base, "--grep", "後発"]) == 0
    assert _read_jsonl(capsys)[-1] == {"kind": "summary", "count": 0}
    assert evidence.main([*base, "--stats"]) == 0
    assert [event["agent"] for event in _events_by_kind(_read_jsonl(capsys), "stats-subagent")] == ["agent-child"]

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    assert evidence.main([*base, "--bundle", str(bundle_dir)]) == 0
    _read_jsonl(capsys)
    assert "後発の振り返り失敗" not in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8")


def test_without_observation_boundary_output_is_unchanged(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """観測境界を指定しない呼び出しは境界後相当の記録も従来どおり返す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            _timestamped_entry("2026-09-01T00:00:01Z", "先行記録"),
            _timestamped_entry("2026-09-01T00:00:03Z", "後続記録"),
        ],
    )

    assert evidence.main([str(transcript)]) == 0

    assert [event["text"] for event in _read_jsonl(capsys)] == ["先行記録", "後続記録"]


def test_invalid_observation_boundary_returns_exit_code_two(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """解析できない観測境界はエラーイベントと終了コード2を返す。"""
    transcript = _write_transcript(tmp_path, [_timestamped_entry("2026-09-01T00:00:01Z", "記録")])

    assert evidence.main([str(transcript), "--observation-boundary", "not-a-timestamp"]) == 2

    assert _read_jsonl(capsys) == [{"kind": "error", "text": "観測境界が不正: not-a-timestamp"}]


def test_elapsed_until_returns_seconds_from_first_record(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """指定時刻までの経過秒数と入力の時刻表現を返す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            _timestamped_entry("2026-09-01T00:00:01Z", "開始"),
            _timestamped_entry("2026-09-01T00:00:03Z", "終了"),
        ],
    )

    assert evidence.main([str(transcript), "--elapsed-until", "2026-09-01T00:01:01Z"]) == 0

    assert _read_jsonl(capsys) == [
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
            _timestamped_entry("2026-09-01T00:00:01Z", "開始"),
            _timestamped_entry("2026-09-01T00:00:03Z", "境界後"),
        ],
    )
    elapsed_args = [str(transcript), "--elapsed-until", "2026-09-01T00:01:01Z"]

    assert evidence.main(elapsed_args) == 0
    without_boundary = _read_jsonl(capsys)
    assert evidence.main([*elapsed_args, "--observation-boundary", "2026-09-01T00:00:02Z"]) == 0

    assert _read_jsonl(capsys) == without_boundary


def test_elapsed_until_rejects_invalid_observation_boundary(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """経過時間照会と併用した解析不能な観測境界を拒否する。"""
    transcript = _write_transcript(tmp_path, [_timestamped_entry("2026-09-01T00:00:01Z", "記録")])

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

    assert _read_jsonl(capsys) == [{"kind": "error", "text": "観測境界が不正: not-a-timestamp"}]


def test_elapsed_until_rejects_invalid_timestamp(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """解析できない経過時間の終端を拒否する。"""
    transcript = _write_transcript(tmp_path, [_timestamped_entry("2026-09-01T00:00:01Z", "記録")])

    assert evidence.main([str(transcript), "--elapsed-until", "not-a-timestamp"]) == 2

    assert _read_jsonl(capsys) == [{"kind": "error", "text": "経過時間の終端が不正: not-a-timestamp"}]


def test_elapsed_until_rejects_records_without_timestamp(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """起点になる時刻を持たない記録を拒否する。"""
    transcript = _write_transcript(tmp_path, [_timestamped_entry(None, "記録")])

    assert evidence.main([str(transcript), "--elapsed-until", "2026-09-01T00:00:01Z"]) == 2

    assert _read_jsonl(capsys) == [{"kind": "error", "text": f"経過時間を算出できる記録が無い: {transcript}"}]


def test_elapsed_until_rejects_time_before_first_record(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """最初の記録より前の経過時間の終端を拒否する。"""
    transcript = _write_transcript(tmp_path, [_timestamped_entry("2026-09-01T00:00:02Z", "記録")])

    assert evidence.main([str(transcript), "--elapsed-until", "2026-09-01T00:00:01Z"]) == 2

    assert _read_jsonl(capsys) == [
        {
            "kind": "error",
            "text": "経過時間の終端が最初のレコードより前: 2026-09-01T00:00:01Z",
        }
    ]


def test_elapsed_until_conflicts_with_other_query_options(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """経過時間照会と他の照会モードの同時指定を拒否する。"""
    transcript = _write_transcript(tmp_path, [_timestamped_entry("2026-09-01T00:00:01Z", "記録")])

    assert evidence.main([str(transcript), "--elapsed-until", "2026-09-01T00:00:02Z", "--stats"]) == 2

    assert _read_jsonl(capsys) == [
        {
            "kind": "error",
            "text": "--warn・--grep・--detail・--stats・--hook-notices・--bundle・--elapsed-untilは併用できない",
        }
    ]


def test_default_events_separate_main_user_message_from_subagent_task_prompt(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """人間の入力と委譲先のタスク入力を由来記録で区別する。"""
    transcript = _write_transcript(tmp_path, [_timestamped_entry("2026-09-01T00:00:01Z", "人間の入力")])
    _write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-child",
        [_timestamped_entry("2026-09-01T00:00:02Z", "タスク入力")],
    )

    assert evidence.main([str(transcript)]) == 0

    user_events = [event for event in _read_jsonl(capsys, raw=True) if event["kind"] == "user"]
    assert [(event["record"], event["text"]) for event in user_events] == [
        ("main", "人間の入力"),
        ("agent-child", "タスク入力"),
    ]


def test_reconciliation_repeats_until_no_main_user_intervention_is_added(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """再取得中に届いた人間の入力も次の境界で追加し、0件まで照合する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            _timestamped_entry("2026-09-01T00:00:01Z", "初回境界前の入力"),
            _timestamped_entry("2026-09-01T00:00:03Z", "初回境界後の入力"),
            _timestamped_entry("2026-09-01T00:00:05Z", "1回目の再取得中の入力"),
            _timestamped_entry("2026-09-01T00:00:07Z", "2回目の再取得中の入力"),
        ],
    )
    _write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-child",
        [_timestamped_entry("2026-09-01T00:00:07Z", "委譲先のタスク入力")],
    )

    assert evidence.main([str(transcript), "--observation-boundary", "2026-09-01T00:00:02Z"]) == 0
    initial_events = _read_jsonl(capsys, raw=True)
    known_locators = {(event["record"], event["line"]) for event in initial_events}
    additions_by_reconciliation = []
    for boundary in [
        "2026-09-01T00:00:04Z",
        "2026-09-01T00:00:06Z",
        "2026-09-01T00:00:08Z",
        "2026-09-01T00:00:09Z",
    ]:
        assert evidence.main([str(transcript), "--observation-boundary", boundary]) == 0
        additional_users = [
            event
            for event in _read_jsonl(capsys, raw=True)
            if event["kind"] == "user" and event["record"] == "main" and (event["record"], event["line"]) not in known_locators
        ]
        additions_by_reconciliation.append([event["text"] for event in additional_users])
        known_locators.update((event["record"], event["line"]) for event in additional_users)

    assert additions_by_reconciliation == [
        ["初回境界後の入力"],
        ["1回目の再取得中の入力"],
        ["2回目の再取得中の入力"],
        [],
    ]


def test_elapsed_until_after_reconciliation_includes_finalization_time(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """再照合後の成果確定時刻までを経過時間へ含める。"""
    transcript = _write_transcript(
        tmp_path,
        [
            _timestamped_entry("2026-09-01T00:00:01Z", "開始"),
            _timestamped_entry("2026-09-01T00:00:07Z", "再取得中の入力"),
        ],
    )
    final_reconciliation_boundary = "2026-09-01T00:00:09Z"
    finalized_at = "2026-09-01T00:00:12Z"

    assert evidence.main([str(transcript), "--observation-boundary", final_reconciliation_boundary]) == 0
    _read_jsonl(capsys, raw=True)
    assert evidence.main([str(transcript), "--elapsed-until", finalized_at]) == 0

    assert _read_jsonl(capsys) == [
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
        {"kind": "final-result", "text": "回答待ち", "line": 2, "timestamp": None, "sequence": 1},
        {
            "kind": "user",
            "text": "質問: 最初の質問\n回答: 最初の回答\n質問: 次の質問\n回答: 次の回答1\n次の回答2",
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

    assert not evidence.load_and_extract(str(transcript))


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

    event = evidence.load_and_extract(str(transcript))[0]

    assert event["command"] == json.dumps(command, ensure_ascii=False)
    assert event["exit_code"] == 17
    assert event["text"] == "失敗"
    assert event["executable"] == (command[0] if command else "")
    assert event["diagnostic"] == "失敗"


@pytest.mark.parametrize(
    ("commands", "diagnostics", "expected_candidates"),
    (
        ([["rg", "-F", "one"], ["rg", "-F", "two"]], ["", ""], 0),
        ([["test", "-e", "/one"], ["test", "-e", "/two"]], ["", ""], 0),
        ([["rg", "-F", "one"], ["rg", "-F", "one"]], ["", ""], 0),
        ([["tool", "one"], ["tool", "two"]], ["same diagnostic", "same diagnostic"], 1),
        ([["tool", "one"], ["tool", "two"]], ["first diagnostic", "second diagnostic"], 2),
    ),
)
def test_codex_failed_commands_group_by_executable_exit_and_diagnostic(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    commands: list[list[str]],
    diagnostics: list[str],
    expected_candidates: int,
) -> None:
    """正常な偽判定は除外し、診断を伴う失敗を既存軸で集約する。"""
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
                        "exit_code": 1,
                        "stderr": diagnostic,
                    },
                },
            }
            for command, diagnostic in zip(commands, diagnostics, strict=True)
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    _read_jsonl(capsys, raw=True)
    records = [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    candidates = [record for record in records if record["kind"] == "candidate"]

    assert len(candidates) == expected_candidates
    if expected_candidates:
        assert {candidate["candidate_kind"] for candidate in candidates} == {"command-failure"}
        assert sum(candidate["count"] for candidate in candidates) == 2
        assert sum(len(candidate["locators"]) for candidate in candidates) == 2


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

    records = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

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

    records = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    candidates = [record for record in records if record["kind"] == "candidate"]
    assert {locator["line"] for candidate in candidates for locator in candidate["locators"]} == {5, 6}
    assert records[-1]["excluded"] == {"normal-negative-result": 4}


def test_codex_failed_commands_without_diagnostic_or_command_use_record_position(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """診断とcommandを欠く失敗は異なる記録位置を同一候補へ集約しない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {"type": "CommandExecution", "status": "failed", "exit_code": 1},
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {"type": "CommandExecution", "status": "failed", "exit_code": 1},
                },
            },
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    _read_jsonl(capsys, raw=True)
    records = [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]

    assert len([record for record in records if record["kind"] == "candidate"]) == 2


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


@pytest.mark.parametrize("field", ["isMeta", "turnCompanion"])
def test_claude_runtime_generated_user_entry_is_marked(field: str, tmp_path: pathlib.Path) -> None:
    """実行環境が生成したエントリの利用者イベントへ標識を付ける。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "依頼"}},
            {"type": "user", field: True, "message": {"role": "user", "content": "[Image: original 2938x1682]"}},
        ],
    )

    events = evidence.load_and_extract(str(transcript))

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

    events = evidence.load_and_extract(str(transcript))

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

    assert not evidence.load_and_extract(str(transcript))


def test_unsupported_nonempty_jsonl_returns_fallback(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(tmp_path, [{"type": "unknown", "payload": {"type": "unknown"}}])

    events = evidence.load_and_extract(str(transcript))

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


def _read_jsonl(capsys: pytest.CaptureFixture[str], *, raw: bool = False) -> list[dict]:
    """標準出力のJSONLを読み、既存の単一記録テストでは由来欄を除く。"""
    captured = capsys.readouterr()
    assert captured.err == ""
    events = [json.loads(line) for line in captured.out.splitlines()]
    if raw:
        return events
    return [
        {key: value for key, value in event.items() if key != "record"}
        for event in events
        if not (event.get("kind") == "summary" and "record" in event)
    ]


def test_warn_excludes_hook_marker_in_command_output(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """コマンド出力に現れたフック通知標識を実行時のフック警告として返さない。"""
    notice = "[auto-generated: agent-toolkit/pretooluse][warn] 文書中の例示"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"stdout": notice},
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": notice}],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert _read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]


def test_warn_keeps_hook_marker_in_hook_record(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """フック実行の記録に由来する通知標識を実行時警告として返す。"""
    notice = "[auto-generated: agent-toolkit/pretooluse][warn] 実行時の警告"
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
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert _read_jsonl(capsys) == [{"kind": "warning", "line": 1, "text": notice}]


@pytest.mark.parametrize("element", ["agent-toolkit-hook-message", "agent-toolkit-auto-inserted"])
@pytest.mark.parametrize(
    "attributes",
    ['source="agent-toolkit/pretooluse" kind="warn"', 'kind="warn" source="agent-toolkit/pretooluse"'],
)
def test_warn_keeps_xml_hook_marker_in_hook_record(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    element: str,
    attributes: str,
) -> None:
    """XML境界のwarn通知を実行時警告として返す。"""
    notice = f"<{element} {attributes}>\n実行時の警告\n</{element}>"
    transcript = _write_transcript(
        tmp_path,
        [
            _hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-xml-warn",
                    "content": [notice],
                }
            )
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert _read_jsonl(capsys) == [{"kind": "warning", "line": 1, "text": "実行時の警告"}]


@pytest.mark.parametrize("element", ["agent-toolkit-hook-message", "agent-toolkit-auto-inserted"])
@pytest.mark.parametrize(
    "attributes",
    ['source="agent-toolkit/pretooluse" kind="warn"', 'kind="warn" source="agent-toolkit/pretooluse"'],
)
def test_hook_notices_mode_parses_xml_boundary_without_closing_tag(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    element: str,
    attributes: str,
) -> None:
    """XML境界の属性と本文を分類し、閉じタグを種別本文から除く。"""
    notice = f"<{element} {attributes}>\n入力を補正した\n</{element}>"
    transcript = _write_transcript(
        tmp_path,
        [
            _hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-xml-notice",
                    "content": [notice],
                }
            )
        ],
    )

    assert evidence.main([str(transcript), "--hook-notices"]) == 0

    assert _read_jsonl(capsys) == [
        {
            "kind": "hook-notice",
            "hook": "agent-toolkit/pretooluse",
            "hook_name": "PreToolUse:Bash",
            "tag": "warn",
            "kind_text": "入力を補正した",
            "count": 1,
        },
        {"kind": "summary", "count": 1},
    ]


def test_warn_keeps_command_output_warning(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """フック通知標識を持たないコマンド出力の警告は検出対象に保つ。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"stdout": "warning: build failed"},
                "message": {"role": "user", "content": []},
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert _read_jsonl(capsys) == [{"kind": "warning", "line": 1, "text": "warning: build failed"}]


def test_warn_excludes_quoted_warning_inside_code_fence_of_command_output(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """コマンドが表示した文書のコードフェンス内の警告は、実行時警告として扱わない。

    `sed`や`cat -n`で過去の振り返りのAWIなどのMarkdownを表示すると、過去の警告の引用がフェンス内に現れる。
    これを候補にすると、対象セッションで発生していない警告が候補一覧へ載る。フェンス外の警告は保持する。
    """
    shown_document = "\n".join(
        [
            "### c0071 warning",
            "```text",
            "warn: 一括stageに編集記録が無いファイルが含まれている",
            "```",
            "  12\t~~~",
            "  13\twarning: 行番号付きで表示した引用",
            "  14\t~~~",
            "warning: 表示の後に出た実行時警告",
        ]
    )
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Bash", "id": "call-1", "input": {"command": "sed -n '1,9p' a.md"}}
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": shown_document}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert [event["text"] for event in _read_jsonl(capsys)] == ["warning: 表示の後に出た実行時警告"]


def test_warn_excludes_absence_statement(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """問題の不在を述べる警告本文を除き、実在する警告は保持する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"stdout": "警告: なし\n警告: 3件\nwarning: none.\nwarning: build failed"},
                "message": {"role": "user", "content": []},
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert [event["text"] for event in _read_jsonl(capsys)] == ["警告: 3件", "warning: build failed"]


def test_warn_collects_codex_item_completed_command_output(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codexの完了したコマンド実行から通常の実行時警告を返す。"""
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
                        "aggregated_output": "warning: build failed, waiting for other jobs to finish...",
                    },
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert _read_jsonl(capsys) == [
        {
            "kind": "warning",
            "line": 1,
            "text": "warning: build failed, waiting for other jobs to finish...",
        }
    ]


def test_warn_excludes_hook_marker_in_codex_command_output(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codexのコマンド出力に現れたフック通知標識を警告として返さない。"""
    notice = "[auto-generated: agent-toolkit/pretooluse][warn] 文書中の例示"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {"type": "CommandExecution", "status": "completed", "aggregated_output": notice},
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert _read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]


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

    assert evidence.load_and_extract(str(commentary_only)) == [
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

    assert [event["kind"] for event in evidence.load_and_extract(str(with_final_answer))] == [
        "final-result",
        "assistant",
    ]


def test_user_events_returns_main_user_events_in_range(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """指定区間のメイン記録にある利用者イベントだけを由来位置付きで返す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            _timestamped_entry("2026-09-01T00:00:00Z", "開始前"),
            _timestamped_entry("2026-09-01T00:00:01Z", "開始境界"),
            _timestamped_entry("2026-09-01T00:00:02Z", "区間内1"),
            _timestamped_entry(None, "時刻なし"),
            {
                "type": "assistant",
                "timestamp": "2026-09-01T00:00:03Z",
                "message": {"role": "assistant", "content": "中間報告"},
            },
            _timestamped_entry("2026-09-01T00:00:04Z", "区間内2"),
            _timestamped_entry("2026-09-01T00:00:05Z", "終了後"),
        ],
    )
    _write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-child",
        [_timestamped_entry("2026-09-01T00:00:03Z", "委譲先の入力")],
    )

    assert (
        evidence.main(
            [
                str(transcript),
                "--user-events",
                "--since",
                "2026-09-01T00:00:01Z",
                "--observation-boundary",
                "2026-09-01T00:00:04Z",
            ]
        )
        == 0
    )

    events = _read_jsonl(capsys, raw=True)
    assert [(event["record"], event["line"], event["text"]) for event in events[:-1]] == [
        ("main", 3, "区間内1"),
        ("main", 6, "区間内2"),
    ]
    assert events[-1] == {"kind": "summary", "count": 2}


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

    events = _read_jsonl(capsys, raw=True)
    assert [(event["line"], event["text"]) for event in events[:-1]] == [
        (4, "質問: 境界越え2\n回答: 回答2"),
        (5, "質問: 区間内\n回答: 回答3"),
        (3, "質問: 境界越え1\n回答: 回答1"),
    ]
    assert events[-1] == {"kind": "summary", "count": 3}


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

    events = _read_jsonl(capsys, raw=True)
    assert [(event["line"], event["text"]) for event in events[:-1]] == [
        (4, "質問: 境界越え2\n回答: 回答2"),
        (5, "質問: 区間内\n回答: 回答3"),
        (3, "質問: 境界越え1\n回答: 回答1"),
    ]
    assert events[-1] == {"kind": "summary", "count": 3}


def test_user_events_requires_since(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """照会開始境界の欠落・誤用・不正値と他モード併用を拒否する。"""
    transcript = _write_transcript(
        tmp_path,
        [_timestamped_entry("2026-09-01T00:00:01Z", "入力")],
    )

    invocations = (
        [str(transcript), "--user-events"],
        [str(transcript), "--since", "2026-09-01T00:00:00Z"],
        [str(transcript), "--user-events", "--since", "不正な時刻"],
        [str(transcript), "--user-events", "--since", "2026-09-01T00:00:00Z", "--warn"],
    )
    for arguments in invocations:
        assert evidence.main(arguments) == 2
        assert _read_jsonl(capsys)[0]["kind"] == "error"


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


@pytest.mark.parametrize("tool_name", ["WebSearch", "WebFetch", "Read", "web__run"])
def test_warn_mode_excludes_plain_warning_lines_from_external_content_tools(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    tool_name: str,
) -> None:
    """外部検索及び文書取得の本文にある警告語を実行時警告へ数えない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": tool_name, "id": "call-1", "input": {}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "warning: 外部本文"}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert _read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]


def test_warn_mode_keeps_structured_warning_from_external_tool(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """外部ツールも構造化して返した警告は実行時警告として保持する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "WebSearch", "id": "call-1", "input": {}}],
                },
            },
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


def test_warn_mode_excludes_plain_warning_lines_from_codex_web_output(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codexの外部検索結果に含まれる警告語も実行時警告へ数えない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {"type": "function_call", "name": "web__run", "call_id": "call-1", "arguments": "{}"},
            },
            {
                "type": "response_item",
                "payload": {"type": "function_call_output", "call_id": "call-1", "output": "warning: 外部本文"},
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert _read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]


def test_warn_mode_excludes_plain_warning_lines_from_unmatched_results(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """呼び出し名を対応付けられない結果本文を実行時警告へ数えない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "missing-tool", "content": "warning: 外部本文"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "missing-function",
                    "output": "warning: 外部本文",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "missing-custom",
                    "output": "warning: 外部本文",
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert _read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]


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
    ("warning_line", "expected"),
    [
        ("[warn] 実行時警告", True),
        ("[warning] 実行時警告", True),
        ("[auto-generated: agent-toolkit/pretooluse][warn] 実行時警告", False),
        ("warning: 実行時警告", True),
        ("warn: 実行時警告", True),
        ("警告: 実行時警告", True),
        ("⚠: 実行時警告", True),
        ("⚠ 実行時警告", True),
        ("⚠    実行時警告", True),
    ],
)
def test_warn_mode_accepts_real_line_start_markers_only(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    warning_line: str,
    expected: bool,
) -> None:
    """実在する行頭マーカーを受理し、コマンド出力内のフック通知標識を除く。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "本文途中の warning と warn"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": "call-1", "input": {}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": warning_line}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    expected_events = (
        [{"kind": "warning", "line": 3, "text": warning_line, "tool": "call-1"}]
        if expected
        else [{"kind": "warning", "text": "一致なし"}]
    )
    assert _read_jsonl(capsys) == expected_events


@pytest.mark.parametrize(
    ("warning_line", "matched"),
    [
        ("  [warn] 明示警告", True),
        ("12\t[warning] 明示警告", True),
        ("12\t [warn] 明示警告", False),
        ("  warning: 一般警告", False),
        ("12\twarning: 一般警告", False),
        ("12\t warning: 一般警告", False),
        ("warning: 一般警告", True),
    ],
)
def test_warn_mode_restricts_generic_markers_to_actual_line_start(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    warning_line: str,
    matched: bool,
) -> None:
    """一般警告語だけを引用・行番号付き表示から除外し、明示マーカーは維持する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": "call-1", "input": {}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": warning_line}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    expected = (
        [{"kind": "warning", "line": 2, "text": warning_line.lstrip(), "tool": "call-1"}]
        if matched
        else [{"kind": "warning", "text": "一致なし"}]
    )
    assert _read_jsonl(capsys) == expected


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
        {"kind": "match", "line": 1, "timestamp": None, "text": '{"warning_message": "引数内の警告"}'},
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
    "marker",
    (
        {"severity": "warning"},
        {"type": "warning"},
        {"kind": "warning"},
        {"severity": "warning", "stdout": "通常の標準出力", "stderr": "通常の標準エラー"},
    ),
)
def test_warn_mode_rejects_structured_markers_without_a_warning_body(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    marker: dict[str, str],
) -> None:
    """区分だけを持つ構造化結果は、警告本文が無いため警告として数えない。"""
    transcript = _write_transcript(
        tmp_path,
        [{"type": "user", "toolUseResult": marker, "message": {"role": "user", "content": []}}],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0
    assert _read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]


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
        {"kind": "match", "line": 1, "timestamp": None, "text": expected_text},
        {"kind": "summary", "count": 1},
    ]


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


def test_warn_mode_ignores_read_result_body_and_keeps_hook_notice(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """読み取り結果の本文を除外し、同じレコードのフック通知だけを警告として返す。"""
    file_body = 'warning: 文書内の例示\n{"warning_message": "文書内の構造化警告"}'
    notice = "[auto-generated: agent-toolkit/posttooluse][warn] 読み取り後の通知"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {
                    "type": "text",
                    "file": {"filePath": "/tmp/rules.md", "content": file_body, "numLines": 2},
                },
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "read-1", "content": file_body}],
                },
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PostToolUse:Read",
                    "toolUseID": "read-1",
                    "content": [notice],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert [event["text"] for event in _read_jsonl(capsys)] == [notice]


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
    transcript = _execution_result_transcript(tmp_path, "12\twarning: 同じ本文", "warning: 同じ本文")

    arguments = [str(transcript), option]
    if pattern is not None:
        arguments.append(pattern)
    assert evidence.main(arguments) == 0

    events = _read_jsonl(capsys)
    expected_text = "warning: 同じ本文" if option == "--warn" else "12\twarning: 同じ本文"
    expected_event: dict[str, object] = {
        "kind": "warning" if option == "--warn" else "match",
        "line": 2,
        "text": expected_text,
    }
    if option == "--warn":
        expected_event["tool"] = "call-1"
    else:
        expected_event["timestamp"] = None
    assert events[0] == expected_event
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
    transcript = _execution_result_transcript(tmp_path, "12\twarning: 本文\nwarning: 本文")

    arguments = [str(transcript), option]
    if pattern is not None:
        arguments.append(pattern)
    assert evidence.main(arguments) == 0

    events = _read_jsonl(capsys)
    expected = ["warning: 本文"] if option == "--warn" else ["12\twarning: 本文", "warning: 本文"]
    assert [event["text"] for event in events if event["kind"] in {"warning", "match"}] == expected
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
    assert events[0] == {"kind": "match", "line": 1, "timestamp": None, "text": "退避された本文の照合語"}
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
        "python3 agent-toolkit/skills/session-review/scripts/session_review_evidence.py --warn /tmp/foo.jsonl",
        "uv run --project /plugin --locked --no-default-groups "
        "/plugin/skills/session-review/scripts/session_review_evidence.py /tmp/foo.jsonl",
        "./agent-toolkit/skills/session-review/scripts/session_review_evidence.py --grep 'warn' /tmp/foo.jsonl",
        "cd /repo && python3 agent-toolkit/skills/session-review/scripts/session_review_evidence.py --warn /tmp/foo.jsonl",
        "bash -lc 'python3 /plugin/skills/session-review/scripts/session_review_evidence.py --warn /tmp/foo.jsonl'",
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
    command = "python3 /plugin/skills/session-review/scripts/session_review_evidence.py --warn /tmp/foo.jsonl"
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
    command = "python3 agent-toolkit/skills/session-review/scripts/session_review_evidence.py --warn /tmp/foo.jsonl"
    entries = [
        *_self_invocation_entries(command),
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Bash", "id": "tu2", "input": {}}],
            },
        },
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
    assert [event["line"] for event in events] == [4]


@pytest.mark.parametrize(
    "command",
    [
        "rg -n session_review_evidence agent-toolkit/skills/session-review/scripts",
        "grep -rn TODO agent-toolkit/skills/session-review/scripts/session_review_evidence.py",
        "cat agent-toolkit/skills/session-review/scripts/session_review_evidence.py",
        "sed -n '1,20p' agent-toolkit/skills/session-review/scripts/session_review_evidence.py",
        "head -n 5 agent-toolkit/skills/session-review/scripts/session_review_evidence_test.py",
        "tail -n 5 agent-toolkit/skills/session-review/scripts/session_review_evidence.py",
        "vim agent-toolkit/skills/session-review/scripts/session_review_evidence.py",
        "bash -lc 'rg -n session_review_evidence agent-toolkit/skills/session-review/scripts'",
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
        {"kind": "match", "line": 1, "timestamp": None, "text": "done"},
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
        {"kind": "match", "line": 1, "timestamp": None, "text": "needle-value"},
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
                        {"type": "tool_use", "name": "Bash", "id": "c1", "input": {"command": "atk wi list", "n": 1}},
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

    assert evidence.main([str(transcript), "--detail", "1", "--detail", "2"]) == 0

    events = _read_jsonl(capsys)
    assert [event["line"] for event in events] == [1, 1, 1, 2]
    assert [event.get("name") for event in events[:3]] == ["Bash", "Read", "Agent"]
    assert events[0]["input"] == {"command": "atk wi list", "n": 1}
    assert events[1]["input"] == {"file_path": "/tmp/x.md"}
    assert events[2]["input"] == {"prompt": "依頼本文"}
    assert events[3] == {"kind": "detail", "line": 2, "timestamp": None, "tool": "c1", "text": "結果本文"}


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

    assert _read_jsonl(capsys) == [
        {"kind": "detail", "line": 2, "timestamp": None, "tool": "c1", "text": "warning: real output"}
    ]


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


def test_detail_mode_rejects_space_separated_values(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """複数位置は`--detail`を繰り返して指定し、空白区切りの複数値は受理しない。"""
    transcript = _write_transcript(
        tmp_path,
        [{"type": "user", "message": {"role": "user", "content": "依頼"}}],
    )

    with pytest.raises(SystemExit) as raised:
        evidence.main([str(transcript), "--detail", "1", "2"])

    assert raised.value.code == 2
    assert capsys.readouterr().out == ""


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
                        {"type": "tool_use", "name": "Bash", "id": "c1", "input": {"command": "atk wi list"}},
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "失敗: atk wi list"}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--grep", "atk wi"]) == 0

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


def test_stats_collects_all_subagents_without_exclusion(tmp_path: pathlib.Path, capsys) -> None:
    """全てのサブエージェント記録を主体別集計へ含め、種別による除外を行わない。"""
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
    other = subagents / "agent-other.jsonl"
    other.write_text(
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-08-19T00:00:01Z",
                "message": {"role": "assistant", "id": "o", "usage": {"input_tokens": 100, "output_tokens": 100}},
            }
        )
        + "\n"
    )
    (subagents / "agent-other.meta.json").write_text(json.dumps({"agentType": "general-purpose"}))

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    assert sorted(event["agent"] for event in _events_by_kind(events, "stats-subagent")) == ["agent-normal", "agent-other"]
    total = _events_by_kind(events, "stats-subagent-total")[0]
    assert total["count"] == 2
    assert "excluded_review_agents" not in total
    assert total["tokens"]["input_tokens"] == 102


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


def test_stats_discovers_codex_threads_from_structured_shapes(tmp_path: pathlib.Path, monkeypatch, capsys) -> None:
    """起動ツールの構造化終端結果からthreadIdを収集し、引用と既存session操作は除外する。

    `mcpMeta.structuredContent`・JSON文字列型`toolUseResult`は起動`tool_result`へ対応付け、
    同一threadIdへ重複排除する。タスク通知の`<result>`要素だけで到達するthreadIdも収集する。
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
                tool_name="mcp__agents_server__kill",
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
                            "input": {"engine": "codex"},
                        }
                    ],
                },
            },
            {
                "type": "user",
                "timestamp": "2026-08-19T00:00:02Z",
                "mcpMeta": {"structuredContent": {"engine": "codex", "threadId": thread_id}},
                "toolUseResult": json.dumps({"engine": "codex", "conversationId": thread_id}),
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "a", "content": "完了"}],
                },
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
    assert [event["thread"] for event in threads] == [notified_id, thread_id]
    assert [event["tokens"]["total_tokens"] for event in threads] == [17, 9]
    assert quoted_id not in {event["thread"] for event in threads}
    assert missing_id not in {event["thread"] for event in threads}
    assert sent_id not in {event["thread"] for event in threads}


def test_stats_resolves_claude_session_from_codex_rollout_tool_result(
    tmp_path: pathlib.Path,
    monkeypatch,
    capsys,
) -> None:
    """Codex rolloutのcustom tool call終端結果からClaude sessionを解決し、エンジン別に集計する。"""
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
                    "arguments": json.dumps({"engine": "claude"}),
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:01Z",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "call-claude",
                    "output": {"engine": "claude", "session_id": session_id},
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    thread = _events_by_kind(events, "stats-agent-thread")[0]
    assert thread["engine"] == "claude"
    assert thread["session_id"] == session_id
    assert thread["tokens"] == _usage(4, 5)
    assert _events_by_kind(events, "stats-total")[0]["agent_thread_counts"] == {"claude": 1}


def test_collect_includes_start_custom_and_start_write_delegates(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`start_custom`と`start_write`で起動した委譲先も収集し、記録の無い委譲先は`unresolved-record`にする。"""
    custom_session = "claude-session-custom-1111"
    write_session = "agy-session-write-2222"
    home = tmp_path / "home"
    custom_transcript = home / ".claude" / "projects" / "repo" / f"{custom_session}.jsonl"
    custom_transcript.parent.mkdir(parents=True)
    delegate_request = {
        "type": "user",
        "timestamp": "2026-08-19T00:00:01Z",
        "message": {"role": "user", "content": "委譲先への依頼"},
    }
    custom_transcript.write_text(
        json.dumps(delegate_request)
        + "\n"
        + json.dumps(_assistant_usage_entry("2026-08-19T00:00:02Z", "custom-message", _usage(4, 5)))
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))

    transcript = _write_transcript(
        tmp_path,
        [
            _codex_tool_use_entry(
                "2026-08-19T00:00:00Z",
                "call-custom",
                custom_session,
                tool_name="mcp__plugin_agent-toolkit_agents_server__start_custom",
            ),
            _codex_tool_result_entry("2026-08-19T00:00:01Z", "call-custom", custom_session, engine=None),
            _codex_tool_use_entry(
                "2026-08-19T00:00:03Z",
                "call-write",
                write_session,
                tool_name="mcp__plugin_agent-toolkit_agents_server__start_write",
            ),
            _codex_tool_result_entry("2026-08-19T00:00:04Z", "call-write", write_session, engine=None),
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    stats_events = _read_jsonl(capsys, raw=True)
    assert [event["session_id"] for event in _events_by_kind(stats_events, "stats-agent-thread")] == [custom_session]
    assert _events_by_kind(stats_events, "unresolved-record") == [
        {"kind": "unresolved-record", "record": write_session, "line": 4}
    ]

    assert evidence.main([str(transcript)]) == 0
    default_events = _read_jsonl(capsys, raw=True)
    assert f"claude:{custom_session}" in {event.get("record") for event in default_events}
    assert _events_by_kind(default_events, "unresolved-record") == [
        {"kind": "unresolved-record", "record": write_session, "line": 4}
    ]


_AGY_ROOT_SESSION = "0741ee80-54a0-44b4-a070-e288e4a71dc0"


def _agy_delegation_transcript(tmp_path: pathlib.Path, session_id: str) -> pathlib.Path:
    """`start_write`でagyの委譲先を起動したClaude transcriptを作成する。"""
    return _write_transcript(
        tmp_path,
        [
            {"type": "user", "timestamp": "2026-09-26T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
            _codex_tool_use_entry(
                "2026-09-26T00:00:01Z",
                "call-agy",
                session_id,
                tool_name="mcp__plugin_agent-toolkit_agents_server__start_write",
            ),
            _codex_tool_result_entry("2026-09-26T00:00:02Z", "call-agy", session_id, engine=None),
        ],
    )


def _write_agy_log(state_home: pathlib.Path, session_id: str, events: list[dict]) -> None:
    """agents_serverが保存する位置へagyの委譲先ログを書く。"""
    _write_jsonl(state_home / "agent-toolkit" / "agents-server" / _AGY_ROOT_SESSION / "logs" / f"{session_id}.jsonl", events)


def _agy_step(step_index: int, step_type: str, state: str, **fields: object) -> dict:
    return {
        "event": "step_update",
        "step_update": {
            "conversation_id": "agy",
            "step_index": step_index,
            "state": state,
            "step_type": step_type,
            **fields,
        },
    }


def test_agy_delegate_failures_become_candidates_and_stats(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """agyの委譲先の失敗したツール、エラー報告及び失敗終端が候補へ現れ、件数とトークン数が集計へ現れる。"""
    session_id = "a9244465-1577-44a9-a7b0-500b1f976b0e"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    run_command = {"CommandLine": "npx textlint README.md"}
    _write_agy_log(
        tmp_path / "state",
        session_id,
        [
            {"event": "init", "conversation_id": session_id, "init": {"model": "gemini", "cwd": "/work"}},
            _agy_step(
                1, "tool", "ACTIVE", tool_name="run_command", tool_info={"name": "run_command", "parameters": run_command}
            ),
            _agy_step(
                1,
                "tool",
                "ERROR",
                tool_name="run_command",
                tool_info={
                    "name": "run_command",
                    "parameters": run_command,
                    "error": {"type": "TOOL_ERROR", "message": "sandbox server did not answer Run within 30s"},
                },
            ),
            _agy_step(
                2,
                "tool",
                "DONE",
                tool_name="view_file",
                tool_info={"name": "view_file", "parameters": {"AbsolutePath": "/work/a.md"}, "output": "本文"},
            ),
            _agy_step(3, "error_message", "DONE"),
            _agy_step(
                4,
                "agent_response",
                "DONE",
                usage={"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 300, "total_tokens": 120},
            ),
            {
                "event": "result",
                "result": {
                    "conversation_id": session_id,
                    "status": "ERROR",
                    "error": "Individual quota reached.",
                    "response": "",
                },
            },
        ],
    )
    transcript = _agy_delegation_transcript(tmp_path, session_id)
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    _read_jsonl(capsys, raw=True)
    candidates = [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    failures = [
        item["locators"] for item in candidates if item["kind"] == "candidate" and item["candidate_kind"] == "tool-failure"
    ]
    record_id = f"agy:{session_id}"
    assert sorted(locator["line"] for locators in failures for locator in locators if locator["record"] == record_id) == [
        3,
        5,
        7,
    ]

    assert evidence.main([str(transcript), "--stats"]) == 0
    stats = _read_jsonl(capsys, raw=True)
    thread = _events_by_kind(stats, "stats-agent-thread")[0]
    assert (thread["engine"], thread["session_id"]) == ("agy", session_id)
    assert thread["tokens"] == {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 300,
    }
    total = _events_by_kind(stats, "stats-total")[0]
    assert total["agent_thread_counts"] == {"agy": 1}
    assert total["tokens"]["cache_read_input_tokens"] == 300
    assert not _events_by_kind(stats, "unresolved-record")


def test_agy_delegate_without_log_is_unresolved_record(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """状態ディレクトリにログが無いagyの委譲先は、記録を解決できない委譲先として報告する。"""
    session_id = "b9244465-1577-44a9-a7b0-500b1f976b0e"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    transcript = _agy_delegation_transcript(tmp_path, session_id)

    assert evidence.main([str(transcript)]) == 0

    events = _read_jsonl(capsys, raw=True)
    assert _events_by_kind(events, "unresolved-record") == [{"kind": "unresolved-record", "record": session_id, "line": 3}]


def test_agy_grandchild_launch_is_neither_detected_nor_collected(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """agyの委譲先が孫を起動しても、孫の記録を探さず、未解決の委譲としても報告しない。"""
    session_id = "c9244465-1577-44a9-a7b0-500b1f976b0e"
    grandchild = "d9244465-1577-44a9-a7b0-500b1f976b0e"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _write_agy_log(
        tmp_path / "state",
        session_id,
        [
            _agy_step(
                1,
                "tool",
                "DONE",
                tool_name="invoke_subagent",
                tool_info={"name": "invoke_subagent", "parameters": {"Prompt": "調査"}, "output": {"session_id": grandchild}},
            ),
            _agy_step(
                2,
                "tool",
                "DONE",
                tool_name="call_mcp_tool",
                tool_info={
                    "name": "call_mcp_tool",
                    "parameters": {"ServerName": "agents_server", "ToolName": "start"},
                    "output": json.dumps({"session_id": grandchild}),
                },
            ),
            {"event": "result", "result": {"conversation_id": session_id, "status": "SUCCESS", "response": "完了した"}},
        ],
    )
    transcript = _agy_delegation_transcript(tmp_path, session_id)

    assert evidence.main([str(transcript)]) == 0

    events = _read_jsonl(capsys, raw=True)
    assert f"agy:{session_id}" in {event.get("record") for event in events}
    assert not _events_by_kind(events, "unresolved-record")
    assert not _events_by_kind(events, "unresolved-delegation")
    assert grandchild not in json.dumps(events, ensure_ascii=False)


def test_extractor_runtimes_match_supported_engines() -> None:
    """抽出器が時系列へ変換できる実行系は、agents_serverが起動できる実行系と一致する。

    実行系を追加して変換を追随させないと、その実行系の委譲先の記録は候補と集計へ現れない。
    """
    assert set(evidence.RUNTIME_EXTRACTORS) == agents_server_mcp.SUPPORTED_ENGINES


def test_collect_resolves_codex_agents_server_delegations(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codexの3つの完了形状から`structuredContent`直下の識別子を解決する。"""
    codex_home = tmp_path / "codex"
    thread_ids = [
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
    ]
    for index, thread_id in enumerate(thread_ids, start=1):
        _write_rollout(
            codex_home,
            thread_id,
            [(f"2026-08-19T00:00:0{index}Z", {"input_tokens": index, "total_tokens": index})],
        )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "timestamp": "2026-08-19T00:00:00Z",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "server": "agents_server",
                        "tool": "mcp__agents_server__start",
                        "arguments": {"engine": "codex"},
                        "result": {"structuredContent": {"session_id": thread_ids[0]}},
                    },
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:01Z",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "mcp__agents_server__start_explore",
                    "call_id": "custom",
                    "arguments": {"engine": "codex"},
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:02Z",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "custom",
                    "output": {"structuredContent": {"session_id": thread_ids[1]}},
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:03Z",
                "payload": {
                    "type": "function_call",
                    "name": "mcp__agents_server__start_shell",
                    "call_id": "function",
                    "arguments": {"engine": "codex"},
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-08-19T00:00:04Z",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "function",
                    "output": {"structuredContent": {"session_id": thread_ids[2]}},
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    assert {event["session_id"] for event in _events_by_kind(events, "stats-agent-thread")} == set(thread_ids)
    assert not _events_by_kind(events, "unresolved-delegation")


@pytest.mark.parametrize("tool", ["start", "start_explore", "start_shell"])
def test_collect_reports_unresolved_delegation(
    tool: str,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """agents_server起動の出力から識別子を得られない場合は未確認範囲を返す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": f"mcp__agents_server__{tool}",
                    "call_id": "missing",
                    "arguments": {"engine": "codex"},
                },
            },
            {
                "type": "response_item",
                "payload": {"type": "custom_tool_call_output", "call_id": "missing", "output": {"status": "done"}},
            },
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    assert _events_by_kind(_read_jsonl(capsys, raw=True), "unresolved-delegation") == [
        {"kind": "unresolved-delegation", "record": "main", "line": 2}
    ]


def test_collect_reports_unresolved_event_msg_delegation(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Codexのitem_completedから識別子を得られない場合は未確認範囲を返す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "server": "agents_server",
                        "tool": "mcp__agents_server__start",
                        "arguments": {"engine": "codex"},
                        "result": {"status": "done"},
                    },
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    assert _events_by_kind(_read_jsonl(capsys, raw=True), "unresolved-delegation") == [
        {"kind": "unresolved-delegation", "record": "main", "line": 1}
    ]


def test_collect_ignores_existing_session_operations_as_delegation_sources(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """wait等の既存session操作と外側実行セルの文字列は委譲発見元にしない。"""
    missing_session_id = "99999999-9999-4999-8999-999999999999"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "functions.exec",
                    "call_id": "outer-exec",
                    "arguments": (
                        'await tools.mcp__agents_server__wait({"session_id":"known"}); '
                        'await tools.mcp__agents_server__start_shell({"command":"true"});'
                    ),
                },
            },
            {
                "type": "response_item",
                "payload": {"type": "custom_tool_call_output", "call_id": "outer-exec", "output": {"status": "done"}},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "mcp__agents_server__wait",
                    "call_id": "missing-wait",
                    "arguments": {"session_id": missing_session_id},
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "missing-wait",
                    "output": {"status": "failed"},
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "server": "agents_server",
                        "tool": "mcp__agents_server__list",
                        "arguments": {},
                        "result": {"structuredContent": {"sessions": [{"session_id": missing_session_id}]}},
                    },
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "mcp__plugin_agent-toolkit_agents_server__wait",
                            "id": "claude-wait",
                            "input": {"session_id": missing_session_id},
                        }
                    ],
                },
            },
            {
                "type": "user",
                "mcpMeta": {"structuredContent": {"engine": "codex", "threadId": missing_session_id}},
                "toolUseResult": {"engine": "codex", "session_id": missing_session_id},
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "claude-wait", "content": "完了"}],
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "mcp__plugin_agent-toolkit_agents_server__list",
                            "id": "claude-list",
                            "input": {},
                        }
                    ],
                },
            },
            {
                "type": "user",
                "mcpMeta": {"structuredContent": {"engine": "codex", "threadId": missing_session_id}},
                "toolUseResult": {"engine": "codex", "session_id": missing_session_id},
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "claude-list", "content": "完了"}],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys, raw=True)
    assert not _events_by_kind(events, "stats-agent-thread")
    assert not _events_by_kind(events, "unresolved-delegation")
    assert not _events_by_kind(events, "unresolved-record")


def test_stats_does_not_discover_session_from_claude_plugin_kill_tool_call(
    tmp_path: pathlib.Path,
    capsys,
) -> None:
    """Claude transcriptの`kill`入力は新しい委譲先の発見元にしない。"""
    session_id = "claude-session-kill-11111111"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "timestamp": "2026-08-19T00:00:00Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "mcp__plugin_agent-toolkit_agents_server__kill",
                            "id": "call-claude-kill",
                            "input": {"engine": "claude", "session_id": session_id},
                        }
                    ],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    assert not _events_by_kind(events, "stats-agent-thread")
    assert not _events_by_kind(events, "unresolved-delegation")
    assert not _events_by_kind(events, "unresolved-record")


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


def test_stats_resolves_runtime_unspecified_thread_once(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """実行系未指定の識別子を実体から解決し、実行系付きの再出現と重複させない。"""
    thread_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    codex_home = tmp_path / "codex"
    _write_rollout(
        codex_home,
        thread_id,
        [("2026-08-19T00:00:01Z", {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7})],
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
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
                            "name": "mcp__agents_server__start",
                            "id": "unspecified",
                            "input": {"session_id": thread_id},
                        }
                    ],
                },
            },
            _codex_tool_result_entry("2026-08-19T00:00:01Z", "unspecified", thread_id, engine=None),
            _codex_tool_use_entry("2026-08-19T00:00:02Z", "specified", thread_id),
            _codex_tool_result_entry("2026-08-19T00:00:02Z", "specified", thread_id),
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    assert not _events_by_kind(events, "unresolved-record")
    assert [event["session_id"] for event in _events_by_kind(events, "stats-agent-thread")] == [thread_id]


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


def _codex_tool_result_entry(
    timestamp: str,
    call_id: str,
    thread_id: str,
    *,
    engine: str | None = "codex",
) -> dict:
    """Claude形式のagents_server終端結果を作成する。"""
    result = {"threadId": thread_id}
    if engine is not None:
        result["engine"] = engine
    return {
        "type": "user",
        "timestamp": timestamp,
        "mcpMeta": {"structuredContent": result},
        "toolUseResult": result,
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": call_id, "content": json.dumps(result)}],
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
        entries.append(_codex_tool_result_entry("2026-08-19T00:00:01Z", f"call-{index}", thread_id))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(tmp_path, entries)

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    threads = _events_by_kind(events, "stats-agent-thread")
    assert [event["thread"] for event in threads] == list(reversed(thread_ids))
    assert [event["tokens"]["total_tokens"] for event in threads] == list(range(22, 1, -1))


def test_stats_collects_thread_ids_from_every_subagent(tmp_path: pathlib.Path, monkeypatch, capsys) -> None:
    """全てのサブエージェント発の委譲を`agent`キー付きで出力し、種別による除外を行わない。"""
    normal_thread = "55555555-5555-4555-8555-555555555555"
    other_thread = "66666666-6666-4666-8666-666666666666"
    codex_home = tmp_path / "codex"
    _write_rollout(
        codex_home,
        normal_thread,
        [("2026-08-19T00:00:02Z", {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7})],
    )
    _write_rollout(
        codex_home,
        other_thread,
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
        [
            _codex_tool_use_entry("2026-08-19T00:00:01Z", "call-normal", normal_thread),
            _codex_tool_result_entry("2026-08-19T00:00:01Z", "call-normal", normal_thread),
        ],
        {"agentType": "Explore"},
    )
    _write_subagent(
        subagents,
        "agent-other",
        [
            _codex_tool_use_entry("2026-08-19T00:00:01Z", "call-other", other_thread),
            _codex_tool_result_entry("2026-08-19T00:00:01Z", "call-other", other_thread),
        ],
        {"agentType": "general-purpose"},
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    threads = _events_by_kind(events, "stats-agent-thread")
    assert sorted(event["thread"] for event in threads) == sorted([normal_thread, other_thread])
    assert {event["thread"]: event["agent"] for event in threads} == {
        normal_thread: "agent-normal",
        other_thread: "agent-other",
    }


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
            _codex_tool_result_entry("2026-08-19T00:00:01Z", "call-main", main_thread),
        ],
    )
    _write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-sub",
        [
            {"type": "user", "timestamp": "2026-08-19T00:00:01Z", "message": {"role": "user", "content": "委譲"}},
            {"type": "user", "timestamp": "2026-08-19T00:00:01Z", "message": {"role": "user", "content": "追記"}},
            _codex_tool_use_entry("2026-08-19T00:00:01Z", "call-sub", sub_thread),
            _codex_tool_result_entry("2026-08-19T00:00:01Z", "call-sub", sub_thread),
        ],
        {"agentType": "Explore"},
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys)
    threads = {event["thread"]: event for event in _events_by_kind(events, "stats-agent-thread")}
    assert threads[main_thread]["line"] == 3
    assert "agent" not in threads[main_thread]
    assert threads[sub_thread]["agent"] == "agent-sub"
    assert "line" not in threads[sub_thread]

    assert evidence.main([str(transcript), "--detail", "3"]) == 0
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
            _codex_tool_result_entry("2026-08-19T00:00:02Z", "call-1", thread_id),
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


def _compaction_entry(timestamp: str, metadata: dict | None = None) -> dict:
    """Claude Codeのコンパクション境界レコードを作成する。"""
    entry: dict = {"type": "system", "subtype": "compact_boundary", "timestamp": timestamp}
    if metadata is not None:
        entry["compactMetadata"] = metadata
    return entry


# pylint: disable=protected-access
def test_stats_reports_critical_path() -> None:
    """並行threadの重複を二重計上せず、測定不能なthreadも分けて返す。"""

    def collected(
        record_id: str, timestamps: list[str], *, role: Literal["main", "subagent", "session"] = "session"
    ) -> evidence._CollectedRecord:
        return evidence._CollectedRecord(
            record_id,
            pathlib.Path(f"/{record_id}"),
            [evidence._Record(index + 1, "", {"timestamp": timestamp}) for index, timestamp in enumerate(timestamps)],
            "codex",
            "main" if role == "session" else None,
            1 if role == "session" else None,
            None,
            role,
        )

    events = evidence._stats_events(
        [
            collected("main", ["2026-09-02T00:00:00Z", "2026-09-02T00:01:00Z"], role="main"),
            collected("codex:first", ["2026-09-02T00:00:10Z", "2026-09-02T00:00:30Z"]),
            collected("codex:second", ["2026-09-02T00:00:20Z", "2026-09-02T00:00:40Z"]),
            collected("codex:unmeasured", []),
        ],
        pathlib.Path("/missing-compaction-records"),
    )

    assert _events_by_kind(events, "stats-critical-path") == [
        {
            "kind": "stats-critical-path",
            "elapsed_seconds": 60.0,
            "main_only_seconds": 30.0,
            "overlap_seconds": 10.0,
            "segments": [
                {"owner": "first", "exclusive_seconds": 10.0},
                {"owner": "second", "exclusive_seconds": 10.0},
            ],
            "unmeasured_threads": ["unmeasured"],
        }
    ]


# pylint: enable=protected-access


def test_stats_reports_compaction_events_for_both_runtimes(tmp_path: pathlib.Path, capsys) -> None:
    """メイン記録とサブエージェント記録のコンパクションを全件数え、記録に無い欄を補わない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "timestamp": "2026-09-02T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
            _compaction_entry(
                "2026-09-02T00:01:00Z",
                {"trigger": "auto", "preTokens": 493099, "postTokens": 14213, "durationMs": 201674},
            ),
            _compaction_entry("2026-09-02T00:02:00Z", {"trigger": "manual"}),
        ],
    )
    _write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-child",
        [_compaction_entry("2026-09-02T00:03:00Z", {"trigger": "auto", "durationMs": 1300})],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys, raw=True)
    assert _events_by_kind(events, "stats-compaction") == [
        {
            "kind": "stats-compaction",
            "record": "agent-child",
            "line": 1,
            "engine": "claude",
            "timestamp": "2026-09-02T00:03:00Z",
            "trigger": "auto",
            "duration_seconds": 1.3,
        },
        {
            "kind": "stats-compaction",
            "record": "main",
            "line": 2,
            "engine": "claude",
            "timestamp": "2026-09-02T00:01:00Z",
            "trigger": "auto",
            "pre_tokens": 493099,
            "post_tokens": 14213,
            "duration_seconds": 201.7,
        },
        {
            "kind": "stats-compaction",
            "record": "main",
            "line": 3,
            "engine": "claude",
            "timestamp": "2026-09-02T00:02:00Z",
            "trigger": "manual",
        },
    ]
    total = _events_by_kind(events, "stats-compaction-total")[0]
    assert total == {
        "kind": "stats-compaction-total",
        "count": 3,
        "by_record": {"main": 2, "agent-child": 1},
        "total_duration_seconds": 203.0,
        "duration_unknown_count": 1,
    }
    assert list(total["by_record"]) == ["main", "agent-child"]


def test_stats_reports_codex_compaction_records(tmp_path: pathlib.Path, capsys) -> None:
    """Codex形式のコンパクションを`codex`として数え、所要時間の欄を付けない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            _codex_token_count_entry("2026-09-02T00:00:00Z", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
            {
                "type": "compacted",
                "timestamp": "2026-09-02T00:01:00Z",
                "payload": {"message": "", "window_number": 1},
            },
        ],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys, raw=True)
    assert _events_by_kind(events, "stats-compaction") == [
        {
            "kind": "stats-compaction",
            "record": "main",
            "line": 2,
            "engine": "codex",
            "timestamp": "2026-09-02T00:01:00Z",
        }
    ]
    assert _events_by_kind(events, "stats-compaction-total")[0] == {
        "kind": "stats-compaction-total",
        "count": 1,
        "by_record": {"main": 1},
        "total_duration_seconds": 0.0,
        "duration_unknown_count": 1,
    }


def test_stats_assigns_codex_compaction_measurements_in_occurrence_order(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """同じthreadの計測記録を発生順に対応付け、残りは所要時間不明として数える。"""
    thread_id = "019945be-498f-70f2-a964-93e2c8d38954"
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "session_meta", "payload": {"id": thread_id}},
            _codex_token_count_entry("2026-09-02T00:00:00Z", {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
            {"type": "compacted", "timestamp": "2026-09-02T00:01:00Z", "payload": {"window_number": 1}},
            {"type": "compacted", "timestamp": "2026-09-02T00:02:00Z", "payload": {"window_number": 2}},
        ],
    )
    record_dir = tmp_path / "compaction"
    record_dir.mkdir()
    (record_dir / f"{thread_id}.jsonl").write_text(
        json.dumps(
            {
                "version": 1,
                "thread_id": thread_id,
                "item_id": "item-1",
                "started_at_ms": 1000,
                "completed_at_ms": 3234,
                "duration_seconds": 2.2,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert evidence.main([str(transcript), "--stats", "--compaction-record-dir", str(record_dir)]) == 0

    events = _read_jsonl(capsys, raw=True)
    compactions = _events_by_kind(events, "stats-compaction")
    assert compactions[0]["duration_seconds"] == 2.2
    assert "duration_seconds" not in compactions[1]
    assert _events_by_kind(events, "stats-compaction-total")[0] == {
        "kind": "stats-compaction-total",
        "count": 2,
        "by_record": {"main": 2},
        "total_duration_seconds": 2.2,
        "duration_unknown_count": 1,
    }


def test_stats_reports_zero_compaction_total_without_records(tmp_path: pathlib.Path, capsys) -> None:
    """コンパクションの記録が無い場合は件数0の集計だけを返す。"""
    transcript = _write_transcript(
        tmp_path,
        [{"type": "user", "timestamp": "2026-09-02T00:00:00Z", "message": {"role": "user", "content": "依頼"}}],
    )

    assert evidence.main([str(transcript), "--stats"]) == 0
    events = _read_jsonl(capsys, raw=True)
    assert not _events_by_kind(events, "stats-compaction")
    assert _events_by_kind(events, "stats-compaction-total") == [
        {
            "kind": "stats-compaction-total",
            "count": 0,
            "by_record": {},
            "total_duration_seconds": 0.0,
            "duration_unknown_count": 0,
        }
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


@pytest.mark.parametrize(
    ("query_args", "event_index", "expected_event"),
    [
        (
            ["--warn"],
            0,
            {
                "kind": "warning",
                "line": 4,
                "text": "warning: successful command warning",
                "tool": "call-1",
            },
        ),
        (
            ["--stats"],
            0,
            {
                "kind": "stats-total",
                "tokens": {},
                "subagent_count": 0,
                "agent_thread_count": 0,
                "agent_thread_counts": {},
            },
        ),
        (
            ["--hook-notices"],
            0,
            {
                "kind": "hook-notice",
                "hook": "agent-toolkit/posttooluse",
                "hook_name": "PostToolUse:Bash",
                "tag": "warn",
                "kind_text": "warn: 成功結果を確認する",
                "count": 1,
            },
        ),
        (
            ["--detail", "4"],
            0,
            {
                "kind": "detail",
                "line": 4,
                "timestamp": None,
                "tool": "call-1",
                "text": "warning: successful command warning",
            },
        ),
    ],
)
def test_query_event_is_a_stable_problem_locator(
    query_args: list[str],
    event_index: int,
    expected_event: dict,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """既存照会結果の同じ位置から統計、警告、hook通知及び詳細を再取得する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "locatorへ含めない利用者本文1"}},
            {"type": "user", "message": {"role": "user", "content": "locatorへ含めない利用者本文2"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": "call-1", "input": {"command": "true"}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call-1", "content": "warning: successful command warning"}
                    ],
                },
            },
            _hook_attachment(
                {
                    "type": "hook_system_message",
                    "hookName": "PostToolUse:Bash",
                    "toolUseID": "call-1",
                    "content": "[auto-generated: agent-toolkit/posttooluse][warn] warn: 成功結果を確認する",
                }
            ),
        ],
    )

    locator = {"event_index": event_index}

    assert evidence.main([str(transcript), *query_args]) == 0
    first = _read_jsonl(capsys)
    assert evidence.main([str(transcript), *query_args]) == 0
    second = _read_jsonl(capsys)

    assert first == second
    assert second[locator["event_index"]] == expected_event
    assert set(locator) == {"event_index"}
    assert "successful command warning" not in json.dumps(locator)
    assert "locatorへ含めない利用者本文" not in json.dumps(locator, ensure_ascii=False)


def test_multi_line_detail_query_keeps_each_problem_locator_stable(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """先行行が複数イベントを返しても、同じ完全引数列なら各証拠位置を再取得する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Bash", "id": "call-1", "input": {"command": "true"}},
                        {"type": "tool_use", "name": "Read", "id": "call-2", "input": {"file_path": "/tmp/x"}},
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "別イベント"}],
                },
            },
        ],
    )
    query = "--detail 1 --detail 2"
    query_args = query.split()
    locators = [{"event_index": index} for index in range(3)]

    assert evidence.main([str(transcript), *query_args]) == 0
    first = _read_jsonl(capsys)
    assert evidence.main([str(transcript), *query_args]) == 0
    second = _read_jsonl(capsys)

    assert first == second
    assert [second[locator["event_index"]] for locator in locators] == first
    assert [event["line"] for event in second] == [1, 1, 2]
    assert second[2] == {"kind": "detail", "line": 2, "timestamp": None, "tool": "call-1", "text": "別イベント"}
    assert query == "--detail 1 --detail 2"
    assert "別イベント" not in query
    assert all(set(locator) == {"event_index"} for locator in locators)

    assert evidence.main([str(transcript), "--detail", "2"]) == 0
    individual = _read_jsonl(capsys)
    assert individual[0] == second[2]
    assert individual[0] != second[0]


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


def test_hook_notices_mode_counts_each_marker_of_a_multi_marker_body(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """1つの本文が複数の標識を持つ場合、標識ごとに別の発生源として数える。"""
    body = (
        "[auto-generated: agent-toolkit/pretooluse][warn] 入力を補正した "
        "[auto-generated: agent-toolkit/pretooluse][block] 固定待機を検出した"
    )
    transcript = _write_transcript(
        tmp_path,
        [
            _hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "content": [body],
                }
            )
        ],
    )

    assert evidence.main([str(transcript), "--hook-notices"]) == 0

    events = _read_jsonl(capsys)
    assert sorted(event["tag"] for event in events[:-1]) == ["block", "warn"]
    assert {event["kind_text"] for event in events[:-1]} == {"入力を補正した", "固定待機を検出した"}
    assert events[-1] == {"kind": "summary", "count": 2}


def test_hook_notices_mode_ignores_nested_delivery_and_deduplicates_same_call(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    body = (
        '<agent-toolkit-auto-inserted kind="notice" source="hook/a">参考</agent-toolkit-auto-inserted>'
        '<agent-toolkit-auto-inserted kind="warn" source="hook/b">理由B'
        '<agent-toolkit-auto-inserted kind="rules-main" source="agent-toolkit">規範</agent-toolkit-auto-inserted>'
        "</agent-toolkit-auto-inserted>"
        '<agent-toolkit-auto-inserted kind="block" source="hook/c">理由C</agent-toolkit-auto-inserted>'
    )
    transcript = _write_transcript(
        tmp_path,
        [
            _hook_attachment(
                {"type": "hook_additional_context", "hookName": "PreToolUse:Bash", "toolUseID": "call-1", "content": [body]}
            ),
            _hook_attachment(
                {"type": "hook_additional_context", "hookName": "PreToolUse:Bash", "toolUseID": "call-1", "content": [body]}
            ),
        ],
    )

    assert evidence.main([str(transcript), "--hook-notices"]) == 0

    events = _read_jsonl(capsys)
    assert [(event["hook"], event["tag"], event["count"]) for event in events[:-1]] == [
        ("hook/a", "notice", 1),
        ("hook/b", "warn", 1),
        ("hook/c", "block", 1),
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


def test_bundle_writes_every_scan_to_files_and_returns_summary_only(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """集約実行が全走査と一次選別候補を保存し、標準出力へ要約だけを返す。"""
    missing_thread = "99999999-9999-4999-8999-999999999999"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "依頼"}},
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Base directory for this skill: /skills/demo"}],
                },
            },
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "作業中"}]}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Bash", "id": "call-1", "input": {}},
                        {"type": "tool_use", "name": "Bash", "id": "call-2", "input": {}},
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "is_error": True, "content": "失敗の詳細"}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-2", "content": "warning: 警告が出た"}],
                },
            },
            _hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PostToolUse:Bash",
                    "toolUseID": "call-2",
                    "content": ["[auto-generated: agent-toolkit/posttooluse][notice] 通知本文"],
                }
            ),
            {
                "type": "user",
                "toolUseResult": {"status": "completed", "agentId": "agent-1", "summary": "完了報告"},
                "message": {"role": "user", "content": "<task-notification>内部通知</task-notification>"},
            },
            _codex_tool_use_entry("2026-09-02T00:00:00Z", "call-3", missing_thread),
            _codex_tool_result_entry("2026-09-02T00:00:00Z", "call-3", missing_thread),
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "最終結果"}]}},
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    bundle_events = _read_jsonl(capsys, raw=True)

    stored: dict[str, list[dict]] = {}
    for filename, single_args in (
        ("timeline.jsonl", []),
        ("warnings.jsonl", ["--warn"]),
        ("stats.jsonl", ["--stats"]),
        ("hook-notices.jsonl", ["--hook-notices"]),
    ):
        path = bundle_dir / filename
        stored[filename] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert evidence.main([str(transcript), *single_args]) == 0
        single = [event for event in _read_jsonl(capsys, raw=True) if event["kind"] != "unresolved-record"]
        assert stored[filename] == single
        assert {"kind": "bundle-file", "path": str(path.resolve()), "count": len(single)} in bundle_events

    candidates = [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    candidate_items = [item for item in candidates if item["kind"] == "candidate"]
    assert [item["candidate_id"] for item in candidate_items] == ["c0001", "c0002"]
    assert [(item["locators"], item["candidate_kind"]) for item in candidate_items] == [
        ([{"record": "main", "line": 5}], "tool-failure"),
        ([{"record": "main", "line": 6}], "warning"),
    ]
    assert candidates[-1]["excluded"] == {"hook-notice-informational": 1, "initial-request": 1}
    assert candidates[-1]["included_locator_count"] == 2
    assert {"kind": "bundle-file", "path": str((bundle_dir / "candidates.jsonl").resolve()), "count": 3} in bundle_events
    evidence_index = [
        json.loads(line) for line in (bundle_dir / "candidate-evidence.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [item["candidate_id"] for item in evidence_index] == ["c0001", "c0002"]
    assert [item["locators"] for item in evidence_index] == [item["locators"] for item in candidate_items]
    assert all(item["evidence_count"] > 0 and item["total_chars"] > 0 for item in evidence_index)
    candidate_evidence = [json.loads((bundle_dir / item["path"]).read_text(encoding="utf-8")) for item in evidence_index]
    assert [item["candidate_id"] for item in candidate_evidence] == ["c0001", "c0002"]
    assert all(item["events"] for item in candidate_evidence)
    # 失敗したツール結果の本文は切り詰めず、実行時警告など定型本文の種別だけに上限を残す。
    assert [item["text_limit"] for item in candidate_evidence] == [None, 2000]
    assert all(item["source_chars"] > 0 for item in candidate_evidence)
    assert {
        "kind": "bundle-file",
        "path": str((bundle_dir / "candidate-evidence.jsonl").resolve()),
        "count": 2,
    } in bundle_events
    metrics = next(item for item in bundle_events if item["kind"] == "bundle-evidence-metrics")
    assert metrics["candidate_evidence_lines"] == 2
    assert metrics["decision_count"] == 2
    assert metrics["analysis_group_count"] == 2
    assert metrics["full_scan_lines"] > metrics["candidate_evidence_lines"]
    assert metrics["full_scan_bytes"] > 0
    assert metrics["candidate_evidence_bytes"] > 0

    assert {event["event_kind"]: event["count"] for event in bundle_events if event["kind"] == "bundle-kind-count"} == {
        "user": 1,
        "skill-invocation": 1,
        "assistant": 1,
        "failed-tool": 1,
        "agent-completion": 1,
        "final-result": 1,
    }
    serialized = json.dumps(bundle_events, ensure_ascii=False)
    assert "作業中" not in serialized
    assert "Base directory for this skill" not in serialized
    locators = [event for event in bundle_events if event["kind"] == "bundle-locator"]
    assert [{key: value for key, value in event.items() if key != "timestamp"} for event in locators] == [
        {"kind": "bundle-locator", "event_kind": "user", "record": "main", "line": 1},
        {"kind": "bundle-locator", "event_kind": "failed-tool", "record": "main", "line": 5, "text": "失敗の詳細"},
        {"kind": "bundle-locator", "event_kind": "agent-completion", "record": "main", "line": 8, "text": "agent-1: 完了報告"},
        {"kind": "bundle-locator", "event_kind": "final-result", "record": "main", "line": 11, "text": "最終結果"},
    ]
    # 区間の境界の時刻を`--detail`の追加照会なしで確定できるよう、全イベントが`timestamp`を持つ。
    assert all("timestamp" in event for event in locators)
    assert [event for event in bundle_events if event["kind"] == "bundle-warning-group"] == [
        {
            "kind": "bundle-warning-group",
            "text": "warning: 警告が出た",
            "count": 1,
            "samples": [{"record": "main", "line": 6}],
        }
    ]
    assert [event for event in bundle_events if str(event["kind"]).startswith("stats-")] == []
    assert bundle_events[-1] == {"kind": "unresolved-record", "record": missing_thread, "line": 10}
    assert [event for event in bundle_events if event["kind"] == "hook-notice"] == []


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

    events = evidence._candidate_events([], [], notices)  # pylint: disable=protected-access
    candidates = [event for event in events if event["kind"] == "candidate"]
    first_emitter = [candidate for candidate in candidates if candidate["event_key"][0] == "agent-toolkit/pretooluse"]

    assert [candidate["occurrence_count"] for candidate in first_emitter] == [7, 6, 5, 4, 3]
    assert all(candidate["count"] == 1 and len(candidate["locators"]) == 1 for candidate in candidates)
    assert any(candidate["event_key"][0] == "dotfiles/pretooluse" for candidate in candidates)
    assert events[-1]["included_locator_count"] == 6
    assert events[-1]["excluded"] == {"hook-notice-detail-budget": 23}


def test_bundle_clips_locator_body_and_groups_warnings_by_leading_text(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """位置イベントの本文を冒頭200文字へ切り詰め、冒頭120文字が同じ警告を1分類へまとめる。"""
    body = "あ" * 300
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "name": "Bash", "id": f"call-{index}", "input": {}} for index in range(1, 5)
                    ],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "is_error": True, "content": body}],
                },
            },
            *(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": f"call-{index}", "content": f"warning: {'い' * 130}{index}"}
                        ],
                    },
                }
                for index in range(2, 5)
            ),
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0

    events = _read_jsonl(capsys, raw=True)
    locator = next(event for event in events if event["kind"] == "bundle-locator")
    assert locator["text"] == "あ" * 200 + "…[省略]"
    assert [event for event in events if event["kind"] == "bundle-warning-group"] == [
        {
            "kind": "bundle-warning-group",
            "text": ("warning: " + "い" * 130)[:120],
            "count": 3,
            "samples": [{"record": "main", "line": 3}, {"record": "main", "line": 4}, {"record": "main", "line": 5}],
        }
    ]


def test_bundle_writes_one_evidence_file_per_candidate(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """候補数が多い場合も、全候補IDへ対応する個別ファイルを索引から解決できる。"""
    entries: list[dict[str, object]] = [{"type": "user", "message": {"role": "user", "content": "最初の依頼"}}]
    for index in range(12):
        entries.append(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": f"call-{index}", "is_error": True, "content": f"失敗{index}"}
                    ],
                },
            }
        )
    transcript = _write_transcript(tmp_path, entries)
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0

    capsys.readouterr()
    evidence_index = [
        json.loads(line) for line in (bundle_dir / "candidate-evidence.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert evidence_index
    for item in evidence_index:
        body = json.loads((bundle_dir / item["path"]).read_text(encoding="utf-8"))
        assert body["candidate_id"] == item["candidate_id"]
        assert body["events"]


def test_candidate_evidence_file_holds_only_its_own_candidate(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """1候補の証拠が大きい場合も、個別ファイルへ別候補の証拠を混ぜない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "最初の依頼"}},
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "is_error": True, "content": "あ" * 4000}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-2", "is_error": True, "content": "別の失敗"}],
                },
            },
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0

    capsys.readouterr()
    evidence_index = [
        json.loads(line) for line in (bundle_dir / "candidate-evidence.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(evidence_index) >= 2
    for item in evidence_index:
        body = json.loads((bundle_dir / item["path"]).read_text(encoding="utf-8"))
        assert {locator["line"] for locator in body["locators"]} == {locator["line"] for locator in item["locators"]}
        assert all(event.get("record") is not None for event in body["events"])


def test_bundle_writes_conversation_of_main_utterances_with_full_text_detail(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """会話の流れはメイン記録の利用者発話とアシスタント発話だけを全文で載せ、記録位置の`--detail`で全文を返す。

    振り返りは会話の流れからセッション全体の遠回りや是正を探すため、配送本文、実行環境の挿入、
    スキル展開、ツール呼び出しとツール結果が混ざると利用者の発話と区別できなくなる。
    長い発話は会話の流れの表示で先頭と末尾だけになるため、記録位置の照会が全文を返す必要がある。
    """
    long_reply = "長い応答の先頭。" + "あ" * 1500 + "長い応答の末尾。"
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "振り返りを速くしたい"}},
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": '<agent-toolkit-auto-inserted kind="notice">配送</agent-toolkit-auto-inserted>',
                },
            },
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": "実行環境の注記"}},
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "text", "text": "Base directory for this skill: /x"}]},
            },
            {"type": "user", "message": {"role": "user", "content": "<system-reminder>注入</system-reminder>"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "調べます。"},
                        {"type": "tool_use", "id": "call-1", "name": "Bash", "input": {"command": "ls"}},
                    ],
                },
            },
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "a.txt"}]},
            },
            {"type": "user", "message": {"role": "user", "content": "全文抽出すべきでは？"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": long_reply}]}},
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    capsys.readouterr()

    conversation = [json.loads(line) for line in (bundle_dir / "conversation.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(item["role"], item["line"], item["text"]) for item in conversation] == [
        ("user", 1, "振り返りを速くしたい"),
        ("assistant", 6, "調べます。"),
        ("user", 8, "全文抽出すべきでは？"),
        ("assistant", 9, long_reply),
    ]

    assert evidence.main([str(transcript), "--detail", "main:9"]) == 0
    assert _read_jsonl(capsys) == [{"kind": "detail", "line": 9, "timestamp": None, "role": "assistant", "text": long_reply}]


@pytest.mark.parametrize("existing", [False, True])
def test_bundle_rejects_output_that_is_not_an_existing_directory(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    existing: bool,
) -> None:
    """出力先が実在しない場合とディレクトリでない場合は、ファイルを生成せず終了コード2で返す。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])
    destination = tmp_path / ("regular.txt" if existing else "missing")
    if existing:
        destination.write_text("", encoding="utf-8")

    assert evidence.main([str(transcript), "--bundle", str(destination)]) == 2

    events = _read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["error"]
    assert str(destination) in events[0]["text"]
    assert list(tmp_path.rglob("*.jsonl")) == [transcript]


@pytest.mark.parametrize(
    "conflicting",
    [["--warn"], ["--grep", "依頼"], ["--detail", "1"], ["--stats"], ["--hook-notices"]],
)
def test_bundle_is_exclusive_with_other_query_modes(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    conflicting: list[str],
) -> None:
    """集約実行と他の照会モードの併用は引数誤用として終了コード2で返す。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir), *conflicting]) == 2

    events = _read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["error"]
    assert "--bundle" in events[0]["text"]
    assert not list(bundle_dir.iterdir())


def test_claude_subagent_record_keeps_normal_entries(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "委譲された依頼"}},
            {
                "type": "assistant",
                "isSidechain": True,
                "message": {"role": "assistant", "content": [{"type": "text", "text": "実装を完了した"}]},
            },
        ],
    )

    events = evidence.load_and_extract(str(transcript))

    assert [event["text"] for event in events] == ["委譲された依頼", "実装を完了した"]


def test_claude_subagent_handback_message_becomes_final_result(tmp_path: pathlib.Path) -> None:
    """`SubagentHandback`で渡した報告本文を最終結果とし、送信後の定型文を最終結果にしない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "委譲された依頼"}},
            {
                "type": "assistant",
                "isSidechain": True,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "SubagentHandback",
                            "input": {"message": "判定: 適合\n根拠: 全行を確認した"},
                        }
                    ],
                },
            },
            {
                "type": "assistant",
                "isSidechain": True,
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Report delivered."}]},
            },
        ],
    )

    events = evidence.load_and_extract(str(transcript))

    assert [(event["kind"], event["text"]) for event in events] == [
        ("user", "委譲された依頼"),
        ("final-result", "判定: 適合\n根拠: 全行を確認した"),
        ("assistant", "Report delivered."),
    ]


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


def _assistant_text(text: str) -> dict[str, object]:
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def test_bundle_excludes_interim_text_of_running_delegate(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """最後の行動がツール呼び出しである委譲先（抽出時点で稼働中）の途中発話を委譲返却の候補にしない。"""
    texts = _bundle_delegate_return_texts(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "委譲された依頼"}},
            _assistant_text("本文の起草に入ります。"),
            _execution_tool_use("call-1"),
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "ok"}]},
            },
            _assistant_text("status: needs_escalation\nreason: 途中の見込み"),
            _execution_tool_use("call-2"),
        ],
    )
    capsys.readouterr()

    assert texts == []


def test_bundle_keeps_final_text_of_finished_delegate(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """最後の本文の後にツール呼び出しが無い委譲先では、その本文を委譲返却の候補にし、途中発話は候補にしない。"""
    texts = _bundle_delegate_return_texts(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "委譲された依頼"}},
            _assistant_text("調査を始めます。"),
            _execution_tool_use("call-1"),
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "ok"}]},
            },
            _assistant_text("調査結果を報告する。\n対象の関数は3件だった。"),
        ],
    )
    capsys.readouterr()

    assert texts == ["調査結果を報告する。\n対象の関数は3件だった。"]


@pytest.mark.parametrize(
    ("final_text", "expected"),
    [
        ("status: completed\noutput_file: /tmp/out.md", []),
        (
            "統合完了\nmerged_head: abc1234\n想定外事象: 統合後の検査が1件失敗した",
            ["統合完了\nmerged_head: abc1234\n想定外事象: 統合後の検査が1件失敗した"],
        ),
    ],
)
def test_bundle_excludes_delegate_returns_that_only_report_success(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    final_text: str,
    expected: list[str],
) -> None:
    """`--bundle`の候補から成功の定型形式だけの返却を除き、想定外事象を持つ定型返却は残す。"""
    texts = _bundle_delegate_return_texts(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "委譲された依頼"}},
            _assistant_text(final_text),
        ],
    )
    capsys.readouterr()

    assert texts == expected


def test_bundle_reports_hook_blocked_tool_call_as_hook_notice(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """hookが遮断したツール呼び出しの失敗を、`--bundle`の候補でhook通知として発生源と区分を付けて返す。"""
    blocked = (
        "PreToolUse:TaskStop hook error: [uv run hook.py pretooluse]: "
        '<agent-toolkit-auto-inserted source="agent-toolkit/pretooluse" kind="block">'
        "blocked: 所有記録の無いタスクの停止</agent-toolkit-auto-inserted>"
    )
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "依頼"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "call-1", "name": "TaskStop", "input": {"task_id": "t1"}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "is_error": True, "content": blocked}],
                },
            },
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    capsys.readouterr()

    records = [json.loads(line) for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    candidates = [record for record in records if record["kind"] == "candidate"]
    assert [(record["candidate_kind"], record["event_key"][0], record["event_key"][2]) for record in candidates] == [
        ("hook-notice", "agent-toolkit/pretooluse", "block")
    ]
    assert "所有記録の無いタスクの停止" in candidates[0]["text"]


def test_claude_main_record_keeps_only_completion_from_subagent_entries(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "依頼"}},
            {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "委譲された依頼"}},
            {
                "type": "assistant",
                "isSidechain": True,
                "message": {"role": "assistant", "content": [{"type": "text", "text": "実装を完了した"}]},
            },
        ],
    )

    events = evidence.load_and_extract(str(transcript))

    assert [event["text"] for event in events] == ["依頼"]


def _write_jsonl(path: pathlib.Path, entries: list[dict]) -> None:
    """任意の記録正本をテスト用の絶対パスへ書く。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n", encoding="utf-8")


def test_delegate_record_with_ambiguous_thread_id_is_unresolved(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """委譲先rolloutの複数一致では先頭を採用せず未解決として報告する。"""
    thread_id = "55555555-5555-4555-8555-555555555555"
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [
            _codex_tool_use_entry("2026-09-01T00:00:00Z", "ambiguous", thread_id),
            _codex_tool_result_entry("2026-09-01T00:00:00Z", "ambiguous", thread_id),
        ],
    )
    for day, text in (("01", "混入してはならない記録1"), ("02", "混入してはならない記録2")):
        _write_jsonl(
            codex_home / "sessions" / "2026" / "09" / day / f"rollout-child-{thread_id}.jsonl",
            [{"type": "response_item", "payload": {"type": "message", "role": "user", "content": text}}],
        )

    assert evidence.main([str(transcript)]) == 0

    events = _read_jsonl(capsys, raw=True)
    assert events[-1] == {"kind": "unresolved-record", "record": thread_id, "line": 2}
    serialized = json.dumps(events, ensure_ascii=False)
    assert "混入してはならない記録1" not in serialized
    assert "混入してはならない記録2" not in serialized


def test_explicit_codex_home_applies_to_parent_and_delegate_records(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """明示した保存先から親rolloutと委譲先rolloutを再帰収集する。"""
    parent_id = "77777777-7777-4777-8777-777777777777"
    child_id = "88888888-8888-4888-8888-888888888888"
    explicit_home = tmp_path / "explicit-codex"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "environment-codex"))
    rollout_dir = explicit_home / "sessions" / "2026" / "09" / "01"
    _write_jsonl(
        rollout_dir / f"rollout-parent-{parent_id}.jsonl",
        [
            _codex_tool_use_entry("2026-09-01T00:00:00Z", "parent-child", child_id),
            _codex_tool_result_entry("2026-09-01T00:00:00Z", "parent-child", child_id),
        ],
    )
    _write_jsonl(
        rollout_dir / f"rollout-child-{child_id}.jsonl",
        [
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": "明示先の委譲記録"},
            }
        ],
    )

    assert evidence.main(["--codex-thread-id", parent_id, "--codex-home", str(explicit_home)]) == 0

    events = _read_jsonl(capsys, raw=True)
    assert events == [
        {
            "kind": "user",
            "text": "明示先の委譲記録",
            "line": 1,
            "timestamp": None,
            "sequence": 1,
            "record": f"codex:{child_id}",
        }
    ]


def test_backup_only_thread_id_is_evidence_insufficient(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """backupに写しだけがあるthread IDは親・委譲先の両経路で証拠不足とする。"""
    thread_id = "66666666-6666-4666-8666-666666666666"
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    _write_jsonl(
        codex_home / "backups" / "transcripts" / f"rollout-backup-{thread_id}.jsonl",
        [{"type": "response_item", "payload": {"type": "message", "role": "user", "content": "backupの写し"}}],
    )

    assert evidence.main(["--codex-thread-id", thread_id]) == 2
    parent_events = _read_jsonl(capsys)
    assert parent_events == [
        {
            "kind": "error",
            "text": f"対象記録を解決できない: Codex thread ID {thread_id}に一致するrolloutが"
            f"{codex_home / 'sessions'}配下に無い",
        }
    ]

    transcript = _write_transcript(
        tmp_path,
        [
            _codex_tool_use_entry("2026-09-01T00:00:00Z", "backup-only", thread_id),
            _codex_tool_result_entry("2026-09-01T00:00:00Z", "backup-only", thread_id),
        ],
    )
    assert evidence.main([str(transcript)]) == 0
    delegate_events = _read_jsonl(capsys, raw=True)
    assert delegate_events[-1] == {"kind": "unresolved-record", "record": thread_id, "line": 2}
    assert "backupの写し" not in json.dumps(delegate_events, ensure_ascii=False)


def test_all_modes_recursively_scan_cross_engine_delegations(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """全照会が3段の実行系横断委譲と委譲先サブエージェントを1回で走査する。"""
    codex_a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    claude_b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    codex_c = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    missing = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    unrelated = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    home = tmp_path / "home"
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "main needle"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "mcp__agents_server__start",
                            "id": "main-a",
                            "input": {"engine": "codex", "threadId": codex_a},
                        }
                    ],
                },
            },
            _codex_tool_result_entry("2026-08-30T00:00:00Z", "main-a", codex_a),
        ],
    )
    _write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-root",
        [
            {"type": "user", "message": {"role": "user", "content": "root record"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": "root-warning", "input": {}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "root-warning", "content": "[warn] root warning"}],
                },
            },
        ],
    )

    rollout_dir = codex_home / "sessions" / "2026" / "08" / "30"
    _write_jsonl(
        rollout_dir / f"rollout-test-{codex_a}.jsonl",
        [
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": "codex-a needle"}},
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "mcp__agents_server__start",
                    "call_id": "exec-agents-server",
                    "arguments": {"engine": "claude"},
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "exec-agents-server",
                    "output": [
                        {"type": "input_text", "text": "Script completed"},
                        {"type": "input_text", "text": json.dumps({"session_id": claude_b})},
                    ],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "call_id": "exec-unrelated",
                    "input": "text('unrelated')",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "exec-unrelated",
                    "output": [{"type": "input_text", "text": json.dumps({"engine": "claude", "session_id": unrelated})}],
                },
            },
        ],
    )
    claude_path = home / ".claude" / "projects" / "repo" / f"{claude_b}.jsonl"
    _write_jsonl(
        claude_path,
        [
            {"type": "user", "message": {"role": "user", "content": "claude-b needle"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "mcp__agents_server__start",
                            "id": "b-c",
                            "input": {"engine": "codex", "threadId": codex_c},
                        }
                    ],
                },
            },
            _codex_tool_result_entry("2026-08-30T00:00:00Z", "b-c", codex_c),
            _hook_attachment(
                {
                    "type": "hook_system_message",
                    "hookName": "PostToolUse:Bash",
                    "toolUseID": "b-c",
                    "content": "[auto-generated: agent-toolkit/posttooluse][warn] nested notice",
                }
            ),
        ],
    )
    _write_jsonl(
        home / ".claude" / "projects" / "repo" / f"{unrelated}.jsonl",
        [{"type": "user", "message": {"role": "user", "content": "unrelated needle"}}],
    )
    _write_subagent(
        claude_path.with_suffix("") / "subagents",
        "agent-child",
        [
            _assistant_usage_entry("2026-08-30T00:00:00Z", "child", _usage(2, 3)),
            {"type": "user", "message": {"role": "user", "content": "child record"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": "child-warning", "input": {}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "child-warning", "content": "[warning] child warning"}],
                },
            },
        ],
    )
    _write_jsonl(
        rollout_dir / f"rollout-test-{codex_c}.jsonl",
        [
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "codex-c needle"}},
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "mcp__agents_server__start",
                    "call_id": "missing",
                    "arguments": {"engine": "codex"},
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "missing",
                    "output": {"engine": "codex", "session_id": missing},
                },
            },
        ],
    )

    assert evidence.main([str(transcript)]) == 0
    default_events = _read_jsonl(capsys, raw=True)
    assert {event["record"] for event in default_events if event["kind"] != "unresolved-record"} == {
        "main",
        "agent-root",
        f"codex:{codex_a}",
        f"claude:{claude_b}",
        f"claude:{claude_b}/agent-child",
        f"codex:{codex_c}",
    }
    assert default_events[-1] == {"kind": "unresolved-record", "record": missing, "line": 3}
    assert unrelated not in {event["record"] for event in default_events}

    assert evidence.main([str(transcript), "--warn"]) == 0
    warning_events = _read_jsonl(capsys, raw=True)
    assert [event["record"] for event in warning_events if event["kind"] == "warning"] == [
        "agent-root",
        f"claude:{claude_b}",
        f"claude:{claude_b}/agent-child",
    ]

    assert evidence.main([str(transcript), "--grep", "needle"]) == 0
    grep_events = _read_jsonl(capsys, raw=True)
    assert {event["record"] for event in grep_events if event["kind"] == "match"} == {
        "main",
        f"codex:{codex_a}",
        f"claude:{claude_b}",
        f"codex:{codex_c}",
    }
    assert [event["record"] for event in grep_events if event["kind"] == "summary" and "record" in event] == [
        "main",
        f"codex:{codex_a}",
        f"claude:{claude_b}",
        f"codex:{codex_c}",
    ]
    assert grep_events[-2] == {"kind": "summary", "count": 4}

    assert evidence.main([str(transcript), "--detail", f"claude:{claude_b}:1"]) == 0
    assert _read_jsonl(capsys, raw=True)[0]["record"] == f"claude:{claude_b}"
    assert evidence.main([str(transcript), "--detail", "unknown:1"]) == 2
    assert _read_jsonl(capsys, raw=True) == [{"kind": "error", "text": "記録が不明: unknown"}]

    assert evidence.main([str(transcript), "--hook-notices"]) == 0
    hook_events = _read_jsonl(capsys, raw=True)
    assert next(event for event in hook_events if event["kind"] == "summary")["count"] == 1

    assert evidence.main([str(transcript), "--stats"]) == 0
    stats_events = _read_jsonl(capsys, raw=True)
    assert {event["agent"] for event in stats_events if event["kind"] == "stats-subagent"} == {
        "agent-root",
        f"claude:{claude_b}/agent-child",
    }
    assert {event["session_id"] for event in stats_events if event["kind"] == "stats-agent-thread"} == {
        codex_a,
        claude_b,
        codex_c,
    }


def test_help_uses_one_claude_only_limitation_note(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """説明・統計・hook通知の注意書きが同じ定数を使い、Codexスレッドを除外しない。"""
    note = "集計の母集団はClaude Code形式の記録に限られ、Codex形式の記録からは件数が上がらない。"
    monkeypatch.setenv("COLUMNS", "1000")
    with pytest.raises(SystemExit) as raised:
        evidence.main(["--help"])
    help_text = " ".join(capsys.readouterr().out.split())

    assert raised.value.code == 0
    assert help_text.count(note) == 3
    assert "Codexスレッド別集計はClaude Code形式" not in help_text
    assert "`--since`が必須" in help_text
    assert help_text.count("`--since`と`--observation-boundary`が必須") == 2


def test_hook_record_scan_tolerates_non_string_type_values(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`type`の値がdictの記録を含むtranscriptでも警告走査とhook通知集計が完遂する。

    ツール定義を含む記録では`schema.input_schema.properties`配下に`type`という名前の
    プロパティ定義が現れ、その値がJSON Schemaのdictになる。
    """
    notice = "[auto-generated: agent-toolkit/pretooluse][warn] warn: 出力を切り詰めている"
    tool_schema_entry = {
        "type": "attachment",
        "attachment": {
            "tools": [
                {
                    "name": "feedback",
                    "schema": {
                        "input_schema": {
                            "properties": {
                                "type": {"type": "string", "enum": ["bug", "idea"]},
                                "title": {"type": "string"},
                            }
                        }
                    },
                }
            ]
        },
    }
    transcript = _write_transcript(
        tmp_path,
        [
            tool_schema_entry,
            _hook_attachment(
                {
                    "type": "hook_success",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "stdout": json.dumps({"hookSpecificOutput": {"additionalContext": notice}}, ensure_ascii=False),
                    "stderr": "",
                }
            ),
        ],
    )

    assert evidence.main([str(transcript), "--hook-notices"]) == 0
    hook_events = _read_jsonl(capsys)
    assert hook_events[-1] == {"kind": "summary", "count": 1}

    assert evidence.main([str(transcript), "--warn"]) == 0
    warn_events = _read_jsonl(capsys)
    assert [event["text"] for event in warn_events if event["kind"] == "warning"] == [notice]


def test_transcript_alias_selects_the_single_record_source(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--transcript`を位置引数と同じ単一記録の入口として扱う。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "入力"}}])

    assert evidence.main(["--transcript", str(transcript)]) == 0
    assert _read_jsonl(capsys)[0]["text"] == "入力"
    assert evidence.main([str(transcript), "--transcript", str(transcript)]) == 2


def test_catalog_claude_project_aggregates_traceable_descendants_without_leaving_root(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Claudeカタログはroot内の子記録だけを集約し、root外参照を未解決として数える。"""
    root = tmp_path / "project"
    child_id = "child-session"
    missing_id = "outside-session"
    _write_jsonl(
        root / "parent-session.jsonl",
        [
            {
                "type": "user",
                "timestamp": "2026-09-10T00:00:00Z",
                "cwd": "/repo",
                "gitBranch": "develop",
                "message": {"role": "user", "content": "Base directory for this skill: /plugin/skills/process-wi"},
            },
            {
                "type": "assistant",
                "timestamp": "2026-09-10T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "mcp__agents_server__start", "id": "child", "input": {}}],
                },
            },
            {
                "type": "user",
                "timestamp": "2026-09-10T00:00:02Z",
                "toolUseResult": {"sessionId": child_id, "engine": "claude"},
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "child"}]},
            },
            {
                "type": "assistant",
                "timestamp": "2026-09-10T00:00:03Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "mcp__agents_server__start", "id": "missing", "input": {}}],
                },
            },
            {
                "type": "user",
                "timestamp": "2026-09-10T00:00:04Z",
                "toolUseResult": {"sessionId": missing_id, "engine": "claude"},
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "missing"}]},
            },
        ],
    )
    _write_jsonl(
        root / f"{child_id}.jsonl",
        [
            {
                "type": "assistant",
                "timestamp": "2026-09-10T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": "wi", "input": {"command": "atk wi get"}}],
                },
            },
            {
                "type": "user",
                "timestamp": "2026-09-10T00:00:02Z",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "wi", "content": "ok"}]},
            },
        ],
    )

    assert (
        evidence.main(
            [
                "--catalog-claude-project",
                str(root),
                "--since",
                "2026-09-09T00:00:00Z",
                "--observation-boundary",
                "2026-09-11T00:00:00Z",
            ]
        )
        == 0
    )
    events = _read_jsonl(capsys, raw=True)
    parent, summary = events
    assert parent["session_id"] == "parent-session"
    assert parent["workflow"] == "process-wi"
    assert parent["descendant_count"] == 1
    assert parent["successful_wi_operation_count"] == 1
    assert parent["successful_wi_operations"][0]["operation"] == "get"
    assert summary["parent_record_count"] == 1
    assert summary["unresolved_record_count"] == 1


def test_catalog_counts_agy_children(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """走査rootの外にあるagyの委譲先ログを親の子孫数とトークンへ含め、未解決の参照に数えない。"""
    child_id = "e9244465-1577-44a9-a7b0-500b1f976b0e"
    root = tmp_path / "project"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _write_jsonl(
        root / "parent-session.jsonl",
        [
            {"type": "user", "timestamp": "2026-09-10T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
            _codex_tool_use_entry(
                "2026-09-10T00:00:01Z",
                "call-agy",
                child_id,
                tool_name="mcp__plugin_agent-toolkit_agents_server__start_write",
            ),
            _codex_tool_result_entry("2026-09-10T00:00:02Z", "call-agy", child_id, engine=None),
        ],
    )
    _write_agy_log(
        tmp_path / "state",
        child_id,
        [
            _agy_step(
                1,
                "agent_response",
                "DONE",
                usage={"input_tokens": 7, "output_tokens": 3, "cache_read_tokens": 11, "total_tokens": 10},
            )
        ],
    )

    assert (
        evidence.main(
            [
                "--catalog-claude-project",
                str(root),
                "--since",
                "2026-09-09T00:00:00Z",
                "--observation-boundary",
                "2026-09-11T00:00:00Z",
            ]
        )
        == 0
    )
    parent, summary = _read_jsonl(capsys, raw=True)
    assert parent["session_id"] == "parent-session"
    assert parent["descendant_count"] == 1
    assert parent["tokens"]["cache_read_input_tokens"] == 11
    assert summary["unresolved_record_count"] == 0


def test_catalog_codex_history_reports_unknown_fields_and_successful_wi_operation(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codexカタログは親記録を期間で選び、未記録値をunknownとして返す。"""
    root = tmp_path / "codex-history"
    session_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    _write_jsonl(
        root / f"rollout-test-{session_id}.jsonl",
        [
            {
                "type": "session_meta",
                "timestamp": "2026-09-10T00:00:00Z",
                "payload": {"id": session_id, "cwd": "/repo"},
            },
            {
                "type": "event_msg",
                "timestamp": "2026-09-10T00:00:01Z",
                "payload": {
                    "type": "item_completed",
                    "item": {"type": "CommandExecution", "status": "completed", "command": ["atk", "wi", "list"]},
                },
            },
        ],
    )

    assert (
        evidence.main(
            [
                "--catalog-codex-history",
                str(root),
                "--since",
                "2026-09-09T00:00:00Z",
                "--observation-boundary",
                "2026-09-11T00:00:00Z",
            ]
        )
        == 0
    )
    parent, summary = _read_jsonl(capsys, raw=True)
    assert parent["session_id"] == session_id
    assert parent["branch"] == "unknown"
    assert parent["workflow"] == "unknown"
    assert parent["tokens"] == "unknown"
    assert parent["successful_wi_operations"][0]["operation"] == "list"
    assert summary["parent_record_count"] == 1


def test_catalog_rejects_an_unclassifiable_root_and_reversed_period(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """記録を判別できないrootと開始後に終わる観測期間を終了コード2で拒否する。"""
    root = tmp_path / "empty"
    root.mkdir()
    base = ["--catalog-claude-project", str(root), "--since", "2026-09-10T00:00:00Z"]

    assert evidence.main([*base, "--observation-boundary", "2026-09-11T00:00:00Z"]) == 2
    assert "判別できない" in _read_jsonl(capsys)[0]["text"]

    assert evidence.main([*base, "--observation-boundary", "2026-09-09T00:00:00Z"]) == 2
    assert _read_jsonl(capsys) == [{"kind": "error", "text": "観測境界は開始境界以後を指定する"}]


def test_candidates_exclude_runtime_inputs_before_selecting_initial_request() -> None:
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": "環境情報", "runtime_generated": True},
        {"kind": "user", "record": "main", "line": 2, "text": "<skill>\n本文\n</skill>"},
        {"kind": "user", "record": "main", "line": 3, "text": "最初の依頼"},
        {"kind": "user", "record": "main", "line": 4, "text": "後続の訂正"},
        {"kind": "user", "record": "main", "line": 5, "text": "通常文中の <skill> という表記"},
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    items = [item for item in candidates if item["kind"] == "candidate"]
    assert sorted(item["locators"][0]["line"] for item in items) == [3, 4, 5]
    assert candidates[-1]["excluded"] == {"runtime-inserted": 1, "runtime-meta": 1}


def test_candidates_exclude_boundary_marked_injections() -> None:
    """属性を伴う境界標識付きの自動注入本文を、実行環境の挿入として除外する。"""
    normative = '<normative-context source="agent-toolkit" kind="rules-main">\n条文\n</normative-context>'
    hook_notice = (
        '<agent-toolkit-hook-message source="agent-toolkit/rules_context" kind="notice">\n注記\n</agent-toolkit-hook-message>'
    )
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": normative},
        {"kind": "user", "record": "main", "line": 2, "text": hook_notice},
        {"kind": "user", "record": "main", "line": 3, "text": "最初の依頼"},
        {"kind": "user", "record": "main", "line": 4, "text": "後続の訂正"},
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    items = [item for item in candidates if item["kind"] == "candidate"]
    assert sorted(item["locators"][0]["line"] for item in items) == [3, 4]
    assert candidates[-1]["excluded"]["runtime-inserted"] == 2


def test_candidates_exclude_initial_codex_skill_pair_without_hiding_later_intervention() -> None:
    """先頭スキル要求と対応本文を別区分で除外し、後続の利用者介入を保持する。"""
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

    records = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

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
            _hook_attachment(
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
            "record": "main",
            "kind": "tool-use",
            "line": 2,
            "timestamp": "2026-09-25T00:00:01Z",
            "name": "Bash",
            "input": {"command": "find . -name '*.md'", "description": "Markdownを探す"},
        }
    ]


def test_context_hook_output_is_excluded(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """block又はwarn以外の区分を持つhook出力は候補へ残らず、区分の種類によらず除外件数へ計上される。"""
    candidates, _ = _bundle_candidates_and_evidence(
        tmp_path,
        capsys,
        [
            {"type": "user", "message": {"role": "user", "content": "初期要求"}},
            _hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "SessionStart",
                    "content": [
                        '<agent-toolkit-auto-inserted source="agent-toolkit" kind="rules-main">\n# 規範\n'
                        "</agent-toolkit-auto-inserted>"
                    ],
                }
            ),
            _hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "SubagentStart",
                    "content": [
                        '<agent-toolkit-auto-inserted source="agent-toolkit" kind="auto-resume">\n再開\n'
                        "</agent-toolkit-auto-inserted>"
                    ],
                }
            ),
            _hook_attachment(
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
    assert [item["locators"] for item in hook_candidates] == [[{"record": "main", "line": 4}]]
    assert candidates[-1]["excluded"]["hook-notice-context"] == 2


def test_detail_and_grep_include_timestamp(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--detail`と`--grep`の各イベントが元記録行の時刻を持ち、時刻の無い行では`null`となる。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "timestamp": "2026-09-25T01:02:03Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": "c1", "input": {"command": "needle"}}],
                },
            },
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "needle結果"}]},
            },
        ],
    )

    assert evidence.main([str(transcript), "--detail", "1", "--detail", "2"]) == 0
    assert [event["timestamp"] for event in _read_jsonl(capsys)] == ["2026-09-25T01:02:03Z", None]

    assert evidence.main([str(transcript), "--grep", "needle"]) == 0
    matches = [event for event in _read_jsonl(capsys) if event["kind"] == "match"]
    assert [(event["line"], event["timestamp"]) for event in matches] == [(1, "2026-09-25T01:02:03Z"), (2, None)]

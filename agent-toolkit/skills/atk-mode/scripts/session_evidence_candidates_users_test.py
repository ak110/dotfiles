from __future__ import annotations

import json
import pathlib

import pytest
import session_evidence_candidates as evidence_candidates
import session_evidence_extract as evidence_extract
import session_review_evidence as evidence
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._atk.wi.constants import PROCESS_WI_GOAL_BODY
from agent_toolkit._common import skill_modes
from agent_toolkit._testing.helpers import _write_transcript


def _answer_event(line: int, answers: list[str], *, intervention: bool = False) -> dict[str, object]:
    event: dict[str, object] = {
        "kind": "user",
        "record": "main",
        "line": line,
        "text": "\n".join(answers),
        "assistant_context": [{"question": "方針", "options": [{"label": "推奨", "description": ""}]}],
        "user_response": [{"answers": answers}],
    }
    if intervention:
        event["answer_intervention"] = True
    return event


def test_candidate_events_excludes_non_interventions_and_reports_counts() -> None:
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": "初期要求"},
        {"kind": "user", "record": "agent-1", "line": 1, "text": "委譲入力"},
        {"kind": "user", "record": "main", "line": 2, "text": "<normative-context>規範", "runtime_inserted": True},
        _answer_event(3, ["推奨"]),
        {"kind": "user", "record": "main", "line": 4, "text": "実際の是正要求"},
    ]

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert [(candidate["candidate_kind"], candidate["locators"]) for candidate in candidates[:-1]] == [
        ("confirmation-request", [{"record": "main", "line": 3}]),
        ("user-intervention", [{"record": "main", "line": 4}]),
    ]
    assert candidates[1]["text"] == "実際の是正要求"
    assert candidates[-1]["excluded"] == {"delegated-record": 1, "initial-request": 1, "runtime-inserted": 1}


def test_candidate_events_keeps_answers_marked_as_intervention() -> None:
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": "初期要求"},
        _answer_event(2, ["推奨"]),
        _answer_event(3, ["対象範囲を広げる"], intervention=True),
    ]

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert [(candidate["candidate_kind"], candidate["locators"]) for candidate in candidates[:-1]] == [
        ("confirmation-request", [{"record": "main", "line": 2}]),
        ("user-intervention", [{"record": "main", "line": 3}]),
    ]
    assert candidates[-1]["excluded"] == {"initial-request": 1}


_OFFERED = [{"label": "既存機構へ統合"}, {"label": "新機構を追加"}]


_ORDINARY_UTTERANCE = "質問: 方針はどうしますか\n回答: 既存機構へ統合で進めて"


def _claude_question(question_id: str, answer: str, notes: str | None) -> list[dict[str, object]]:
    annotations = {"方針": {"notes": notes}} if notes is not None else {}
    return [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "AskUserQuestion",
                        "id": question_id,
                        "input": {"questions": [{"question": "方針", "options": _OFFERED}]},
                    }
                ],
            },
        },
        {
            "type": "user",
            "toolUseResult": {"answers": {"方針": answer}, "annotations": annotations},
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": question_id, "content": "回答"}]},
        },
    ]


def _codex_question(call_id: str, answer: str) -> list[dict[str, object]]:
    arguments = {"questions": [{"id": "scope", "question": "方針", "options": _OFFERED}]}
    return [
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "request_user_input",
                "call_id": call_id,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps({"answers": {"scope": {"answers": [answer]}}}, ensure_ascii=False),
            },
        },
    ]


def _extracted_candidates(transcript: pathlib.Path) -> tuple[list[str], list[str], dict[str, int]]:
    events = [{**event, "record": "main"} for event in evidence_extract.load_and_extract(str(transcript))]
    candidates = evidence_candidates._candidate_events(events, [], [])  # pylint: disable=protected-access
    texts = [candidate["text"] for candidate in candidates[:-1] if candidate["candidate_kind"] == "user-intervention"]
    confirmations = [
        candidate["text"] for candidate in candidates[:-1] if candidate["candidate_kind"] == "confirmation-request"
    ]
    return texts, confirmations, candidates[-1]["excluded"]


def test_claude_answers_from_transcript_are_classified_by_offered_choices(tmp_path: pathlib.Path) -> None:
    entries: list[dict[str, object]] = [
        {"type": "user", "message": {"role": "user", "content": "初期要求"}},
        *_claude_question("q1", "既存機構へ統合", None),
        *_claude_question("q2", "既存機構へ統合, 新機構を追加", None),
        *_claude_question("q3", "対象範囲を全件へ広げる", None),
        *_claude_question("q4", "既存機構へ統合", "ただし対象は全件とする"),
        {"type": "user", "message": {"role": "user", "content": _ORDINARY_UTTERANCE}},
    ]

    texts, confirmations, excluded = _extracted_candidates(_write_transcript(tmp_path, entries))

    assert texts == ["対象範囲を全件へ広げる", "既存機構へ統合\nただし対象は全件とする", _ORDINARY_UTTERANCE]
    assert confirmations == ["既存機構へ統合", "既存機構へ統合, 新機構を追加"]
    assert excluded == {"initial-request": 1}


def test_codex_answers_from_transcript_are_classified_by_offered_choices(tmp_path: pathlib.Path) -> None:
    """Codexの回答も、選択肢どおりなら確認の候補、選択肢と一致しなければユーザー介入候補にする。"""
    entries: list[dict[str, object]] = [
        {
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "初期要求"}]},
        },
        *_codex_question("call-1", "既存機構へ統合"),
        *_codex_question("call-2", "対象範囲を全件へ広げる"),
    ]

    texts, confirmations, excluded = _extracted_candidates(_write_transcript(tmp_path, entries))

    assert texts == ["対象範囲を全件へ広げる"]
    assert confirmations == ["既存機構へ統合"]
    assert excluded == {"initial-request": 1}


def test_candidate_events_excludes_runtime_generated_user_messages() -> None:
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": "初期要求"},
        {
            "kind": "user",
            "record": "main",
            "line": 2,
            "text": "Goal check-in: «目標» is still active",
            "runtime_inserted": True,
        },
        {
            "kind": "user",
            "record": "main",
            "line": 3,
            "text": "Stop hook feedback:\n[目標]: incomplete evidence",
            "runtime_inserted": True,
        },
        {
            "kind": "user",
            "record": "main",
            "line": 4,
            "text": "<local-command-stdout>Goal set</local-command-stdout>",
            "runtime_inserted": True,
        },
        {
            "kind": "user",
            "record": "main",
            "line": 5,
            "text": "A session-scoped Stop hook is now active with条件",
            "runtime_inserted": True,
        },
        {"kind": "user", "record": "main", "line": 6, "text": "実際の是正要求"},
    ]

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert [candidate["locators"] for candidate in candidates[:-1]] == [[{"record": "main", "line": 6}]]
    assert candidates[-1]["excluded"]["runtime-inserted"] == 4


def _claude_user(content: object, **fields: object) -> dict[str, object]:
    """Claude Codeのユーザーロールの記録を返す。"""
    return {"type": "user", "message": {"role": "user", "content": content}, **fields}


def _codex_user(text: str) -> dict[str, object]:
    """Codexのユーザーロールのmessage記録を返す。"""
    return {
        "type": "response_item",
        "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
    }


# Claude Codeの各記録は2.1.281〜2.1.291の実記録（`~/.claude/projects`配下）、Codexの各記録は2026年8月〜10月の
# rollout（`~/.codex/sessions`配下）から、本文の格納先と構造の項目の形を写した。本文は短縮した。
_INTERRUPT = "[Request interrupted by user]"


_TOOL_INTERRUPT = "[Request interrupted by user for tool use]"


_BASH_STDOUT = "<bash-stdout>+ FS_UUID=24d572ab</bash-stdout><bash-stderr></bash-stderr>"


_CODEX_GENERATED = (
    '<codex_internal_context source="goal">\nContinue working toward the active thread goal.\n</codex_internal_context>',
    '<hook_prompt hook_run_id="stop:8:hooks.codex.json">&lt;atk-auto kind="block"&gt;通知</hook_prompt>',
    '<subagent_notification>\n{"agent_path":"01a0280f","status":{"completed":"status: completed"}}',
)


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_bundle_keeps_interrupt_markers_and_excludes_runtime_outputs(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], host: str
) -> None:
    """中断の標識は介入候補と会話の流れに残し、実行環境の出力は除外件数へ数えて会話の流れから除く。

    中断の標識が候補から消えると、振り返りはツール実行の拒否・中断を検討できない。
    実行環境の出力が候補に残ると、人間の介入として誤って分析される。
    """
    if host == "claude":
        entries = [
            _claude_user("最初の依頼", origin={"kind": "human"}),
            _claude_user([{"type": "text", "text": _INTERRUPT}], interruptedMessageId="msg_1"),
            _claude_user([{"type": "text", "text": _TOOL_INTERRUPT}], interruptedMessageId="msg_2"),
            _claude_user(_BASH_STDOUT, turnOrigin="human"),
        ]
        expected_candidates = [_INTERRUPT, _TOOL_INTERRUPT]
        expected_utterances = ["最初の依頼", _INTERRUPT, _TOOL_INTERRUPT]
        expected_runtime_inserted = 1
    else:
        entries = [_codex_user("最初の依頼"), *(_codex_user(text) for text in _CODEX_GENERATED), _codex_user("後続の訂正")]
        expected_candidates = ["後続の訂正"]
        expected_utterances = ["最初の依頼", "後続の訂正"]
        expected_runtime_inserted = 3
    transcript = _write_transcript(tmp_path, entries)
    bundle = tmp_path / "bundle"
    bundle.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle)]) == 0
    capsys.readouterr()

    records = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]

    assert sorted(item["text"] for item in records[:-1] if item["candidate_kind"] == "user-intervention") == sorted(
        expected_candidates
    )
    assert records[-1]["excluded"]["runtime-inserted"] == expected_runtime_inserted
    conversation = [json.loads(line) for line in (bundle / "conversation.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [event["text"] for event in conversation if event.get("role") == "user"] == expected_utterances


def test_candidate_events_excludes_runtime_generated_user_records() -> None:
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": "初期要求"},
        {"kind": "user", "record": "main", "line": 2, "text": "注記", "runtime_generated": True},
        {"kind": "user", "record": "main", "line": 3, "text": "実際の是正要求"},
    ]

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert [candidate["locators"] for candidate in candidates[:-1]] == [[{"record": "main", "line": 3}]]
    assert candidates[-1]["excluded"]["runtime-meta"] == 1


def test_first_human_request_after_automated_start_is_initial_request() -> None:
    timeline = [
        {
            "kind": "user",
            "record": "main",
            "line": 1,
            "text": "<agent-toolkit-auto-inserted>自動起動",
            "runtime_inserted": True,
        },
        {"kind": "user", "record": "main", "line": 2, "text": "最初の人間の依頼"},
        {"kind": "user", "record": "main", "line": 3, "text": "後続の人間の指摘"},
    ]

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert candidates[0]["locators"] == [{"record": "main", "line": 3}]
    assert candidates[-1]["excluded"] == {"initial-request": 1, "runtime-inserted": 1}


@pytest.mark.parametrize(
    ("goal_event", "expected_excluded"),
    [
        # Claude Codeはスラッシュコマンドを生成本文として記録し、`_event`が実行環境の挿入へ分類する。
        (
            {
                "text": "<command-name>/goal</command-name>\n"
                f"<command-args>`{skill_modes.PROCESS_WI}`を完遂してください。</command-args>",
                "runtime_inserted": True,
            },
            {"runtime-inserted": 2},
        ),
        # Codexは前置きのない本文として記録し、起動の目的文を初期要求として除く。
        (
            {"text": f"/goal `{skill_modes.PROCESS_WI}`を完遂してください。", "runtime_inserted": False},
            {"runtime-inserted": 1, "initial-request": 1},
        ),
    ],
    ids=["claude", "codex"],
)
def test_human_intervention_after_process_loop_start_remains_candidate(
    goal_event: dict[str, object], expected_excluded: dict[str, int]
) -> None:
    """process-loopが自動起動した記録では、最初の人間の発話を初期要求へ除かず介入の候補に残す。"""
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": "<atk-auto>規範", "runtime_inserted": True},
        {"kind": "user", "record": "main", "line": 2, **goal_event},
        {"kind": "user", "record": "main", "line": 3, "text": "途中で割り込んだ人間の指摘"},
    ]

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert [candidate["locators"] for candidate in candidates[:-1]] == [[{"record": "main", "line": 3}]]
    assert candidates[-1]["excluded"] == expected_excluded


@pytest.mark.parametrize("prompt", [PROCESS_WI_GOAL_BODY, _pl_session.build_process_loop_prompt()])
def test_process_loop_goal_matches_launch_prompt_body(prompt: str) -> None:
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": "<atk-auto>規範", "runtime_inserted": True},
        {"kind": "user", "record": "main", "line": 2, "text": "# AGENTS.md instructions\n" + prompt},
        {"kind": "user", "record": "main", "line": 3, "text": "途中で割り込んだ人間の指摘"},
    ]
    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access
    assert [candidate["locators"] for candidate in candidates[:-1]] == [[{"record": "main", "line": 3}]]

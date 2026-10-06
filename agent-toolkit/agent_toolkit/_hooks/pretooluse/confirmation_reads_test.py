"""agent-toolkit/agent_toolkit/_hooks/pretooluse/confirmation_reads.py のテスト。

PreToolUseの統合フックをsubprocessで起動し、transcriptの読取の記録に応じた警告の有無を検証する。
transcriptのエントリの形は、Claude Code 2.1.291の`~/.claude/projects`配下の記録（`tool_use`ブロックを持つ
`assistant`エントリと、`type`が`system`・`subtype`が`compact_boundary`のエントリ）から写した。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit._hooks.pretooluse.test_support_test import _additional_context, _run

_SKILL_REFERENCES = pathlib.Path(__file__).resolve().parents[3] / "skills" / "user-confirmation-and-report" / "references"
_APPROVAL = _SKILL_REFERENCES / "approval-scope.md"
_CHOICE = _SKILL_REFERENCES / "choice-construction.md"
_WARNING = "確認の前に全文読む資料"
_QUESTION_INPUT = {
    "questions": [{"question": "どちらにしますか？", "header": "方針", "options": [{"label": "A"}, {"label": "B"}]}]
}


def _read(path: pathlib.Path) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"file_path": str(path)}}]},
    }


def _bash(command: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}


_COMPACT = {"type": "system", "subtype": "compact_boundary"}


def _payload(tmp_path: pathlib.Path, entries: list[dict], **extra: object) -> dict:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    return {
        "tool_name": "AskUserQuestion",
        "tool_input": _QUESTION_INPUT,
        "session_id": "confirmation-reads",
        "transcript_path": str(transcript),
        **extra,
    }


@pytest.mark.parametrize(
    ("entries", "unread"),
    [
        pytest.param([], [_APPROVAL, _CHOICE], id="both-unread"),
        pytest.param([_read(_CHOICE)], [_APPROVAL], id="approval-unread"),
        pytest.param([_bash(f"cat {_APPROVAL}")], [_CHOICE], id="choice-unread"),
        pytest.param([_read(_APPROVAL), _read(_CHOICE), _COMPACT], [_APPROVAL, _CHOICE], id="read-before-compaction"),
    ],
)
def test_unread_reference_warns_without_blocking(
    tmp_path: pathlib.Path, entries: list[dict], unread: list[pathlib.Path]
) -> None:
    """最後の会話圧縮より後に読んでいない資料の絶対パスと次の操作を示して通し、反復しても遮断しない。

    確認を組む時点に読込表の行が想起されないと、規定を満たさない前提と選択肢のまま確認が発行される。
    遮断は`AskUserQuestion`の入力の再発行を要し損失が大きいため、警告で返す。
    """
    payload = _payload(tmp_path, entries)
    for _ in range(2):
        result = _run(payload)
        assert result.returncode == 0
        context = _additional_context(result)
        assert context.count(_WARNING) == 1
        for path in (_APPROVAL, _CHOICE):
            assert (str(path) in context) == (path in unread)
        assert "次の操作:" in context


@pytest.mark.parametrize(
    "entries",
    [
        pytest.param([_COMPACT, _read(_APPROVAL), _read(_CHOICE)], id="read-after-compaction"),
        pytest.param([_bash(f"cat {_APPROVAL} {_CHOICE}")], id="bash"),
    ],
)
def test_read_references_do_not_warn(tmp_path: pathlib.Path, entries: list[dict]) -> None:
    result = _run(_payload(tmp_path, entries))
    assert result.returncode == 0
    assert _WARNING not in _additional_context(result)


def test_non_main_or_unreadable_context_does_not_warn(tmp_path: pathlib.Path) -> None:
    """処置できない主体と判定できない入力では警告しない。"""
    assert _WARNING not in _additional_context(_run(_payload(tmp_path, [], agent_id="sub-1")))
    assert _WARNING not in _additional_context(_run(_payload(tmp_path, []), {"AGENT_TOOLKIT_DELEGATED_SESSION": "1"}))
    missing = {**_payload(tmp_path, []), "transcript_path": str(tmp_path / "absent.jsonl")}
    assert _WARNING not in _additional_context(_run(missing))


def test_exit_plan_mode_and_mojibake_warning_are_unchanged(tmp_path: pathlib.Path) -> None:
    """`ExitPlanMode`は対象外とし、文字化けの警告は未読の警告と並べて返す。"""
    plan = {**_payload(tmp_path, []), "tool_name": "ExitPlanMode", "tool_input": {"plan": "計画"}}
    assert _additional_context(_run(plan)) == ""
    mojibake = _payload(tmp_path, [])
    mojibake["tool_input"] = {"questions": [{"question": "日本語の�本文", "header": "見出し", "options": [{"label": "A"}]}]}
    context = _additional_context(_run(mojibake))
    assert "U+FFFD" in context and _WARNING in context

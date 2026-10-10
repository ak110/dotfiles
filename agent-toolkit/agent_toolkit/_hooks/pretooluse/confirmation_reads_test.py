"""agent-toolkit/agent_toolkit/_hooks/pretooluse/confirmation_reads.py のテスト。

PreToolUseの統合フックをsubprocessで起動し、transcriptの読取の記録に応じた警告の有無を検証する。
transcriptのエントリの形は、Claude Code 2.1.291の`~/.claude/projects`配下の記録（`tool_use`ブロックを持つ
`assistant`エントリ、呼出IDに対応する`tool_result`を持つ`user`エントリと、
`type`が`system`・`subtype`が`compact_boundary`のエントリ）から写した。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit._testing.pretooluse_support import _additional_context, _run

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
        "message": {
            "content": [{"type": "tool_use", "id": f"read-{path.name}", "name": "Read", "input": {"file_path": str(path)}}]
        },
    }


def _bash(command: str) -> dict:
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": "bash-read", "name": "Bash", "input": {"command": command}}]},
    }


def _result(tool_id: str, *, error: bool = False) -> dict:
    return {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "取得した本文", "is_error": error}]},
    }


def _successful_read(path: pathlib.Path) -> list[dict]:
    return [_read(path), _result(f"read-{path.name}")]


def _successful_bash(command: str) -> list[dict]:
    return [_bash(command), _result("bash-read")]


_COMPACT = {"type": "system", "subtype": "compact_boundary"}


def _payload(tmp_path: pathlib.Path, entries: list[dict], **extra: object) -> dict:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    return {
        "tool_name": "AskUserQuestion",
        "tool_input": _QUESTION_INPUT,
        "session_id": "confirmation-reads",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
        **extra,
    }


@pytest.mark.parametrize(
    ("entries", "unread"),
    [
        pytest.param([], [_APPROVAL, _CHOICE], id="both-unread"),
        pytest.param(_successful_read(_CHOICE), [_APPROVAL], id="approval-unread"),
        pytest.param(_successful_bash(f"cat {_APPROVAL}"), [_CHOICE], id="choice-unread"),
        pytest.param(
            [*_successful_read(_APPROVAL), *_successful_read(_CHOICE), _COMPACT],
            [_APPROVAL, _CHOICE],
            id="read-before-compaction",
        ),
        pytest.param([_read(_APPROVAL), _result("read-approval-scope.md", error=True)], [_APPROVAL, _CHOICE], id="failed-read"),
        pytest.param([_read(_APPROVAL)], [_APPROVAL, _CHOICE], id="read-without-result"),
        pytest.param(
            [_bash(f"cat {_APPROVAL} {_CHOICE}"), _result("bash-read", error=True)], [_APPROVAL, _CHOICE], id="failed-bash"
        ),
        pytest.param([_bash(f"cat {_APPROVAL} {_CHOICE}")], [_APPROVAL, _CHOICE], id="bash-without-result"),
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
        pytest.param([_COMPACT, *_successful_read(_APPROVAL), *_successful_read(_CHOICE)], id="read-after-compaction"),
        pytest.param(_successful_bash(f"cat {_APPROVAL} {_CHOICE}"), id="bash"),
        pytest.param(_successful_bash(f"cat -- {_APPROVAL} {_CHOICE}"), id="bash-terminator"),
        pytest.param(
            _successful_bash(f"cd {_SKILL_REFERENCES} && cat approval-scope.md choice-construction.md"), id="bash-relative"
        ),
        pytest.param(
            [*_successful_bash(f"atk read-file --max-bytes 12000 -- {_APPROVAL}"), *_successful_read(_CHOICE)],
            id="budgeted-read-file",
        ),
    ],
)
def test_read_references_do_not_warn(tmp_path: pathlib.Path, entries: list[dict]) -> None:
    result = _run(_payload(tmp_path, entries))
    assert result.returncode == 0
    assert _WARNING not in _additional_context(result)


@pytest.mark.parametrize(
    "operation", ["printf '%s'", "ls", "test -e", "python3 -c \"print('approval-scope.md choice-construction.md')\""]
)
def test_name_mentions_and_attributes_do_not_suppress_warning(tmp_path: pathlib.Path, operation: str) -> None:
    """成功しても対象本文を読まない操作では、未取得資料の警告を保持する。"""
    entries = _successful_bash(f"{operation} '{_APPROVAL}' '{_CHOICE}'")
    result = _run(_payload(tmp_path, entries))
    assert result.returncode == 0
    context = _additional_context(result)
    assert _WARNING in context and str(_APPROVAL) in context and str(_CHOICE) in context


@pytest.mark.parametrize("tool", ["Read", "Bash"])
def test_other_copy_of_same_named_reference_is_not_the_target(tmp_path: pathlib.Path, tool: str) -> None:
    """別の場所の同名資料への成功した読取を、hookが案内する資料の取得へ変換しない。"""
    other = tmp_path / "other/skills/user-confirmation-and-report/references/approval-scope.md"
    other.parent.mkdir(parents=True)
    other.write_text("別資料", encoding="utf-8")
    entries = _successful_read(other) if tool == "Read" else _successful_bash(f"cat {other}")
    assert _WARNING in _additional_context(_run(_payload(tmp_path, entries)))


def test_compaction_drops_unfinished_read_and_later_success_restores_it(tmp_path: pathlib.Path) -> None:
    """圧縮をまたぐ未完了の読取結果を除き、圧縮後の成功だけで不足を解消する。"""
    entries = [_read(_APPROVAL), _COMPACT, _result("read-approval-scope.md"), *_successful_read(_CHOICE)]
    first = _run(_payload(tmp_path, entries))
    assert _WARNING in _additional_context(first)
    assert str(_APPROVAL) in _additional_context(first)
    assert str(_CHOICE) not in _additional_context(first)
    assert _WARNING not in _additional_context(_run(_payload(tmp_path, [*entries, *_successful_read(_APPROVAL)])))


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

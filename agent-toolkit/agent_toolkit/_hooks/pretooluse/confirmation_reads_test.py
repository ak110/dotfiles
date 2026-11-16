"""確認操作に伴う資料取得の拒否と取得観測のテスト。

PreToolUseとPostToolUseの公開hookを同一プロセスで起動し、読取成功の観測に応じた拒否の有無を検証する。
transcriptのエントリの形は、Claude Code 2.1.291の`~/.claude/projects`配下の記録（`tool_use`ブロックを持つ
`assistant`エントリ、呼出IDに対応する`tool_result`を持つ`user`エントリと、
`type`が`system`・`subtype`が`compact_boundary`のエントリ）から写した。
"""

from __future__ import annotations

import json
import pathlib
import shlex

import pytest

from agent_toolkit._common import read_triggers, session_state
from agent_toolkit._hooks import document_reads, rules_context, work_contract
from agent_toolkit._testing.document_delivery import run_hook
from agent_toolkit._testing.pretooluse_support import _additional_context, _run

_SKILL_REFERENCES = pathlib.Path(__file__).resolve().parents[3] / "skills" / "user-confirmation-and-report" / "references"
_APPROVAL = _SKILL_REFERENCES / "approval-scope.md"
_CHOICE = _SKILL_REFERENCES / "choice-construction.md"
_DECISIONS = _SKILL_REFERENCES / "user-confirmation-and-report-decisions.md"
_QUESTIONS = _SKILL_REFERENCES / "user-confirmation-and-report-questions.md"
_WARNING = "未取得資料"
_DENIAL = "この質問は未表示"
_CONFIRMATION_TOOLS = ("AskUserQuestion", "request_user_input", "request_user_input_async")
_QUESTION_INPUT = {
    "questions": [{"question": "どちらにしますか？", "header": "方針", "options": [{"label": "A"}, {"label": "B"}]}]
}


@pytest.fixture(autouse=True)
def _local_plugin_root(tmp_path, monkeypatch):
    """読取を記録する資料と警告が指す資料を、この作業ツリーへそろえる。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT", raising=False)
    path = tmp_path / "work-contract.md"
    path.write_text("目的: 確認操作固有の通知の検証\n", encoding="utf-8")
    monkeypatch.setattr(work_contract, "record_path", lambda *_args, **_kwargs: path)


def _denial(result) -> str:
    """PreToolUseの拒否の理由を返す。拒否していなければ空文字列を返す。"""
    output = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
    return output.get("permissionDecisionReason", "") if output.get("permissionDecision") == "deny" else ""


def _assert_reference_state(path: pathlib.Path, expected: bool) -> None:
    """通知の有無と分けて、現在本文の実受領を確認する。"""
    records = session_state.read_state("confirmation-reads").get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert (document_reads.acquisition_state(records, read_triggers.ReadTarget(path), "main") == "current") is expected


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


def _result(tool_id: str, *, error: bool = False, content: str = "取得した本文") -> dict:
    return {
        "type": "user",
        "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content, "is_error": error}]},
    }


def _successful_read(path: pathlib.Path) -> list[dict]:
    return [_read(path), _result(f"read-{path.name}", content=path.read_text(encoding="utf-8"))]


def _successful_skill() -> list[dict]:
    call = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "skill",
                    "name": "Skill",
                    "input": {"skill": "agent-toolkit:user-confirmation-and-report"},
                }
            ]
        },
    }
    return [call, _result("skill", content="スキルの起動に成功した")]


def _successful_bash(command: str) -> list[dict]:
    arguments = shlex.split(command)
    base = _SKILL_REFERENCES
    if arguments[:1] == ["cd"]:
        base = pathlib.Path(arguments[1])
        arguments = arguments[arguments.index("&&") + 1 :]
    if arguments[:1] == ["cat"]:
        paths = [pathlib.Path(value) for value in arguments[1:] if not value.startswith("-")]
        text = "".join((path if path.is_absolute() else base / path).read_text(encoding="utf-8") for path in paths)
    elif arguments[:2] == ["sed", "-n"]:
        start, end = map(int, arguments[2].removesuffix("p").split(","))
        lines = pathlib.Path(arguments[-1]).read_text(encoding="utf-8").splitlines(keepends=True)
        text = "".join(lines[start - 1 : end])
    else:
        text = " ".join(arguments[1:])
    return [_bash(command), _result("bash-read", content=text)]


_COMPACT = {"type": "system", "subtype": "compact_boundary"}


def _payload(tmp_path: pathlib.Path, entries: list[dict], **extra: object) -> dict:
    transcript = tmp_path / "transcript.jsonl"
    # 比較対象の2資料とは独立に、共有の読込方法を含む親スキル本文を圧縮後にも取得する。
    omitted = extra.pop("omit_reference", None)
    entries = [*entries, *_successful_skill()]
    for path in (_DECISIONS, _QUESTIONS):
        if path != omitted:
            entries.extend(_successful_read(path))
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    payload = {
        "tool_name": "AskUserQuestion",
        "tool_input": _QUESTION_INPUT,
        "session_id": "confirmation-reads",
        "transcript_path": str(transcript),
        "cwd": str(tmp_path),
        **extra,
    }
    # 履歴だけから取得済みを推定せず、現在の文脈で完了した公開取得イベントを先に観測する。
    boundary = max((i + 1 for i, entry in enumerate(entries) if entry == _COMPACT), default=0)
    pending = {}
    for entry in entries[boundary:]:
        for block in entry.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                pending[block["id"]] = block
            elif block.get("type") == "tool_result" and (call := pending.pop(block["tool_use_id"], None)):
                event = {
                    **payload,
                    "hook_event_name": "PostToolUse",
                    "tool_name": call["name"],
                    "tool_input": call["input"],
                    "tool_response": {"stdout": block["content"], "exit_code": 1 if block.get("is_error") else 0},
                }
                assert run_hook("posttooluse", event, {}).returncode == 0
    return payload


@pytest.mark.parametrize(
    ("entries", "unread"),
    [
        pytest.param([], [_APPROVAL, _CHOICE], id="both-unread"),
        pytest.param(_successful_read(_CHOICE), [_APPROVAL], id="approval-unread"),
        pytest.param(_successful_read(_APPROVAL), [_CHOICE], id="choice-unread"),
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
@pytest.mark.cpu_limit(
    3.0,
    reason=(
        "未取得/部分/圧縮/失敗/結果欠落の公開Pre2回とPostで、取得状態と毎回の拒否を照合する（Linux限定再確認のC"
        "PU合計最大1.370秒）。"
    ),
)
def test_unread_reference_is_denied_until_read(tmp_path: pathlib.Path, entries: list[dict], unread: list[pathlib.Path]) -> None:
    """最後の会話圧縮より後に読んでいない資料の絶対パスと次の操作を示して、質問の表示前に毎回拒否する。

    確認を組む時点に読込表の行が想起されないと、規定を満たさない前提と選択肢のまま確認が発行される。
    警告は回答と一緒に届くため、基準を適用しないまま質問する害を防げない。拒否は表示前に返り、ユーザーの入力は生じない。
    """
    payload = _payload(tmp_path, entries)
    for _attempt in range(2):
        result = _run(payload)
        assert result.returncode == 0 and not result.stderr
        reason = _denial(result)
        assert _DENIAL in reason
        for path in (_APPROVAL, _CHOICE):
            _assert_reference_state(path, path not in unread)
            assert (f"agent-toolkit:user-confirmation-and-report/references/{path.name}" in reason) == (path in unread)


@pytest.mark.parametrize("tool", _CONFIRMATION_TOOLS)
@pytest.mark.cpu_limit(
    4.75,
    reason=(
        "各確認toolから正規2資料の未取得拒否と全文取得後通過まで公開Pre3回/Post4回で検証する（Linux限定再確認"
        "のCPU合計最大2.207秒）。"
    ),
)
def test_every_confirmation_tool_is_denied_then_passes_after_full_reads(tmp_path: pathlib.Path, tool: str) -> None:
    """3つの確認ツールを同じ判定で拒否し、2資料を全文読んだ後は通す。"""
    unread = {**_payload(tmp_path, []), "tool_name": tool}
    for _attempt in range(2):
        assert _DENIAL in _denial(_run(unread))
    read = {**_payload(tmp_path, [*_successful_read(_APPROVAL), *_successful_read(_CHOICE)]), "tool_name": tool}
    result = _run(read)
    assert result.returncode == 0 and not _denial(result)
    assert _WARNING not in _additional_context(result)


@pytest.mark.parametrize("missing", [_DECISIONS, _QUESTIONS])
@pytest.mark.cpu_limit(
    7.0,
    reason="実規範の宣言集合と公開Pre/Postによる取得状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大3.716秒。",
)
def test_split_confirmation_body_is_required_then_not_requested_again(tmp_path, missing):
    """従来の2資料だけでは、分割された判定・手順の本文の取得を代替しない。"""
    entries = [*_successful_read(_APPROVAL), *_successful_read(_CHOICE)]
    result = _run(_payload(tmp_path, entries, omit_reference=missing))
    assert f"agent-toolkit:user-confirmation-and-report/references/{missing.name}" in _denial(result)
    assert str(_APPROVAL) not in _denial(result)
    assert str(_CHOICE) not in _denial(result)
    result = _run(_payload(tmp_path, [*entries, *_successful_read(missing)]))
    assert not _denial(result)
    assert str(missing) not in _additional_context(result)


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
            [*_successful_bash(f"sed -n '1,400p' -- {_APPROVAL}"), *_successful_read(_CHOICE)],
            id="line-range",
        ),
    ],
)
@pytest.mark.cpu_limit(
    5.25,
    reason=(
        "Read/cat/sedの実記録と公開Pre/Postで正規2資料の全文取得を検証し、Codexでは外側配送観測まで通す"
        "（Linux限定再確認のCPU合計最大2.454秒）。"
    ),
)
def test_read_references_do_not_warn(tmp_path: pathlib.Path, entries: list[dict]) -> None:
    codex = any(entry.get("message", {}).get("content", [{}])[0].get("name") == "Bash" for entry in entries)
    result = _run(_payload(tmp_path, entries, **({"turn_id": "turn"} if codex else {})))
    assert result.returncode == 0 and not _denial(result)
    assert _WARNING not in _additional_context(result)
    for path in (_APPROVAL, _CHOICE):
        _assert_reference_state(path, True)


@pytest.mark.parametrize(
    "operation", ["printf '%s'", "ls", "test -e", "python3 -c \"print('approval-scope.md choice-construction.md')\""]
)
@pytest.mark.cpu_limit(
    2.25,
    reason=(
        "2資料への非読取言及を公開Pre/Postへ渡し、両正規資料が未取得である状態まで検証する（Linux限定再確認のCPU"
        "合計最大0.991秒）。"
    ),
)
def test_name_mentions_and_attributes_do_not_suppress_warning(tmp_path: pathlib.Path, operation: str) -> None:
    """成功しても対象本文を読まない操作では、未取得資料の拒否を保持する。"""
    entries = _successful_bash(f"{operation} '{_APPROVAL}' '{_CHOICE}'")
    result = _run(_payload(tmp_path, entries))
    assert result.returncode == 0
    reason = _denial(result)
    assert _DENIAL in reason and "approval-scope.md" in reason and "choice-construction.md" in reason
    for path in (_APPROVAL, _CHOICE):
        _assert_reference_state(path, False)


@pytest.mark.parametrize("tool", ["Read", "Bash"])
@pytest.mark.cpu_limit(
    2.25,
    reason=(
        "別場所の同名資料の公開取得とPreで、正規2資料の未取得を状態と通知から検証する（Linux限定再確認のCPU合計最大0.909秒）。"
    ),
)
def test_other_copy_of_same_named_reference_is_not_the_target(tmp_path: pathlib.Path, tool: str) -> None:
    """別の場所の同名資料への成功した読取を、hookが案内する資料の取得へ変換しない。"""
    other = tmp_path / "other/skills/user-confirmation-and-report/references/approval-scope.md"
    other.parent.mkdir(parents=True)
    other.write_text("別資料", encoding="utf-8")
    entries = _successful_read(other) if tool == "Read" else _successful_bash(f"cat {other}")
    result = _run(_payload(tmp_path, entries))
    assert result.returncode == 0 and not result.stderr
    assert "agent-toolkit:user-confirmation-and-report/references/approval-scope.md" in _denial(result)
    _assert_reference_state(_APPROVAL, False)


@pytest.mark.cpu_limit(
    4.75,
    reason=(
        "2資料の途中読取・圧縮・後続取得を公開Pre2回/Post5回で照合し、未完了を取得へ補わないことを検証する（Linux"
        "限定再確認のCPU合計最大2.182秒）。"
    ),
)
def test_compaction_drops_unfinished_read_and_later_success_restores_it(tmp_path: pathlib.Path) -> None:
    """圧縮をまたぐ未完了の読取結果を除き、圧縮後の成功だけで不足を解消する。"""
    entries = [_read(_APPROVAL), _COMPACT, _result("read-approval-scope.md"), *_successful_read(_CHOICE)]
    first = _denial(_run(_payload(tmp_path, entries)))
    assert _DENIAL in first
    assert "agent-toolkit:user-confirmation-and-report/references/approval-scope.md" in first
    assert "agent-toolkit:user-confirmation-and-report/references/choice-construction.md" not in first
    _assert_reference_state(_APPROVAL, False)
    _assert_reference_state(_CHOICE, True)
    assert not _denial(_run(_payload(tmp_path, [*entries, *_successful_read(_APPROVAL)])))
    _assert_reference_state(_APPROVAL, True)
    _assert_reference_state(_CHOICE, True)


@pytest.mark.cpu_limit(
    4.25,
    reason=(
        "sub/delegate/不明履歴の3入力を公開Pre各1回と準備Postへ渡し、誤拒否と誤案内を検証する（Linux限"
        "定再確認のCPU合計最大1.932秒）。"
    ),
)
def test_non_main_or_unreadable_context_is_neither_denied_nor_warned(tmp_path: pathlib.Path) -> None:
    """処置できない主体と判定できない入力では、拒否も警告もしない。"""
    missing = {**_payload(tmp_path, []), "transcript_path": str(tmp_path / "absent.jsonl")}
    for result in (
        _run(_payload(tmp_path, [], agent_id="sub-1")),
        _run(_payload(tmp_path, []), {"AGENT_TOOLKIT_DELEGATED_SESSION": "1"}),
        _run(missing),
    ):
        assert not _denial(result) and _WARNING not in _additional_context(result)


@pytest.mark.cpu_limit(
    2.0,
    reason="実規範の宣言集合と公開Pre/Postによる取得状態の遷移を検収する最小の状態列。所有thread合計CPUのLinux観測最大0.865秒。",
)
def test_unresolvable_declarations_warn_without_denying(tmp_path: pathlib.Path, monkeypatch) -> None:
    """読込宣言を取得できない場合は、判定基盤の不調で質問を止めないよう警告にとどめる。"""
    from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    def broken(*_args, **_kwargs):
        raise ValueError("宣言の検証用の失敗")

    payload = _payload(tmp_path, [])
    monkeypatch.setattr(read_delivery, "declarations", broken)
    result = _run(payload)
    assert result.returncode == 0 and not _denial(result)
    assert "読込表を取得できない" in _additional_context(result)


@pytest.mark.cpu_limit(
    3.0,
    reason=(
        "計画と文字化け質問の2入力を公開Pre2回/Post2回で通し、取得拒否と既存警告の併存を検証する（Linux限定再確認"
        "のCPU合計最大1.351秒）。"
    ),
)
def test_exit_plan_mode_and_mojibake_warning_are_unchanged(tmp_path: pathlib.Path) -> None:
    """`ExitPlanMode`は対象外とし、文字化けの警告は未読の拒否と並べて返す。"""
    plan = {**_payload(tmp_path, []), "tool_name": "ExitPlanMode", "tool_input": {"plan": "計画"}}
    plan_result = _run(plan)
    plan_context = _additional_context(plan_result)
    assert not _denial(plan_result) and _WARNING not in plan_context
    assert all(str(path) not in plan_context for path in (_APPROVAL, _CHOICE))
    mojibake = _payload(tmp_path, [])
    mojibake["tool_input"] = {"questions": [{"question": "日本語の�本文", "header": "見出し", "options": [{"label": "A"}]}]}
    result = _run(mojibake)
    assert "U+FFFD" in _additional_context(result) and _DENIAL in _denial(result)

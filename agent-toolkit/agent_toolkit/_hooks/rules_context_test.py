"""実行主体別の規範追加handlerの契約テスト。"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import shlex
from collections.abc import Callable

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.wi import process_loop_log
from agent_toolkit._common import plugin_environment
from agent_toolkit._common.read_triggers import ReadTarget
from agent_toolkit._common.session_state import read_state
from agent_toolkit._hooks import posttooluse, pretooluse, rules_context, rules_context_codex
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules


@pytest.mark.parametrize("source", ("startup", "resume", "clear", "compact", "fork"))
@pytest.mark.parametrize("temp_failure", (False, True))
def test_session_start_delivers_current_conversation_identity(
    source: str,
    temp_failure: bool,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """会話切替と一時領域の成否を変えても、現在の入力のIDと絶対パスが対応して届く。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)

    def make_temp(*_args: object, **_kwargs: object) -> pathlib.Path:
        if temp_failure:
            raise OSError("一時領域を作成できません")
        return tmp_path

    monkeypatch.setattr(rules_context.managed_temp, "create_managed_temp", make_temp)
    for session in ("conversation-a", "conversation-b"):
        transcript = str(tmp_path / f"{session}.jsonl")
        rules_context.main(
            json.dumps(
                {
                    "hook_event_name": "SessionStart",
                    "source": source,
                    "session_id": session,
                    "transcript_path": transcript,
                }
            )
        )
        output = _output(capsys)
        assert f"この会話のセッションID: {session}" in output
        assert f"この会話のtranscript: {transcript}" in output
        other = "conversation-b" if session == "conversation-a" else "conversation-a"
        assert f"この会話のセッションID: {other}" not in output
        assert f"この会話のtranscript: {tmp_path / (other + '.jsonl')}" not in output


@pytest.mark.parametrize("host,delegated", (("claude", True), ("codex", False), ("codex", True)))
def test_conversation_identity_notice_is_limited_to_claude_main(
    host: str,
    delegated: bool,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Claude委譲先とCodexへメイン自身の記録取得の通知を配送しない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    if delegated:
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    monkeypatch.setattr(rules_context.managed_temp, "create_managed_temp", lambda *_args, **_kwargs: tmp_path)
    rules_context.main(
        json.dumps(
            {
                "hook_event_name": "SessionStart",
                "source": "startup",
                "session_id": "conversation-a",
                "transcript_path": str(tmp_path / "conversation-a.jsonl"),
            }
        ),
        host=host,
    )
    raw = capsys.readouterr().out
    output = json.loads(raw)["hookSpecificOutput"]["additionalContext"] if raw else ""
    assert "この会話のセッションID:" not in output
    assert "この会話のtranscript:" not in output


def _output(capsys: pytest.CaptureFixture[str]) -> str:
    return json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact", "fork"])
def test_session_start_main_claude_includes_main_and_claude_rules(
    source: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": source}))
    output = _output(capsys)
    assert rules_context.MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip() in output
    assert rules_context.MAIN_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip() in output
    assert rules_context.SUBAGENT_RULES_PATH.read_text(encoding="utf-8").rstrip() not in output
    assert (rules_context.QUALITY_CHECKPOINT_NOTICE in output) is (source == "compact")
    assert rules_context.RESPONSE_LANGUAGE_NOTICE in output
    normative_start = (
        f'<{rules_context.NORMATIVE_ELEMENT} source="{rules_context.NORMATIVE_SOURCE}" '
        f'kind="{rules_context.NORMATIVE_KIND_MAIN}">'
    )
    assert output.count(normative_start) == 1
    assert output.endswith(f"</{rules_context.NORMATIVE_ELEMENT}>")
    normative_body = output.split(normative_start, maxsplit=1)[1]
    assert rules_context.MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip() in normative_body
    assert rules_context.MAIN_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip() in normative_body


@pytest.mark.parametrize(
    ("name", "value"),
    [("AGENT_TOOLKIT_DELEGATED_SESSION", "1"), ("AGENT_TOOLKIT_OWNER_SESSION", "owner")],
)
def test_session_start_delegated_omits_main_rules(
    name: str, value: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setenv(name, value)
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup"}))
    output = _output(capsys)
    assert rules_context.MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip() not in output
    assert rules_context.MAIN_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip() not in output
    assert rules_context.SUBAGENT_RULES_PATH.read_text(encoding="utf-8").rstrip() not in output
    assert rules_context.RESPONSE_LANGUAGE_NOTICE not in output
    assert str(rules_context.SHARE_DIR.parent / "scripts" / ("atk.cmd" if os.name == "nt" else "atk")) in output


def test_session_start_compact_prepends_quality_notice(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact"}))
    output = _output(capsys)
    assert output.index(rules_context.QUALITY_CHECKPOINT_NOTICE) < output.index(
        rules_context.MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip()
    )

    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact"}))
    output = _output(capsys)
    assert rules_context.QUALITY_CHECKPOINT_NOTICE in output
    assert rules_context.MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip() not in output
    assert rules_context.MAIN_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip() not in output


def test_session_start_main_places_response_language_notice_first(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """使用言語の1文を、同じ本文の他の条文より前へ置く。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact"}))
    output = _output(capsys)
    notice_index = output.index(rules_context.RESPONSE_LANGUAGE_NOTICE)

    assert notice_index < output.index(rules_context.QUALITY_CHECKPOINT_NOTICE)
    assert notice_index < output.index(
        f'<{rules_context.NORMATIVE_ELEMENT} source="{rules_context.NORMATIVE_SOURCE}" '
        f'kind="{rules_context.NORMATIVE_KIND_MAIN}">'
    )


def test_response_language_notice_absent_for_delegates(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """委譲先とサブエージェントの本文へ使用言語の1文を渡さない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact"}))
    assert rules_context.RESPONSE_LANGUAGE_NOTICE not in _output(capsys)

    rules_context.main(json.dumps({"hook_event_name": "SubagentStart", "agent_type": "general-purpose"}))
    assert rules_context.RESPONSE_LANGUAGE_NOTICE not in _output(capsys)


def test_session_start_codex_excludes_claude_code_rules(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    rules_context_codex.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup"}))
    output = _output(capsys)
    assert rules_context.MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip() in output
    assert rules_context.MAIN_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip() not in output


def test_codex_main_receives_codex_main_rules(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Codexのメインだけが`rules-main.codex.md`を`rules-main.md`の後で受け取り、委譲先とClaude Codeは受け取らない。"""
    main_rules = rules_context.MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip()
    codex_main_rules = rules_context.MAIN_RULES_CODEX_PATH.read_text(encoding="utf-8").rstrip()
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)

    rules_context_codex.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup"}))
    codex_output = _output(capsys)
    assert codex_output.index(main_rules) < codex_output.index(codex_main_rules)

    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup"}))
    assert codex_main_rules not in _output(capsys)

    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    rules_context_codex.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup"}))
    delegated_output = _output(capsys)
    assert main_rules not in delegated_output
    assert codex_main_rules not in delegated_output
    rules_context_codex.main(json.dumps({"hook_event_name": "SubagentStart", "agent_type": "explorer"}))
    assert codex_main_rules not in _output(capsys)


@pytest.mark.parametrize("agent_type", ["Explore", "Plan", "general-purpose", "plan-reviewer"])
@pytest.mark.parametrize("environment", [None, "delegated", "owner"])
def test_subagent_start_claude_includes_common_and_claude_subagent_rules(
    agent_type: str, environment: str | None, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    if environment == "delegated":
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    elif environment == "owner":
        monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "owner")
    rules_context.main(json.dumps({"hook_event_name": "SubagentStart", "agent_type": agent_type}))
    output = _output(capsys)
    assert rules_context.SUBAGENT_RULES_PATH.read_text(encoding="utf-8").rstrip() in output
    assert rules_context.SUBAGENT_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip() in output
    assert rules_context.MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip() not in output


def test_subagent_start_codex_excludes_claude_code_rules(capsys: pytest.CaptureFixture[str]) -> None:
    """CodexのSubagentStartへClaude固有規範を追加しない。"""
    rules_context_codex.main(json.dumps({"hook_event_name": "SubagentStart", "agent_type": "explorer"}))
    output = _output(capsys)
    assert rules_context.SUBAGENT_RULES_PATH.read_text(encoding="utf-8").rstrip() in output
    assert rules_context.SUBAGENT_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip() not in output


def _session_start_length_report(output: str) -> str:
    """SessionStartの追加本文が上限を超えた場合の失敗本文を組み立てる。

    超過分と文書別の長さを示し、追随すべき対象を失敗本文から確定できるようにする。
    """
    total = len(output)
    overage = total - rules_context.CLAUDE_CODE_OUTPUT_LIMIT
    breakdown = "、".join(
        f"{path.name}={len(path.read_text(encoding='utf-8'))}文字"
        for path in (rules_context.MAIN_RULES_PATH, rules_context.MAIN_RULES_CLAUDE_CODE_PATH)
    )
    return (
        f"SessionStartの追加本文が{total}文字であり、上限{rules_context.CLAUDE_CODE_OUTPUT_LIMIT}文字を"
        f"{overage}文字超過した。文書別の長さ: {breakdown}"
    )


def test_session_start_context_fits_claude_code_cap(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "session-1"}))
    output = _output(capsys)
    assert len(output) <= rules_context.CLAUDE_CODE_OUTPUT_LIMIT, _session_start_length_report(output)


# compact前の内部状態を既存PreToolUse記録で組み立てる局所fixtureだけを許可する。
# pylint: disable=protected-access
@pytest.mark.parametrize(
    ("selected_agent", "acquired", "heading", "expected"),
    [
        (None, False, "", None),
        ("main", False, "", None),
        ("main", True, "", "全文の基準"),
        ("main", True, "「必要範囲」", "指定範囲の基準"),
        ("sub-1", True, "", None),
    ],
)
def test_compact_restores_selected_material_only_for_the_same_main_agent(
    selected_agent: str | None,
    acquired: bool,
    heading: str,
    expected: str | None,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """compact直後は、同一主体が選択済みの必要範囲だけを次のPreToolUseより先に回復する。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    document = tmp_path / "changed-rule.md"
    document.write_text(
        "# 変更規範\n\n全文の基準\n\n## 「必要範囲」\n\n指定範囲の基準\n\n## 「別範囲」\n\n配送しない本文\n",
        encoding="utf-8",
    )
    target = ReadTarget(document, heading)
    if selected_agent is not None:
        read_delivery._select_targets("compact-recovery", [target], selected_agent)
        if acquired:
            read_delivery._mark("compact-recovery", document, selected_agent, heading)

    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "compact-recovery"}))

    output = _output(capsys)
    records = read_state("compact-recovery").get(rules_context.OPERATION_SKILL_READY_KEY, {})
    if expected is None:
        assert "全文の基準" not in output
        assert not read_delivery.target_ready(records, target, "main")
    else:
        assert expected in output
        assert read_delivery.target_ready(records, target, "main")
        if heading:
            assert "配送しない本文" not in output


def test_compact_keeps_target_unread_and_shows_its_range_when_recovery_budget_is_insufficient(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """本文がSessionStart残予算へ収まらない場合、取得済みへ戻さず読取先と必要範囲を示す。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    document = tmp_path / "large-rule.md"
    document.write_text("# 大きい変更規範\n\n" + "本文" * rules_context.CLAUDE_CODE_OUTPUT_LIMIT, encoding="utf-8")
    target = ReadTarget(document)
    read_delivery._select_targets("compact-budget", [target], "main")
    read_delivery._mark("compact-budget", document, "main")

    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "compact-budget"}))

    output = _output(capsys)
    records = read_state("compact-budget").get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert str(document) in output
    assert "未取得" in output
    assert not read_delivery.target_ready(records, target, "main")
    assert len(output) <= rules_context.CLAUDE_CODE_OUTPUT_LIMIT


def test_compact_restores_acquired_material_for_a_delegated_independent_session(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """独立した委譲sessionも、自身のmain主体が取得済みだった資料をcompact後に回復する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    document = tmp_path / "delegated-rule.md"
    document.write_text("# 委譲規範\n\n委譲先の取得済み基準\n", encoding="utf-8")
    target = ReadTarget(document)
    read_delivery._select_targets("delegated-compact", [target], "main")
    read_delivery._mark("delegated-compact", document, "main")

    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "delegated-compact"}))

    output = _output(capsys)
    records = read_state("delegated-compact").get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert "委譲先の取得済み基準" in output
    assert read_delivery.target_ready(records, target, "main")


@pytest.mark.parametrize("between", ["clear", "compact"])
def test_compact_discards_undelivered_recovery_target_before_a_later_context_boundary(
    between: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """予算超過後の復旧候補をclearまたは次のcompactへ持ち越さない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    document = tmp_path / "large-rule.md"
    document.write_text("# 大きい変更規範\n\n" + "本文" * rules_context.CLAUDE_CODE_OUTPUT_LIMIT, encoding="utf-8")
    target = ReadTarget(document)
    session_id = f"stale-{between}"
    read_delivery._select_targets(session_id, [target], "main")
    read_delivery._mark(session_id, document, "main")

    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": session_id}))
    assert str(document) in _output(capsys)

    if between == "clear":
        rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "clear", "session_id": session_id}))
        assert str(document) not in _output(capsys)
        rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": session_id}))
    else:
        rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": session_id}))

    output = _output(capsys)
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert str(document) not in output
    assert not read_delivery.target_ready(records, target, "main")


# pylint: enable=protected-access
def test_subagent_start_context_fits_claude_code_cap(capsys: pytest.CaptureFixture[str]) -> None:
    """Claude固有規範を含むSubagentStart本文がhook上限へ収まる。"""
    rules_context.main(json.dumps({"hook_event_name": "SubagentStart", "agent_type": "general-purpose"}))
    assert len(_output(capsys)) <= rules_context.CLAUDE_CODE_OUTPUT_LIMIT


def test_session_start_length_report_shows_overage_and_breakdown() -> None:
    fake_output = "x" * (rules_context.CLAUDE_CODE_OUTPUT_LIMIT + 5)
    report = _session_start_length_report(fake_output)
    assert "5文字超過した" in report
    assert "文書別の長さ" in report
    assert f"{rules_context.MAIN_RULES_PATH.name}=" in report
    assert f"{rules_context.MAIN_RULES_CLAUDE_CODE_PATH.name}=" in report


def test_subagent_start_does_not_create_session_scoped_managed_temp(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """SubagentStartはsession_idを受け取っても領域を確保しない。"""
    monkeypatch.setattr(
        rules_context.managed_temp,
        "create_managed_temp",
        lambda *_args, **_kwargs: pytest.fail("SubagentStartで領域を作成した"),
    )

    rules_context.main(json.dumps({"hook_event_name": "SubagentStart", "session_id": "session-1"}))

    assert rules_context.SUBAGENT_RULES_PATH.read_text(encoding="utf-8").rstrip() in _output(capsys)


@pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact", "fork"])
def test_session_start_provides_one_session_scoped_managed_temp(
    source: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """同じsession_idの全SessionStartで1件の領域と同じ絶対パスを渡す。"""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    payload = json.dumps({"hook_event_name": "SessionStart", "source": source, "session_id": "session-1"})

    rules_context.main(payload)
    first_output = _output(capsys)
    rules_context.main(payload)
    second_output = _output(capsys)
    entries = managed_temp.list_managed_temp(session_id="session-1")

    assert len(entries) == 1
    assert entries[0]["path"] in first_output
    assert entries[0]["path"] in second_output
    assert managed_temp.sweep_expired_managed_temp(now=datetime.datetime.now(datetime.UTC)) == []
    session_root = pathlib.Path(entries[0]["path"])
    assert session_root.exists()
    child = managed_temp.create_session_temp("child", session_root)
    assert child.parent == session_root


@pytest.mark.parametrize(
    "payload",
    [{"hook_event_name": "Unknown"}, {"hook_event_name": "SessionStart"}],
)
def test_rejects_unknown_event_and_missing_source(payload: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        rules_context.main(json.dumps(payload))


@pytest.mark.parametrize("windows", [False, True])
def test_session_start_context_fits_cap_with_maximum_instruction(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, windows: bool
) -> None:
    """追加指示が上限まで載った場合も、固定分との合計がhook上限へ収まる。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv(rules_context.PROCESS_LOOP_INSTRUCTION_ENV, "あ" * process_loop_log.INSTRUCTION_MAX_CHARS)
    cli_instructions = plugin_environment.cli_instructions
    monkeypatch.setattr(plugin_environment, "cli_instructions", lambda root: cli_instructions(root, windows=windows))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")

    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "session-1"}))

    output = _output(capsys)
    assert len(output) <= rules_context.CLAUDE_CODE_OUTPUT_LIMIT, _session_start_length_report(output)
    assert "あ" * process_loop_log.INSTRUCTION_MAX_CHARS in output
    assert rules_context.MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip() in output
    assert rules_context.MAIN_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip() in output
    assert "このセッションのmanaged-temp:" in output


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("event", ["SessionStart", "SubagentStart"])
@pytest.mark.parametrize("windows", [False, True])
def test_direct_hooks_deliver_cli_when_bash_environment_file_is_unavailable(
    host: str, event: str, windows: bool, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bashの環境ファイルが無くても、hookの出力が実在するCLIの絶対パスとPOSIX・PowerShellでの呼び方を届ける。"""
    monkeypatch.delenv("CLAUDE_ENV_FILE", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setenv("PATH", "/user/tools")
    cli_instructions = plugin_environment.cli_instructions
    monkeypatch.setattr(plugin_environment, "cli_instructions", lambda root: cli_instructions(root, windows=windows))
    assert rules_context.main(json.dumps({"hook_event_name": event, "source": "startup"}), host=host) == 0
    output = _output(capsys)
    entry = rules_context.SHARE_DIR.parent / "scripts" / ("atk.cmd" if windows else "atk")
    assert entry.is_file()
    notified_entry = next(
        pathlib.Path(line.removeprefix("agent-toolkitのCLI: "))
        for line in output.splitlines()
        if line.startswith("agent-toolkitのCLI: ")
    )
    plugin_root = notified_entry.parent.parent
    assert notified_entry == entry
    assert plugin_root == rules_context.SHARE_DIR.parent
    skill = plugin_root / "skills" / "writing-standards" / "SKILL.md"
    assert skill.is_file()
    assert "name: writing-standards" in skill.read_text(encoding="utf-8")
    command = next(line.removeprefix("起動部分: ") for line in output.splitlines() if line.startswith("起動部分: "))
    if windows:
        assert command == "& '" + str(entry).replace("'", "''") + "'"
    else:
        assert shlex.split(command) == [str(entry)]
    assert len(output) <= rules_context.CLAUDE_CODE_OUTPUT_LIMIT
    assert os.environ["PATH"] == "/user/tools"
    assert "CLAUDE_ENV_FILE" not in os.environ


def test_session_start_injects_process_loop_instruction(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """process-loopが渡した追加指示をメインへ注入し、委譲先へは注入しない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv(rules_context.PROCESS_LOOP_INSTRUCTION_ENV, "既存のテストコードを先に読む")
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")

    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup", "session_id": "session-1"}))
    main_output = _output(capsys)

    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup", "session_id": "session-2"}))
    delegated_output = _output(capsys)

    assert "既存のテストコードを先に読む" in main_output
    assert f"<{rules_context.PROCESS_LOOP_INSTRUCTION_ELEMENT} " in main_output
    assert 'origin="user"' in main_output
    assert "既存のテストコードを先に読む" not in delegated_output


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("delegated", [False, True])
def test_session_start_temp_notice_points_to_registered_directory(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    host: str,
    delegated: bool,
) -> None:
    """両ホストのメイン・委譲先へ、実際に登録したセッションの領域を通知する。"""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    if delegated:
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")

    rules_context.main(
        json.dumps({"hook_event_name": "SessionStart", "source": "startup", "session_id": "session-2"}), host=host
    )
    output = _output(capsys)
    entries = managed_temp.list_managed_temp(session_id="session-2")

    assert len(entries) == 1
    assert str(entries[0]["path"]) in output
    assert pathlib.Path(entries[0]["path"]).is_dir()


def test_subagent_start_notifies_existing_session_temp_without_creating(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """SubagentStartは親のSessionStartが作成した領域を新たに作成せずに解決し、SessionStartと同じ所在と行動を通知する。

    Agentツールのサブエージェントへ所在が届かないと、委譲元が委譲プロンプトへ渡し忘れた場合に`/tmp`が選ばれる。
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup", "session_id": "session-3"}))
    _output(capsys)
    session_root = managed_temp.list_managed_temp(session_id="session-3")[0]["path"]
    monkeypatch.setattr(
        rules_context.managed_temp,
        "create_managed_temp",
        lambda *_args, **_kwargs: pytest.fail("SubagentStartで領域を作成した"),
    )

    rules_context.main(json.dumps({"hook_event_name": "SubagentStart", "session_id": "session-3"}))
    output = _output(capsys)

    assert f"このセッションのmanaged-temp: {session_root}" in output
    assert rules_context.SUBAGENT_RULES_PATH.read_text(encoding="utf-8").rstrip() in output


def test_subagent_start_without_parent_session_temp_omits_notice(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """親の領域が無い場合は領域の通知を加えない。"""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")

    rules_context.main(json.dumps({"hook_event_name": "SubagentStart", "session_id": "no-parent-area"}))

    assert "このセッションのmanaged-temp" not in _output(capsys)


def test_subagent_start_separates_temp_files_and_reuses_agent_directory(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """異なるAgentが同じ用途名で書いても衝突せず、再開は同じ場所へ届く。"""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup", "session_id": "parent"}))
    _output(capsys)
    parent = pathlib.Path(managed_temp.list_managed_temp(session_id="parent")[0]["path"])
    paths = []
    for agent in ("group-a", "group-b", "group-a"):
        rules_context.main(json.dumps({"hook_event_name": "SubagentStart", "session_id": "parent", "agent_id": agent}))
        output = _output(capsys)
        path = parent / f"agent-{agent}"
        assert str(path) in output
        assert str(parent) in output
        assert path.is_dir()
        paths.append(path)
    assert paths[0] == paths[2]
    assert paths[0] != paths[1]
    (paths[0] / "msg1.txt").write_text("担当A", encoding="utf-8")
    (paths[1] / "msg1.txt").write_text("担当B", encoding="utf-8")
    assert (paths[0] / "msg1.txt").read_text(encoding="utf-8") == "担当A"
    assert len(managed_temp.list_managed_temp(session_id="parent")) == 1


@pytest.mark.parametrize(
    ("source", "delegated", "main_warns"),
    [("clear", False, True), ("compact", False, True), ("compact", True, True), ("resume", False, False)],
)
@pytest.mark.parametrize(
    ("skill_name", "operation"),
    [
        ("search", {"tool_name": "Bash", "tool_input": {"command": "rg x"}}),
        ("delegation", {"tool_name": "Agent", "tool_input": {"prompt": "調査する"}}),
    ],
)
def test_session_start_resets_operation_skill_ready_for_main(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    delegated: bool,
    main_warns: bool,
    skill_name: str,
    operation: dict,
) -> None:
    """`clear`・`compact`の後は検索・委譲の本文を再配送し、サブエージェントの記録と`resume`の後の判定を保つ。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    if delegated:
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    else:
        monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))

    def _no_session_temp(*_args: object, **_kwargs: object) -> pathlib.Path:
        raise OSError("テストではセッションのmanaged-tempを作成しない")

    monkeypatch.setattr(rules_context.managed_temp, "create_managed_temp", _no_session_temp)

    def _hook_context(handler: Callable[[str], int], payload: dict) -> str:
        code = handler(json.dumps(payload))
        captured = capsys.readouterr()
        if code == 2:
            return captured.err
        return json.loads(captured.out)["hookSpecificOutput"]["additionalContext"] if captured.out else ""

    def _operation_context(agent_id: str | None) -> str:
        payload = {**operation, "session_id": "reset-skill"}
        if agent_id is not None:
            payload["agent_id"] = agent_id
        return _hook_context(pretooluse.main, payload)

    for agent_id in (None, "sub-1"):
        skill_payload: dict[str, object] = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": f"agent-toolkit:{skill_name}"},
            "session_id": "reset-skill",
        }
        if agent_id is not None:
            skill_payload["agent_id"] = agent_id
        _hook_context(posttooluse.main, skill_payload)

    _hook_context(rules_context.main, {"hook_event_name": "SessionStart", "source": source, "session_id": "reset-skill"})

    assert (_operation_context(None) != "") is main_warns
    assert _operation_context("sub-1") == ""

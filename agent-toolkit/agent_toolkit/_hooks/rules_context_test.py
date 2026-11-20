# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""rules_contextの契約テスト。"""

from __future__ import annotations

import json
import pathlib
import re
import shlex

import pytest

from agent_toolkit._common import plugin_environment
from agent_toolkit._common.session_state import read_state, update_state
from agent_toolkit._hooks import rules_context, rules_context_codex
from agent_toolkit._hooks.pretooluse import operation_skills
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit._testing.rules_context_support import _disable_review_model_start as _disable_review_model_start
from agent_toolkit._testing.rules_context_support import _output, _session_start_length_report


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

    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", make_temp)
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
        assert f"--transcript {shlex.quote(transcript)}" in output
        if source != "compact":
            assert output.count(f"--transcript {shlex.quote(transcript)}") == 1
        else:
            assert "この会話の原文が必要な場合" not in output
        other = "conversation-b" if session == "conversation-a" else "conversation-a"
        assert str(tmp_path / (other + ".jsonl")) not in output
        assert not any(line.startswith(("この会話のセッションID:", "この会話のtranscript:")) for line in output.splitlines())


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
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args, **_kwargs: tmp_path)
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


def _document_references(output: str) -> set[str]:
    """通知が名指しするMarkdownの資料を返す。規範のファイルへの案内が混入していないかの判定に使う。"""
    return set(re.findall(r"[^\s`「」（）]+\.md", output))


@pytest.mark.parametrize("delegated", [False, True])
@pytest.mark.parametrize("host", ["claude", "codex", "agy"])
@pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact", "fork"])
def test_session_start_carries_no_rule_bodies_or_rule_file_pointers(
    source: str, host: str, delegated: bool, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """SessionStartは規範の本文も規範のファイルへの案内も載せず、CLIの起動部分は届ける。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    if delegated:
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": source}), host=host)
    output = _output(capsys)
    assert 'kind="rules-' not in output
    assert _document_references(output) <= {str(rules_context.SUBAGENT_PLAYBOOK_PATH)}
    assert plugin_environment.cli_instructions(rules_context.PLUGIN_ROOT) in output


_DELEGATED_ENVIRONMENTS = [None, ("AGENT_TOOLKIT_DELEGATED_SESSION", "1"), ("AGENT_TOOLKIT_OWNER_SESSION", "owner")]


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("agent_id", [None, "builtin-child"])
@pytest.mark.parametrize("environment", _DELEGATED_ENVIRONMENTS)
@pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact", "fork"])
def test_session_start_delegated_requests_reread_only_after_compaction(
    environment: tuple[str, str] | None,
    source: str,
    agent_id: str | None,
    host: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """委譲先へは圧縮の後だけ、起動の本文の原本と役割文書の読み直しを1行で求める。メインへは渡さない。

    委譲先は`agents_server`の環境変数か、組み込みの委譲機能の子が入力に持つ`agent_id`で判定する。
    委譲先の起動時は起動の本文かSubagentStartが入口を届けるため、案内を重ねない。
    """
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    if environment is not None:
        monkeypatch.setenv(*environment)
    payload = {"hook_event_name": "SessionStart", "source": source} | ({"agent_id": agent_id} if agent_id else {})
    handler = rules_context.main if host == "claude" else rules_context_codex.main
    handler(json.dumps(payload))
    output = _output(capsys)
    delegated = environment is not None or agent_id is not None
    assert ("send_to_user" in output) == (host == "claude" and not delegated)
    assert ("AskUserQuestion" in output) == (host == "claude" and not delegated)
    assert output.count(rules_context.DELEGATED_COMPACT_NOTICE) == (1 if delegated and source == "compact" else 0)
    if delegated:
        assert rules_context.ENTRY_START_NOTICE not in output
        assert rules_context.ENTRY_COMPACT_NOTICE not in output
    notice = rules_context.delegated_compact_notice(None)
    assert "\n" not in notice
    assert str(rules_context.SUBAGENT_PLAYBOOK_PATH) in notice
    assert rules_context.SUBAGENT_PLAYBOOK_PATH.is_file()


def test_builtin_child_compaction_keeps_main_records_and_points_to_its_own_temp(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """組み込みの子の圧縮は、親と同じsession_idでもメインの記録と会話の要約に触れず、子の記録と一時領域だけを扱う。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args, **_kwargs: tmp_path)

    def unexpected(*_args, **_kwargs):
        pytest.fail("子の圧縮でメインの会話の要約を作った")

    monkeypatch.setattr(rules_context.process_tree, "run_with_timeout", unexpected)
    session_id = "builtin-child-compact"
    main_notice, child_notice = "pretooluse|main|notice", "pretooluse|child-1|notice"
    update_state(
        session_id,
        lambda _state: {
            operation_skills.NOTIFIED_KEY: {"main": ["agent-basics:search"], "child-1": ["agent-basics:search"]},
            "notice_fingerprints": {main_notice: ["main"], child_notice: ["child"]},
        },
    )
    transcript = tmp_path / "main-transcript.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    payload = {
        "hook_event_name": "SessionStart",
        "source": "compact",
        "session_id": session_id,
        "agent_id": "child-1",
        "transcript_path": str(transcript),
    }
    assert rules_context.main(json.dumps(payload)) == 0
    output = _output(capsys)
    state = read_state(session_id)
    assert state[operation_skills.NOTIFIED_KEY] == {"main": ["agent-basics:search"]}
    assert state["notice_fingerprints"] == {main_notice: ["main"]}
    assert f"このサブエージェント専用の一時領域: {tmp_path / 'agent-child-1'}" in output
    assert "compaction-digest" not in output
    assert not (tmp_path / "compaction-digest").exists()
    assert rules_context.DELEGATED_COMPACT_NOTICE in output


# test-copy-contract: 主体種別・委譲環境・ホストを変えても、サブエージェントへは入口の所在の1行だけを配送する契約。
@pytest.mark.parametrize("agent_type", ["Explore", "Plan", "general-purpose", "plan-reviewer"])
@pytest.mark.parametrize("environment", [None, "delegated", "owner"])
@pytest.mark.parametrize("host", ["claude", "codex"])
def test_subagent_start_points_to_subagent_entry_without_rule_bodies(
    agent_type: str,
    environment: str | None,
    host: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    if environment == "delegated":
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    elif environment == "owner":
        monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "owner")
    handler = rules_context.main if host == "claude" else rules_context_codex.main
    handler(json.dumps({"hook_event_name": "SubagentStart", "agent_type": agent_type}))
    output = _output(capsys)
    assert _notice_lines(output, rules_context.SUBAGENT_ENTRY_NOTICE) == [rules_context.SUBAGENT_ENTRY_NOTICE]
    assert str(rules_context.SUBAGENT_PLAYBOOK_PATH) in rules_context.SUBAGENT_ENTRY_NOTICE
    assert rules_context.SUBAGENT_PLAYBOOK_PATH.is_file()
    assert 'kind="rules-' not in output
    assert _document_references(output) == {str(rules_context.SUBAGENT_PLAYBOOK_PATH)}
    assert rules_context.ENTRY_START_NOTICE not in output


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


def _notice_lines(output: str, notice: str) -> list[str]:
    """`notice`を含む自動挿入の区分の本文を行で返す。"""
    block = next(block for block in output.split("<atk-auto ")[1:] if notice in block)
    return block.split(">\n", 1)[1].split("\n</atk-auto>", 1)[0].split("\n")


@pytest.mark.parametrize("delegated", [False, True])
@pytest.mark.parametrize("host", ["claude", "codex", "agy"])
@pytest.mark.parametrize("source", ["startup", "clear", "compact", "resume", "fork"])
def test_session_start_guides_main_to_entry_with_one_line(
    host: str, source: str, delegated: bool, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """文脈を失って始まる会話のメインへ、入口の起動を1行で案内する。入口の本文は通知へ載せない。

    委譲先へは起動の本文が入口を届けるため、案内しない。
    """
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    if delegated:
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": source}), host=host)
    output = _output(capsys)
    expected = (
        None
        if delegated
        else {
            "startup": rules_context.ENTRY_START_NOTICE,
            "clear": rules_context.ENTRY_START_NOTICE,
            "compact": rules_context.ENTRY_COMPACT_NOTICE,
        }.get(source)
    )
    for notice in (rules_context.ENTRY_START_NOTICE, rules_context.ENTRY_COMPACT_NOTICE):
        assert output.count(notice) == (1 if notice == expected else 0)
    if expected is None:
        return
    assert "\n" not in expected and "agent-toolkit:atk-mode" in expected
    # 同じ区分に並ぶのは1行の案内だけであり、入口の本文の行は通知のどこにも無い。
    one_line_notices = {rules_context.ENTRY_START_NOTICE, rules_context.ENTRY_COMPACT_NOTICE}
    assert set(_notice_lines(output, expected)) <= one_line_notices
    entry = (rules_context.PLUGIN_ROOT / "skills" / "atk-mode" / "SKILL.md").read_text(encoding="utf-8")
    headings = [line for line in entry.split("\n---\n", 1)[1].splitlines() if line.startswith("#")]
    assert headings and not any(f"\n{heading}\n" in output for heading in headings)


@pytest.mark.parametrize("source", ("startup", None))
@pytest.mark.parametrize("host", ("claude", "codex"))
def test_process_loop_claim_survives_session_start_failure(
    host: str, source: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """統括の確保は、source検証や以後のSessionStart処理が例外で失敗しても成立し、後から始まる入れ子の会話は統括にならない。"""
    from agent_toolkit._common import process_loop_session  # pylint: disable=import-outside-toplevel

    environ = {"AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1", "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_CLAIM": "launch-x"}
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    for name, value in environ.items():
        monkeypatch.setenv(name, value)

    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("SessionStartの処理の失敗")

    monkeypatch.setattr(rules_context, "compose_session_start", fail)
    for session in ("coordinator", "nested"):
        with pytest.raises((RuntimeError, ValueError)):
            rules_context.main(
                json.dumps({"hook_event_name": "SessionStart", "source": source, "session_id": session}), host=host
            )

    assert process_loop_session.is_identified_process_loop_session("coordinator", environ)
    assert not process_loop_session.is_identified_process_loop_session("nested", environ)

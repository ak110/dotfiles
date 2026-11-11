# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""rules_contextの契約テスト。"""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import subprocess

import pytest

from agent_toolkit._common import plugin_roots
from agent_toolkit._common.plugin_roots import _plugin_inventory
from agent_toolkit._common.session_state import read_state, update_state
from agent_toolkit._hooks import rules_context, rules_context_codex
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit._testing.rules_context_support import _disable_review_model_start as _disable_review_model_start
from agent_toolkit._testing.rules_context_support import _output, _session_start_length_report


@pytest.mark.parametrize("source", ["startup", "resume", "clear", "compact", "fork"])
def test_session_start_reacquires_plugin_inventory_for_the_same_session(tmp_path, monkeypatch, capsys, source):
    """同IDの再起動でも最初だけ再取得し、次hookは一覧を共有して他の状態を保つ。"""
    session_id = "inventory-restart"
    executable = tmp_path / "claude"
    executable.touch()
    monkeypatch.setattr(plugin_roots.shutil, "which", lambda *_args, **_kwargs: str(executable))
    monkeypatch.setattr(rules_context, "compose_session_start", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args: tmp_path)
    monkeypatch.setattr(rules_context, "_compact_recovery_context", lambda *_args, **_kwargs: None)
    calls = []

    def acquire(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps([{"generation": len(calls)}]), "")

    monkeypatch.setattr(plugin_roots.process_tree, "run_with_timeout", acquire)
    update_state(session_id, lambda _state: {"unrelated": {"preserved": True}})

    def hook_request():
        with plugin_roots.plugin_resolution_scope(session_id=session_id):
            return _plugin_inventory("claude", tmp_path)

    assert hook_request() == hook_request() == [{"generation": 1}]
    assert rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": source, "session_id": session_id})) == 0
    capsys.readouterr()
    assert "plugin_inventory" not in read_state(session_id)
    assert read_state(session_id)["unrelated"] == {"preserved": True}
    assert hook_request() == hook_request() == [{"generation": 2}]
    assert len(calls) == 2


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
    assert "日本語で書け" not in rules_context.RESPONSE_LANGUAGE_NOTICE
    assert "「使用言語」を参照せよ" in rules_context.RESPONSE_LANGUAGE_NOTICE
    assert "思考を除いてエージェントが書く全ての文" not in output
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

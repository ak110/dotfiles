"""実行主体別の規範追加handlerの契約テスト。"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._hooks import rules_context, rules_context_codex

_PLUGIN_ROOT = pathlib.Path(__file__).resolve().parents[2]


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
    assert capsys.readouterr().out == ""


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


def test_session_start_codex_excludes_claude_code_rules(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    rules_context_codex.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup"}))
    output = _output(capsys)
    assert rules_context.MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip() in output
    assert rules_context.MAIN_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip() not in output


@pytest.mark.parametrize("agent_type", ["Explore", "Plan", "general-purpose", "plan-reviewer"])
@pytest.mark.parametrize("environment", [None, "delegated", "owner"])
def test_subagent_start_includes_subagent_rules_only(
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
    assert rules_context.MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip() not in output


def test_hooks_json_registers_rules_context_without_matcher() -> None:
    hooks = json.loads((_PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    for event in ("SessionStart", "SubagentStart"):
        assert "matcher" not in hooks[event][0]
        assert hooks[event][0]["hooks"][0]["command"].endswith("hook.py rules_context")


def test_session_start_context_fits_claude_code_cap(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(managed_temp.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(managed_temp, "_state_root_path", lambda: tmp_path / "external-state")
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "session-1"}))
    assert len(_output(capsys)) <= rules_context.CLAUDE_CODE_OUTPUT_LIMIT


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
    monkeypatch.setattr(managed_temp.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(managed_temp, "_state_root_path", lambda: tmp_path / "external-state")
    payload = json.dumps({"hook_event_name": "SessionStart", "source": source, "session_id": "session-1"})

    rules_context.main(payload)
    first_output = _output(capsys)
    rules_context.main(payload)
    second_output = _output(capsys)
    entries = managed_temp.list_managed_temp(session_id="session-1")

    assert len(entries) == 1
    assert entries[0]["path"] in first_output
    assert entries[0]["path"] in second_output


def test_rules_files_do_not_contain_role_specific_sections() -> None:
    common = "\n".join(path.read_text(encoding="utf-8") for path in (_PLUGIN_ROOT / "rules").glob("*.md"))
    for value in ("## ユーザー向け発話ルール", "### ユーザー発話の解釈", "process_wi_skill_invoked"):
        assert value not in common
    assert "## ユーザー向け発話ルール" in rules_context.MAIN_RULES_PATH.read_text(encoding="utf-8")
    assert "## 確認事項の差し戻し" in rules_context.SUBAGENT_RULES_PATH.read_text(encoding="utf-8")


def test_rules_files_have_no_role_specific_sentences() -> None:
    allowed = {
        "- サブエージェントは細かく分け過ぎない（起動するごとに固定コストがあるため）",
        "委譲先は事象、根本原因及び対応案を完了報告へ含めて委譲元へ返し、自らは登録しない。",
        "委譲先は、起動側が確認した当該完備と実在を着手前に再確認して差し戻す手順を持たない（努力目標）。",
    }
    pattern = re.compile(r"^(?:- |\d+\. )?(?:委譲先|サブエージェント|メインエージェント)は")
    actual = {
        line
        for path in (_PLUGIN_ROOT / "rules").glob("*.md")
        for line in path.read_text(encoding="utf-8").splitlines()
        if pattern.match(line)
    }
    assert actual == allowed


def test_rules_files_have_no_main_only_capabilities() -> None:
    prohibited = (
        "`AskUserQuestion`で",
        "ユーザーへ報告",
        "UWIへ記録",
        "をSkill機能で起動して登録",
        "をSkill機能で起動してUWI",
    )
    common = "\n".join(path.read_text(encoding="utf-8") for path in (_PLUGIN_ROOT / "rules").glob("*.md"))
    assert not any(value in common for value in prohibited)


@pytest.mark.parametrize(
    "payload",
    [{"hook_event_name": "Unknown"}, {"hook_event_name": "SessionStart"}],
)
def test_rejects_unknown_event_and_missing_source(payload: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        rules_context.main(json.dumps(payload))

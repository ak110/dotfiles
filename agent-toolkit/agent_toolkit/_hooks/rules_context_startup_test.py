# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""rules_context_startupの契約テスト。"""

from __future__ import annotations

import datetime
import html
import json
import os
import pathlib
import shlex

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.wi import process_loop_log
from agent_toolkit._common import plugin_environment, plugin_roots
from agent_toolkit._hooks import rules_context
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit._testing.rules_context_support import _disable_review_model_start as _disable_review_model_start
from agent_toolkit._testing.rules_context_support import _output, _session_start_length_report


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
        "resolve_session_temp",
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
    session_root = managed_temp.find_session_temp("session-1")

    assert session_root is not None and session_root == managed_temp.session_temp_path("session-1")
    assert str(session_root) in first_output
    assert str(session_root) in second_output
    assert managed_temp.sweep_managed_temp(now=datetime.datetime.now(datetime.UTC)).deleted == []
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


@pytest.mark.parametrize("contract", ["", "short", "long"])
@pytest.mark.parametrize("root_length", [21, 241])
@pytest.mark.parametrize("windows", [False, True])
def test_session_start_context_fits_cap_with_maximum_instruction(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    windows: bool,
    root_length: int,
    contract: str,
) -> None:
    """追加指示が上限まで載った場合も、固定分との合計がhook上限へ収まる。

    CLIのrootと一時領域は通知本文へそのまま入るため、実checkoutやpytestの一時パスの長さで結果が変わらないよう、
    観測した短い配置（21文字）と長い配置（241文字）を明示した値で固定する。
    """
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv(rules_context.PROCESS_LOOP_INSTRUCTION_ENV, "あ" * process_loop_log.INSTRUCTION_MAX_CHARS)
    root = pathlib.Path("/" + "r" * (root_length - 1))
    assert len(str(root)) == root_length
    session_temp = root / "cache" / "agent-toolkit" / "managed-temp" / "session-0123456789abcdef"
    cli_instructions = plugin_environment.cli_instructions
    monkeypatch.setattr(plugin_environment, "cli_instructions", lambda _root: cli_instructions(root, windows=windows))
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda _session_id: session_temp)
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    # 圧縮後は作業契約の通知も同じ本文へ加わるため、契約の有無と長さを変えても上限へ収まることを確かめる。
    record = tmp_path / "work-contract.md"
    if contract:
        record.write_text("目的: 継続\n" + ("要件の記録\n" * (2000 if contract == "long" else 1)), encoding="utf-8")
        monkeypatch.setattr(rules_context.work_contract, "record_path", lambda _session_id: record)

    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "session-1"}))

    output = _output(capsys)
    assert len(output) <= rules_context.CLAUDE_CODE_OUTPUT_LIMIT, _session_start_length_report(output)
    assert "あ" * process_loop_log.INSTRUCTION_MAX_CHARS in output
    assert rules_context.MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip() in output
    assert rules_context.MAIN_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip() in output
    assert f"このセッションのmanaged-temp: {session_temp}" in output
    assert "起動部分:" in output and str(root / "scripts") in output
    if contract:
        assert str(record) in output or rules_context.work_contract.SHORT_NOTICE in output


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
    command = next(line.removeprefix("起動部分: ") for line in output.splitlines() if line.startswith("起動部分: "))
    notified_entry = pathlib.Path(shlex.split(command)[-1])
    plugin_root = notified_entry.parent.parent
    assert notified_entry == entry
    assert plugin_root == rules_context.SHARE_DIR.parent
    basics = plugin_roots.resolve_plugin_root("agent-basics", plugin_root)
    skill = basics / "skills" / "writing-standards" / "SKILL.md"
    assert skill.is_file()
    assert "name: writing-standards" in skill.read_text(encoding="utf-8")
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


@pytest.mark.parametrize("source", ["startup", "resume", "compact"])
@pytest.mark.parametrize("host", ["claude", "codex", "agy"])
@pytest.mark.parametrize("actor", ["main", "delegated", "owner", "subagent"])
def test_startup_input_is_delivered_only_to_main(source, host, actor, monkeypatch, capsys):
    """同じ実行の再開でも元のデータを届け、継承した委譲環境とサブエージェントへ適用しない。"""
    data = {"source": "run-skill", "skill": "example:check", "arguments": "</atk-auto>\n追加指示"}
    monkeypatch.setenv("AGENT_TOOLKIT_SESSION_START_INPUT", json.dumps(data))
    if actor == "delegated":
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    if actor == "owner":
        monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "parent")
    event = "SubagentStart" if actor == "subagent" else "SessionStart"
    assert rules_context.main(json.dumps({"hook_event_name": event, "source": source}), host=host) == 0
    context = _output(capsys)
    if actor == "main":
        marker = '<atk-auto source="run-skill" kind="startup-input">\n起動入力: '
        encoded = context.split(marker, 1)[1].splitlines()[0]
        assert json.loads(html.unescape(encoded)) == data
        assert "&lt;/atk-auto&gt;" in encoded
        assert "references/answer-transitions.md" in context and "agent-toolkit:report-completion" in context
    else:
        assert 'kind="startup-input"' not in context


@pytest.mark.parametrize("value", ["{", "[]", '{"source": []}', '{"source": "unknown"}'])
def test_invalid_startup_input_is_diagnosed(value, monkeypatch):
    """不正な起動入力を実行データとして配送しない。"""
    monkeypatch.setenv("AGENT_TOOLKIT_SESSION_START_INPUT", value)
    with pytest.raises(ValueError):
        rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup"}))


def test_large_startup_input_keeps_all_data_within_hook_cap(tmp_path, monkeypatch, capsys):
    """自由記述がhook上限より長くても、原本への参照から全実行データを取得できる。"""
    data = {"source": "run-skill", "skill": "example:check", "arguments": "追加指示" * 10000}
    monkeypatch.setenv("AGENT_TOOLKIT_SESSION_START_INPUT", json.dumps(data, ensure_ascii=False))
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args: tmp_path)
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup", "session_id": "large-input"}))
    context = _output(capsys)
    assert len(context) <= rules_context.CLAUDE_CODE_OUTPUT_LIMIT
    path = tmp_path / "startup-input.json"
    assert str(path) in context
    assert json.loads(path.read_text(encoding="utf-8")) == data
    assert "references/answer-transitions.md" in context and "agent-toolkit:report-completion" in context

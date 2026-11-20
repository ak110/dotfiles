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
from agent_toolkit._atk.wi import process_loop_identity, process_loop_log, submit
from agent_toolkit._common import plugin_environment, plugin_roots, session_state, skill_modes, windows_powershell
from agent_toolkit._hooks import rules_context
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit._testing.rules_context_support import _disable_review_model_start as _disable_review_model_start
from agent_toolkit._testing.rules_context_support import _output, _session_start_length_report


# test-copy-contract: 実際に生成したSubagentStart全出力がホストの文字数上限に収まる容量契約。
def test_subagent_start_context_fits_claude_code_cap(capsys: pytest.CaptureFixture[str]) -> None:
    """SubagentStart本文がhook上限へ収まる。"""
    rules_context.main(json.dumps({"hook_event_name": "SubagentStart", "agent_type": "general-purpose"}))
    assert len(_output(capsys)) <= rules_context.CLAUDE_CODE_OUTPUT_LIMIT


# test-copy-contract: SubagentStartでは入口の所在を配送しても領域作成APIを呼ばない副作用契約。
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

    assert rules_context.SUBAGENT_ENTRY_NOTICE in _output(capsys)


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
    assert rules_context.ENTRY_COMPACT_NOTICE in output
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
    monkeypatch.setattr(windows_powershell, "can_run_script", lambda script: script.is_file())
    cli_instructions = plugin_environment.cli_instructions
    monkeypatch.setattr(plugin_environment, "cli_instructions", lambda root: cli_instructions(root, windows=windows))
    assert rules_context.main(json.dumps({"hook_event_name": event, "source": "startup"}), host=host) == 0
    output = _output(capsys)
    entry = rules_context.PLUGIN_ROOT / "scripts" / ("atk.ps1" if windows else "atk")
    assert entry.is_file()
    command = next(line.removeprefix("起動部分: ") for line in output.splitlines() if line.startswith("起動部分: "))
    notified_entry = pathlib.Path(shlex.split(command)[-1])
    plugin_root = notified_entry.parent.parent
    assert notified_entry == entry
    assert plugin_root == rules_context.PLUGIN_ROOT
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
        # 過去の実行のUWIの照会は開始前の契約であり、終了契約の案内に含めない。
        before, contract = context.split("終了契約", 1)
        assert "作業を始める前" in before and "references/answer-transitions.md" in before
        assert "playbooks/report-completion.md" in contract and "answer-transitions" not in contract
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
    assert "references/answer-transitions.md" in context and "playbooks/report-completion.md" in context


@pytest.mark.parametrize("skill", ["agent-toolkit:review-session", skill_modes.PROCESS_WI, submit.SKILL])
def test_startup_input_does_not_inject_launch(skill, monkeypatch, capsys):
    """作業の始め方は目的文が示し、SessionStartは起動を差し込まずに終了契約の所在だけを示す。"""
    data = {"source": "run-skill", "skill": skill, "arguments": "入力"}
    monkeypatch.setenv("AGENT_TOOLKIT_SESSION_START_INPUT", json.dumps(data))
    assert rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup"}), host="claude") == 0
    context = _output(capsys)
    assert "指定スキルを起動し" not in context and "目的文が示す始め方" not in context
    assert "playbooks/report-completion.md" in context.split("終了契約", 1)[1]


def test_process_loop_startup_points_to_result_contract(monkeypatch, capsys):
    """常駐の統括には、移した手順書の「結果と終了」を終了契約として示す。"""
    data = {"source": "process-loop", "skill": skill_modes.PROCESS_WI, "mode": "all-repos", "manifest": "/m.json"}
    monkeypatch.setenv("AGENT_TOOLKIT_SESSION_START_INPUT", json.dumps(data))
    assert rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup"}), host="claude") == 0
    contract = _output(capsys).split("終了契約", 1)[1]
    assert "`agent-toolkit:atk-mode/playbooks/process-wi-all-repos-results.md`「結果と終了」" in contract
    assert "指定スキルを起動し" not in contract


@pytest.mark.parametrize(
    ("data", "autonomous", "kind"),
    [
        ({"source": "process-loop", "skill": skill_modes.PROCESS_WI, "mode": "all-repos"}, True, "all-repos"),
        ({"source": "run-skill", "skill": skill_modes.PROCESS_WI, "arguments": "single-lane 追加"}, True, "single-lane"),
        ({"source": "run-skill", "skill": "agent-toolkit:atk-mode", "arguments": "process-wi"}, True, "process-wi"),
        ({"source": "run-skill", "skill": "agent-toolkit:review-session", "arguments": "batch"}, False, None),
        ({"source": "run-skill", "skill": "example:check", "arguments": "process-wi"}, False, None),
    ],
)
@pytest.mark.parametrize("actor", ["main", "delegated", "compact"])
def test_startup_input_sets_autonomous_state(data, autonomous, kind, actor, monkeypatch, capsys, tmp_path):
    """自律モードの状態は起動入力が作業`process-wi`を名指しした最上位のセッションでだけ立てる。

    圧縮後のSessionStartでは再び設定しない。エージェントによる入口の起動から立てないことは、
    PostToolUseの試験（`posttooluse_process_wi_test.py`）が確かめる。
    """
    session_id = f"autonomous-{actor}-{data['skill'].replace(':', '-')}-{autonomous}"
    monkeypatch.setenv("AGENT_TOOLKIT_SESSION_START_INPUT", json.dumps(data, ensure_ascii=False))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    if data["source"] == "process-loop":
        monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
        monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", session_id)
    if actor == "delegated":
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    source = "compact" if actor == "compact" else "startup"
    payload = {"hook_event_name": "SessionStart", "source": source, "session_id": session_id}
    assert rules_context.main(json.dumps(payload), host="claude") == 0
    capsys.readouterr()
    state = session_state.read_state(session_id)
    expected = autonomous and actor == "main"
    assert bool(state.get(session_state.PROCESS_WI_STATE_KEY)) is expected
    assert state.get("session_title_kind") == (kind if expected else None)


def _process_loop_input(manifest: pathlib.Path) -> dict[str, str]:
    data = {"source": "process-loop", "skill": skill_modes.PROCESS_WI, "mode": "all-repos", "manifest": str(manifest)}
    return {"AGENT_TOOLKIT_SESSION_START_INPUT": json.dumps(data, ensure_ascii=False)}


@pytest.mark.parametrize("resume_only", [True, False])
def test_picker_resume_of_ordinary_conversation_stays_out_of_autonomous_mode(resume_only, monkeypatch, capsys, tmp_path):
    """`process-loop resume`のpickerで統括でない会話を選んだ再開は、元の目的の続行だけを通知し自律モードを立てない。

    統括の会話を選んだ再開（`resume_only`が偽）は、作業`process-wi all-repos`の統括として立てる。
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"instruction": ""}), encoding="utf-8")
    environ = _process_loop_input(manifest)
    process_loop_identity.prepare(environ)
    process_loop_identity.acknowledge(environ, resume_only=resume_only)
    environ |= {"AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1", "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_CLAIM": "picker"}
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    payload = {"hook_event_name": "SessionStart", "source": "resume", "session_id": "picked", "cwd": str(tmp_path)}
    assert rules_context.main(json.dumps(payload), host="claude") == 0
    assert ("再開予約から同じ会話を再開した" in _output(capsys)) is resume_only
    state = session_state.read_state("picked")
    assert bool(state.get(session_state.PROCESS_WI_STATE_KEY)) is not resume_only
    assert state.get("session_title_kind") == (None if resume_only else "all-repos")


@pytest.mark.parametrize(
    "coordinator_env",
    [{"AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID": "coordinator"}, {"AGENT_TOOLKIT_PROCESS_LOOP_SESSION_CLAIM": "claim"}],
)
def test_nested_cli_inheriting_launch_environment_stays_out_of_autonomous_mode(coordinator_env, monkeypatch, capsys, tmp_path):
    """統括が起動した入れ子のCLIは起動入力と環境印を継承するが、会話IDが統括と異なるため自律モードを立てない。"""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    for name, value in {
        **_process_loop_input(tmp_path / "manifest.json"),
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1",
        **coordinator_env,
    }.items():
        monkeypatch.setenv(name, value)
    for session_id in ("coordinator", "nested"):
        payload = {"hook_event_name": "SessionStart", "source": "startup", "session_id": session_id}
        assert rules_context.main(json.dumps(payload), host="claude") == 0
        capsys.readouterr()
    assert session_state.read_state("coordinator").get(session_state.PROCESS_WI_STATE_KEY) is True
    nested = session_state.read_state("nested")
    assert not nested.get(session_state.PROCESS_WI_STATE_KEY)
    assert nested.get("session_title_kind") is None


def test_batch_startup_input_separates_procedure_from_termination(monkeypatch, capsys):
    """batchの手順全体を終了契約として案内せず、開始前のUWI照会と区別する。"""
    data = {"source": "run-skill", "skill": "agent-toolkit:review-session", "batch_manifest": "/batch/manifest.json"}
    monkeypatch.setenv("AGENT_TOOLKIT_SESSION_START_INPUT", json.dumps(data))
    assert rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup"}), host="claude") == 0
    before, contract = _output(capsys).split("終了契約", 1)
    assert "references/answer-transitions.md" in before and "playbooks/review-session-batch.md" in before
    assert "playbooks/report-completion.md" in contract and "batch.md" not in contract

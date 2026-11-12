# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""rules_context_tempの契約テスト。"""

from __future__ import annotations

import json
import pathlib
import subprocess
import types
from collections.abc import Callable

import pytest

from agent_toolkit._atk import managed_temp, run_skill_batch
from agent_toolkit._common import session_activity, session_review
from agent_toolkit._hooks import posttooluse, pretooluse, rules_context
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit._testing.rules_context_support import _disable_review_model_start as _disable_review_model_start
from agent_toolkit._testing.rules_context_support import _output


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
    session_root = managed_temp.find_session_temp("session-2")

    assert session_root is not None and str(session_root) in output
    assert session_root.is_dir()


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
    session_root = managed_temp.find_session_temp("session-3")
    monkeypatch.setattr(
        rules_context.managed_temp,
        "resolve_session_temp",
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
    parent = managed_temp.find_session_temp("parent")
    assert parent is not None
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
    assert managed_temp.find_session_temp("parent") == parent


@pytest.mark.parametrize(
    ("source", "delegated", "main_warns"),
    [("clear", False, True), ("compact", False, True), ("compact", True, True), ("resume", False, False)],
)
@pytest.mark.parametrize(
    ("skill_name", "operation"),
    [
        ("agent-basics:search", {"tool_name": "Bash", "tool_input": {"command": "rg x"}}),
        (
            "agent-toolkit:delegation",
            {"tool_name": "Agent", "tool_input": {"prompt": "調査する", "subagent_type": "specialist"}},
        ),
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

    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", _no_session_temp)
    contract = tmp_path / "work-contract.md"
    contract.write_text("資料配送の試験\n", encoding="utf-8")
    monkeypatch.setattr(rules_context.work_contract, "record_path", lambda _session: contract)

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
            "tool_input": {"skill": skill_name},
            "tool_response": "起動成功",
            "session_id": "reset-skill",
        }
        if agent_id is not None:
            skill_payload["agent_id"] = agent_id
        _hook_context(posttooluse.main, skill_payload)

    _hook_context(rules_context.main, {"hook_event_name": "SessionStart", "source": source, "session_id": "reset-skill"})

    # 専用定義の起動を使い、起動手段の拒否と資料の取得案内を分ける。
    acquisition = f"`Skill`で`{skill_name}`"
    assert (acquisition in _operation_context(None)) is main_warns
    assert acquisition not in _operation_context("sub-1")


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize("event", ["SessionStart", "SessionEnd"])
@pytest.mark.parametrize("error", ["ModuleNotFoundError('agent_toolkit')", "RuntimeError('initialization-failed')"])
def test_hook_reports_unaccepted_child_failure_at_next_start(tmp_path, monkeypatch, capsys, host, event, error):
    """実native子の受付前失敗を、次の規範配送で一度報告しpendingを保持する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_SESSION_REVIEW_AUTO_START", "true")
    session_review.record(
        {
            "hook_event_name": "SessionStart",
            "session_id": "pending-main",
            "cwd": str(tmp_path),
            "transcript_path": str(tmp_path / "pending-main.jsonl"),
        },
        host=host,
    )
    monkeypatch.setattr(session_activity, "host_process", object)
    original_popen = subprocess.Popen

    def spawn(argv, **kwargs):
        with original_popen([argv[0], "-c", f"raise {error}"], **kwargs) as child:
            assert child.wait(timeout=30) == 1
        return types.SimpleNamespace(pid=None, wait=lambda: 1)

    monkeypatch.setattr(run_skill_batch.subprocess, "Popen", spawn)
    run_skill_batch.hook_request({"hook_event_name": event, "session_id": "source-main"}, host=host)
    assert session_review.pending()
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_SESSION_REVIEW_AUTO_START", "false")
    monkeypatch.setattr(rules_context, "compose_session_start", lambda *args, **kwargs: None)
    payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
    assert rules_context.main(payload, host=host) == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "実行受付前に終了" in context
    assert f"{host} {event}" in context
    assert "ユーザーへ伝えよ" in context
    assert "Get-Content -LiteralPath" in context or "cat --" in context
    assert "/agent-toolkit:review-session batch" in context
    log_path = next((session_review.directory() / "launches").glob("*.log"))
    assert str(log_path) in context
    assert error.split("(", 1)[0] in log_path.read_text(encoding="utf-8")
    assert rules_context.main(payload, host=host) == 0
    assert "実行受付前に終了" not in capsys.readouterr().out
    assert session_review.pending()

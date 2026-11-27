# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""rules_context_compactの契約テスト。"""

from __future__ import annotations

import json
import pathlib
from types import SimpleNamespace

import pytest

from agent_toolkit._common import session_review, session_review_analysis
from agent_toolkit._common.session_state import update_state
from agent_toolkit._hooks import (
    agents_server_observations,
    agents_server_session_advisor,
    rules_context,
    termination_evidence_state,
)
from agent_toolkit._hooks.agent_id import MAIN_AGENT_ID
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process
from agent_toolkit._testing.rules_context_support import _disable_review_model_start as _disable_review_model_start
from agent_toolkit._testing.rules_context_support import _output


@pytest.mark.parametrize("host", ["claude", "codex", "agy"])
@pytest.mark.parametrize("waiting", [False, True])
def test_compact_public_notice_uses_current_stage(tmp_path, monkeypatch, capsys, host, waiting):
    """待機の有無に依存せず、圧縮後に読む範囲を通知する。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    monkeypatch.setattr(rules_context, "compose_session_start", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rules_context, "_compaction_notice", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args: tmp_path)
    if waiting:
        rules_context.update_state("stage", lambda current: {**current, "waiting": {"target": "child"}})
    assert (
        rules_context.main(
            json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "stage"}), host=host
        )
        == 0
    )
    output = _output(capsys)
    assert "会話が圧縮された" in output
    assert "作業契約と会話の要約で、作業の目的と現行の依頼を復元してください" in output
    assert "再開時に案内された資料" in output
    assert "次に行う操作・判断の条件" in output
    assert "作業契約と会話の要約だけ" not in output
    assert "compaction-recovery.md" not in output


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize("boundary", ["clear", "compact"])
def test_context_boundary_restores_skill_notice(
    boundary: str, codex: bool, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    session_id = f"skill-boundary-{boundary}-{codex}"
    search = {
        "session_id": session_id,
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": "rg needle ."},
        **({"turn_id": "turn"} if codex else {}),
    }
    assert "agent-basics:search" in _run_hook_in_process("pretooluse", search).stdout
    again = _run_hook_in_process("pretooluse", search)
    assert "agent-basics:search" not in again.stdout and 'permissionDecision": "deny' not in again.stdout
    launched = {
        "hook_event_name": "PostToolUse",
        "session_id": session_id,
        "tool_name": "Skill",
        "tool_input": {"skill": "agent-basics:search"},
    }
    assert _run_hook_in_process("posttooluse", launched).returncode == 0
    assert "agent-basics:search" not in _run_hook_in_process("pretooluse", search).stdout
    start = {"hook_event_name": "SessionStart", "source": boundary, "session_id": session_id}
    assert _run_hook_in_process("rules_context", start).returncode == 0
    assert "agent-basics:search" in _run_hook_in_process("pretooluse", search).stdout


@pytest.mark.parametrize("agent_id", [None, "child-1"])
@pytest.mark.parametrize("boundary", ["clear", "compact"])
def test_context_boundary_forgets_completion_playbook_only_for_main(
    boundary: str, agent_id: str | None, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    session_id = f"completion-boundary-{boundary}-{agent_id}"
    digest = termination_evidence_state.playbook_digest()
    update_state(
        session_id,
        lambda _state: {
            termination_evidence_state.STATE_KEY: {"session_id": session_id, termination_evidence_state.CONTEXT_KEY: digest}
        },
    )
    assert termination_evidence_state.completion_in_context(session_id)
    start = {"hook_event_name": "SessionStart", "source": boundary, "session_id": session_id}
    if agent_id is not None:
        start["agent_id"] = agent_id
    assert _run_hook_in_process("rules_context", start).returncode == 0
    assert termination_evidence_state.completion_in_context(session_id) is (agent_id is not None)


@pytest.mark.parametrize("own_wait", [False, True])
def test_compact_delegation_wait_is_scoped_to_current_owner(tmp_path, monkeypatch, capsys, own_wait):
    """未回収と応答待ちを分け、別主体のロックを自分の待機に数えない。"""
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "state")
    monkeypatch.setattr(rules_context, "compose_session_start", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args: tmp_path)
    monkeypatch.setattr(
        agents_server_observations.shared_roots,
        "resolve_wait_identity",
        lambda *_args: SimpleNamespace(root_session_id="root", file_name="own.json"),
    )
    owners = ["other.json", "own.json"] if own_wait else ["other.json"]
    monkeypatch.setattr(agents_server_observations, "_held_wait_lock_owners", lambda _root: owners)
    monkeypatch.setattr(agents_server_observations, "_registered_wait_targets", lambda *_args: {"running"})
    monkeypatch.setattr(
        agents_server_session_advisor, "continuation_wait_seconds", lambda identifier: 5 if identifier == "reply" else None
    )
    update_state(
        "compact-wait",
        lambda state: {
            **state,
            "agents_server_sessions": {
                "running": {"owner_agent_id": MAIN_AGENT_ID, "pending_observation": True},
                "reply": {"owner_agent_id": MAIN_AGENT_ID, "pending_observation": False},
                "other-agent-child": {"owner_agent_id": "another-agent", "pending_observation": True},
            },
        },
    )
    payload = {"hook_event_name": "SessionStart", "source": "compact", "session_id": "compact-wait"}
    assert rules_context.main(json.dumps(payload), host="claude") == 0
    output = _output(capsys)
    assert "未回収: running" in output and "応答待ち: reply" in output
    assert "other-agent-child" not in output
    if own_wait:
        assert "自セッションが待機中: running" in output and "待機の再開は不要" in output
    else:
        assert "自セッションに有効な待機なし" in output and "待機の再開が必要: running" in output


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_batch_compaction_names_current_analysis_and_only_unfinished(tmp_path, monkeypatch, capsys, host):
    for target_host in ("claude", "codex"):
        assert session_review.record(
            {"hook_event_name": "SessionStart", "session_id": "same", "transcript_path": str(tmp_path / "same.jsonl")},
            host=target_host,
        )
    path = tmp_path / "batch.json"
    writer = session_review.BatchWriter(path)
    writer.create({"run_id": "batch", "targets": session_review.pending(), "prepared": []})
    analysis = session_review_analysis.ensure(writer)
    text = analysis.read_text(encoding="utf-8")
    analysis.write_text(text.replace("| claude:same | 未着手 |", "| claude:same | 完了 |"), encoding="utf-8")
    monkeypatch.setenv(session_review.BATCH_ENV, str(path))
    assert (
        rules_context.main(
            json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "batch"}), host=host
        )
        == 0
    )
    result = _output(capsys)
    assert str(analysis) in result
    assert "未完了の対象: codex:same。" in result
    assert "claude:same" not in result

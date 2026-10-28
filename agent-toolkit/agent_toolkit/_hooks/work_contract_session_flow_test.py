"""公開hookから既存の作業契約の所在を通知する流れを確認する。"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit._hooks import pretooluse, rules_context, user_prompt_submit, work_contract


def test_user_prompt_and_compaction_notify_existing_contract(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "work-contract.md"
    path.write_text("目的: A全体\n", encoding="utf-8")
    monkeypatch.setattr(work_contract, "record_path", lambda _session: path)
    monkeypatch.setattr(user_prompt_submit, "_is_machine_injected", lambda *_args: False)
    monkeypatch.setattr(user_prompt_submit, "_claim_user_confirmation_notice", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(user_prompt_submit.termination_evidence, "observe_user", lambda *_args: None)
    assert user_prompt_submit.main(json.dumps({"session_id": "session", "prompt": "続けて"})) == 0
    assert str(path) in capsys.readouterr().out
    monkeypatch.setattr(rules_context, "is_delegated", lambda *_args: False)
    assert work_contract.notice("session") is not None


@pytest.mark.parametrize("initial_prompt", [None, "調査を進めて", '<atk-auto source="process-loop">/goal 作業</atk-auto>'])
def test_missing_contract_public_hook_flow(tmp_path, monkeypatch, capsys, initial_prompt):
    """初回、自動goal、追加入力、圧縮後の最初の操作だけで作成条件を届ける。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    path = tmp_path / "work-contract.md"
    monkeypatch.setattr(work_contract, "record_path", lambda *_args: path)
    session = "missing-public-flow"

    def operation():
        assert (
            pretooluse.main(json.dumps({"session_id": session, "tool_name": "Skill", "tool_input": {"skill": "unknown"}})) == 0
        )
        return capsys.readouterr().out

    if initial_prompt is not None:
        assert user_prompt_submit.main(json.dumps({"session_id": session, "prompt": initial_prompt})) == 0
        capsys.readouterr()
    output = operation()
    assert str(path) in output
    assert "成果物の作成・変更・操作または複数工程の調査" in output
    assert "事実回答では作成しない" in output
    assert "作業契約が未作成" not in operation()
    assert user_prompt_submit.main(json.dumps({"session_id": session, "prompt": "続きを進めて"})) == 0
    capsys.readouterr()
    assert str(path) in operation()
    assert "作業契約が未作成" not in operation()
    assert rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": session})) == 0
    capsys.readouterr()
    assert str(path) in operation()
    assert "作業契約が未作成" not in operation()
    path.write_text("目的: 継続\n", encoding="utf-8")
    assert user_prompt_submit.main(json.dumps({"session_id": session, "prompt": "続けて"})) == 0
    capsys.readouterr()
    assert "作業契約が未作成" not in operation()


@pytest.mark.parametrize("env", ["AGENT_TOOLKIT_OWNER_SESSION", "AGENT_TOOLKIT_DELEGATED_SESSION"])
def test_delegated_public_hook_has_no_missing_notice(tmp_path, monkeypatch, capsys, env):
    monkeypatch.setenv(env, "1")
    monkeypatch.setattr(work_contract, "record_path", lambda *_args: tmp_path / "work-contract.md")
    assert pretooluse.main(json.dumps({"session_id": "delegated", "tool_name": "Skill", "tool_input": {}})) == 0
    assert "作業契約が未作成" not in capsys.readouterr().out

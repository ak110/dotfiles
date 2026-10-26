"""公開hookから既存の作業契約の所在を通知する流れを確認する。"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit._hooks import rules_context, user_prompt_submit, work_contract


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

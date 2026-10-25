"""現行の作業契約と実ユーザー入力の機械的な対応を検証する。"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._hooks import rules_context, user_prompt_submit, work_contract


@pytest.fixture(name="contract")
def _contract(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[pathlib.Path, dict[str, Any]]:
    path = tmp_path / "work-contract.md"
    state: dict[str, Any] = {}

    def update(_session: str, function: Callable[[dict], dict | None], **_kwargs: Any) -> bool:
        result = function(state)
        if result is not None:
            state.update(result)
        return result is not None

    monkeypatch.setattr(work_contract, "record_path", lambda _session: path)
    monkeypatch.setattr(work_contract, "read_state", lambda _session: state.copy())
    monkeypatch.setattr(work_contract, "update_state", update)
    return path, state


def test_input_without_record_only_advances_position(contract: tuple[pathlib.Path, dict[str, Any]]) -> None:
    """自己完結した発話へ現行記録の作成通知を増やさない。"""
    path, state = contract
    assert work_contract.observe_input("session") is None
    assert not path.exists()
    assert state["work_contract_input_sequence"] == 1


def test_multiple_inputs_remain_pending_until_contiguous_integration(contract: tuple[pathlib.Path, dict[str, Any]]) -> None:
    """途中の未処理を残した記録では、後続入力と圧縮後にも同じ未処理区間を示す。"""
    path, _state = contract
    path.write_text("目的: A全体\n処理済み入力: session:0\n", encoding="utf-8")
    work_contract.observe_input("session")
    assert "session:1..2" in (work_contract.observe_input("session") or "")
    assert "session:1..2" in (work_contract.notice("session") or "")
    path.write_text("目的: A全体\n処理済み入力: session:2\n", encoding="utf-8")
    notice = work_contract.notice("session")
    assert notice is not None
    assert "未処理入力:" not in notice
    assert "正しさの証拠にはならない" in notice
    assert "session:3..3" in (work_contract.observe_input("session") or "")


def test_foreign_session_marker_does_not_consume_input(contract: tuple[pathlib.Path, dict[str, Any]]) -> None:
    """前任の処理済み連番を、現在の会話の入力の証拠として使わない。"""
    path, _state = contract
    path.write_text("処理済み入力: predecessor:90\n", encoding="utf-8")
    assert "session:1..1" in (work_contract.observe_input("session") or "")


@pytest.mark.parametrize("prompt,source", [("状況は？", "user"), ("A-bはc'へ", "user")])
def test_real_user_prompt_routes_to_contract(
    contract: tuple[pathlib.Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    prompt: str,
    source: str,
) -> None:
    """質問と訂正を同じ未処理入力の通知へ渡す。分類はエージェントが行う。"""
    path, _state = contract
    path.write_text("目的: A全体\n", encoding="utf-8")
    monkeypatch.setattr(user_prompt_submit, "_is_machine_injected", lambda *_args: False)
    monkeypatch.setattr(user_prompt_submit, "_claim_user_confirmation_notice", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(user_prompt_submit.termination_evidence, "observe_user", lambda *_args: None)
    assert user_prompt_submit.main(json.dumps({"session_id": "session", "prompt": prompt, "source": source})) == 0
    result = json.loads(capsys.readouterr().out)
    assert "session:1..1" in result["hookSpecificOutput"]["additionalContext"]


def test_machine_prompt_does_not_advance_input(
    contract: tuple[pathlib.Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """機械注入の通知は実ユーザー入力の連番を消費しない。"""
    path, state = contract
    path.write_text("目的: A全体\n", encoding="utf-8")
    monkeypatch.setattr(user_prompt_submit, "_is_machine_injected", lambda *_args: True)
    monkeypatch.setattr(user_prompt_submit.termination_evidence, "observe_user", lambda *_args: None)
    assert user_prompt_submit.main(json.dumps({"session_id": "session", "prompt": "自動通知"})) == 0
    assert not state
    assert not capsys.readouterr().out


def test_compaction_notifies_record_and_pending_inputs(
    contract: tuple[pathlib.Path, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: pathlib.Path,
) -> None:
    """会話圧縮後のSessionStartから現行記録と未処理位置へ到達できる。"""
    path, _state = contract
    path.write_text("目的: A全体\n", encoding="utf-8")
    work_contract.observe_input("session")
    monkeypatch.setattr(rules_context, "is_delegated", lambda *_args: False)
    monkeypatch.setattr(rules_context, "reset_main_operation_skill_ready", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(rules_context, "mark_user_confirmation_pending", lambda *_args: None)
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args, **_kwargs: tmp_path)
    assert (
        rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "session"})) == 0
    )
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert str(path) in context
    assert "session:1..1" in context


def test_state_save_failure_is_delivered_without_false_acknowledgement(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """atomic_writeの実際のOSError→update_stateのFalseを公開hookが通知する。"""
    path = tmp_path / "work-contract.md"
    path.write_text("目的: A全体\n処理済み入力: session:1\n", encoding="utf-8")
    state = tmp_path / "state.json"
    state.write_text('{"work_contract_input_sequence":1}', encoding="utf-8")
    monkeypatch.setattr(session_state, "state_path", lambda *_args: state)
    monkeypatch.setattr(work_contract, "record_path", lambda *_args: path)
    monkeypatch.setattr(work_contract, "update_state", session_state.update_state)
    monkeypatch.setattr(work_contract, "read_state", session_state.read_state)

    def fail_write(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("保存失敗の故障注入")

    monkeypatch.setattr(session_state, "_atomic_write", fail_write)
    monkeypatch.setattr(user_prompt_submit, "_is_machine_injected", lambda *_args: False)
    monkeypatch.setattr(user_prompt_submit, "_claim_user_confirmation_notice", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(user_prompt_submit.termination_evidence, "observe_user", lambda *_args: None)
    assert user_prompt_submit.main(json.dumps({"session_id": "session", "prompt": "A-bをc'へ"})) == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "今回の実ユーザー入力の位置を保存できなかった" in context
    assert "処理済み連番は更新せず" in context
    assert "session:2" not in context
    assert session_state.read_state("session")["work_contract_input_sequence"] == 1
    assert "処理済み入力: session:1" in path.read_text(encoding="utf-8")

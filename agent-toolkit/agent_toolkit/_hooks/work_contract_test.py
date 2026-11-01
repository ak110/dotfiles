"""現行の作業契約の本文通知を検証する。"""

from __future__ import annotations

import pathlib

import pytest

from agent_toolkit._hooks import work_contract


def test_existing_contract_is_not_a_input_ledger(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "work-contract.md"
    path.write_text("目的: A全体\n", encoding="utf-8")
    monkeypatch.setattr(work_contract, "record_path", lambda _session: path)
    notice = work_contract.observe_input("session")
    assert notice is not None and str(path) in notice
    assert "目的: A全体" in notice
    assert "機械生成の参照資料" in notice
    assert "元の未完了工程へ戻れ" in notice
    assert "今回の原入力" in notice
    assert path.read_text(encoding="utf-8") == "目的: A全体\n"


def test_oversized_contract_falls_back_to_location(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """上限を超える本文は載せず、所在と復帰基準だけを示す。"""
    path = tmp_path / "work-contract.md"
    path.write_text("目的: " + "長" * 50 + "\n", encoding="utf-8")
    monkeypatch.setattr(work_contract, "record_path", lambda _session: path)
    notice = work_contract.notice("session", budget=10)
    assert notice is not None and str(path) in notice
    assert "長" * 50 not in notice
    assert "上限10文字を超える" in notice
    assert "元の未完了工程へ戻れ" in notice


def test_unreadable_contract_reports_diagnostic(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """読めない記録は診断と所在を示し、例外で入力処理を止めない。"""
    path = tmp_path / "work-contract.md"
    path.write_bytes(b"\xff\xfe\x00broken")
    monkeypatch.setattr(work_contract, "record_path", lambda _session: path)
    notice = work_contract.notice("session")
    assert notice is not None and "現行の作業契約を読めない（UnicodeDecodeError" in notice
    assert str(path) in notice


def test_missing_contract_has_no_notice(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(work_contract, "record_path", lambda _session: tmp_path / "work-contract.md")
    assert work_contract.notice("session") is None


def test_missing_notice_resolves_path_before_temp_exists(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setattr(work_contract, "record_path", lambda *_args: None)
    monkeypatch.setattr(work_contract.managed_temp, "session_temp_path", lambda *_args: tmp_path)
    assert str(tmp_path / "work-contract.md") in (work_contract.missing_notice("missing-directory") or "")
    assert work_contract.missing_notice("missing-directory") is None
    assert work_contract.missing_notice("subagent", agent_id="child") is None

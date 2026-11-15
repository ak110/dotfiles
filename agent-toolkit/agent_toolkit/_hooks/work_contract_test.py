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
    assert "work-contract-updates.md" in notice and "work-contract-boundaries.md" in notice
    assert "`agent-toolkit/share/work-contract.md`の条件別資料" in notice
    assert "部分的な回答・調査・説明・計画・途中報告だけ" not in notice
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
    assert "work-contract-updates.md" in notice
    assert "agent-toolkit/share/work-contract.md" in notice
    assert "cat --" not in notice and "Get-Content" not in notice


def test_unreadable_contract_reports_diagnostic(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """読めない記録は診断と所在を示し、例外で入力処理を止めない。"""
    path = tmp_path / "work-contract.md"
    path.write_bytes(b"\xff\xfe\x00broken")
    monkeypatch.setattr(work_contract, "record_path", lambda _session: path)
    notice = work_contract.notice("session")
    assert notice is not None and "現行の作業契約を読めない（UnicodeDecodeError" in notice
    assert str(path) in notice
    assert "work-contract-updates.md" in notice
    assert "agent-toolkit/share/work-contract.md" in notice
    assert "cat --" not in notice and "Get-Content" not in notice


def test_missing_contract_has_no_notice(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(work_contract, "record_path", lambda _session: tmp_path / "work-contract.md")
    assert work_contract.notice("session") is None


def test_read_instruction_preserves_external_path():
    """空白と引用符を含む外部原本の所在を保持し、取得手段を重ねない。"""
    path = pathlib.Path("a b'c/work-contract.md")
    instruction = work_contract.short_notice(path)
    assert str(path) in instruction
    assert "cat --" not in instruction and "Get-Content" not in instruction

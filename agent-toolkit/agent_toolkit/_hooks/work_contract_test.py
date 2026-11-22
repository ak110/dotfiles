"""現行の作業契約の本文通知を検証する。"""

from __future__ import annotations

import os
import pathlib

import pytest

from agent_toolkit._hooks import work_contract


def test_existing_contract_is_not_a_input_ledger(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "work-contract.md"
    path.write_text("目的: A全体\n", encoding="utf-8")
    monkeypatch.setattr(work_contract, "record_path", lambda _session: path)
    notice = work_contract.observe_input("session")
    assert notice is not None and str(path) in notice
    assert "目的: A全体" not in notice
    assert "作業契約の最終更新後の実ユーザー発話: 1件" in notice
    assert "最終更新: `" not in notice
    assert path.read_text(encoding="utf-8") == "目的: A全体\n"


def test_oversized_contract_falls_back_to_location(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "work-contract.md"
    path.write_text("目的: " + "長" * 50 + "\n", encoding="utf-8")
    monkeypatch.setattr(work_contract, "record_path", lambda _session: path)
    notice = work_contract.notice("session", budget=10)
    assert notice is not None and str(path) in notice
    assert "長" * 50 not in notice
    assert "上限10文字を超える" in notice
    assert "完了した要求が残っていれば、現在の目的・要件だけに書き直せ" in notice
    assert "成果物を編集する前に作業契約の「現在有効な要求・制約」へ反映" in notice
    assert "質問、状況照会、継続だけなら更新しない" in notice
    assert "「更新する時点」" in notice
    assert "agent-toolkit/skills/atk-mode/references/work-contract.md" in notice
    assert "cat --" not in notice and "Get-Content" not in notice


def test_unreadable_contract_reports_diagnostic(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "work-contract.md"
    path.write_bytes(b"\xff\xfe\x00broken")
    monkeypatch.setattr(work_contract, "record_path", lambda _session: path)
    notice = work_contract.notice("session")
    assert notice is not None and "現行の作業契約を読めない（UnicodeDecodeError" in notice
    assert str(path) in notice
    assert "「更新する時点」" in notice
    assert "agent-toolkit/skills/atk-mode/references/work-contract.md" in notice
    assert "cat --" not in notice and "Get-Content" not in notice


def test_missing_contract_has_no_notice(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(work_contract, "record_path", lambda _session: tmp_path / "work-contract.md")
    assert work_contract.notice("session") is None


def test_read_instruction_preserves_external_path():
    path = pathlib.Path("a b'c/work-contract.md")
    instruction = work_contract.short_notice(path)
    assert str(path) in instruction
    assert "cat --" not in instruction and "Get-Content" not in instruction


def test_input_count_since_update_distinguishes_first_observation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """契約の更新後の実ユーザー発話を数え、初回観測より前を0件と表示しない。"""
    path = tmp_path / "work-contract.md"
    path.write_text("目的: A\n", encoding="utf-8")
    os.utime(path, (1_000_000_000, 1_000_000_000))
    monkeypatch.setattr(work_contract, "record_path", lambda _session: path)
    first = work_contract.observe_input("counting")
    second = work_contract.observe_input("counting")
    assert first is not None and "作業契約の最終更新後の実ユーザー発話: 1件（今回を含む。初回観測より前は不明）" in first
    assert second is not None and "作業契約の最終更新後の実ユーザー発話: 2件（今回を含む。初回観測より前は不明）" in second
    assert "更新漏れの件数ではない" in second
    path.write_text("目的: A\n要件: B\n", encoding="utf-8")
    os.utime(path, (1_000_000_100, 1_000_000_100))
    updated = work_contract.observe_input("counting")
    assert updated is not None and "作業契約の最終更新後の実ユーザー発話: 1件（今回を含む）" in updated
    assert "最終更新: `" not in updated

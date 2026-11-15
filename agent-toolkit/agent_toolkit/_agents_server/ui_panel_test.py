"""費用接続は既存の原本ローダーと算出を通し、自己だけのCLI範囲を保つ。"""

import pathlib
from types import SimpleNamespace

import pytest

from agent_toolkit._agents_server import ui_panel


def test_cost_entry_does_not_turn_unreadable_record_into_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """原本の解釈失敗を空の費用として返さない。"""
    path = tmp_path / "broken.jsonl"
    path.write_text("{\n", encoding="utf-8")
    monkeypatch.setattr(
        ui_panel.session_record_paths,
        "find_session_record",
        lambda _session: SimpleNamespace(engine="claude", paths=[path]),
    )
    with pytest.raises(ValueError):
        ui_panel.main(["--session-id", "record"])
    assert capsys.readouterr().out == ""

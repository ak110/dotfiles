"""プロジェクト設定と配布元ルールの横断契約を検証する。"""

from pathlib import Path

import pytest

from agent_toolkit._atk.setup_project_test import _run


def test_with_rules_copies_plugin_rules_and_clean_removes_both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """配布元の全ルールを同じ内容で配置し、cleanで配布先を除去する。"""
    target = tmp_path / "project"
    (target / ".claude" / "skills").mkdir(parents=True)
    source = Path(__file__).resolve().parents[2] / "rules"

    assert _run(monkeypatch, target, "--with-rules") == 0
    assert _run(monkeypatch, target, "--with-rules") == 0
    destination = target / ".claude" / "rules" / "agent-toolkit"
    assert {path.name for path in destination.iterdir()} == {path.name for path in source.iterdir()}
    assert (destination / "01-agent.md").read_bytes() == (source / "01-agent.md").read_bytes()
    assert _run(monkeypatch, target, "--clean") == 0
    assert not (target / ".agents").exists()
    assert not (target / ".claude" / "rules").exists()

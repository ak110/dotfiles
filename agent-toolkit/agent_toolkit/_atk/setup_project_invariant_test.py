"""プロジェクト設定の削除が、以前に複製した規範まで回収することを検証する。"""

from pathlib import Path

import pytest

from agent_toolkit._atk.setup_project_test import _run


def test_clean_removes_links_and_previously_copied_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """規範の複製は廃止したため、以前の版が複製した規範をcleanが共有リンクと一緒に除去する。"""
    target = tmp_path / "project"
    (target / ".claude" / "skills").mkdir(parents=True)
    assert _run(monkeypatch, target) == 0
    for name in ("agent-toolkit", "agent-basics"):
        copied = target / ".claude" / "rules" / name
        copied.mkdir(parents=True)
        (copied / "legacy.md").write_text("# 旧い規範\n", encoding="utf-8")

    assert _run(monkeypatch, target, "--clean") == 0
    assert not (target / ".agents").exists()
    assert not (target / ".claude" / "rules").exists()


def test_with_rules_option_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """配布元の規範が無いため、規範を複製する指定は引数の誤りとして拒否する。"""
    target = tmp_path / "project"
    target.mkdir()
    assert _run(monkeypatch, target, "--with-rules") == 2
    assert not (target / ".claude" / "rules").exists()

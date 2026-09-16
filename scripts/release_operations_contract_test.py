"""日次リリースの再開契約を検証する。"""

from pathlib import Path


def test_daily_release_reuses_exact_open_pull_request() -> None:
    """既存PRの再利用、未存在時の作成、複数時の停止を手順へ保持する。"""
    procedure = (Path(__file__).resolve().parents[1] / ".claude/skills/dotfiles-release/SKILL.md").read_text(encoding="utf-8")

    assert "--base master --head develop --state open --limit 2" in procedure
    assert "1件ならそのPRを再利用" in procedure
    assert "0件なら" in procedure and "gh pr create" in procedure
    assert "2件取得した場合は対象を推測せず" in procedure
    assert "既存又は新規PRの完全なURL" in procedure

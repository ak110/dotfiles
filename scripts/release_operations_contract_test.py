"""日次リリースの再開契約を検証する。"""

from pathlib import Path


def test_daily_release_reuses_exact_open_pull_request() -> None:
    """既存PRの再利用、未存在時の作成、複数時の停止を手順へ保持する。"""
    operations = (Path(__file__).resolve().parents[1] / "docs" / "development" / "operations.md").read_text(encoding="utf-8")

    assert "--base master --head develop --state open --limit 2" in operations
    assert "1件ならそのPRを再利用" in operations
    assert "0件なら" in operations and "gh pr create" in operations
    assert "2件取得した場合は対象を推測せず" in operations
    assert "既存又は新規PRの完全なURL" in operations

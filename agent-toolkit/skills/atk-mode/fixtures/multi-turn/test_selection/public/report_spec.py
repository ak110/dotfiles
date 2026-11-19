"""report.build_reportの集計値を確かめる。"""

import report  # ty: ignore[import-not-found]  # 模擬リポジトリは同じディレクトリのモジュールを実行時のsys.pathで解決する


def test_build_report_values() -> None:
    """行数を集計する。"""
    assert report.build_report(["a", "b"])["count"] == 2

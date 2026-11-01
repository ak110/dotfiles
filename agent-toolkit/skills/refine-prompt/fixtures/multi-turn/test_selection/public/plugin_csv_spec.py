"""CSVプラグインの分解処理を確かめる。"""

import plugin_csv  # ty: ignore[unresolved-import]  # 模擬リポジトリは同じディレクトリのモジュールを実行時のsys.pathで解決する


def test_parse_cells() -> None:
    """前後の空白を除いて分解する。"""
    assert plugin_csv.parse(" a ,b") == ["a", "b"]

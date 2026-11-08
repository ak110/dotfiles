"""符号化の形式を確かめる。"""

import protocol  # ty: ignore[import-not-found]  # 模擬リポジトリは同じディレクトリのモジュールを実行時のsys.pathで解決する


def test_encode_sorted_pairs() -> None:
    """キー順に並べる。"""
    assert protocol.encode({"b": 1, "a": 2}) == "a=2;b=1"

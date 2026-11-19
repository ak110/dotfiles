"""文字列の補助処理を確かめる。"""

import util_text  # ty: ignore[import-not-found]  # 模擬リポジトリは同じディレクトリのモジュールを実行時のsys.pathで解決する


def test_slugify() -> None:
    """記号を区切りとして扱う。"""
    assert util_text.slugify("Hello, World!") == "hello-world"

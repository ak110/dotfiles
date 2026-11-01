"""設定からのプラグイン読み込みを確かめる。"""

import loader  # ty: ignore[unresolved-import]  # 模擬リポジトリは同じディレクトリのモジュールを実行時のsys.pathで解決する
import spec_support  # ty: ignore[unresolved-import]  # 模擬リポジトリは同じディレクトリのモジュールを実行時のsys.pathで解決する


def test_loads_configured_plugins() -> None:
    """設定に並んだプラグインを読み込み、その分解処理を使える。"""
    [(module, codec)] = loader.load_plugins(spec_support.load_sample_config())
    assert module.parse("a, b") == ["a", "b"]
    assert codec({"b": 1, "a": 2}) == "a=2;b=1"

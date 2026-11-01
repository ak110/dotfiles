"""設定に列挙したプラグインを読み込む。"""

import importlib

import registry  # ty: ignore[unresolved-import]  # 模擬リポジトリは同じディレクトリのモジュールを実行時のsys.pathで解決する


def load_plugins(config: dict) -> list:
    """設定の`plugins`に並んだモジュールを読み込み、符号化関数とともに返す。"""
    codec = registry.resolve(config["codec"])
    return [(importlib.import_module(name), codec) for name in config["plugins"]]

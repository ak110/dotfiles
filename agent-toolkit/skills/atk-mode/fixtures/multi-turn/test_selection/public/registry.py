"""`module:function`形式の名前から関数を解決する。

名前は設定ファイル、環境変数、呼び出し側の引数のどれからも渡せる。
"""

import importlib


def resolve(name: str):
    """名前に対応する関数を返す。"""
    module, _, function = name.partition(":")
    return getattr(importlib.import_module(module), function)

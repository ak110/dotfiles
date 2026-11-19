"""テストのログの取り込みから、大量に発生するMarkdown解析のDEBUGログを外す。

リポジトリ直下の`pyproject.toml`の`[tool.pytest.ini_options]`の`log_level = "DEBUG"`は、
失敗したテストの報告へ本リポジトリのコードのDEBUGログを残すために置いている。
pytestはこの値でルートロガーの閾値を下げるため、閾値を持たない依存ライブラリのロガーのDEBUGレコードも書式化して保持する。
markdown-it-pyはブロック規則に入るたびにDEBUGレコードを出力し、1200行の計画を確認する1件のテストで約40万件に達して、
本来1秒未満のテストが8〜11秒かかっていた。

対象の`markdown_it`ロガーだけをINFOへ上げる。子のロガーは親の閾値を継承する。
本リポジトリのDEBUGと依存ライブラリのINFO以上の取り込みを保つ。

`log_level`を下げる案は、本リポジトリのコードのDEBUGログを失敗報告から失い、姉妹プロジェクトと同じ値にそろえた設定の目的に反するため採らない。
抑制対象を選ぶための全配布物のメタデータ走査は行わない。
追加のライブラリへ抑制を広げるのは、大量のDEBUG出力による負荷を観測したときに判断する。

リポジトリ直下の`conftest.py`と`agent-toolkit/conftest.py`は、`isolation`と同じく本モジュールのfixtureを自身の名前空間へ代入して適用する。
"""

import logging

import pytest


def raise_dependency_logger_levels() -> None:
    """大量のDEBUG出力を観測したMarkdown解析のロガーをINFOへ上げる。"""
    logging.getLogger("markdown_it").setLevel(logging.INFO)


@pytest.fixture(name="_quiet_dependency_loggers", scope="session", autouse=True)
def quiet_dependency_loggers() -> None:
    """テストの開始時に、Markdown解析の大量のDEBUGログを取り込みから外す。"""
    raise_dependency_logger_levels()

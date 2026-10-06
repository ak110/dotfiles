"""テストのログの取り込みから、依存ライブラリのDEBUGログを外す定義。

`pyproject.toml`と`agent-toolkit/pyproject.toml`の`[tool.pytest.ini_options]`の`log_level = "DEBUG"`は、
失敗したテストの報告へ本リポジトリのコードのDEBUGログを残すために置いている。
pytestはこの値でルートロガーの閾値を下げるため、閾値を持たない依存ライブラリのロガーのDEBUGレコードも書式化して保持する。
markdown-it-pyはブロック規則に入るたびにDEBUGレコードを出力し、1200行の計画を確認する1件のテストで約40万件に達して、
本来1秒未満のテストが8〜11秒かかっていた。

本モジュールは、インストール済みの配布物が提供する上位パッケージ名（`importlib.metadata.packages_distributions()`のキー）の
ロガーの閾値をINFOへ上げる。子のロガーは親の閾値を継承するため、上位名へ設定すれば配下も含まれる。
本リポジトリのコード（`agent_toolkit`、`pytools`、`scripts/`配下のモジュール）は配布物として登録されていないため対象に入らず、
DEBUGの取り込みが続く。依存ライブラリのINFO以上のレコードは引き続き取り込まれる。

`log_level`を下げる案は、本リポジトリのコードのDEBUGログを失敗報告から失い、姉妹プロジェクトと同じ値にそろえた設定の目的に反するため採らない。
特定のライブラリのロガーだけを名指しで外す案は、今後加わる依存ライブラリへ働かないため採らない。

リポジトリ直下の`conftest.py`と`agent-toolkit/conftest.py`は、`isolation`と同じく本モジュールのfixtureを自身の名前空間へ代入して適用する。
"""

import importlib.metadata
import logging

import pytest


def raise_dependency_logger_levels() -> None:
    """インストール済みの配布物が提供する上位パッケージ名のロガーの閾値をINFOへ上げる。"""
    for package_name in importlib.metadata.packages_distributions():
        logging.getLogger(package_name).setLevel(logging.INFO)


@pytest.fixture(name="_quiet_dependency_loggers", scope="session", autouse=True)
def quiet_dependency_loggers() -> None:
    """テストのセッションの開始時に、依存ライブラリのロガーをDEBUGの取り込みから外す。"""
    raise_dependency_logger_levels()

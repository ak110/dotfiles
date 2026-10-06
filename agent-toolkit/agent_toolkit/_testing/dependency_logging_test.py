"""`agent_toolkit/`配下のテストで、依存ライブラリのDEBUGログが取り込まれず本リポジトリのDEBUGログは取り込まれることを確かめる。"""

import logging

import pytest


def test_dependency_debug_logs_are_not_captured(caplog: pytest.LogCaptureFixture) -> None:
    """設定のままの実行で、`markdown_it`のDEBUGレコードは`caplog`に入らず、本リポジトリのロガーのDEBUGレコードは入る。"""
    logging.getLogger("markdown_it.rules_block.fence").debug("依存ライブラリのDEBUG")
    logging.getLogger("agent_toolkit.sample").debug("本リポジトリのDEBUG")

    assert [record.name for record in caplog.records] == ["agent_toolkit.sample"]

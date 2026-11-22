import logging

import pytest


def test_dependency_debug_logs_are_not_captured(caplog: pytest.LogCaptureFixture) -> None:
    logging.getLogger("markdown_it.rules_block.fence").debug("依存ライブラリのDEBUG")
    logging.getLogger("agent_toolkit.sample").debug("本リポジトリのDEBUG")

    assert [record.name for record in caplog.records] == ["agent_toolkit.sample"]

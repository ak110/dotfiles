"""agents_serverの共通診断ログ設定を検証する。"""

import json
import logging
import pathlib

import pytest

from agent_toolkit._agents_server import logging_config
from agent_toolkit._common import state_paths


def test_configure_logging_reuses_handlers_and_rotation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """設定を繰り返しても同じ保存先とhandlerを再利用する。

    loggerのhandlerの退避と回収は`pytest_plugin`の自動fixtureが行い、試験は空のhandler一覧から始まる。
    """
    logger = logging.getLogger("agent-toolkit.agents-server")
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    first = logging_config.configure_logging()
    second = logging_config.configure_logging()
    file_handlers = [handler for handler in logger.handlers if isinstance(handler, logging_config._LogFileHandler)]  # pylint: disable=protected-access  # noqa: SLF001

    assert first == second == tmp_path / "agents-server.log"
    assert len(file_handlers) == 1
    stderr_handlers = [handler for handler in logger.handlers if isinstance(handler, logging_config._StderrHandler)]  # pylint: disable=protected-access  # noqa: SLF001
    assert len(stderr_handlers) == 1


def test_instance_diagnostics_independent_of_log_rotation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """共通ログの回転と別の起動単位の開始・終了は、記録済みの起動単位の終了診断を変えない。"""
    logger = logging.getLogger("agent-toolkit.agents-server")
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(logging_config, "_CURRENT_INSTANCE", None)
    monkeypatch.setattr(logging_config, "LOG_MAX_BYTES", 4096)
    try:
        first = logging_config.start_server_instance(environment={}, state_root=tmp_path)
        logging_config.record_instance_event("exited", outcome="normal", exception_type=None, message=None)
        recorded = first.path.read_text(encoding="utf-8")
        logging_config.configure_logging()
        for index in range(200):
            logger.warning("回転させるための記録 %d %s", index, "x" * 100)
        logging_config.flush_logging()
        second = logging_config.start_server_instance(environment={}, state_root=tmp_path)
        logging_config.record_instance_event("exited", outcome="exception", exception_type="RuntimeError", message="x")
    finally:
        # 回転後のファイルを確かめる前に、ファイル向けworkerの出力を完了させる。
        logging_config.close_logging()

    assert (tmp_path / "agents-server.log.3").exists()
    assert first.path.read_text(encoding="utf-8") == recorded
    assert second.instance_id != first.instance_id
    assert [json.loads(line)["event"] for line in recorded.splitlines()] == ["started", "exited"]
    reference = logging_config.diagnostics_reference(
        logging_config.InstanceReference(first.instance_id, 1), state_root=tmp_path
    )
    assert reference["status"] == "collected"

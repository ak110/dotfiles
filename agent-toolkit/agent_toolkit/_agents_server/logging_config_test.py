"""agents_serverの共通診断ログ設定を検証する。"""

import logging
import pathlib
from logging.handlers import RotatingFileHandler

import pytest

from agent_toolkit._agents_server import logging_config


def test_configure_logging_reuses_handlers_and_rotation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """反復設定でも同じ保存先とhandlerを再利用する。"""
    logger = logging.getLogger("agent-toolkit.agents-server")
    original_handlers = tuple(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    logger.handlers.clear()
    monkeypatch.setattr(logging_config, "user_state_dir", lambda *_args, **_kwargs: str(tmp_path))
    try:
        first = logging_config.configure_logging()
        second = logging_config.configure_logging()
        file_handlers = [handler for handler in logger.handlers if isinstance(handler, logging_config._LogFileHandler)]  # pylint: disable=protected-access  # noqa: SLF001

        assert first == second == tmp_path / "agents-server.log"
        assert len(file_handlers) == 1
        assert isinstance(file_handlers[0], RotatingFileHandler)
        assert file_handlers[0].maxBytes == logging_config.LOG_MAX_BYTES
        assert file_handlers[0].backupCount == logging_config.LOG_BACKUP_COUNT
        stderr_handlers = [handler for handler in logger.handlers if isinstance(handler, logging_config._StderrHandler)]  # pylint: disable=protected-access  # noqa: SLF001
        assert len(stderr_handlers) == 1
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers[:] = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate

"""agents_serverの共通診断ログを設定する。"""

from __future__ import annotations

import logging
import logging.handlers
import os
import pathlib

from platformdirs import user_state_dir

LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 3


def configure_logging() -> pathlib.Path:
    """標準エラーと永続ファイルへagents_serverの診断ログを出力する。"""
    log_level = os.environ.get("AGENT_TOOLKIT_AGENTS_LOG_LEVEL", "WARNING")
    server_logger = logging.getLogger("agent-toolkit.agents-server")
    server_logger.setLevel(logging.INFO)
    server_logger.propagate = False
    if not any(getattr(handler, "agents_server_stderr", False) for handler in server_logger.handlers):
        stderr_handler = logging.StreamHandler()
        stderr_handler.setLevel(log_level)
        stderr_handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
        stderr_handler.agents_server_stderr = True  # type: ignore[attr-defined]
        server_logger.addHandler(stderr_handler)

    log_path = pathlib.Path(user_state_dir("agent-toolkit", appauthor=False)) / "agents-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    for handler in tuple(server_logger.handlers):
        if not getattr(handler, "agents_server_file", False):
            continue
        if pathlib.Path(handler.baseFilename) == log_path:  # type: ignore[attr-defined]
            break
        server_logger.removeHandler(handler)
        handler.close()
    if not any(getattr(handler, "agents_server_file", False) for handler in server_logger.handlers):
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        file_handler.agents_server_file = True  # type: ignore[attr-defined]
        server_logger.addHandler(file_handler)
    return log_path

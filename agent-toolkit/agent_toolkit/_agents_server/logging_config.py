"""agents_serverの共通診断ログを設定する。"""

from __future__ import annotations

import logging
import logging.handlers
import os
import pathlib

from platformdirs import user_state_dir

LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 3


class _StderrHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """agents_serverが追加した標準エラー向けhandler。

    同じloggerへ二重に追加しないための識別を、動的属性ではなく型で表す。
    """


class _LogFileHandler(logging.handlers.RotatingFileHandler):
    """agents_serverが追加した永続ファイル向けhandler。

    出力先の比較で`baseFilename`を読むため、その属性を持つ型として扱えるようにする。
    """


def state_dir() -> pathlib.Path:
    """agents_serverの診断記録を置く状態ディレクトリを返す。"""
    return pathlib.Path(user_state_dir("agent-toolkit", appauthor=False))


def configure_logging() -> pathlib.Path:
    """標準エラーと永続ファイルへagents_serverの診断ログを出力する。"""
    log_level = os.environ.get("AGENT_TOOLKIT_AGENTS_LOG_LEVEL", "WARNING")
    server_logger = logging.getLogger("agent-toolkit.agents-server")
    server_logger.setLevel(logging.INFO)
    server_logger.propagate = False
    if not any(isinstance(handler, _StderrHandler) for handler in server_logger.handlers):
        stderr_handler = _StderrHandler()
        stderr_handler.setLevel(log_level)
        stderr_handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
        server_logger.addHandler(stderr_handler)

    log_path = state_dir() / "agents-server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    for handler in tuple(server_logger.handlers):
        if not isinstance(handler, _LogFileHandler):
            continue
        if pathlib.Path(handler.baseFilename) == log_path:
            break
        server_logger.removeHandler(handler)
        handler.close()
    if not any(isinstance(handler, _LogFileHandler) for handler in server_logger.handlers):
        file_handler = _LogFileHandler(
            log_path,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        server_logger.addHandler(file_handler)
    return log_path

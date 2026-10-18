"""agents_serverの共通診断ログを設定する。"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import pathlib
import queue
import threading
from typing import Any

from agent_toolkit._common import state_paths as _state_paths

LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 3


class _StderrHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """agents_serverが追加した標準エラー向けhandler。

    同じloggerへ二重に追加しないための識別を型で表す。
    """


class _LogFileHandler(logging.Handler):
    """agents_serverが追加した永続ファイル向けhandler。

    出力先の比較で`baseFilename`を読むため、その属性を持つ型として扱えるようにする。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self._sink = logging.handlers.RotatingFileHandler(*args, **kwargs)
        self.baseFilename = self._sink.baseFilename
        self._records: queue.Queue[logging.LogRecord | None] = queue.Queue()
        self._accepting = True
        self._worker = threading.Thread(target=self._write_records, name="agents-server-log", daemon=True)
        self._worker.start()

    def _write_records(self) -> None:
        while (record := self._records.get()) is not None:
            try:
                self._sink.handle(record)
            finally:
                self._records.task_done()
        self._records.task_done()

    def emit(self, record: logging.LogRecord) -> None:
        """ログの順序を保つ専用workerへ記録を渡す。"""
        if not self._accepting:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self._records.join()
            self._sink.handle(record)
        else:
            self._records.put(record)

    def setFormatter(self, fmt: logging.Formatter | None) -> None:
        """出力を所有するhandlerへ書式を渡す。"""
        super().setFormatter(fmt)
        self._sink.setFormatter(fmt)

    def flush(self) -> None:
        """受理済みの全記録を保存する。"""
        self._records.join()
        self._sink.flush()

    def close(self) -> None:
        """受理した全記録の出力とworkerの終了を待つ。"""
        self.acquire()
        try:
            if self._accepting:
                self._accepting = False
                self._records.put(None)
        finally:
            self.release()
        self._worker.join()
        self._sink.close()
        super().close()


def close_logging() -> None:
    """所有するログworkerを全て終了し、保存を完了する。"""
    logger = logging.getLogger("agent-toolkit.agents-server")
    for handler in tuple(logger.handlers):
        if isinstance(handler, _LogFileHandler):
            logger.removeHandler(handler)
            handler.close()


def flush_logging() -> None:
    """所有するログworkerの受理済み出力を完了させる。"""
    for handler in logging.getLogger("agent-toolkit.agents-server").handlers:
        if isinstance(handler, _LogFileHandler):
            handler.flush()


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

    log_path = _state_paths.state_dir() / "agents-server.log"
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

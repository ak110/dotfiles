"""更新親子と単独post-applyが共有する、容量制限付きログの書込。"""

import logging
import logging.handlers
import sys
from pathlib import Path

import filelock
import platformdirs

from pytools._internal import log_format

# 更新とpost-applyと独立プロセスが共有する永続ログ。`libexec/update_dotfiles_runtime.py`の`LOG_PATH`と同じパス。
LOG_PATH = Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "update-dotfiles.log"


class ProcessSafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """書込と世代移動を排他し、Windowsで他writerのrenameを妨げない。"""

    def __init__(self, filename: Path, *, max_bytes: int, backup_count: int, encoding: str = "utf-8") -> None:
        self._file_lock = filelock.FileLock(str(filename) + ".lock", timeout=30, preserve_lock_file=True)
        super().__init__(filename, maxBytes=max_bytes, backupCount=backup_count, encoding=encoding, delay=True)
        # 開始時の書込可否は従来どおり確認するが、待機中にファイルを開いたままにしない。
        with self._file_lock, self._open():
            pass

    def emit(self, record: logging.LogRecord) -> None:
        """他writerが閉じている区間で世代移動と追記を行う。"""
        try:
            with self._file_lock:
                try:
                    super().emit(record)
                finally:
                    if self.stream is not None:
                        self.stream.close()
                        self.stream = None
        except OSError, filelock.Timeout:
            self.handleError(record)

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 -- logging.Handlerの契約
        """画面から省いた失敗の詳細は、開始後に追記・世代移動できなくなっても失わない。"""
        if record.levelno >= logging.WARNING and log_format.is_log_only(record):
            print(f"永続ログへ診断を書き込めません: {self.baseFilename}\n{self.format(record)}", file=sys.stderr)
        else:
            super().handleError(record)

import logging
import logging.handlers
import re
import sys
from pathlib import Path

import filelock
import platformdirs

from pytools._internal import log_format

# 更新とpost-applyと独立プロセスが共有する永続ログ。`libexec/update_dotfiles_runtime.py`の`LOG_PATH`と同じパス。
LOG_PATH = Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "update-dotfiles.log"

RECORD_HEADER = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} run=(\S+) \S+ ")
STEP_DIAGNOSTIC = "post-apply診断: "


def generations(path: Path, backup_count: int) -> list[Path]:
    return [path] + [path.with_name(f"{path.name}.{number}") for number in range(1, backup_count + 1)]


def has_step_diagnostic(path: Path, backup_count: int, run_id: str) -> bool:
    for generation in generations(path, backup_count):
        try:
            with generation.open(encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    if STEP_DIAGNOSTIC not in line:
                        continue
                    header = RECORD_HEADER.match(line)
                    if header and header[1] == run_id and line.startswith(STEP_DIAGNOSTIC, header.end()):
                        return True
        except OSError:
            continue
    return False


def is_step_diagnostic(record: logging.LogRecord) -> bool:
    """工程が表示先を指定した診断（LOG_ONLY・SHOW）だけを数え、後の失敗件数の要約は含めない。"""
    return record.levelno >= logging.WARNING and hasattr(record, log_format.LOG_ONLY_ATTR)


class ProcessSafeRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """書込と世代移動を排他し、Windowsで他writerのrenameを妨げない。"""

    def __init__(
        self, filename: Path, *, max_bytes: int, backup_count: int, encoding: str = "utf-8", diagnostic_hint: str | None = None
    ) -> None:
        self._file_lock = filelock.FileLock(str(filename) + ".lock", timeout=30, preserve_lock_file=True)
        self.write_failed = False

        self._post_apply_hint = diagnostic_hint
        self._stderr_notified = False
        super().__init__(filename, maxBytes=max_bytes, backupCount=backup_count, encoding=encoding, delay=True)
        # 開始時の書込可否は従来どおり確認するが、待機中にファイルを開いたままにしない。
        with self._file_lock, self._open():
            pass

    def emit(self, record: logging.LogRecord) -> None:
        self.write_failed = False
        try:
            with self._file_lock:
                try:
                    super().emit(record)
                finally:
                    if self.stream is not None:
                        stream, self.stream = self.stream, None
                        stream.close()
        except OSError, filelock.Timeout:
            # 満杯時には同じレコードのflushとcloseが両方失敗する。
            if not self.write_failed:
                self.handleError(record)
        if self._post_apply_hint and not self.write_failed and is_step_diagnostic(record):
            print(self._post_apply_hint, file=sys.stderr)
            self._post_apply_hint = ""

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 -- logging.Handlerの契約
        """画面から省いた失敗の詳細は、開始後に追記・世代移動できなくなっても失わない。"""
        self.write_failed = True
        if self._post_apply_hint is not None and not is_step_diagnostic(record):
            return
        if not self._stderr_notified:
            if self._post_apply_hint is not None:
                print("  post-applyの詳細を永続ログへ保存できません。この標準エラーを確認してください。", file=sys.stderr)
            else:
                print("更新本体の記録を永続ログへ保存できません。未保存の行を標準エラーに表示します。", file=sys.stderr)
            self._stderr_notified = True
        if self._post_apply_hint is None or log_format.is_log_only(record):
            print(self.format(record), file=sys.stderr)

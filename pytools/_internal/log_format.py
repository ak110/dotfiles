import logging
import types
from pathlib import Path

# 永続ログにだけ残し、post-applyの画面（標準出力）から外すレコードの目印。
# `logger.info(..., extra=LOG_ONLY)`で付け、post-applyの標準出力ハンドラーが除く。
LOG_ONLY_ATTR = "post_apply_log_only"
LOG_ONLY = types.MappingProxyType({LOG_ONLY_ATTR: True})
# INFOでも失敗や手動操作を伝える行は、更新の画面に残す。
SHOW = types.MappingProxyType({LOG_ONLY_ATTR: False})


def is_log_only(record: logging.LogRecord) -> bool:
    return getattr(record, LOG_ONLY_ATTR, False) is True


def format_status(target: str, state: str) -> str:
    return f"    {target}: {state}"


def home_short(path: Path, *, home: Path | None = None) -> str:
    home = home or Path.home()
    if path == home:
        return "~"
    try:
        return "~/" + path.relative_to(home).as_posix()
    except ValueError:
        return str(path)

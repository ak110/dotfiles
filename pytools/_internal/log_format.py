"""post-apply系モジュールで共有するログフォーマットヘルパー。"""

import logging
import types
from pathlib import Path

# 永続ログにだけ残し、post-applyの画面（標準出力）から外すレコードの目印。
# `logger.info(..., extra=LOG_ONLY)`で付け、post-applyの標準出力ハンドラーが除く。
LOG_ONLY_ATTR = "post_apply_log_only"
LOG_ONLY = types.MappingProxyType({LOG_ONLY_ATTR: True})


def is_log_only(record: logging.LogRecord) -> bool:
    """レコードが永続ログ専用の目印を持つかを返す。"""
    return getattr(record, LOG_ONLY_ATTR, False) is True


def format_status(target: str, state: str) -> str:
    """`    <target>: <state>` 形式の詳細行を返す。

    post-applyの`logging.basicConfig`が行頭に "  " を付けるため、
    本関数は追加で4スペースを持たせ、最終的に6スペースインデントの出力になる。
    """
    return f"    {target}: {state}"


def home_short(path: Path, *, home: Path | None = None) -> str:
    """`Path.home()` 配下なら `~/...` に短縮する。配下外はそのまま `str(path)`。

    `Path.home()` 自身は `~` を返す。シンボリックリンク解決は行わない
    （見た目を整えるだけの用途のため、resolveでユーザーの意図しないパスへ
    展開されないよう素のパスで判定する）。
    """
    home = home or Path.home()
    if path == home:
        return "~"
    try:
        return "~/" + path.relative_to(home).as_posix()
    except ValueError:
        return str(path)

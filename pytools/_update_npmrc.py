"""~/.npmrc のサプライチェーン対策設定を管理するモジュール。

公開直後のパッケージインストールをブロックするため `minimum-release-age=1440`
(1440 分 = 24 時間) を設定する。既存エントリがあれば置換、なければ追記、
一致している場合は何もしない。

詳細は docs/guide/security.md を参照。
"""

import logging
import re
from pathlib import Path

from pytools import _log_format

logger = logging.getLogger(__name__)

_KEY = "minimum-release-age"
_VALUE = "1440"
_LINE = f"{_KEY}={_VALUE}"
_PATTERN = re.compile(rf"^{re.escape(_KEY)}=.*$", re.MULTILINE)


def _main() -> None:
    """スタンドアロン実行用エントリポイント。"""
    logging.basicConfig(format="%(message)s", level="INFO")
    run()


def run(npmrc_path: Path | None = None) -> bool:
    """~/.npmrc に `minimum-release-age=1440` を設定する。

    Args:
        npmrc_path: 対象パス。None の場合は `~/.npmrc` を使用 (テスト時に差し替え可能)。

    Returns:
        ファイルを書き換えたかどうか。
    """
    path = npmrc_path if npmrc_path is not None else Path.home() / ".npmrc"
    short = _log_format.home_short(path)
    if not path.exists():
        path.write_text(_LINE + "\n", encoding="utf-8")
        logger.info(_log_format.format_status(short, f"作成し {_LINE} を設定しました"))
        return True

    content = path.read_text(encoding="utf-8")
    if _PATTERN.search(content):
        new_content = _PATTERN.sub(_LINE, content)
        if new_content == content:
            logger.info(_log_format.format_status(short, f"{_LINE} は既に設定済み"))
            return False
        path.write_text(new_content, encoding="utf-8")
        logger.info(_log_format.format_status(short, f"{_KEY} を更新しました"))
        return True

    # キーが無い: 末尾に追記 (末尾改行を保証)
    suffix = "" if content.endswith("\n") or content == "" else "\n"
    path.write_text(content + suffix + _LINE + "\n", encoding="utf-8")
    logger.info(_log_format.format_status(short, f"{_LINE} を追加しました"))
    return True


if __name__ == "__main__":
    _main()

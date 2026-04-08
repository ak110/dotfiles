"""汎用「配布元から削除されたファイル/ディレクトリを削除する」モジュール。

chezmoi は配布元から削除されたファイルを自動で destination 側から削除しない
(管理対象から外れただけ扱い) ため、過去に配布して不要になったファイルを
追従して削除する仕組みを本モジュールで提供する。

Claude に限らず他の配布物の削除でも使える汎用 API。
"""

import logging
import shutil
from collections.abc import Iterable
from pathlib import Path

from pytools import _log_format

logger = logging.getLogger(__name__)


def cleanup_paths(base_dir: Path, relative_paths: Iterable[Path]) -> int:
    """base_dir 配下から relative_paths に列挙されたパスを安全に削除する。

    シンボリックリンクを辿って base_dir 外を消さないよう、削除前に resolve 後のパスが
    base_dir 配下に収まることを確認する。

    Returns:
        実際に削除した件数 (存在しないパスはカウントしない)。
    """
    if not base_dir.exists():
        return 0
    base_resolved = base_dir.resolve()
    removed = 0
    for rel in relative_paths:
        target = base_dir / rel
        if not target.exists() and not target.is_symlink():
            logger.debug("%s は存在しないためスキップ", target)
            continue
        try:
            target.resolve().relative_to(base_resolved)
        except ValueError:
            logger.warning("%s は %s 配下ではないためスキップします", target, base_dir)
            continue
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        else:
            target.unlink()
        logger.info(_log_format.format_status(_log_format.home_short(target), "旧配布物を削除"))
        removed += 1
    return removed

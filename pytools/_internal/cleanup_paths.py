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

from pytools._internal import log_format

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
        logger.info(log_format.format_status(log_format.home_short(target), "旧配布物を削除"))
        removed += 1
    return removed


def cleanup_paths_if_content_matches(base_dir: Path, expected: dict[Path, bytes]) -> int:
    """内容が期待値と完全一致する場合に限り、base_dir 配下のファイルを削除する。

    cleanup_paths との違いは「ユーザーが独自に編集済みの可能性があるファイル」を保護するため、
    bytes 完全一致のときのみ削除する点。テキスト正規化を挟まないのは改行差異で誤判定しないため。

    Returns:
        実際に削除した件数。
    """
    if not base_dir.exists():
        return 0
    base_resolved = base_dir.resolve()
    removed = 0
    for rel, expected_bytes in expected.items():
        target = base_dir / rel
        if not target.exists() and not target.is_symlink():
            logger.debug("%s は存在しないためスキップ", target)
            continue
        try:
            target.resolve().relative_to(base_resolved)
        except ValueError:
            logger.warning("%s は %s 配下ではないためスキップします", target, base_dir)
            continue
        if not target.is_file() or target.is_symlink():
            logger.warning("%s は通常ファイルではないためスキップします", target)
            continue
        actual_bytes = target.read_bytes()
        if actual_bytes != expected_bytes:
            logger.warning(
                "%s はユーザーによる編集の可能性があるためスキップします",
                log_format.home_short(target),
            )
            continue
        target.unlink()
        logger.info(log_format.format_status(log_format.home_short(target), "旧配布物を削除"))
        removed += 1
    return removed

"""配布元から削除されたファイル/ディレクトリを削除する汎用モジュール。

chezmoiは配布元から削除されたファイルをdestination側から自動削除しないため、
過去に配布して不要になったファイルを追従して削除する仕組みを提供する。
"""

import logging
import shutil
from collections.abc import Iterable
from pathlib import Path

from pytools._internal import log_format

logger = logging.getLogger(__name__)


def _is_link_like(path: Path) -> bool:
    """シンボリックリンクまたはWindowsのディレクトリジャンクションかを返す。"""
    if path.is_symlink():
        return True
    # Path.is_junction()はPython 3.12で追加されたため、対応版以外でもimport可能に保つ。
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _remove_link_like(path: Path) -> None:
    """リンク先を辿らず、リンクまたはジャンクション自体を除去する。"""
    if path.is_symlink():
        path.unlink()
    else:
        # Windowsのディレクトリジャンクションはunlinkではなくrmdirで除去する。
        path.rmdir()


def cleanup_paths(base_dir: Path, relative_paths: Iterable[Path]) -> int:
    """`base_dir` 配下から `relative_paths` に列挙されたパスを安全に削除する。

    シンボリックリンクを辿って `base_dir` 外を削除しないよう、削除前にresolve後のパスが
    `base_dir` 配下に収まることを確認する。

    Returns:
        実際に削除した件数（存在しないパスはカウントしない）。
    """
    if not base_dir.exists():
        return 0
    base_resolved = base_dir.resolve()
    removed = 0
    for rel in relative_paths:
        target = base_dir / rel
        is_link_like = _is_link_like(target)
        if not target.exists() and not is_link_like:
            logger.debug("%s は存在しないためスキップ", target)
            continue
        try:
            if is_link_like:
                # リンク自体の削除はリンク先を削除しないため、親ディレクトリだけを確認する。
                target.parent.resolve().relative_to(base_resolved)
            else:
                target.resolve().relative_to(base_resolved)
        except ValueError:
            logger.warning("%s は %s 配下ではないためスキップします", target, base_dir)
            continue
        if is_link_like:
            _remove_link_like(target)
        elif target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        logger.info(log_format.format_status(log_format.home_short(target), "旧配布物を削除"))
        removed += 1
    return removed


def cleanup_paths_if_content_matches(base_dir: Path, expected: dict[Path, bytes]) -> int:
    """内容が期待値と完全一致する場合に限り、`base_dir` 配下のファイルを削除する。

    `cleanup_paths` との違いは「ユーザーが独自に編集済みの可能性があるファイル」を保護するため、
    bytes完全一致のときのみ削除する点。テキスト正規化を介在させないのは改行差異で誤判定しないためである。

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

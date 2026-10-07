"""配布元から削除されたファイル/ディレクトリを削除する汎用モジュール。

chezmoiは配布元から削除されたファイルをdestination側から自動削除しないため、
過去に配布して不要になったファイルを追従して削除する仕組みを提供する。
各関数は`failures`を渡すと、検査と削除の失敗を警告の代わりにそこへ加えて残りの項目を続ける。
"""

import dataclasses
import datetime
import hashlib
import logging
import shutil
from collections.abc import Iterable
from pathlib import Path

from pytools._internal import log_format

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class RemovedPath:
    """撤去表の1項目。

    `empty_dir_only`が真の項目は、空のディレクトリである場合だけ削除し、中身が残る場合は保持する。
    偽の項目はファイル・ディレクトリ（配下ごと）・リンクを削除する。
    `registered`は`removal_registry`が定める登録日。
    """

    path: Path
    registered: datetime.date
    empty_dir_only: bool = False


@dataclasses.dataclass(frozen=True)
class RemovedPathIfContent:
    """内容が期待値と一致する場合だけ削除する撤去表の1項目。

    `expected`はbytesなら内容そのもの、strなら内容のSHA-256の16進文字列（小文字）として比較する。
    大きな撤去前の内容をソースへ直書きしないためにダイジェストを使う。
    `registered`は`removal_registry`が定める登録日。
    """

    path: Path
    registered: datetime.date
    expected: bytes | str


def _report_failure(failures: list[str] | None, message: str) -> None:
    """検査または削除の失敗を`failures`へ加える。`failures`が無い場合は警告として記録して処理を続ける。"""
    if failures is None:
        logger.warning("%s（スキップして続行した）", message)
    else:
        failures.append(message)


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


def cleanup_paths(base_dir: Path, relative_paths: Iterable[Path], *, failures: list[str] | None = None) -> int:
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
        try:
            is_link_like = _is_link_like(target)
            if not target.exists() and not is_link_like:
                logger.debug("%s は存在しないためスキップ", target)
                continue
            if is_link_like:
                # リンク自体の削除はリンク先を削除しないため、親ディレクトリだけを確認する。
                target.parent.resolve().relative_to(base_resolved)
            else:
                target.resolve().relative_to(base_resolved)
        except ValueError:
            logger.warning("%s は %s 配下ではないためスキップします", target, base_dir)
            continue
        except OSError as error:
            _report_failure(failures, f"{target} の検査または削除に失敗: {error}")
            continue
        try:
            if is_link_like:
                _remove_link_like(target)
            elif target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        except OSError as error:
            _report_failure(failures, f"{target} の検査または削除に失敗: {error}")
            continue
        logger.info(log_format.format_status(log_format.home_short(target), "旧配布物を削除"))
        removed += 1
    return removed


def cleanup_empty_dirs(base_dir: Path, relative_dirs: Iterable[Path], *, failures: list[str] | None = None) -> int:
    """`base_dir` 配下の`relative_dirs`を、空のディレクトリである場合だけ列挙順に削除する。

    子を先に列挙すると、子の削除で空になった親も同じ呼び出しで削除できる。
    シンボリックリンクを辿って`base_dir`外を削除しないよう、resolve後のパスが配下に収まることを確認する。

    Returns:
        実際に削除した件数。
    """
    if not base_dir.exists():
        return 0
    try:
        base_resolved = base_dir.resolve()
    except OSError as error:
        _report_failure(failures, f"{base_dir} の検査に失敗: {error}")
        return 0
    removed = 0
    for rel in relative_dirs:
        target = base_dir / rel
        try:
            if _is_link_like(target) or not target.is_dir():
                continue
            target.resolve().relative_to(base_resolved)
        except ValueError:
            logger.warning("%s は %s 配下ではないためスキップします", target, base_dir)
            continue
        except OSError as error:
            _report_failure(failures, f"{target} の検査に失敗: {error}")
            continue
        try:
            target.rmdir()
        except OSError:
            # 中身が残るディレクトリは保持する。
            continue
        logger.info(log_format.format_status(log_format.home_short(target), "空の旧配布先を削除"))
        removed += 1
    return removed


def _content_matches(actual: bytes, expected: bytes | str) -> bool:
    """内容が期待値（bytesまたはSHA-256の16進文字列）と完全一致するかを返す。"""
    if isinstance(expected, bytes):
        return actual == expected
    return hashlib.sha256(actual).hexdigest() == expected


def cleanup_paths_if_content_matches(
    base_dir: Path, expected: dict[Path, bytes | str], *, failures: list[str] | None = None
) -> int:
    """内容が期待値と完全一致する場合に限り、`base_dir` 配下のファイルを削除する。

    `cleanup_paths` との違いは「ユーザーが独自に編集済みの可能性があるファイル」を保護するため、
    完全一致のときのみ削除する点。期待値はbytesか、内容のSHA-256の16進文字列で与える。
    テキスト正規化を介在させないのは改行差異で誤判定しないためである。

    Returns:
        実際に削除した件数。
    """
    if not base_dir.exists():
        return 0
    base_resolved = base_dir.resolve()
    removed = 0
    for rel, expected_content in expected.items():
        target = base_dir / rel
        try:
            if not target.exists() and not target.is_symlink():
                logger.debug("%s は存在しないためスキップ", target)
                continue
            target.resolve().relative_to(base_resolved)
        except ValueError:
            logger.warning("%s は %s 配下ではないためスキップします", target, base_dir)
            continue
        except OSError as error:
            _report_failure(failures, f"{target} の検査または削除に失敗: {error}")
            continue
        try:
            if not target.is_file() or target.is_symlink():
                logger.warning("%s は通常ファイルではないためスキップします", target)
                continue
            actual_bytes = target.read_bytes()
        except OSError as error:
            _report_failure(failures, f"{target} の検査または削除に失敗: {error}")
            continue
        if not _content_matches(actual_bytes, expected_content):
            logger.warning(
                "%s はユーザーによる編集の可能性があるためスキップします",
                log_format.home_short(target),
            )
            continue
        try:
            target.unlink()
        except OSError as error:
            _report_failure(failures, f"{target} の検査または削除に失敗: {error}")
            continue
        logger.info(log_format.format_status(log_format.home_short(target), "旧配布物を削除"))
        removed += 1
    return removed

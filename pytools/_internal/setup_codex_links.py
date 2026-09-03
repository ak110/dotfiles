"""`~/.codex/`配下にdotfiles固有スキル・参照文書・agent-toolkit補助資料へのリンクを生成する。

chezmoiの`symlink_`はWindowsで`CreateSymbolicLinkW`の特権不足により失敗するため採用しない。
Linux/macOSではシンボリックリンクを、Windowsではディレクトリジャンクションを生成する。
"""

import logging
import os
import sys
from pathlib import Path

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)

CODEX_HOME = Path.home() / ".codex"

# 配布先（`~/.codex/`起点）→配布元（dotfilesルート起点）のマップ。
# 配布先は全てディレクトリで、Windowsではディレクトリジャンクションで実現する。
_LINKS: dict[str, str] = {
    "skills/export-session": ".chezmoi-source/dot_claude/skills/export-session",
    "skills/refine-prompt": ".chezmoi-source/dot_claude/skills/refine-prompt",
    "skills/sync-cross-project": ".chezmoi-source/dot_claude/skills/sync-cross-project",
    "references": ".chezmoi-source/dot_claude/references",
    "agent-toolkit/rules": "agent-toolkit/rules",
}


def run() -> bool:
    """`~/.codex/`配下のリンクを冪等に生成する。"""
    dotfiles_root = claude_common.find_dotfiles_root()
    if dotfiles_root is None:
        logger.info(log_format.format_status("codex links", "dotfiles ルートが見つからずスキップ"))
        return False

    changed = False
    for dest_rel, src_rel in _LINKS.items():
        dest = CODEX_HOME / dest_rel
        target = dotfiles_root / src_rel
        if _process_link(dest, target):
            changed = True
    return changed


def _process_link(dest: Path, target: Path) -> bool:
    """単一のリンクを処理し、新規作成または更新したら`True`を返す。"""
    if not target.exists():
        logger.warning(log_format.format_status("codex links", f"配布元が存在しないためスキップ: {target}"))
        return False

    try:
        changed = sync_directory_link(dest, target)
    except FileExistsError:
        logger.warning(
            log_format.format_status(
                "codex links",
                f"通常ファイル／通常ディレクトリが存在するためスキップ: {dest}",
            )
        )
        return False
    if not changed:
        return False
    logger.info(log_format.format_status("codex links", f"作成: {dest} -> {target}"))
    return True


def sync_directory_link(dest: Path, target: Path) -> bool:
    """ディレクトリリンクを期待する参照先へ収束させる。"""
    # is_symlink単独だとリンク切れも捕捉できるが、リンク切れのジャンクションは
    # is_symlinkがFalseを返すため_is_link_likeも条件へ加え、CreateJunctionの衝突を避ける。
    if dest.exists() or dest.is_symlink() or _is_link_like(dest):
        if not _is_link_like(dest):
            raise FileExistsError(dest)
        if dest.resolve() == target.resolve():
            return False
        _remove_link(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)
    _create_link(dest, target)
    return True


def _is_link_like(path: Path) -> bool:
    """シンボリックリンクまたはディレクトリジャンクションかを判定する。"""
    if path.is_symlink():
        return True
    # Path.is_junction() は Python 3.12 で追加された。
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _remove_link(path: Path) -> None:
    """リンクまたはジャンクションを除去する。"""
    if path.is_symlink():
        path.unlink()
        return
    # ジャンクションは通常ディレクトリと同じ `rmdir` で除去できる。
    path.rmdir()


def _create_link(dest: Path, target: Path) -> None:
    """シンボリックリンクまたはジャンクションを生成する。"""
    if sys.platform == "win32":
        # pylint: disable=import-outside-toplevel,import-error
        import _winapi  # noqa: PLC0415 -- Windows専用、Linux環境では import 不可

        _winapi.CreateJunction(str(target), str(dest))
    else:
        relative_target = Path(os.path.relpath(target, start=dest.parent))
        dest.symlink_to(relative_target, target_is_directory=True)

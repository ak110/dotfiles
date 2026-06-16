"""`~/.codex/`配下にClaude Code側スキル・agent-toolkitルールへのリンクを生成する。

chezmoiの`symlink_`はWindowsで`CreateSymbolicLinkW`の特権不足により失敗するため採用しない。
Linux/macOSではシンボリックリンクを、Windowsではディレクトリジャンクションを生成する。
"""

import logging
import sys
from pathlib import Path

from pytools._internal import claude_common, log_format

logger = logging.getLogger(__name__)

CODEX_HOME = Path.home() / ".codex"

# 配布先（`~/.codex/`起点）→配布元（dotfilesルート起点）のマップ。
# 配布先は全てディレクトリで、Windowsではディレクトリジャンクションで実現する。
_LINKS: dict[str, str] = {
    "skills/agent-standards": "agent-toolkit/skills/agent-standards",
    "skills/coding-standards": "agent-toolkit/skills/coding-standards",
    "skills/export-for-resume": "agent-toolkit/skills/export-for-resume",
    "skills/export-session": ".chezmoi-source/dot_claude/skills/export-session",
    "skills/add-feedback": ".chezmoi-source/dot_claude/skills/add-feedback",
    "skills/gitlab-ci-usage": "agent-toolkit/skills/gitlab-ci-usage",
    "skills/plan-codex-review": "agent-toolkit/skills/plan-codex-review",
    "skills/plan-impl": "agent-toolkit/skills/plan-impl",
    "skills/plan-mode": "agent-toolkit/skills/plan-mode",
    "skills/pyfltr-usage": "agent-toolkit/skills/pyfltr-usage",
    "skills/pytilpack-usage": "agent-toolkit/skills/pytilpack-usage",
    "skills/process-feedbacks": ".chezmoi-source/dot_claude/skills/process-feedbacks",
    "skills/refine-prompt": ".chezmoi-source/dot_claude/skills/refine-prompt",
    "skills/review-standards": "agent-toolkit/skills/review-standards",
    "skills/session-review-dotfiles": ".chezmoi-source/dot_claude/skills/session-review-dotfiles",
    "skills/spec-driven": "agent-toolkit/skills/spec-driven",
    "skills/spec-driven-init": "agent-toolkit/skills/spec-driven-init",
    "skills/spec-driven-promote": "agent-toolkit/skills/spec-driven-promote",
    "skills/sync-cross-project": ".chezmoi-source/dot_claude/skills/sync-cross-project",
    "skills/writing-standards": "agent-toolkit/skills/writing-standards",
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

    # is_symlink単独だとリンク切れも捕捉できる。
    if dest.exists() or dest.is_symlink():
        if _is_link_like(dest):
            if dest.resolve() == target.resolve():
                return False
            _remove_link(dest)
        else:
            logger.warning(
                log_format.format_status(
                    "codex links",
                    f"通常ファイル／通常ディレクトリが存在するためスキップ: {dest}",
                )
            )
            return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    _create_link(dest, target)
    logger.info(log_format.format_status("codex links", f"作成: {dest} -> {target}"))
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
        dest.symlink_to(target, target_is_directory=True)

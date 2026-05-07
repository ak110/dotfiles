# PYTHON_ARGCOMPLETE_OK
"""Codex設定のシンボリックリンクを構築するコマンド。

cwd直下に`AGENTS.md`→`CLAUDE.md`と`.agents/skills`→`../.claude/skills`の
シンボリックリンクを作成する。
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from pytools._internal.cli import enable_completion, setup_logging

logger = logging.getLogger(__name__)

_AGENTS_TARGET = "CLAUDE.md"
_SKILLS_TARGET = "../.claude/skills"


def _main() -> None:
    setup_logging(verbose=True)
    parser = argparse.ArgumentParser(description="Codex向けシンボリックリンクを構築する。")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="作成したシンボリックリンクを削除する。",
    )
    enable_completion(parser)
    args = parser.parse_args()
    _codexize(Path.cwd(), clean=args.clean)


def _codexize(target_dir: Path, *, clean: bool = False) -> None:
    """本体ロジック。テスト時にパスを差し替え可能にするため分離。"""
    agents_link = target_dir / "AGENTS.md"
    skills_link = target_dir / ".agents" / "skills"

    if clean:
        removed = _remove_symlink(agents_link, _AGENTS_TARGET)
        removed |= _remove_symlink(skills_link, _SKILLS_TARGET)
        agents_dir = target_dir / ".agents"
        if agents_dir.is_dir() and not any(agents_dir.iterdir()):
            agents_dir.rmdir()
            logger.info("削除: %s", agents_dir)
        if not removed:
            logger.info("削除対象なし")
        return

    claude_md = target_dir / "CLAUDE.md"
    if not claude_md.exists():
        logger.error("CLAUDE.md が見つかりません: %s", claude_md)
        sys.exit(1)
    _ensure_symlink(agents_link, _AGENTS_TARGET)

    skills_source = target_dir / ".claude" / "skills"
    if not skills_source.exists():
        logger.info(".agents/skills の作成をスキップ（.claude/skills が無い）: %s", skills_source)
        return
    skills_link.parent.mkdir(exist_ok=True)
    _ensure_symlink(skills_link, _SKILLS_TARGET)


def _ensure_symlink(link_path: Path, expected_target: str) -> None:
    """期待のシンボリックリンクを冪等に作成する。

    既に期待どおりのシンボリックリンクなら何もしない。
    実ファイル・実ディレクトリ・別リンク先のリンクの場合はエラー終了する。
    """
    if link_path.is_symlink():
        actual = os.readlink(link_path)
        if actual == expected_target:
            logger.info("維持: %s -> %s", link_path, actual)
            return
        logger.error(
            "期待しないリンク先: %s -> %s （期待: %s）",
            link_path,
            actual,
            expected_target,
        )
        sys.exit(1)
    if link_path.exists():
        kind = "ディレクトリ" if link_path.is_dir() else "ファイル"
        logger.error("シンボリックリンクではない%sを検出: %s", kind, link_path)
        sys.exit(1)
    link_path.symlink_to(expected_target)
    logger.info("作成: %s -> %s", link_path, expected_target)


def _remove_symlink(link_path: Path, expected_target: str) -> bool:
    """期待のシンボリックリンクを削除する。

    Returns:
        削除した場合True。存在しない場合False。
    """
    if not link_path.is_symlink():
        if link_path.exists():
            kind = "ディレクトリ" if link_path.is_dir() else "ファイル"
            logger.error("シンボリックリンクではない%sを検出: %s", kind, link_path)
            sys.exit(1)
        return False
    actual = os.readlink(link_path)
    if actual != expected_target:
        logger.error(
            "期待しないリンク先のため削除をスキップ: %s -> %s （期待: %s）",
            link_path,
            actual,
            expected_target,
        )
        sys.exit(1)
    link_path.unlink()
    logger.info("削除: %s -> %s", link_path, actual)
    return True


if __name__ == "__main__":
    _main()

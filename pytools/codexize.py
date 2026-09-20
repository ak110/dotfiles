# PYTHON_ARGCOMPLETE_OK
"""Codex向けに`AGENTS.md`実体と共有スキルへ収束させるコマンド。

cwd直下のプロジェクト指示をAGENTS.mdの実体ファイルへ統一する。
あわせて`.claude/skills`が存在する場合は`.agents/skills -> ../.claude/skills`の
シンボリックリンクを冪等に作成する。

`--clean`実行時は`.agents/skills`だけを削除する。
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from pytools._internal.cli import enable_completion, setup_logging
from pytools.claudize import migrate_project_instructions

logger = logging.getLogger(__name__)

_SKILLS_LINK_TARGET = "../.claude/skills"


def main() -> None:
    """AGENTS.md実体と共有スキルへ収束させるエントリポイント。"""
    setup_logging(verbose=True)
    parser = argparse.ArgumentParser(description="AGENTS.md実体と.agents/skillsシンボリックリンクへ収束させる。")
    parser.add_argument(
        "--clean",
        action="store_true",
        help=".agents/skillsシンボリックリンクを削除する。",
    )
    enable_completion(parser)
    args = parser.parse_args()
    _codexize(Path.cwd(), clean=args.clean)
    sys.exit(0)


def _codexize(target_dir: Path, *, clean: bool = False) -> None:
    """本体ロジック。テスト時にパスを差し替え可能にする目的で分離する。"""
    if clean:
        _remove_skills_symlink(target_dir)
        return

    migrate_project_instructions(target_dir)
    _ensure_skills_symlink(target_dir)


def _ensure_skills_symlink(target_dir: Path) -> None:
    """`.claude/skills`が存在する場合のみ`.agents/skills`を作成する。"""
    skills_source = target_dir / ".claude" / "skills"
    skills_link = target_dir / ".agents" / "skills"
    if not skills_source.exists():
        logger.info(".agents/skills の作成をスキップ（.claude/skills が無い）: %s", skills_source)
        return
    skills_link.parent.mkdir(exist_ok=True)
    _ensure_symlink(skills_link, _SKILLS_LINK_TARGET)


def _remove_skills_symlink(target_dir: Path) -> None:
    """`.agents/skills`を削除し、空になった`.agents/`も除去する。"""
    skills_link = target_dir / ".agents" / "skills"
    _remove_symlink(skills_link, _SKILLS_LINK_TARGET)
    agents_dir = target_dir / ".agents"
    if agents_dir.is_dir() and not any(agents_dir.iterdir()):
        agents_dir.rmdir()
        logger.info("削除: %s", agents_dir)


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


def _remove_symlink(link_path: Path, expected_target: str) -> None:
    """期待のシンボリックリンクを削除する。"""
    if not link_path.is_symlink():
        if link_path.exists():
            kind = "ディレクトリ" if link_path.is_dir() else "ファイル"
            logger.error("シンボリックリンクではない%sを検出: %s", kind, link_path)
            sys.exit(1)
        return
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


if __name__ == "__main__":
    main()

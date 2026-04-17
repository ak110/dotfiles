"""Claude Code設定ファイルを配布・同期するコマンド。

配布元ディレクトリ (`.chezmoi-source/dot_claude/rules/agent-toolkit/`) の内容を
プロジェクト配下の `.claude/rules/agent-toolkit/` へ完全同期する。
配布先は管理対象であり、ユーザーによる個別カスタマイズを前提としない方針のため、
配布元との一致をシンプルに保つ (frontmatter 維持や旧ファイル個別追跡は行わない)。
"""

import argparse
import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# 旧配布先ディレクトリ名 (agent-basics → agent-toolkit リネーム前の名前)。
# 既存環境のクリーンアップのため、同期時にディレクトリごと削除する。
_LEGACY_RULES_DIRNAME = "agent-basics"
_RULES_DIRNAME = "agent-toolkit"


def _main() -> None:
    logging.basicConfig(format="%(message)s", level="DEBUG")
    parser = argparse.ArgumentParser(description="Claude Code設定ファイルを配布・同期する。")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="配布対象のルールファイルをプロジェクトから削除する。",
    )
    args = parser.parse_args()
    template_dir = Path.home() / "dotfiles" / ".chezmoi-source" / "dot_claude" / "rules" / _RULES_DIRNAME
    target_dir = Path.cwd()
    _claudize(target_dir, template_dir, clean=args.clean)


def _claudize(target_dir: Path, template_dir: Path, *, clean: bool = False) -> None:
    """本体ロジック。テスト時にパスを差し替え可能にするため分離。"""
    rules_dir = target_dir / ".claude" / "rules" / _RULES_DIRNAME
    legacy_rules_dir = target_dir / ".claude" / "rules" / _LEGACY_RULES_DIRNAME

    if clean:
        removed = _clean_rules_dir(rules_dir)
        removed |= _clean_rules_dir(legacy_rules_dir)
        if not removed:
            logger.info("削除対象なし: %s", rules_dir)
        return

    if not template_dir.exists():
        logger.error("テンプレートが見つかりません: %s", template_dir)
        sys.exit(1)

    # 旧ディレクトリ (`agent-basics`) を撤去して移行。
    _clean_rules_dir(legacy_rules_dir)

    # 配布先を配布元と完全一致させる。個別ファイル差分比較はしない。
    if rules_dir.exists():
        shutil.rmtree(rules_dir)
    rules_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template_dir, rules_dir)
    logger.info("配布: %s", rules_dir)


def _clean_rules_dir(rules_dir: Path) -> bool:
    """配布先ディレクトリごと削除する。空になった親ディレクトリも併せて削除する。

    Returns:
        何らかのディレクトリを削除した場合 True。
    """
    if not rules_dir.exists():
        return False
    shutil.rmtree(rules_dir)
    logger.info("削除: %s", rules_dir)
    # 空になった親ディレクトリ (rules/, .claude/) を順に削除する。
    # 他のルールディレクトリが残っていれば rmdir は失敗するため安全。
    for candidate in [rules_dir.parent, rules_dir.parent.parent]:
        if candidate.name not in {"rules", ".claude"}:
            break
        if not candidate.exists() or any(candidate.iterdir()):
            break
        candidate.rmdir()
        logger.info("削除: %s", candidate)
    return True


if __name__ == "__main__":
    _main()

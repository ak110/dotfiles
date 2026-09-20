# PYTHON_ARGCOMPLETE_OK
"""Claude Code設定ファイルを配布・同期するコマンド。

配布元ディレクトリ（`agent-toolkit/rules/`）の内容を
プロジェクト配下の`.claude/rules/agent-toolkit/`へ完全に同期する。
通常実行ではプロジェクト指示を`AGENTS.md`実体へ統一する。
"""

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

from pytools._internal.cli import enable_completion, setup_logging

logger = logging.getLogger(__name__)

# 旧配布先ディレクトリ名 (agent-basics → agent-toolkit リネーム前の名前)。
# 既存環境のクリーンアップのため、同期時にディレクトリごと削除する。
_LEGACY_RULES_DIRNAME = "agent-basics"
_RULES_DIRNAME = "agent-toolkit"
_DOTFILES_ROOT = Path(__file__).resolve().parents[1]
_AGENTS_MD = "AGENTS.md"
_CLAUDE_MD = "CLAUDE.md"


def main() -> None:
    """Claude Code設定ファイルを配布・同期するエントリポイント。"""
    setup_logging(verbose=True)
    parser = argparse.ArgumentParser(description="Claude Code設定ファイルを配布・同期する。")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="配布対象のルールファイルをプロジェクトから削除する。",
    )
    enable_completion(parser)
    args = parser.parse_args()
    template_dir = _DOTFILES_ROOT / "agent-toolkit" / "rules"
    target_dir = Path.cwd()
    claudize(target_dir, template_dir, clean=args.clean)
    sys.exit(0)


def claudize(target_dir: Path, template_dir: Path, *, clean: bool = False) -> None:
    """`template_dir`配下のルールファイル群を`target_dir`配下の配布先へ同期する。

    `clean=True`では配布先を削除する（旧`agent-basics`ディレクトリも併せて削除）。
    """
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

    migrate_project_instructions(target_dir)

    # 旧ディレクトリ (`agent-basics`) が存在する場合は削除する。
    _clean_rules_dir(legacy_rules_dir)

    # 配布先を配布元と完全一致させる。個別ファイル差分比較はしない。
    if rules_dir.exists():
        shutil.rmtree(rules_dir)
    rules_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template_dir, rules_dir)
    logger.info("配布: %s", rules_dir)


def migrate_project_instructions(target_dir: Path) -> None:
    """プロジェクト指示を`AGENTS.md`単一実体へ安全に収束させる。"""
    agents_md = target_dir / _AGENTS_MD
    claude_md = target_dir / _CLAUDE_MD
    agents_kind = _classify_instruction_path(agents_md, expected_target=_CLAUDE_MD)
    claude_kind = _classify_instruction_path(claude_md, expected_target=_AGENTS_MD)

    if agents_kind == "missing" and claude_kind == "regular_file":
        claude_md.rename(agents_md)
        logger.info("移行: %s を %s へリネーム", claude_md, agents_md)
        return
    if agents_kind == "expected_symlink" and claude_kind == "regular_file":
        agents_md.unlink()
        claude_md.rename(agents_md)
        logger.info("移行: %s の旧リンクを撤去し %s を実体化", agents_md, agents_md)
        return
    if agents_kind == "regular_file" and claude_kind in {"adapter", "expected_symlink"}:
        claude_md.unlink()
        logger.info("削除: %s（AGENTS.mdへ統一）", claude_md)
        return
    if (agents_kind, claude_kind) in {
        ("regular_file", "missing"),
        ("missing", "missing"),
    }:
        logger.info("維持: プロジェクト指示ファイルの変更なし")
        return

    logger.error(
        "自動移行対象外の指示ファイル状態: %s=%s, %s=%s",
        agents_md,
        agents_kind,
        claude_md,
        claude_kind,
    )
    sys.exit(1)


def _classify_instruction_path(path: Path, *, expected_target: str) -> str:
    """指示ファイルの種別と既知のアダプターを分類する。"""
    if path.is_symlink():
        return "expected_symlink" if os.readlink(path) == expected_target else "other_symlink"
    if not path.exists():
        return "missing"
    if path.is_dir():
        return "directory"
    if path.is_file():
        return "adapter" if _is_claude_adapter(path) else "regular_file"
    return "unknown"


def _is_claude_adapter(path: Path) -> bool:
    """H1を任意とする`@AGENTS.md`アダプターか判定する。"""
    try:
        non_empty = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return False
    return non_empty in [["@AGENTS.md"], ["# CLAUDE.md", "@AGENTS.md"]]


def _clean_rules_dir(rules_dir: Path) -> bool:
    """配布先ディレクトリごと削除する。空になった親ディレクトリも削除する。

    Returns:
        何らかのディレクトリを削除した場合True。
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
    main()

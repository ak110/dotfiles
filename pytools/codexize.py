# PYTHON_ARGCOMPLETE_OK
"""Codex向けに`AGENTS.md`実体・`CLAUDE.md`=H1+`@AGENTS.md`アダプター構成へ収束させるコマンド。

cwd直下の`AGENTS.md`と`CLAUDE.md`を判定し、AGENTS.mdを実体ファイル、
CLAUDE.mdをClaude Codeのfile import記法`@AGENTS.md`を含むアダプターへ整える。
あわせて`.claude/skills`が存在する場合は`.agents/skills -> ../.claude/skills`の
シンボリックリンクを冪等に作成する。

`--clean`実行時はCLAUDE.md単体実体の状態へ戻す。
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from pytools._internal.cli import enable_completion, setup_logging

logger = logging.getLogger(__name__)

_AGENTS_MD = "AGENTS.md"
_CLAUDE_MD = "CLAUDE.md"
_CLAUDE_ADAPTER_BODY = "# CLAUDE.md\n\n@AGENTS.md\n"
_SKILLS_LINK_TARGET = "../.claude/skills"


def main() -> None:
    """AGENTS.md実体・CLAUDE.mdアダプター構成へ収束させるエントリポイント。"""
    setup_logging(verbose=True)
    parser = argparse.ArgumentParser(
        description=("AGENTS.md実体・CLAUDE.md=@AGENTS.mdアダプター・.agents/skillsシンボリックリンクの3点へ収束させる。")
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="新方式の状態をリバートし、CLAUDE.md単体実体の状態へ戻す。",
    )
    enable_completion(parser)
    args = parser.parse_args()
    _codexize(Path.cwd(), clean=args.clean)
    sys.exit(0)


def _codexize(target_dir: Path, *, clean: bool = False) -> None:
    """本体ロジック。テスト時にパスを差し替え可能にする目的で分離する。"""
    agents_md = target_dir / _AGENTS_MD
    claude_md = target_dir / _CLAUDE_MD

    if clean:
        _apply_clean(agents_md, claude_md)
        _remove_skills_symlink(target_dir)
        return

    _apply_codexize(agents_md, claude_md)
    _ensure_skills_symlink(target_dir)


def _apply_codexize(agents_md: Path, claude_md: Path) -> None:
    """AGENTS.md実体・CLAUDE.md=H1+@AGENTS.mdの目標状態へ遷移する。"""
    state = _classify(agents_md, claude_md)
    if state == "applied":
        current = claude_md.read_text(encoding="utf-8")
        if current != _CLAUDE_ADAPTER_BODY:
            claude_md.write_text(_CLAUDE_ADAPTER_BODY, encoding="utf-8")
            logger.info("整形: %s を正規のアダプター本文へ更新", claude_md)
        else:
            logger.info("維持: %s（実体）・%s（アダプター）", agents_md, claude_md)
        return
    if state == "legacy_symlink":
        agents_md.unlink()
        claude_md.rename(agents_md)
        claude_md.write_text(_CLAUDE_ADAPTER_BODY, encoding="utf-8")
        logger.info("移行: %s シンボリックリンクを撤去し %s を作成", agents_md, claude_md)
        return
    if state == "unapplied":
        claude_md.rename(agents_md)
        claude_md.write_text(_CLAUDE_ADAPTER_BODY, encoding="utf-8")
        logger.info("移行: %s 実体を %s へリネームし新 %s を作成", claude_md, agents_md, claude_md)
        return
    if state == "partial":
        claude_md.write_text(_CLAUDE_ADAPTER_BODY, encoding="utf-8")
        logger.info("補完: %s を作成", claude_md)
        return
    _abort_unsupported(state, agents_md, claude_md)


def _apply_clean(agents_md: Path, claude_md: Path) -> None:
    """CLAUDE.md単体実体の状態へリバートする。"""
    state = _classify(agents_md, claude_md)
    if state == "unapplied":
        logger.info("削除対象なし")
        return
    if state == "applied":
        claude_md.unlink()
        agents_md.rename(claude_md)
        logger.info("リバート: %s 実体を %s へリネーム", agents_md, claude_md)
        return
    if state == "legacy_symlink":
        agents_md.unlink()
        logger.info("削除: %s シンボリックリンク", agents_md)
        return
    if state == "partial":
        agents_md.rename(claude_md)
        logger.info("リバート: %s 実体を %s へリネーム", agents_md, claude_md)
        return
    _abort_unsupported(state, agents_md, claude_md)


def _classify(agents_md: Path, claude_md: Path) -> str:
    """AGENTS.mdとCLAUDE.mdの状態を分類する。

    Returns:
        以下のいずれか。
        - "applied": AGENTS.md実体・CLAUDE.mdがアダプター（新方式適用済み）
        - "legacy_symlink": AGENTS.mdがCLAUDE.mdへのシンボリックリンク・CLAUDE.md実体（旧方式）
        - "unapplied": AGENTS.md欠落・CLAUDE.md実体（未適用）
        - "partial": AGENTS.md実体・CLAUDE.md欠落（部分適用）
        - "unsupported": 上記いずれにも該当しない
    """
    agents_kind = _classify_path(agents_md, expected_symlink_target=_CLAUDE_MD)
    claude_kind = _classify_path(claude_md)

    if agents_kind == "regular_file" and claude_kind == "adapter":
        return "applied"
    if agents_kind == "legacy_symlink" and claude_kind == "regular_file":
        return "legacy_symlink"
    if agents_kind == "missing" and claude_kind == "regular_file":
        return "unapplied"
    if agents_kind == "regular_file" and claude_kind == "missing":
        return "partial"
    return "unsupported"


def _classify_path(path: Path, *, expected_symlink_target: str | None = None) -> str:
    """単一ファイルの種別を分類する。

    Returns:
        - "missing": 存在しない
        - "legacy_symlink": `expected_symlink_target`を指すシンボリックリンク
        - "other_symlink": 別の対象を指すシンボリックリンク
        - "adapter": `@AGENTS.md`importを含む2行以下の本文を持つアダプターファイル
        - "regular_file": 上記以外の通常ファイル
        - "directory": ディレクトリ
        - "unknown": 上記いずれにも該当しない
    """
    if path.is_symlink():
        actual = os.readlink(path)
        if expected_symlink_target is not None and actual == expected_symlink_target:
            return "legacy_symlink"
        return "other_symlink"
    if not path.exists():
        return "missing"
    if path.is_dir():
        return "directory"
    if path.is_file():
        if _is_adapter(path):
            return "adapter"
        return "regular_file"
    return "unknown"


def _is_adapter(path: Path) -> bool:
    """`@AGENTS.md`importを含む短いアダプターファイルか判定する。

    判定条件は次の通り。
    - 空行を除いた行数が1〜2行
    - そのいずれかの行が`@AGENTS.md`1行に一致する

    旧方式の`@AGENTS.md`1行のみのCLAUDE.mdも新方式のH1付きアダプターも受理する。
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    non_empty = [line for line in lines if line.strip()]
    if not 1 <= len(non_empty) <= 2:
        return False
    return any(line.strip() == "@AGENTS.md" for line in non_empty)


def _abort_unsupported(state: str, agents_md: Path, claude_md: Path) -> None:
    """自動回復対象外の状態を検出した場合に状況を出力し非ゼロ終了する。"""
    agents_desc = _describe_path(agents_md)
    claude_desc = _describe_path(claude_md)
    logger.error(
        "自動回復対象外の状態を検出（state=%s）: %s=%s, %s=%s",
        state,
        agents_md,
        agents_desc,
        claude_md,
        claude_desc,
    )
    sys.exit(1)


def _describe_path(path: Path) -> str:
    """エラー出力用にファイル状態を人間可読な文字列で返す。"""
    if path.is_symlink():
        return f"symlink -> {os.readlink(path)}"
    if not path.exists():
        return "missing"
    if path.is_dir():
        return "directory"
    if path.is_file():
        if _is_adapter(path):
            return "regular_file(adapter)"
        return "regular_file"
    return "unknown"


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

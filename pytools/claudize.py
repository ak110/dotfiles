"""Claude Code設定ファイルを配布・同期するコマンド。"""

import argparse
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# 条件付き言語別ルール: (ルールファイル名, 判定に使う拡張子タプル)
# 言語別ルールは agent-toolkit プラグインの coding-standards スキルへ移行済み。
# 条件付き配布は維持する仕組みのみ残し、現在の配布対象は空。
_CONDITIONAL_RULES: list[tuple[str, tuple[str, ...]]] = []
# 無条件で配布するルール
_UNCONDITIONAL_RULES: list[str] = ["markdown.md"]
# 配布対象外になった旧ルール（--clean 時および再配布時に既存インストール先から削除するため保持）
_OBSOLETE_RULES: list[str] = [
    "python.md",
    "python-test.md",
    "typescript.md",
    "typescript-test.md",
    "rust.md",
    "rust-test.md",
    "csharp.md",
    "csharp-test.md",
    "powershell.md",
    "windows-batch.md",
    "claude.md",
    "claude-hooks.md",
    "claude-rules.md",
    "claude-skills.md",
]

# 走査時に降下しないディレクトリ (生成物・依存物など、プロジェクトが
# その言語で書かれていることの指標にならないもの)。
# dot で始まるディレクトリ (.venv, .git 等) は別条件で除外する。
_PRUNE_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        "__pycache__",
        "venv",
        "env",
        "target",
        "build",
        "dist",
    }
)


def _main() -> None:
    logging.basicConfig(format="%(message)s", level="DEBUG")
    parser = argparse.ArgumentParser(description="Claude Code設定ファイルを配布・同期する。")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="配布対象のルールファイルをプロジェクトから削除する。",
    )
    args = parser.parse_args()
    template_dir = Path.home() / "dotfiles" / ".chezmoi-source" / "dot_claude" / "rules" / "agent-basics"
    target_dir = Path.cwd()
    _claudize(target_dir, template_dir, clean=args.clean)


def _claudize(target_dir: Path, template_dir: Path, *, clean: bool = False) -> None:
    """本体ロジック。テスト時にパスを差し替え可能にするため分離。"""
    rules_dir = target_dir / ".claude" / "rules" / "agent-basics"

    if clean:
        # 新レイアウト (`.claude/rules/agent-basics/`) と旧レイアウト
        # (`.claude/rules/` 直下) の両方からルールを削除する。旧レイアウトの
        # 残存ファイルを移行するためのフォールバック。
        legacy_rules_dir = target_dir / ".claude" / "rules"
        removed = _clean_rules(rules_dir)
        removed |= _clean_rules(legacy_rules_dir)
        if not removed:
            logger.info("削除対象なし: %s", rules_dir)
        return

    template_path = template_dir / "agent.md"
    if not template_path.exists():
        logger.error("テンプレートが見つかりません: %s", template_path)
        sys.exit(1)

    # ルールの同期
    rules_dir.mkdir(parents=True, exist_ok=True)
    _sync_rules(target_dir, template_dir, rules_dir)


def _clean_rules(rules_dir: Path) -> bool:
    """配布対象のルールファイルを削除する。空になったディレクトリも削除する。

    Returns:
        何らかのファイルまたはディレクトリを削除した場合 True。
    """
    if not rules_dir.exists():
        return False
    removed = False
    targets = [
        "agent.md",
        *_UNCONDITIONAL_RULES,
        *(name for name, _ in _CONDITIONAL_RULES),
        *_OBSOLETE_RULES,
    ]
    for name in targets:
        path = rules_dir / name
        if path.exists():
            path.unlink()
            logger.info("削除: %s", path)
            removed = True
    # 空になった親ディレクトリ (agent-basics/, rules/, .claude/) を順に削除する
    for candidate in [rules_dir, rules_dir.parent, rules_dir.parent.parent]:
        if candidate.name not in {"agent-basics", "rules", ".claude"}:
            break
        if not candidate.exists() or any(candidate.iterdir()):
            break
        candidate.rmdir()
        logger.info("削除: %s", candidate)
        removed = True
    return removed


def _sync_rules(target_dir: Path, template_dir: Path, rules_dir: Path) -> None:
    """全ルールをテンプレートから同期する。"""
    # agent.md + 無条件ルール
    for rule_name in ["agent.md", *_UNCONDITIONAL_RULES]:
        _sync_rule(rules_dir / rule_name, template_dir / rule_name)

    # 条件付きルール (該当言語のファイルが存在する場合のみ配布)
    # 必要な拡張子を事前に集約し、1パスの走査で検出する
    wanted_exts: set[str] = set()
    for _, exts in _CONDITIONAL_RULES:
        wanted_exts.update(exts)
    found_exts = _detect_extensions(target_dir, wanted_exts)

    for rule_name, exts in _CONDITIONAL_RULES:
        dst = rules_dir / rule_name
        if dst.exists() or any(ext in found_exts for ext in exts):
            _sync_rule(dst, template_dir / rule_name)

    # 配布対象外になった旧ルールを除去する (agent-toolkit プラグインへ移行済み)
    for rule_name in _OBSOLETE_RULES:
        path = rules_dir / rule_name
        if path.exists():
            path.unlink()
            logger.info("削除（旧配布物）: %s", path)


def _sync_rule(dst: Path, src: Path) -> None:
    """テンプレートからルールを同期する (常に上書き、frontmatterは維持)。"""
    if not src.exists():
        return
    src_content = src.read_text(encoding="utf-8")
    if dst.exists():
        dst_content = dst.read_text(encoding="utf-8")
        if dst_content == src_content:
            logger.info("同期済み: %s", dst)
            return
        # 既存ファイルのfrontmatterを維持してbody部分のみ上書き
        dst_fm, _ = _split_frontmatter(dst_content)
        src_fm, src_body = _split_frontmatter(src_content)
        new_content = (dst_fm if dst_fm is not None else src_fm or "") + src_body
        fm_diff = dst_fm is not None and src_fm is not None and dst_fm != src_fm
        if new_content == dst_content:
            # bodyは同一、frontmatterのみ異なる
            logger.info("同期済み: %s (frontmatter差分あり)", dst)
            return
        if fm_diff:
            logger.info("上書き: %s (frontmatter差分あり)", dst)
        else:
            logger.info("上書き: %s", dst)
    else:
        new_content = src_content
        logger.info("配布: %s", dst)
    dst.write_text(new_content, encoding="utf-8")


def _split_frontmatter(content: str) -> tuple[str | None, str]:
    """YAML frontmatter とそれ以降の本文に分割する。

    Returns:
        (frontmatter, body) のタプル。frontmatterがない場合は (None, content)。
        frontmatterには開始・終了の`---`行を含む。
    """
    if not content.startswith("---"):
        return None, content
    # 終了の `---` を探す (開始行の直後から)
    end_idx = content.find("\n---", 3)
    if end_idx == -1:
        return None, content
    # `---\n` の末尾の改行まで含める
    fm_end = end_idx + 4  # len("\n---")
    if fm_end < len(content) and content[fm_end] == "\n":
        fm_end += 1
    return content[:fm_end], content[fm_end:]


def _detect_extensions(target_dir: Path, wanted: set[str]) -> set[str]:
    """target_dir配下で、指定された拡張子のファイルを1パスで検出する。

    降下対象から以下を除外する:
    - 名前が `.` で始まるディレクトリ (`.venv`, `.git` など)
    - `_PRUNE_DIRS` に含まれる生成物・依存物ディレクトリ

    `wanted` を全て発見した時点で即座に打ち切る。

    Returns:
        見つかった拡張子の集合 (`wanted` の部分集合)。
    """
    found: set[str] = set()
    remaining = set(wanted)
    if not remaining:
        return found

    for _, dirnames, filenames in os.walk(target_dir):
        # 降下しないディレクトリを in-place で削除して os.walk を枝刈り
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _PRUNE_DIRS]
        for name in filenames:
            # 拡張子比較 (最後の `.` 以降)。`.tsx` など複数候補に対応
            dot = name.rfind(".")
            if dot < 0:
                continue
            ext = name[dot:]
            if ext in remaining:
                found.add(ext)
                remaining.discard(ext)
                if not remaining:
                    return found
    return found


if __name__ == "__main__":
    _main()

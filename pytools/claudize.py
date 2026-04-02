"""Claude Code設定ファイルを配布・同期するコマンド。"""

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_AGENT_RULE = Path(".claude") / "rules" / "agent.md"

# 条件付き言語別ルール: (ルールファイル名, 検出用glob)
_CONDITIONAL_RULES: list[tuple[str, list[str]]] = [
    ("python.md", ["*.py"]),
    ("python-test.md", ["*.py"]),
    ("typescript.md", ["*.ts", "*.tsx"]),
    ("typescript-test.md", ["*.ts", "*.tsx"]),
]
# 無条件で配布するルール
_UNCONDITIONAL_RULES: list[str] = ["markdown.md", "rules.md", "skills.md"]


def _main() -> None:
    logging.basicConfig(format="%(message)s", level="DEBUG")
    template_dir = Path.home() / "dotfiles" / ".claude" / "rules"
    target_dir = Path.cwd()
    _claudize(target_dir, template_dir)


def _claudize(target_dir: Path, template_dir: Path) -> None:
    """本体ロジック。テスト時にパスを差し替え可能にするため分離。"""
    template_path = template_dir / "agent.md"
    if not template_path.exists():
        logger.error("テンプレートが見つかりません: %s", template_path)
        sys.exit(1)

    # ルールの同期
    rules_dir = target_dir / ".claude" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)
    _sync_rules(target_dir, template_dir, rules_dir)


def _sync_rules(target_dir: Path, template_dir: Path, rules_dir: Path) -> None:
    """全ルールをテンプレートから同期する。"""
    # agent.md + 無条件ルール
    for rule_name in ["agent.md", *_UNCONDITIONAL_RULES]:
        _sync_rule(rules_dir / rule_name, template_dir / rule_name)

    # 条件付きルール (該当言語のファイルが存在する場合のみ配布)
    for rule_name, globs in _CONDITIONAL_RULES:
        dst = rules_dir / rule_name
        if dst.exists() or _has_files(target_dir, globs):
            _sync_rule(dst, template_dir / rule_name)


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


def _has_files(target_dir: Path, globs: list[str]) -> bool:
    """指定globに該当するファイルがtarget_dir内に存在するか判定する。

    `.` で始まるディレクトリ内のファイルは無視する。
    """
    for pattern in globs:
        for path in target_dir.rglob(pattern):
            parts = path.relative_to(target_dir).parts[:-1]
            if not any(p.startswith(".") for p in parts):
                return True
    return False


if __name__ == "__main__":
    _main()

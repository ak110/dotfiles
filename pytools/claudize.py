"""プロジェクトのClaude Code設定を初期化・同期するコマンド。

設計思想:
- CLAUDE.md: 汎用的なエージェント向けの指示。
  ~/dotfiles で最新版を管理し、自身が管理する各プロジェクトへ適用する。
- CLAUDE.project.md: プロジェクト固有の指示。プロジェクトごとにカスタマイズする。
  CLAUDE.md の「## 関連ドキュメント」セクションから @CLAUDE.project.md で明示参照される。
"""

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_SECTION_MARKER = "## 関連ドキュメント"


def _main() -> None:
    logging.basicConfig(format="%(message)s", level="DEBUG")
    template_path = Path.home() / "dotfiles" / "CLAUDE.md"
    target_dir = Path.cwd()
    _claudize(target_dir, template_path)


def _claudize(target_dir: Path, template_path: Path) -> None:
    """本体ロジック。テスト時にパスを差し替え可能にするため分離。"""
    # テンプレート読み込み
    if not template_path.exists():
        logger.error("テンプレートが見つかりません: %s", template_path)
        sys.exit(1)
    template_content = template_path.read_text(encoding="utf-8")

    # テンプレートにマーカーが存在することを検証
    template_section = _extract_section_from(template_content, _SECTION_MARKER)
    if template_section is None:
        logger.error("テンプレートに「%s」セクションがありません: %s", _SECTION_MARKER, template_path)
        sys.exit(1)

    # CLAUDE.project.md の作成
    project_md = target_dir / "CLAUDE.project.md"
    if not project_md.exists():
        project_md.write_text("# カスタム指示 (プロジェクト固有)\n", encoding="utf-8")
        logger.info("作成: %s", project_md)
    else:
        logger.info("スキップ (既存): %s", project_md)

    # 既存 CLAUDE.md から「## 関連ドキュメント」セクションの退避
    claude_md = target_dir / "CLAUDE.md"
    if claude_md.exists():
        _rescue_section(claude_md, project_md, template_section)

    # CLAUDE.md をテンプレートで上書き
    claude_md.write_text(template_content, encoding="utf-8")
    logger.info("上書き: %s", claude_md)


def _rescue_section(claude_md: Path, project_md: Path, template_section: str) -> None:
    """既存 CLAUDE.md の「## 関連ドキュメント」セクションから、テンプレートにない行を退避する。"""
    existing_content = claude_md.read_text(encoding="utf-8")
    existing_section = _extract_section_from(existing_content, _SECTION_MARKER)
    if existing_section is None:
        logger.info("退避スキップ (マーカーなし): %s", claude_md)
        return
    if existing_section == template_section:
        logger.info("退避スキップ (一致): %s", claude_md)
        return

    # テンプレートのセクションに含まれない行のみ退避
    # @CLAUDE.project.md 等のテンプレート標準参照が退避先に混入して自己参照になることを回避
    template_lines = set(template_section.splitlines())
    extra_lines = [line for line in existing_section.splitlines() if line not in template_lines]
    if not extra_lines:
        logger.info("退避スキップ (差分なし): %s", claude_md)
        return

    project_content = project_md.read_text(encoding="utf-8")
    if not project_content.endswith("\n"):
        project_content += "\n"
    # 見出し付きで退避
    project_content += "\n" + _SECTION_MARKER + "\n\n" + "\n".join(extra_lines) + "\n"
    project_md.write_text(project_content, encoding="utf-8")
    logger.info("退避: %d行を %s に追記", len(extra_lines), project_md)


def _extract_section_from(content: str, marker: str) -> str | None:
    """マーカー行からEOFまでを返す。見つからなければ None。"""
    idx = content.find(marker)
    if idx == -1:
        return None
    return content[idx:]


if __name__ == "__main__":
    _main()

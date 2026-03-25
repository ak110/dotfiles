"""claudizeモジュールのテスト。"""

from pathlib import Path

import pytest

from pytools.claudize import _claudize, _extract_section_from

# テスト用テンプレート
TEMPLATE = """\
# カスタム指示

## 基本原則

- ルール1

## 関連ドキュメント

- @CLAUDE.project.md
"""


class TestClaudize:
    """_claudize のテスト。"""

    def _setup_template(self, tmp_path: Path) -> Path:
        template = tmp_path / "dotfiles" / "CLAUDE.md"
        template.parent.mkdir()
        template.write_text(TEMPLATE, encoding="utf-8")
        return template

    def test_initial_run(self, tmp_path: Path):
        """初回実行でファイルが正しく作成される。"""
        template = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template)

        assert (target / "CLAUDE.md").read_text(encoding="utf-8") == TEMPLATE
        assert (target / "CLAUDE.project.md").read_text(encoding="utf-8") == "# カスタム指示 (プロジェクト固有)\n"

    def test_idempotent(self, tmp_path: Path):
        """2回実行しても結果が同じ。"""
        template = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template)
        _claudize(target, template)

        assert (target / "CLAUDE.md").read_text(encoding="utf-8") == TEMPLATE
        assert (target / "CLAUDE.project.md").read_text(encoding="utf-8") == "# カスタム指示 (プロジェクト固有)\n"

    def test_rescue_extra_references(self, tmp_path: Path):
        """既存CLAUDE.mdに追加された参照がCLAUDE.project.mdに退避される。"""
        template = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        # 既存CLAUDE.mdにプロジェクト固有の参照を追加
        existing = TEMPLATE.rstrip() + "\n- @docs/architecture.md\n"
        (target / "CLAUDE.md").write_text(existing, encoding="utf-8")
        (target / "CLAUDE.project.md").write_text("# カスタム指示 (プロジェクト固有)\n", encoding="utf-8")

        _claudize(target, template)

        # テンプレートで上書きされている
        assert (target / "CLAUDE.md").read_text(encoding="utf-8") == TEMPLATE
        # 追加分のみ退避（テンプレート標準行は含まない）
        project_content = (target / "CLAUDE.project.md").read_text(encoding="utf-8")
        # 見出し付きで退避される
        assert "## 関連ドキュメント\n\n- @docs/architecture.md\n" in project_content
        # テンプレート標準行は含まない
        assert "- @CLAUDE.project.md" not in project_content.split("# カスタム指示 (プロジェクト固有)")[1]

    def test_no_marker_in_existing(self, tmp_path: Path):
        """既存CLAUDE.mdにマーカーがない場合、退避スキップしてテンプレートで上書き。"""
        template = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        (target / "CLAUDE.md").write_text("# 古い内容\n", encoding="utf-8")
        (target / "CLAUDE.project.md").write_text("# カスタム指示 (プロジェクト固有)\n", encoding="utf-8")

        _claudize(target, template)

        assert (target / "CLAUDE.md").read_text(encoding="utf-8") == TEMPLATE
        # CLAUDE.project.md は変更されない
        assert (target / "CLAUDE.project.md").read_text(encoding="utf-8") == "# カスタム指示 (プロジェクト固有)\n"

    def test_template_not_found(self, tmp_path: Path):
        """テンプレートが存在しない場合、SystemExitが発生する。"""
        target = tmp_path / "project"
        target.mkdir()

        with pytest.raises(SystemExit):
            _claudize(target, tmp_path / "nonexistent" / "CLAUDE.md")

    def test_template_without_marker(self, tmp_path: Path):
        """テンプレートにマーカーがない場合、SystemExitが発生する。"""
        template = tmp_path / "dotfiles" / "CLAUDE.md"
        template.parent.mkdir()
        template.write_text("# カスタム指示\n\nルールのみ\n", encoding="utf-8")
        target = tmp_path / "project"
        target.mkdir()

        with pytest.raises(SystemExit):
            _claudize(target, template)


class TestExtractSectionFrom:
    """_extract_section_from のテスト。"""

    def test_found(self):
        content = "# Header\n\n## Section\n\n- item1\n- item2\n"
        result = _extract_section_from(content, "## Section")
        assert result == "## Section\n\n- item1\n- item2\n"

    def test_not_found(self):
        assert _extract_section_from("# Header\n", "## Missing") is None

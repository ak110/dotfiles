"""claudizeモジュールのテスト。"""

from pathlib import Path

import pytest

from pytools.claudize import _claudize, _extract_section_from

# テスト用テンプレート (関連ドキュメントセクションなし)
TEMPLATE = """\
# カスタム指示

## 基本原則

- ルール1
"""

# 旧形式のテンプレート (関連ドキュメントセクションあり)
LEGACY_TEMPLATE = """\
# カスタム指示

## 基本原則

- ルール1

## 関連ドキュメント

- @CLAUDE.project.md
"""


class TestClaudize:
    """_claudize のテスト。"""

    def _setup_template(self, tmp_path: Path) -> Path:
        template = tmp_path / "dotfiles" / "CLAUDE.base.md"
        template.parent.mkdir()
        template.write_text(TEMPLATE, encoding="utf-8")
        return template

    def test_initial_run(self, tmp_path: Path):
        """パターンA: 初回実行でCLAUDE.mdとCLAUDE.base.mdが正しく作成される。"""
        template = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template)

        claude_md = (target / "CLAUDE.md").read_text(encoding="utf-8")
        assert "@CLAUDE.base.md" in claude_md
        assert "# カスタム指示" in claude_md
        assert (target / "CLAUDE.base.md").read_text(encoding="utf-8") == TEMPLATE

    def test_idempotent(self, tmp_path: Path):
        """パターンC: 2回実行しても結果が同じ。"""
        template = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template)
        md_after_first = (target / "CLAUDE.md").read_text(encoding="utf-8")

        _claudize(target, template)
        md_after_second = (target / "CLAUDE.md").read_text(encoding="utf-8")

        assert md_after_first == md_after_second
        assert (target / "CLAUDE.base.md").read_text(encoding="utf-8") == TEMPLATE

    def test_migration_from_legacy(self, tmp_path: Path):
        """パターンB: 旧形式から移行、バックアップ作成、CLAUDE.project.md削除。"""
        template = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        # 旧形式のファイルを配置
        (target / "CLAUDE.md").write_text(LEGACY_TEMPLATE, encoding="utf-8")
        project_content = "# カスタム指示 (プロジェクト固有)\n\n## コマンド\n\n- make test\n"
        (target / "CLAUDE.project.md").write_text(project_content, encoding="utf-8")

        _claudize(target, template)

        # CLAUDE.md が新形式になっている
        new_md = (target / "CLAUDE.md").read_text(encoding="utf-8")
        assert "@CLAUDE.base.md" in new_md
        assert "## コマンド" in new_md
        # @CLAUDE.project.md 参照が含まれない
        assert "@CLAUDE.project.md" not in new_md
        # CLAUDE.project.md が削除されている
        assert not (target / "CLAUDE.project.md").exists()
        # CLAUDE.base.md がテンプレートで作成されている
        assert (target / "CLAUDE.base.md").read_text(encoding="utf-8") == TEMPLATE

    def test_migration_merges_extra_refs(self, tmp_path: Path):
        """パターンB: 旧CLAUDE.mdの追加参照が新CLAUDE.mdに統合される。"""
        template = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        # 旧CLAUDE.mdに追加参照あり
        old_md = LEGACY_TEMPLATE.rstrip() + "\n- @docs/architecture.md\n"
        (target / "CLAUDE.md").write_text(old_md, encoding="utf-8")
        project_content = "# カスタム指示 (プロジェクト固有)\n\n## 関連ドキュメント\n\n- @README.md\n"
        (target / "CLAUDE.project.md").write_text(project_content, encoding="utf-8")

        _claudize(target, template)

        new_md = (target / "CLAUDE.md").read_text(encoding="utf-8")
        # 追加参照が統合されている
        assert "@docs/architecture.md" in new_md
        # 既存参照も残っている
        assert "@README.md" in new_md
        # @CLAUDE.project.md は含まれない
        assert "@CLAUDE.project.md" not in new_md

    def test_claude_md_only(self, tmp_path: Path):
        """パターンD: CLAUDE.mdのみの場合、@CLAUDE.base.md参照を自動追加。"""
        template = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        (target / "CLAUDE.md").write_text("# プロジェクト指示\n\n## 設定\n\n- 設定1\n", encoding="utf-8")

        _claudize(target, template)

        new_md = (target / "CLAUDE.md").read_text(encoding="utf-8")
        assert "@CLAUDE.base.md" in new_md
        assert "## 設定" in new_md
        assert (target / "CLAUDE.base.md").read_text(encoding="utf-8") == TEMPLATE

    def test_claude_md_only_already_has_ref(self, tmp_path: Path):
        """パターンD: 既に@CLAUDE.base.md参照がある場合はそのまま。"""
        template = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        content = "# プロジェクト指示\n\n@CLAUDE.base.md\n\n## 設定\n\n- 設定1\n"
        (target / "CLAUDE.md").write_text(content, encoding="utf-8")

        _claudize(target, template)

        assert (target / "CLAUDE.md").read_text(encoding="utf-8") == content

    def test_new_format_existing(self, tmp_path: Path):
        """パターンC: CLAUDE.base.mdのみ上書き、CLAUDE.mdは変更なし。"""
        template = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        md_content = "# プロジェクト指示\n\n@CLAUDE.base.md\n\n## 設定\n\n- 設定1\n"
        (target / "CLAUDE.md").write_text(md_content, encoding="utf-8")
        (target / "CLAUDE.base.md").write_text("# 古いベース\n", encoding="utf-8")

        _claudize(target, template)

        assert (target / "CLAUDE.md").read_text(encoding="utf-8") == md_content
        assert (target / "CLAUDE.base.md").read_text(encoding="utf-8") == TEMPLATE

    def test_template_not_found(self, tmp_path: Path):
        """テンプレートが存在しない場合、SystemExitが発生する。"""
        target = tmp_path / "project"
        target.mkdir()

        with pytest.raises(SystemExit):
            _claudize(target, tmp_path / "nonexistent" / "CLAUDE.base.md")

    def test_error_on_legacy_remnant_pattern_d(self, tmp_path: Path):
        """パターンD: @CLAUDE.project.md参照があればエラー。"""
        template = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        (target / "CLAUDE.md").write_text(LEGACY_TEMPLATE, encoding="utf-8")

        with pytest.raises(SystemExit):
            _claudize(target, template)

    def test_error_on_three_files(self, tmp_path: Path):
        """3ファイル同居はエラー。"""
        template = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        (target / "CLAUDE.md").write_text("# test\n", encoding="utf-8")
        (target / "CLAUDE.project.md").write_text("# test\n", encoding="utf-8")
        (target / "CLAUDE.base.md").write_text("# test\n", encoding="utf-8")

        with pytest.raises(SystemExit):
            _claudize(target, template)

    def test_error_on_project_md_only(self, tmp_path: Path):
        """CLAUDE.project.mdのみ存在はエラー。"""
        template = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        (target / "CLAUDE.project.md").write_text("# test\n", encoding="utf-8")

        with pytest.raises(SystemExit):
            _claudize(target, template)


class TestExtractSectionFrom:
    """_extract_section_from のテスト。"""

    def test_last_section(self):
        content = "# Header\n\n## Section\n\n- item1\n- item2\n"
        result = _extract_section_from(content, "## Section")
        assert result == "## Section\n\n- item1\n- item2\n"

    def test_middle_section(self):
        content = "# Header\n\n## Section A\n\n- a1\n\n## Section B\n\n- b1\n"
        result = _extract_section_from(content, "## Section A")
        assert result == "## Section A\n\n- a1\n\n"

    def test_not_found(self):
        assert _extract_section_from("# Header\n", "## Missing") is None

    def test_ignores_code_block(self):
        content = "# Header\n\n```\n## Not a heading\n```\n\n## Real Section\n\n- item\n"
        result = _extract_section_from(content, "## Real Section")
        assert result == "## Real Section\n\n- item\n"
        # コードブロック内の見出しはマッチしない
        assert _extract_section_from(content, "## Not a heading") is None

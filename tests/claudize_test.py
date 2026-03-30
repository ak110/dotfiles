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

# 言語別ルール用テンプレート
LANG_RULE_TEMPLATE = "# テストルール\n"


def _agent_md(target: Path) -> Path:
    return target / ".claude" / "rules" / "agent.md"


class TestClaudize:
    """_claudize のテスト。"""

    def _setup_template(self, tmp_path: Path) -> Path:
        """テンプレートディレクトリを作成し、agent.md と言語別ルールを配置する。"""
        template_dir = tmp_path / "dotfiles" / ".claude" / "rules"
        template_dir.mkdir(parents=True)
        (template_dir / "agent.md").write_text(TEMPLATE, encoding="utf-8")
        for name in [
            "python.md",
            "python-test.md",
            "markdown.md",
            "rules.md",
            "skills.md",
            "typescript.md",
            "typescript-test.md",
        ]:
            (template_dir / name).write_text(LANG_RULE_TEMPLATE, encoding="utf-8")
        return template_dir

    def test_initial_run(self, tmp_path: Path):
        """パターンA: 初回実行でCLAUDE.mdとagent.mdが正しく作成される。"""
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template_dir)

        claude_md = (target / "CLAUDE.md").read_text(encoding="utf-8")
        assert "# カスタム指示" in claude_md
        # .claude/rules/ は自動読み込みなので @CLAUDE.base.md 参照は含まない
        assert "@CLAUDE.base.md" not in claude_md
        assert _agent_md(target).read_text(encoding="utf-8") == TEMPLATE

    def test_idempotent(self, tmp_path: Path):
        """パターンC: 2回実行しても結果が同じ。"""
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template_dir)
        md_after_first = (target / "CLAUDE.md").read_text(encoding="utf-8")

        _claudize(target, template_dir)
        md_after_second = (target / "CLAUDE.md").read_text(encoding="utf-8")

        assert md_after_first == md_after_second
        assert _agent_md(target).read_text(encoding="utf-8") == TEMPLATE

    def test_migration_from_legacy(self, tmp_path: Path):
        """パターンB: 旧形式から移行、CLAUDE.project.md削除。"""
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        # 旧形式のファイルを配置
        (target / "CLAUDE.md").write_text(LEGACY_TEMPLATE, encoding="utf-8")
        project_content = "# カスタム指示 (プロジェクト固有)\n\n## コマンド\n\n- make test\n"
        (target / "CLAUDE.project.md").write_text(project_content, encoding="utf-8")

        _claudize(target, template_dir)

        # CLAUDE.md が新形式になっている
        new_md = (target / "CLAUDE.md").read_text(encoding="utf-8")
        assert "## コマンド" in new_md
        # @CLAUDE.base.md / @CLAUDE.project.md 参照が含まれない
        assert "@CLAUDE.base.md" not in new_md
        assert "@CLAUDE.project.md" not in new_md
        # CLAUDE.project.md が削除されている
        assert not (target / "CLAUDE.project.md").exists()
        # agent.md がテンプレートで作成されている
        assert _agent_md(target).read_text(encoding="utf-8") == TEMPLATE

    def test_migration_merges_extra_refs(self, tmp_path: Path):
        """パターンB: 旧CLAUDE.mdの追加参照が新CLAUDE.mdに統合される。"""
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        # 旧CLAUDE.mdに追加参照あり
        old_md = LEGACY_TEMPLATE.rstrip() + "\n- @docs/architecture.md\n"
        (target / "CLAUDE.md").write_text(old_md, encoding="utf-8")
        project_content = "# カスタム指示 (プロジェクト固有)\n\n## 関連ドキュメント\n\n- @README.md\n"
        (target / "CLAUDE.project.md").write_text(project_content, encoding="utf-8")

        _claudize(target, template_dir)

        new_md = (target / "CLAUDE.md").read_text(encoding="utf-8")
        # 追加参照が統合されている
        assert "@docs/architecture.md" in new_md
        # 既存参照も残っている
        assert "@README.md" in new_md
        # @CLAUDE.project.md は含まれない
        assert "@CLAUDE.project.md" not in new_md

    def test_claude_md_only(self, tmp_path: Path):
        """パターンD: CLAUDE.mdのみの場合、agent.mdが作成される。"""
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        (target / "CLAUDE.md").write_text("# プロジェクト指示\n\n## 設定\n\n- 設定1\n", encoding="utf-8")

        _claudize(target, template_dir)

        new_md = (target / "CLAUDE.md").read_text(encoding="utf-8")
        assert "## 設定" in new_md
        assert _agent_md(target).read_text(encoding="utf-8") == TEMPLATE

    def test_claude_md_only_removes_base_ref(self, tmp_path: Path):
        """パターンD: @CLAUDE.base.md参照が残っている場合は除去される。"""
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        content = "# プロジェクト指示\n\n@CLAUDE.base.md\n\n## 設定\n\n- 設定1\n"
        (target / "CLAUDE.md").write_text(content, encoding="utf-8")

        _claudize(target, template_dir)

        new_md = (target / "CLAUDE.md").read_text(encoding="utf-8")
        assert "@CLAUDE.base.md" not in new_md
        assert "## 設定" in new_md

    def test_new_format_existing(self, tmp_path: Path):
        """パターンC: agent.mdのみ上書き、CLAUDE.mdは変更なし。"""
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        md_content = "# プロジェクト指示\n\n## 設定\n\n- 設定1\n"
        (target / "CLAUDE.md").write_text(md_content, encoding="utf-8")
        agent = _agent_md(target)
        agent.parent.mkdir(parents=True)
        agent.write_text("# 古いベース\n", encoding="utf-8")

        _claudize(target, template_dir)

        assert (target / "CLAUDE.md").read_text(encoding="utf-8") == md_content
        assert _agent_md(target).read_text(encoding="utf-8") == TEMPLATE

    def test_intermediate_migration(self, tmp_path: Path):
        """パターンE: CLAUDE.base.md → .claude/rules/agent.md の移行。"""
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        # 中間形式のファイルを配置
        (target / "CLAUDE.md").write_text(
            "# カスタム指示\n\n@CLAUDE.base.md\n\n## 設定\n\n- 設定1\n",
            encoding="utf-8",
        )
        (target / "CLAUDE.base.md").write_text("# 古いベース\n", encoding="utf-8")

        _claudize(target, template_dir)

        # CLAUDE.base.md が削除されている
        assert not (target / "CLAUDE.base.md").exists()
        # agent.md がテンプレートで作成されている
        assert _agent_md(target).read_text(encoding="utf-8") == TEMPLATE
        # CLAUDE.md から @CLAUDE.base.md 参照が除去されている
        claude_md = (target / "CLAUDE.md").read_text(encoding="utf-8")
        assert "@CLAUDE.base.md" not in claude_md
        assert "## 設定" in claude_md

    def test_template_not_found(self, tmp_path: Path):
        """テンプレートが存在しない場合、SystemExitが発生する。"""
        target = tmp_path / "project"
        target.mkdir()

        with pytest.raises(SystemExit):
            _claudize(target, tmp_path / "nonexistent" / ".claude" / "rules")

    def test_error_on_legacy_remnant_pattern_d(self, tmp_path: Path):
        """パターンD: @CLAUDE.project.md参照があればエラー。"""
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        (target / "CLAUDE.md").write_text(LEGACY_TEMPLATE, encoding="utf-8")

        with pytest.raises(SystemExit):
            _claudize(target, template_dir)

    def test_error_on_base_and_agent_coexist(self, tmp_path: Path):
        """CLAUDE.base.md と .claude/rules/agent.md の同居はエラー。"""
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        (target / "CLAUDE.md").write_text("# test\n", encoding="utf-8")
        (target / "CLAUDE.base.md").write_text("# test\n", encoding="utf-8")
        agent = _agent_md(target)
        agent.parent.mkdir(parents=True)
        agent.write_text("# test\n", encoding="utf-8")

        with pytest.raises(SystemExit):
            _claudize(target, template_dir)

    def test_error_on_project_md_only(self, tmp_path: Path):
        """CLAUDE.project.mdのみ存在はエラー。"""
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        (target / "CLAUDE.project.md").write_text("# test\n", encoding="utf-8")

        with pytest.raises(SystemExit):
            _claudize(target, template_dir)


class TestLangRules:
    """言語別ルール配布のテスト。"""

    def _setup_template(self, tmp_path: Path) -> Path:
        template_dir = tmp_path / "dotfiles" / ".claude" / "rules"
        template_dir.mkdir(parents=True)
        (template_dir / "agent.md").write_text(TEMPLATE, encoding="utf-8")
        for name in [
            "python.md",
            "python-test.md",
            "markdown.md",
            "rules.md",
            "skills.md",
            "typescript.md",
            "typescript-test.md",
        ]:
            (template_dir / name).write_text(f"# {name} ルール\n", encoding="utf-8")
        return template_dir

    def test_python_rules(self, tmp_path: Path):
        """*.py が存在する場合、python.md と python-test.md が配布される。"""
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "main.py").write_text("", encoding="utf-8")

        _claudize(target, template_dir)

        rules_dir = target / ".claude" / "rules"
        assert (rules_dir / "python.md").exists()
        assert (rules_dir / "python-test.md").exists()

    def test_typescript_rules(self, tmp_path: Path):
        """*.ts が存在する場合、typescript.md と typescript-test.md が配布される。"""
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "main.ts").write_text("", encoding="utf-8")

        _claudize(target, template_dir)

        rules_dir = target / ".claude" / "rules"
        assert (rules_dir / "typescript.md").exists()
        assert (rules_dir / "typescript-test.md").exists()

    def test_markdown_always_deployed(self, tmp_path: Path):
        """markdown.md は無条件で配布される。"""
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template_dir)

        assert (target / ".claude" / "rules" / "markdown.md").exists()

    def test_skip_existing_rules_with_diff(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """既にルールが存在し差分がある場合、スキップされて差分が通知される。"""
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "main.py").write_text("", encoding="utf-8")

        # テンプレートと異なる内容の既存ルールを配置
        rules_dir = target / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        custom_content = "# カスタムルール\n"
        (rules_dir / "python.md").write_text(custom_content, encoding="utf-8")

        _claudize(target, template_dir)

        # 既存のカスタムルールが上書きされていない
        assert (rules_dir / "python.md").read_text(encoding="utf-8") == custom_content
        # 他のルールは配布される
        assert (rules_dir / "python-test.md").exists()
        # 差分ありの警告が出力される
        assert any("差分あり" in r.message and "python.md" in r.message for r in caplog.records)

    def test_skip_existing_rules_no_diff(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """既にルールが存在し差分がない場合、警告は出ない。"""
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "main.py").write_text("", encoding="utf-8")

        # テンプレートと同じ内容の既存ルールを配置
        rules_dir = target / ".claude" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "python.md").write_text(
            (template_dir / "python.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        _claudize(target, template_dir)

        # 差分ありの警告が出力されない
        assert not any("差分あり" in r.message for r in caplog.records)

    def test_skip_no_matching_files(self, tmp_path: Path):
        """該当ファイルがない場合、条件付きルールはスキップされる。"""
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template_dir)

        rules_dir = target / ".claude" / "rules"
        # agent.md と markdown.md は存在
        assert (rules_dir / "agent.md").exists()
        assert (rules_dir / "markdown.md").exists()
        # 条件付きルールは存在しない
        assert not (rules_dir / "python.md").exists()
        assert not (rules_dir / "typescript.md").exists()

    def test_dotdir_files_ignored(self, tmp_path: Path):
        """.で始まるディレクトリ内のファイルは検出対象外。"""
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        # .hidden/ 内にのみ .py ファイルがある
        hidden = target / ".hidden"
        hidden.mkdir()
        (hidden / "secret.py").write_text("", encoding="utf-8")

        _claudize(target, template_dir)

        rules_dir = target / ".claude" / "rules"
        assert not (rules_dir / "python.md").exists()


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

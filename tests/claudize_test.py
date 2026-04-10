"""claudizeモジュールのテスト。"""

import logging
from pathlib import Path

import pytest

from pytools.claudize import _claudize, _split_frontmatter

# テスト用テンプレート
TEMPLATE = """\
# カスタム指示

## 基本原則

- ルール1
"""

# 言語別ルール用テンプレート
LANG_RULE_TEMPLATE = "# テストルール\n"


def _setup_template(tmp_path: Path) -> Path:
    """テンプレートディレクトリを作成し、agent.md と言語別ルールを配置する。"""
    template_dir = tmp_path / "dotfiles" / ".chezmoi-source" / "dot_claude" / "rules" / "agent-basics"
    template_dir.mkdir(parents=True)
    (template_dir / "agent.md").write_text(TEMPLATE, encoding="utf-8")
    for name in [
        "python.md",
        "python-test.md",
        "claude.md",
        "claude-rules.md",
        "claude-skills.md",
        "markdown.md",
        "typescript.md",
        "typescript-test.md",
    ]:
        (template_dir / name).write_text(LANG_RULE_TEMPLATE, encoding="utf-8")
    return template_dir


class TestLangRules:
    """言語別ルール配布のテスト。"""

    def test_python_rules(self, tmp_path: Path):
        """*.py が存在する場合、python.md と python-test.md が配布される。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "main.py").write_text("", encoding="utf-8")

        _claudize(target, template_dir)

        rules_dir = target / ".claude" / "rules" / "agent-basics"
        assert (rules_dir / "python.md").exists()
        assert (rules_dir / "python-test.md").exists()

    def test_typescript_rules(self, tmp_path: Path):
        """*.ts が存在する場合、typescript.md と typescript-test.md が配布される。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "main.ts").write_text("", encoding="utf-8")

        _claudize(target, template_dir)

        rules_dir = target / ".claude" / "rules" / "agent-basics"
        assert (rules_dir / "typescript.md").exists()
        assert (rules_dir / "typescript-test.md").exists()

    def test_unconditional_rules_always_deployed(self, tmp_path: Path):
        """agent.md, claude.md, claude-rules.md, claude-skills.md, markdown.md は無条件で配布される。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template_dir)

        rules_dir = target / ".claude" / "rules" / "agent-basics"
        assert (rules_dir / "agent.md").exists()
        assert (rules_dir / "claude.md").exists()
        assert (rules_dir / "claude-rules.md").exists()
        assert (rules_dir / "claude-skills.md").exists()
        assert (rules_dir / "markdown.md").exists()

    def test_overwrite_existing_rules(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """既にルールが存在し差分がある場合、テンプレートで上書きされる。"""
        caplog.set_level(logging.INFO)
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "main.py").write_text("", encoding="utf-8")

        # テンプレートと異なる内容の既存ルールを配置
        rules_dir = target / ".claude" / "rules" / "agent-basics"
        rules_dir.mkdir(parents=True)
        (rules_dir / "python.md").write_text("# カスタムルール\n", encoding="utf-8")

        _claudize(target, template_dir)

        # テンプレートで上書きされている
        expected = (template_dir / "python.md").read_text(encoding="utf-8")
        assert (rules_dir / "python.md").read_text(encoding="utf-8") == expected
        # 他のルールも配布される
        assert (rules_dir / "python-test.md").exists()
        # 「上書き」ログが出力される
        assert any("上書き" in r.message and "python.md" in r.message for r in caplog.records)

    def test_overwrite_existing_unconditional_rules(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """無条件ルールも差分がある場合はテンプレートで上書きされる。"""
        caplog.set_level(logging.INFO)
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        # テンプレートと異なる内容の既存ルールを配置
        rules_dir = target / ".claude" / "rules" / "agent-basics"
        rules_dir.mkdir(parents=True)
        (rules_dir / "markdown.md").write_text("# 古いルール\n", encoding="utf-8")

        _claudize(target, template_dir)

        # テンプレートで上書きされている
        expected = (template_dir / "markdown.md").read_text(encoding="utf-8")
        assert (rules_dir / "markdown.md").read_text(encoding="utf-8") == expected
        assert any("上書き" in r.message and "markdown.md" in r.message for r in caplog.records)

    def test_idempotent(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """2回実行しても結果が同じ。差分なしの旨が表示される。"""
        caplog.set_level(logging.INFO)
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template_dir)

        caplog.clear()
        _claudize(target, template_dir)

        # 2回目は「同期済み」が表示される
        assert any("同期済み" in r.message for r in caplog.records)

    def test_skip_no_matching_files(self, tmp_path: Path):
        """該当ファイルがない場合、条件付きルールはスキップされる。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template_dir)

        rules_dir = target / ".claude" / "rules" / "agent-basics"
        # agent.md と無条件ルールは存在
        assert (rules_dir / "agent.md").exists()
        assert (rules_dir / "markdown.md").exists()
        # 条件付きルールは存在しない
        assert not (rules_dir / "python.md").exists()
        assert not (rules_dir / "typescript.md").exists()

    def test_dotdir_files_ignored(self, tmp_path: Path):
        """.で始まるディレクトリ内のファイルは検出対象外。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        # .hidden/ 内にのみ .py ファイルがある
        hidden = target / ".hidden"
        hidden.mkdir()
        (hidden / "secret.py").write_text("", encoding="utf-8")

        _claudize(target, template_dir)

        rules_dir = target / ".claude" / "rules" / "agent-basics"
        assert not (rules_dir / "python.md").exists()

    def test_venv_dir_ignored(self, tmp_path: Path):
        """.venv/ 配下の .py は検出対象外。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        venv = target / ".venv"
        venv.mkdir()
        (venv / "foo.py").write_text("", encoding="utf-8")

        _claudize(target, template_dir)

        rules_dir = target / ".claude" / "rules" / "agent-basics"
        assert not (rules_dir / "python.md").exists()

    def test_node_modules_ignored(self, tmp_path: Path):
        """node_modules/ 配下の .ts は検出対象外 (固定除外リスト)。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        nm = target / "node_modules"
        nm.mkdir()
        (nm / "foo.ts").write_text("", encoding="utf-8")

        _claudize(target, template_dir)

        rules_dir = target / ".claude" / "rules" / "agent-basics"
        assert not (rules_dir / "typescript.md").exists()

    def test_build_dir_ignored(self, tmp_path: Path):
        """build/ 配下の .py は検出対象外 (固定除外リスト)。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        build = target / "build"
        build.mkdir()
        (build / "generated.py").write_text("", encoding="utf-8")

        _claudize(target, template_dir)

        rules_dir = target / ".claude" / "rules" / "agent-basics"
        assert not (rules_dir / "python.md").exists()

    def test_detect_extensions_single_pass(self, tmp_path: Path):
        """.py と .ts が両方あれば、両方のルールが配布される (1パス検出の検証)。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "main.py").write_text("", encoding="utf-8")
        (target / "main.ts").write_text("", encoding="utf-8")

        _claudize(target, template_dir)

        rules_dir = target / ".claude" / "rules" / "agent-basics"
        assert (rules_dir / "python.md").exists()
        assert (rules_dir / "python-test.md").exists()
        assert (rules_dir / "typescript.md").exists()
        assert (rules_dir / "typescript-test.md").exists()

    def test_existing_rule_kept_even_if_source_only_in_build(self, tmp_path: Path):
        """既に python.md が配布済みなら、build/ 配下にしか .py がなくても同期される。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        build = target / "build"
        build.mkdir()
        (build / "generated.py").write_text("", encoding="utf-8")

        # 既に python.md を配布済みの状態を作る
        rules_dir = target / ".claude" / "rules" / "agent-basics"
        rules_dir.mkdir(parents=True)
        (rules_dir / "python.md").write_text("# 旧ルール\n", encoding="utf-8")

        _claudize(target, template_dir)

        # dst.exists() フォールバックにより、build/ のみでも同期継続
        expected = (template_dir / "python.md").read_text(encoding="utf-8")
        assert (rules_dir / "python.md").read_text(encoding="utf-8") == expected

    def test_no_claude_md_created(self, tmp_path: Path):
        """claudize は CLAUDE.md を作成しない。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template_dir)

        assert not (target / "CLAUDE.md").exists()

    def test_template_not_found(self, tmp_path: Path):
        """テンプレートが存在しない場合、SystemExitが発生する。"""
        target = tmp_path / "project"
        target.mkdir()

        with pytest.raises(SystemExit):
            _claudize(target, tmp_path / "nonexistent" / ".claude" / "rules")


class TestClean:
    """--clean オプションのテスト。"""

    def test_clean_removes_distributed_rules(self, tmp_path: Path):
        """配布済みの全ルールファイルと空のrules/が削除される。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "main.py").write_text("", encoding="utf-8")
        (target / "main.ts").write_text("", encoding="utf-8")

        # まず配布
        _claudize(target, template_dir)
        rules_dir = target / ".claude" / "rules" / "agent-basics"
        assert (rules_dir / "agent.md").exists()
        assert (rules_dir / "python.md").exists()

        # clean 実行
        _claudize(target, template_dir, clean=True)

        # 配布対象ファイルが全て削除されている
        assert not (rules_dir / "agent.md").exists()
        assert not (rules_dir / "markdown.md").exists()
        assert not (rules_dir / "python.md").exists()
        assert not (rules_dir / "python-test.md").exists()
        assert not (rules_dir / "typescript.md").exists()
        assert not (rules_dir / "typescript-test.md").exists()
        assert not (rules_dir / "claude.md").exists()
        assert not (rules_dir / "claude-rules.md").exists()
        assert not (rules_dir / "claude-skills.md").exists()
        # 空になったディレクトリも削除されている
        assert not rules_dir.exists()

    def test_clean_preserves_other_files(self, tmp_path: Path):
        """claudize 管理外のファイルは削除されず、rules/ も残る。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        _claudize(target, template_dir)
        rules_dir = target / ".claude" / "rules" / "agent-basics"
        # ユーザーが置いた別ファイル
        (rules_dir / "custom.md").write_text("# custom\n", encoding="utf-8")

        _claudize(target, template_dir, clean=True)

        assert not (rules_dir / "agent.md").exists()
        assert (rules_dir / "custom.md").exists()
        assert rules_dir.exists()

    def test_clean_nonexistent_rules_dir(self, tmp_path: Path):
        """rules/ が存在しない場合でもエラーにならない。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template_dir, clean=True)

        assert not (target / ".claude" / "rules" / "agent-basics").exists()

    def test_clean_removes_legacy_layout(self, tmp_path: Path):
        """旧レイアウト (.claude/rules/ 直下) の配布ファイルも削除される。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        # 旧レイアウトを再現: .claude/rules/ 直下に配布ファイルを置く
        legacy_dir = target / ".claude" / "rules"
        legacy_dir.mkdir(parents=True)
        for name in [
            "agent.md",
            "claude.md",
            "claude-rules.md",
            "claude-skills.md",
            "markdown.md",
            "python.md",
            "python-test.md",
            "typescript.md",
            "typescript-test.md",
        ]:
            (legacy_dir / name).write_text("# legacy\n", encoding="utf-8")

        _claudize(target, template_dir, clean=True)

        # 旧配布ファイルが全削除され、空になった rules/ と .claude/ も消える
        assert not legacy_dir.exists()
        assert not (target / ".claude").exists()

    def test_clean_legacy_preserves_other_files(self, tmp_path: Path):
        """旧レイアウトでも管理外ファイルは残し、ディレクトリも削除しない。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        legacy_dir = target / ".claude" / "rules"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "agent.md").write_text("# legacy\n", encoding="utf-8")
        (legacy_dir / "custom.md").write_text("# custom\n", encoding="utf-8")

        _claudize(target, template_dir, clean=True)

        assert not (legacy_dir / "agent.md").exists()
        assert (legacy_dir / "custom.md").exists()
        assert legacy_dir.exists()


class TestSplitFrontmatter:
    """_split_frontmatter のテスト。"""

    def test_with_frontmatter(self):
        content = "---\npaths:\n  - '**/*.py'\n---\n# Body\n"
        fm, body = _split_frontmatter(content)
        assert fm == "---\npaths:\n  - '**/*.py'\n---\n"
        assert body == "# Body\n"

    def test_without_frontmatter(self):
        content = "# No frontmatter\n\n- item\n"
        fm, body = _split_frontmatter(content)
        assert fm is None
        assert body == content

    def test_unclosed_frontmatter(self):
        """閉じ`---`がない場合はfrontmatterなしとして扱う。"""
        content = "---\npaths:\n  - '**/*.py'\n# Body\n"
        fm, body = _split_frontmatter(content)
        assert fm is None
        assert body == content


class TestFrontmatterPreservation:
    """ルール同期時のfrontmatter維持のテスト。"""

    def _setup_template(self, tmp_path: Path) -> Path:
        template_dir = tmp_path / "dotfiles" / ".chezmoi-source" / "dot_claude" / "rules" / "agent-basics"
        template_dir.mkdir(parents=True)
        (template_dir / "agent.md").write_text(TEMPLATE, encoding="utf-8")
        for name in [
            "python.md",
            "python-test.md",
            "claude.md",
            "claude-rules.md",
            "claude-skills.md",
            "markdown.md",
            "typescript.md",
            "typescript-test.md",
        ]:
            (template_dir / name).write_text(
                f"---\npaths:\n  - 'template'\n---\n# {name} ルール\n",
                encoding="utf-8",
            )
        return template_dir

    def test_preserve_custom_frontmatter(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """既存ファイルのカスタムfrontmatterが維持され、bodyのみ上書きされる。"""
        caplog.set_level(logging.INFO)
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "main.py").write_text("", encoding="utf-8")

        # カスタムfrontmatterを持つ既存ルールを配置
        rules_dir = target / ".claude" / "rules" / "agent-basics"
        rules_dir.mkdir(parents=True)
        (rules_dir / "python.md").write_text(
            "---\npaths:\n  - 'custom/path'\n---\n# 古いbody\n",
            encoding="utf-8",
        )

        _claudize(target, template_dir)

        result = (rules_dir / "python.md").read_text(encoding="utf-8")
        # frontmatterはカスタムのまま維持
        assert "custom/path" in result
        assert "template" not in result
        # bodyはテンプレートで上書き
        assert "# python.md ルール" in result
        assert "古いbody" not in result
        # frontmatter差分ありのログ
        assert any("frontmatter差分あり" in r.message for r in caplog.records)

    def test_frontmatter_only_diff(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """frontmatterのみ異なりbodyが同一の場合、上書きせず同期済みとして通知。"""
        caplog.set_level(logging.INFO)
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "main.py").write_text("", encoding="utf-8")

        rules_dir = target / ".claude" / "rules" / "agent-basics"
        rules_dir.mkdir(parents=True)
        # bodyはテンプレートと同じ、frontmatterのみカスタム
        (rules_dir / "python.md").write_text(
            "---\npaths:\n  - 'custom/path'\n---\n# python.md ルール\n",
            encoding="utf-8",
        )

        _claudize(target, template_dir)

        result = (rules_dir / "python.md").read_text(encoding="utf-8")
        # frontmatterはカスタムのまま維持
        assert "custom/path" in result
        # bodyも変わらない
        assert "# python.md ルール" in result
        # 「同期済み: ... (frontmatter差分あり)」が表示される
        assert any("同期済み" in r.message and "frontmatter差分あり" in r.message for r in caplog.records)
        # 「上書き」は表示されない
        assert not any("上書き" in r.message and "python.md" in r.message for r in caplog.records)

    def test_same_frontmatter_no_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """frontmatterが同じでbodyのみ異なる場合、差分警告なしで上書き。"""
        caplog.set_level(logging.INFO)
        template_dir = self._setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()
        (target / "main.py").write_text("", encoding="utf-8")

        rules_dir = target / ".claude" / "rules" / "agent-basics"
        rules_dir.mkdir(parents=True)
        (rules_dir / "python.md").write_text(
            "---\npaths:\n  - 'template'\n---\n# 古いbody\n",
            encoding="utf-8",
        )

        _claudize(target, template_dir)

        result = (rules_dir / "python.md").read_text(encoding="utf-8")
        assert "# python.md ルール" in result
        assert not any("frontmatter差分あり" in r.message for r in caplog.records)

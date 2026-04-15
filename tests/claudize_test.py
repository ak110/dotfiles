"""claudizeモジュールのテスト。"""

import logging
from pathlib import Path

import pytest

from pytools.claudize import _OBSOLETE_RULES, _UNCONDITIONAL_RULES, _claudize, _split_frontmatter

# テスト用テンプレート
TEMPLATE = """\
# カスタム指示

## 基本原則

- ルール1
"""

# ルール用テンプレート（markdown.mdなど配布対象のルール向け）
RULE_TEMPLATE = "# テストルール\n"


def _setup_template(tmp_path: Path) -> Path:
    """テンプレートディレクトリを作成し、agent.md と配布対象ルールを配置する。"""
    template_dir = tmp_path / "dotfiles" / ".chezmoi-source" / "dot_claude" / "rules" / "agent-basics"
    template_dir.mkdir(parents=True)
    (template_dir / "agent.md").write_text(TEMPLATE, encoding="utf-8")
    for name in _UNCONDITIONAL_RULES:
        (template_dir / name).write_text(RULE_TEMPLATE, encoding="utf-8")
    return template_dir


class TestRuleDistribution:
    """ルール配布の基本動作。"""

    def test_agent_md_and_unconditional_rules_deployed(self, tmp_path: Path):
        """agent.md と無条件ルールは配布される。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template_dir)

        rules_dir = target / ".claude" / "rules" / "agent-basics"
        assert (rules_dir / "agent.md").exists()
        for name in _UNCONDITIONAL_RULES:
            assert (rules_dir / name).exists()

    def test_obsolete_rules_are_removed(self, tmp_path: Path):
        """旧配布対象ファイルがプロジェクトに残っていれば削除される。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        # 旧ルールを事前に配置
        rules_dir = target / ".claude" / "rules" / "agent-basics"
        rules_dir.mkdir(parents=True)
        stale_names = ["python.md", "typescript.md", "claude.md"]
        for name in stale_names:
            (rules_dir / name).write_text("# 旧ルール\n", encoding="utf-8")

        _claudize(target, template_dir)

        for name in stale_names:
            assert not (rules_dir / name).exists(), f"{name} が削除されていない"

    def test_obsolete_rules_list_covers_legacy_names(self):
        """_OBSOLETE_RULES は移行前の言語別・claude系ルール名を網羅する。"""
        expected_subset = {
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
        }
        assert expected_subset.issubset(set(_OBSOLETE_RULES))

    def test_overwrite_existing_rule_body(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """既存ルールのbodyに差分があればテンプレートで上書きされる。"""
        caplog.set_level(logging.INFO)
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        rules_dir = target / ".claude" / "rules" / "agent-basics"
        rules_dir.mkdir(parents=True)
        target_name = _UNCONDITIONAL_RULES[0]
        (rules_dir / target_name).write_text("# 古いルール\n", encoding="utf-8")

        _claudize(target, template_dir)

        expected = (template_dir / target_name).read_text(encoding="utf-8")
        assert (rules_dir / target_name).read_text(encoding="utf-8") == expected
        assert any("上書き" in r.message and target_name in r.message for r in caplog.records)

    def test_idempotent(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """2回実行しても結果が同じで、差分なしの旨が表示される。"""
        caplog.set_level(logging.INFO)
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template_dir)

        caplog.clear()
        _claudize(target, template_dir)

        assert any("同期済み" in r.message for r in caplog.records)


class TestClean:
    """`--clean` での削除動作。"""

    def test_clean_removes_all_distributed_and_obsolete(self, tmp_path: Path):
        """--clean は配布対象ルールも旧ルールも全て削除する。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template_dir)

        rules_dir = target / ".claude" / "rules" / "agent-basics"
        # 旧ルールも残存している状況を作る
        (rules_dir / _OBSOLETE_RULES[0]).write_text("# 旧ルール\n", encoding="utf-8")

        _claudize(target, template_dir, clean=True)

        # 配布対象・旧ルール・agent.md のいずれも残っていない
        assert not (rules_dir / "agent.md").exists()
        for name in _UNCONDITIONAL_RULES:
            assert not (rules_dir / name).exists()
        assert not (rules_dir / _OBSOLETE_RULES[0]).exists()


class TestSplitFrontmatter:
    """frontmatter 分割ヘルパーの振る舞い。"""

    def test_split_with_frontmatter(self):
        content = '---\npaths:\n  - "**/*.py"\n---\n# 本文\n'
        fm, body = _split_frontmatter(content)
        assert fm == '---\npaths:\n  - "**/*.py"\n---\n'
        assert body == "# 本文\n"

    def test_split_without_frontmatter(self):
        content = "# 本文のみ\n"
        fm, body = _split_frontmatter(content)
        assert fm is None
        assert body == content

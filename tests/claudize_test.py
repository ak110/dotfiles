"""claudizeモジュールのテスト。"""

import logging
from pathlib import Path

import pytest

from pytools.claudize import _claudize

# テスト用テンプレート本文
AGENT_TEMPLATE = "# カスタム指示\n\n## 基本原則\n\n- ルール1\n"
STYLES_TEMPLATE = "# 記述スタイル\n"


def _setup_template(tmp_path: Path) -> Path:
    """テンプレートディレクトリを作成し、配布対象ファイルを配置する。"""
    template_dir = tmp_path / "dotfiles" / ".chezmoi-source" / "dot_claude" / "rules" / "agent-toolkit"
    template_dir.mkdir(parents=True)
    (template_dir / "agent.md").write_text(AGENT_TEMPLATE, encoding="utf-8")
    (template_dir / "styles.md").write_text(STYLES_TEMPLATE, encoding="utf-8")
    return template_dir


class TestRuleDistribution:
    """ルール配布の基本動作。"""

    def test_basic_deployment(self, tmp_path: Path):
        """配布元のファイルがそのまま配布先へコピーされる。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template_dir)

        rules_dir = target / ".claude" / "rules" / "agent-toolkit"
        assert (rules_dir / "agent.md").read_text(encoding="utf-8") == AGENT_TEMPLATE
        assert (rules_dir / "styles.md").read_text(encoding="utf-8") == STYLES_TEMPLATE

    def test_extra_files_are_removed(self, tmp_path: Path):
        """配布先に余分なファイルがあっても同期で削除される。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        rules_dir = target / ".claude" / "rules" / "agent-toolkit"
        rules_dir.mkdir(parents=True)
        stale = rules_dir / "obsolete.md"
        stale.write_text("# 旧ルール\n", encoding="utf-8")

        _claudize(target, template_dir)

        assert not stale.exists(), "配布元に存在しないファイルが削除されていない"
        assert (rules_dir / "agent.md").exists()

    def test_legacy_agent_basics_dir_removed(self, tmp_path: Path):
        """旧 agent-basics ディレクトリが存在する場合は削除される。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        legacy_dir = target / ".claude" / "rules" / "agent-basics"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "agent.md").write_text("# 旧配布\n", encoding="utf-8")

        _claudize(target, template_dir)

        assert not legacy_dir.exists(), "旧 agent-basics ディレクトリが削除されていない"
        assert (target / ".claude" / "rules" / "agent-toolkit" / "agent.md").exists()

    def test_idempotent(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """2回実行しても同じ結果になる。"""
        caplog.set_level(logging.INFO)
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template_dir)
        expected_agent = (target / ".claude" / "rules" / "agent-toolkit" / "agent.md").read_text(encoding="utf-8")

        _claudize(target, template_dir)
        actual_agent = (target / ".claude" / "rules" / "agent-toolkit" / "agent.md").read_text(encoding="utf-8")

        assert actual_agent == expected_agent

    def test_missing_template_exits(self, tmp_path: Path):
        """配布元が無ければ非ゼロ終了する。"""
        template_dir = tmp_path / "nonexistent"
        target = tmp_path / "project"
        target.mkdir()

        with pytest.raises(SystemExit):
            _claudize(target, template_dir)


class TestClean:
    """`--clean` での削除動作。"""

    def test_clean_removes_agent_toolkit_and_legacy(self, tmp_path: Path):
        """--clean で agent-toolkit と旧 agent-basics の両方が削除される。"""
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template_dir)

        # 旧ディレクトリも作って削除対象に含める
        legacy_dir = target / ".claude" / "rules" / "agent-basics"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "agent.md").write_text("# 旧配布\n", encoding="utf-8")

        _claudize(target, template_dir, clean=True)

        assert not (target / ".claude" / "rules" / "agent-toolkit").exists()
        assert not legacy_dir.exists()
        # 空になった rules/ と .claude/ も削除されている
        assert not (target / ".claude" / "rules").exists()
        assert not (target / ".claude").exists()

    def test_clean_when_absent_is_noop(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """配布先が存在しなくてもエラーにならない。"""
        caplog.set_level(logging.INFO)
        template_dir = _setup_template(tmp_path)
        target = tmp_path / "project"
        target.mkdir()

        _claudize(target, template_dir, clean=True)

        assert any("削除対象なし" in r.message for r in caplog.records)

"""codexizeモジュールのテスト。"""

import os
from pathlib import Path

from pytools.codexize import _codexize

_INSTRUCTIONS = "# プロジェクト指示\n"


def _setup_dir(tmp_path: Path) -> Path:
    """対象ディレクトリと共有スキルを作成する。"""
    target = tmp_path / "project"
    skills_dir = target / ".claude" / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / "dummy.md").write_text("# dummy\n", encoding="utf-8")
    return target


class TestCodexize:
    """`codexize`実行による状態遷移。"""

    def test_migrates_instructions_and_links_skills(self, tmp_path: Path) -> None:
        """CLAUDE.md単体をAGENTS.mdへ移し、共有スキルへリンクする。"""
        target = _setup_dir(tmp_path)
        (target / "CLAUDE.md").write_text(_INSTRUCTIONS, encoding="utf-8")

        _codexize(target)

        assert (target / "AGENTS.md").read_text(encoding="utf-8") == _INSTRUCTIONS
        assert not (target / "CLAUDE.md").exists()
        skills_link = target / ".agents" / "skills"
        assert skills_link.is_symlink()
        assert os.readlink(skills_link) == "../.claude/skills"

    def test_removes_adapter_and_is_idempotent(self, tmp_path: Path) -> None:
        """AGENTS.mdと既知アダプターはAGENTS.md単体へ収束する。"""
        target = _setup_dir(tmp_path)
        (target / "AGENTS.md").write_text(_INSTRUCTIONS, encoding="utf-8")
        (target / "CLAUDE.md").write_text("# CLAUDE.md\n\n@AGENTS.md\n", encoding="utf-8")

        _codexize(target)
        _codexize(target)

        assert (target / "AGENTS.md").read_text(encoding="utf-8") == _INSTRUCTIONS
        assert not (target / "CLAUDE.md").exists()

    def test_both_missing_still_links_skills(self, tmp_path: Path) -> None:
        """指示ファイルが無いプロジェクトでも共有スキルは設定する。"""
        target = _setup_dir(tmp_path)

        _codexize(target)

        assert (target / ".agents" / "skills").is_symlink()
        assert not (target / "AGENTS.md").exists()
        assert not (target / "CLAUDE.md").exists()


class TestClean:
    """`--clean`による状態遷移。"""

    def test_removes_only_skills_link(self, tmp_path: Path) -> None:
        """--cleanは共有スキルリンクだけを削除し、指示ファイルを保つ。"""
        target = _setup_dir(tmp_path)
        (target / "AGENTS.md").write_text(_INSTRUCTIONS, encoding="utf-8")
        (target / "CLAUDE.md").write_text("# 独立指示\n", encoding="utf-8")
        skills_link = target / ".agents" / "skills"
        skills_link.parent.mkdir()
        skills_link.symlink_to("../.claude/skills")

        _codexize(target, clean=True)

        assert not (target / ".agents").exists()
        assert (target / "AGENTS.md").read_text(encoding="utf-8") == _INSTRUCTIONS
        assert (target / "CLAUDE.md").read_text(encoding="utf-8") == "# 独立指示\n"

"""codexizeモジュールのテスト。"""

import logging
import os
from pathlib import Path

import pytest

from pytools.codexize import _codexize


def _setup_target(tmp_path: Path, *, with_skills: bool = True) -> Path:
    """対象ディレクトリを作成し、CLAUDE.mdと.claude/skillsを配置する。"""
    target = tmp_path / "project"
    target.mkdir()
    (target / "CLAUDE.md").write_text("# CLAUDE.md\n", encoding="utf-8")
    if with_skills:
        skills_dir = target / ".claude" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "dummy.md").write_text("# dummy\n", encoding="utf-8")
    return target


class TestSymlinkCreation:
    """シンボリックリンク作成の基本動作。"""

    def test_basic(self, tmp_path: Path):
        """AGENTS.mdと.agents/skillsのシンボリックリンクが作成される。"""
        target = _setup_target(tmp_path)

        _codexize(target)

        agents_link = target / "AGENTS.md"
        assert agents_link.is_symlink()
        assert os.readlink(agents_link) == "CLAUDE.md"

        skills_link = target / ".agents" / "skills"
        assert skills_link.is_symlink()
        assert os.readlink(skills_link) == "../.claude/skills"

    def test_idempotent(self, tmp_path: Path):
        """2回実行しても同じ結果になる。"""
        target = _setup_target(tmp_path)

        _codexize(target)
        _codexize(target)

        assert (target / "AGENTS.md").is_symlink()
        assert (target / ".agents" / "skills").is_symlink()

    def test_missing_claude_md_exits(self, tmp_path: Path):
        """CLAUDE.mdが存在しなければ非ゼロ終了する。"""
        target = tmp_path / "project"
        target.mkdir()

        with pytest.raises(SystemExit):
            _codexize(target)

    def test_skills_missing_skipped(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """.claude/skillsが無いと.agents/skillsの作成はスキップされる。"""
        caplog.set_level(logging.INFO)
        target = _setup_target(tmp_path, with_skills=False)

        _codexize(target)

        assert (target / "AGENTS.md").is_symlink()
        assert not (target / ".agents").exists()
        assert any("スキップ" in r.message for r in caplog.records)

    def test_existing_regular_file_errors(self, tmp_path: Path):
        """AGENTS.mdとして実ファイルが存在する場合はエラー終了する。"""
        target = _setup_target(tmp_path)
        (target / "AGENTS.md").write_text("# 既存\n", encoding="utf-8")

        with pytest.raises(SystemExit):
            _codexize(target)

    def test_existing_directory_errors(self, tmp_path: Path):
        """.agents/skillsとして実ディレクトリが存在する場合はエラー終了する。"""
        target = _setup_target(tmp_path)
        skills_dir = target / ".agents" / "skills"
        skills_dir.mkdir(parents=True)

        with pytest.raises(SystemExit):
            _codexize(target)

    def test_existing_wrong_link_errors(self, tmp_path: Path):
        """別リンク先のシンボリックリンクが存在する場合はエラー終了する。"""
        target = _setup_target(tmp_path)
        (target / "AGENTS.md").symlink_to("wrong-target")

        with pytest.raises(SystemExit):
            _codexize(target)


class TestClean:
    """`--clean`での削除動作。"""

    def test_removes_links_and_empty_parent(self, tmp_path: Path):
        """期待のリンクを削除し、空になった.agents/も除去する。"""
        target = _setup_target(tmp_path)
        _codexize(target)

        _codexize(target, clean=True)

        assert not (target / "AGENTS.md").exists()
        assert not (target / ".agents").exists()

    def test_when_absent_is_noop(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """対象が無くてもエラーにならず、削除対象なしを通知する。"""
        caplog.set_level(logging.INFO)
        target = tmp_path / "project"
        target.mkdir()

        _codexize(target, clean=True)

        assert any("削除対象なし" in r.message for r in caplog.records)

    def test_existing_regular_file_errors(self, tmp_path: Path):
        """AGENTS.mdとして実ファイルが存在する場合はエラー終了する。"""
        target = tmp_path / "project"
        target.mkdir()
        (target / "AGENTS.md").write_text("# 既存\n", encoding="utf-8")

        with pytest.raises(SystemExit):
            _codexize(target, clean=True)

    def test_wrong_link_errors(self, tmp_path: Path):
        """別リンク先のシンボリックリンクが存在する場合はエラー終了する。"""
        target = tmp_path / "project"
        target.mkdir()
        (target / "AGENTS.md").symlink_to("wrong-target")

        with pytest.raises(SystemExit):
            _codexize(target, clean=True)

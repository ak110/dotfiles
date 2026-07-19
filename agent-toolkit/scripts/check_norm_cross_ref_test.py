"""check_norm_cross_ref.pyのユニットテスト。"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

# pylint: disable=wrong-import-position
import check_norm_cross_ref  # noqa: E402


def test_same_dir_bare_filename_resolves_with_existing_section(tmp_path: pathlib.Path):
    """同一ディレクトリ内の裸ファイル名参照が解決でき、参照先H2見出しが実在する場合は違反0件。"""
    source = tmp_path / "source.md"
    source.write_text("`target.md`「対象節」節を参照する。\n", encoding="utf-8")
    (tmp_path / "target.md").write_text("## 対象節\n\n本文。\n", encoding="utf-8")
    assert not check_norm_cross_ref._check_file(tmp_path, source)  # noqa: SLF001  # pylint: disable=protected-access


def test_missing_target_file_is_violation(tmp_path: pathlib.Path):
    """参照先ファイル自体が存在しない場合に「参照先ファイル不在」を検出する。"""
    source = tmp_path / "source.md"
    source.write_text("`missing.md`「対象節」節を参照する。\n", encoding="utf-8")
    violations = check_norm_cross_ref._check_file(tmp_path, source)  # noqa: SLF001  # pylint: disable=protected-access
    assert len(violations) == 1
    assert "参照先ファイル不在" in violations[0]


def test_missing_section_heading_is_violation(tmp_path: pathlib.Path):
    """参照先ファイルは存在するが該当見出しが存在しない場合に「節名不在」を検出する。"""
    source = tmp_path / "source.md"
    source.write_text("`target.md`「存在しない節」節を参照する。\n", encoding="utf-8")
    (tmp_path / "target.md").write_text("## 別の節\n\n本文。\n", encoding="utf-8")
    violations = check_norm_cross_ref._check_file(tmp_path, source)  # noqa: SLF001  # pylint: disable=protected-access
    assert len(violations) == 1
    assert "節名不在" in violations[0]


def test_ambiguous_bare_filename_is_violation(tmp_path: pathlib.Path):
    """裸ファイル名が複数箇所に同名で存在し一意に解決できない場合に「解決不能（曖昧）」を検出する。"""
    rules_dir = tmp_path / "agent-toolkit" / "rules"
    skills_dir = tmp_path / "agent-toolkit" / "skills"
    rules_dir.mkdir(parents=True)
    skills_dir.mkdir(parents=True)
    (rules_dir / "dup.md").write_text("## 節\n", encoding="utf-8")
    (skills_dir / "dup.md").write_text("## 節\n", encoding="utf-8")
    source = tmp_path / "source.md"
    source.write_text("`dup.md`「節」節を参照する。\n", encoding="utf-8")
    violations = check_norm_cross_ref._check_file(tmp_path, source)  # noqa: SLF001  # pylint: disable=protected-access
    assert len(violations) == 1
    assert "解決不能（曖昧）" in violations[0]


def test_skill_ref_resolves_to_skill_md(tmp_path: pathlib.Path):
    """`agent-toolkit:<skill-name>`形式の参照が`agent-toolkit/skills/<skill-name>/SKILL.md`へ解決される。"""
    skill_dir = tmp_path / "agent-toolkit" / "skills" / "example-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("## 対象節\n", encoding="utf-8")
    source = tmp_path / "source.md"
    source.write_text("agent-toolkit:example-skill「対象節」節を参照する。\n", encoding="utf-8")
    assert not check_norm_cross_ref._check_file(tmp_path, source)  # noqa: SLF001  # pylint: disable=protected-access


def test_slash_path_resolves_as_repo_root_relative(tmp_path: pathlib.Path):
    """スラッシュを含むリポジトリルート相対パスの参照が解決される。"""
    target_dir = tmp_path / "agent-toolkit" / "rules"
    target_dir.mkdir(parents=True)
    (target_dir / "01-agent.md").write_text("## 品質最優先\n", encoding="utf-8")
    source = tmp_path / "source.md"
    source.write_text("`agent-toolkit/rules/01-agent.md`「品質最優先」節を参照する。\n", encoding="utf-8")
    assert not check_norm_cross_ref._check_file(tmp_path, source)  # noqa: SLF001  # pylint: disable=protected-access


def test_main_returns_zero_when_no_violations(tmp_path: pathlib.Path):
    """違反が無い対象ファイル群に対して`main`が終了コード0を返す。"""
    source = tmp_path / "source.md"
    source.write_text("no reference here.\n", encoding="utf-8")
    assert check_norm_cross_ref.main([str(source)]) == 0


def test_main_returns_one_when_file_read_fails(tmp_path: pathlib.Path):
    """存在しない対象ファイルを渡した場合に`main`が終了コード1を返す。"""
    missing = tmp_path / "missing_source.md"
    assert check_norm_cross_ref.main([str(missing)]) == 1

"""agent-toolkit/scripts/check_total_size.py のテスト。"""

import pathlib

import check_total_size
import pytest


def _make_agent_toolkit_tree(tmp_path: pathlib.Path) -> pathlib.Path:
    """`rules`・`skills`・`agents`・`references`の4ディレクトリを持つ疑似agent-toolkitツリーを生成する。"""
    root = tmp_path / "agent-toolkit"
    for name in ("rules", "skills", "agents", "references"):
        (root / name).mkdir(parents=True)
    return root


class TestCountLines:
    """`_count_lines`の行数カウント（`wc -l`相当）。"""

    def test_empty_file_returns_zero(self, tmp_path: pathlib.Path):
        """空ファイルは0行を返す。"""
        path = tmp_path / "empty.md"
        path.write_text("", encoding="utf-8")
        assert check_total_size._count_lines(path) == 0  # noqa: SLF001  # pylint: disable=protected-access

    def test_trailing_newline_counts_each_line_once(self, tmp_path: pathlib.Path):
        """末尾に改行のあるファイルは改行数どおりの行数を返す。"""
        path = tmp_path / "a.md"
        path.write_text("line1\nline2\nline3\n", encoding="utf-8")
        assert check_total_size._count_lines(path) == 3  # noqa: SLF001  # pylint: disable=protected-access

    def test_no_trailing_newline_counts_final_line(self, tmp_path: pathlib.Path):
        """末尾に改行のない最終行も1行として数える（`wc -l`の挙動と異なりgit差分行数と一致させる）。"""
        path = tmp_path / "b.md"
        path.write_text("line1\nline2", encoding="utf-8")
        assert check_total_size._count_lines(path) == 2  # noqa: SLF001  # pylint: disable=protected-access


class TestIterTargetFiles:
    """`_iter_target_files`の対象ファイル収集と除外パターン適用。"""

    def test_collects_md_and_py_from_all_target_dirs(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        """`rules`・`skills`・`agents`・`references`配下の`.md`・`.py`を収集する。"""
        root = _make_agent_toolkit_tree(tmp_path)
        (root / "rules" / "a.md").write_text("x\n", encoding="utf-8")
        (root / "skills" / "b.py").write_text("x\n", encoding="utf-8")
        (root / "agents" / "c.md").write_text("x\n", encoding="utf-8")
        (root / "references" / "d.py").write_text("x\n", encoding="utf-8")
        monkeypatch.setattr(check_total_size, "_AGENT_TOOLKIT_ROOT", root)
        files = check_total_size._iter_target_files()  # noqa: SLF001  # pylint: disable=protected-access
        assert {p.name for p in files} == {"a.md", "b.py", "c.md", "d.py"}

    def test_ignores_other_extensions(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        """`.md`・`.py`以外の拡張子は対象外とする。"""
        root = _make_agent_toolkit_tree(tmp_path)
        (root / "rules" / "a.md").write_text("x\n", encoding="utf-8")
        (root / "rules" / "readme.txt").write_text("x\n", encoding="utf-8")
        monkeypatch.setattr(check_total_size, "_AGENT_TOOLKIT_ROOT", root)
        files = check_total_size._iter_target_files()  # noqa: SLF001  # pylint: disable=protected-access
        assert {p.name for p in files} == {"a.md"}

    def test_excludes_references_scripts_test_py(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        """`*/references/*/scripts/*_test.py`パターンに一致するテストファイルは除外する。"""
        root = _make_agent_toolkit_tree(tmp_path)
        nested = root / "skills" / "foo" / "references" / "bar" / "scripts"
        nested.mkdir(parents=True)
        (nested / "helper.py").write_text("x\n", encoding="utf-8")
        (nested / "helper_test.py").write_text("x\n", encoding="utf-8")
        monkeypatch.setattr(check_total_size, "_AGENT_TOOLKIT_ROOT", root)
        files = check_total_size._iter_target_files()  # noqa: SLF001  # pylint: disable=protected-access
        assert {p.name for p in files} == {"helper.py"}

    def test_missing_target_dir_is_skipped(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        """対象ディレクトリが存在しない場合はエラーにならず単に対象外とする。"""
        root = tmp_path / "agent-toolkit"
        (root / "rules").mkdir(parents=True)
        (root / "rules" / "a.md").write_text("x\n", encoding="utf-8")
        monkeypatch.setattr(check_total_size, "_AGENT_TOOLKIT_ROOT", root)
        files = check_total_size._iter_target_files()  # noqa: SLF001  # pylint: disable=protected-access
        assert {p.name for p in files} == {"a.md"}


class TestMain:
    """`main`の上限判定とexit code。"""

    def test_under_limit_returns_zero(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        """合計行数が上限以下ならexit 0で標準エラー出力なし。"""
        root = _make_agent_toolkit_tree(tmp_path)
        (root / "rules" / "a.md").write_text("line1\nline2\n", encoding="utf-8")
        monkeypatch.setattr(check_total_size, "_AGENT_TOOLKIT_ROOT", root)
        monkeypatch.setattr(check_total_size, "LIMIT", 10)
        assert check_total_size.main() == 0
        assert capsys.readouterr().err == ""

    def test_over_limit_returns_one_and_reports(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ):
        """合計行数が上限を超える場合はexit 1で実測値・上限値を標準エラーへ出力する。"""
        root = _make_agent_toolkit_tree(tmp_path)
        (root / "rules" / "a.md").write_text("line1\nline2\nline3\n", encoding="utf-8")
        monkeypatch.setattr(check_total_size, "_AGENT_TOOLKIT_ROOT", root)
        monkeypatch.setattr(check_total_size, "LIMIT", 2)
        assert check_total_size.main() == 1
        err = capsys.readouterr().err
        assert "3行" in err
        assert "2行" in err or "（2行）" in err

    def test_exactly_at_limit_returns_zero(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
        """合計行数が上限とちょうど一致する場合は超過扱いとせずexit 0。"""
        root = _make_agent_toolkit_tree(tmp_path)
        (root / "rules" / "a.md").write_text("line1\nline2\n", encoding="utf-8")
        monkeypatch.setattr(check_total_size, "_AGENT_TOOLKIT_ROOT", root)
        monkeypatch.setattr(check_total_size, "LIMIT", 2)
        assert check_total_size.main() == 0

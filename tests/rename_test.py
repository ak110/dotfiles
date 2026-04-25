"""renameモジュールのテスト。"""

import pathlib
import subprocess
import sys


class TestRename:
    """py-renameコマンドのテスト。"""

    def _run(self, *args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "pytools.rename", *args],
            capture_output=True,
            text=True,
            cwd=cwd,
            check=True,
        )

    def test_stem_rename(self, tmp_path):
        (tmp_path / "foo_bar.txt").touch()
        (tmp_path / "foo_baz.txt").touch()
        self._run("foo", "qux", str(tmp_path / "foo_bar.txt"), str(tmp_path / "foo_baz.txt"), cwd=tmp_path)
        assert (tmp_path / "qux_bar.txt").exists()
        assert (tmp_path / "qux_baz.txt").exists()

    def test_name_rename(self, tmp_path):
        (tmp_path / "hello.txt").touch()
        self._run("--name", r"\.txt$", ".md", str(tmp_path / "hello.txt"), cwd=tmp_path)
        assert (tmp_path / "hello.md").exists()
        assert not (tmp_path / "hello.txt").exists()

    def test_dry_run(self, tmp_path):
        (tmp_path / "original.txt").touch()
        self._run("--dry-run", "original", "renamed", str(tmp_path / "original.txt"), cwd=tmp_path)
        # dry-runではファイルがリネームされない
        assert (tmp_path / "original.txt").exists()
        assert not (tmp_path / "renamed.txt").exists()

    def test_ignore_case(self, tmp_path):
        (tmp_path / "FOO.txt").touch()
        self._run("--ignore-case", "foo", "bar", str(tmp_path / "FOO.txt"), cwd=tmp_path)
        assert (tmp_path / "bar.txt").exists()


class TestPatternFile:
    """rgrename 互換のパターンファイル機能のテスト。"""

    def test_load_pattern_file_basic(self, tmp_path: pathlib.Path) -> None:
        from pytools import rename

        pf = tmp_path / "rules.txt"
        pf.write_text("foo\tbar\n# comment\nF\t^prefix_\t\nD\t_suffix$\t\n", encoding="utf-8")
        rules = rename.load_pattern_file(pf)
        assert len(rules) == 3
        assert rules[0].target == "both"
        assert rules[1].target == "file"
        assert rules[2].target == "dir"

    def test_rename_tree_with_pattern_file(self, tmp_path: pathlib.Path) -> None:
        from pytools import rename

        (tmp_path / "img_01.txt").touch()
        (tmp_path / "img_02.txt").touch()
        pf = tmp_path / "rules.txt"
        pf.write_text("^img_\t\n", encoding="utf-8")
        rules = rename.load_pattern_file(pf)
        rename.rename_tree(tmp_path, rules)
        assert (tmp_path / "01.txt").exists()
        assert (tmp_path / "02.txt").exists()

    def test_rename_tree_files_only(self, tmp_path: pathlib.Path) -> None:
        from pytools import rename

        sub = tmp_path / "sub_dir"
        sub.mkdir()
        (tmp_path / "sub_file.txt").touch()
        pf = tmp_path / "rules.txt"
        pf.write_text("^sub_\t\n", encoding="utf-8")
        rules = rename.load_pattern_file(pf)
        rename.rename_tree(tmp_path, rules, files_only=True)
        assert (tmp_path / "file.txt").exists()
        assert sub.exists()  # ディレクトリは改名されない

    def test_dollar_capture_reference(self, tmp_path: pathlib.Path) -> None:
        """パターンファイルの置換側で `$N` がキャプチャ参照として展開される。"""
        from pytools import rename

        (tmp_path / "img_001.txt").touch()
        pf = tmp_path / "rules.txt"
        pf.write_text(r"^img_(\d+)" + "\t" + r"page_$1" + "\n", encoding="utf-8")
        rules = rename.load_pattern_file(pf)
        rename.rename_tree(tmp_path, rules)
        assert (tmp_path / "page_001.txt").exists()

    def test_dollar_dollar_literal_and_double_digit(self, tmp_path: pathlib.Path) -> None:
        """`$$` がリテラル ``$``、`$10` などの 2 桁参照も解釈される。"""
        from pytools import rename

        # 10 個のキャプチャグループを使い `$10` を 10 番目の参照として検証する
        pattern = "^" + "".join(r"(\d)" for _ in range(10)) + r"\.txt$"
        replacement = r"$$_$10$1.txt"  # $$ → $, $10 → 10 番目, $1 → 1 番目
        (tmp_path / "0123456789.txt").touch()
        pf = tmp_path / "rules.txt"
        pf.write_text(pattern + "\t" + replacement + "\n", encoding="utf-8")
        rules = rename.load_pattern_file(pf)
        rename.rename_tree(tmp_path, rules)
        assert (tmp_path / "$_90.txt").exists()

    def test_backslash_in_replacement_is_literal(self, tmp_path: pathlib.Path) -> None:
        """rgrename 互換: 置換側の ``\\`` はリテラルとして残り、Python 流の ``\\1`` は機能しない。"""
        from pytools import rename

        (tmp_path / "abc.txt").touch()
        pf = tmp_path / "rules.txt"
        # Python 流 \1 を書いてもキャプチャ参照にならず、リテラル ``\1`` のまま
        pf.write_text(r"^(a)" + "\t" + r"\1z" + "\n", encoding="utf-8")
        rules = rename.load_pattern_file(pf)
        rename.rename_tree(tmp_path, rules)
        assert (tmp_path / r"\1zbc.txt").exists()

    def test_rename_tree_dirs_only(self, tmp_path: pathlib.Path) -> None:
        from pytools import rename

        sub = tmp_path / "sub_dir"
        sub.mkdir()
        (tmp_path / "sub_file.txt").touch()
        pf = tmp_path / "rules.txt"
        pf.write_text("^sub_\t\n", encoding="utf-8")
        rules = rename.load_pattern_file(pf)
        rename.rename_tree(tmp_path, rules, dirs_only=True)
        assert (tmp_path / "dir").exists()
        assert (tmp_path / "sub_file.txt").exists()  # ファイルは改名されない

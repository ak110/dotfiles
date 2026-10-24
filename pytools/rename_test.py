"""renameモジュールのテスト。"""

import pathlib
import subprocess
import sys
import typing

import pytest

from pytools import rename


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

    def test_fullpath_rename(self, tmp_path: pathlib.Path) -> None:
        source_dir = tmp_path / "fullpath_source"
        source_dir.mkdir()
        (tmp_path / "fullpath_destination").mkdir()
        source = source_dir / "item.txt"
        source.touch()

        self._run("--fullpath", "fullpath_source", "fullpath_destination", str(source), cwd=tmp_path)

        assert (tmp_path / "fullpath_destination" / "item.txt").exists()
        assert not source.exists()

    def test_dry_run(self, tmp_path):
        (tmp_path / "original.txt").touch()
        self._run("--dry-run", "original", "renamed", str(tmp_path / "original.txt"), cwd=tmp_path)
        assert (tmp_path / "original.txt").exists()
        assert not (tmp_path / "renamed.txt").exists()

    def test_ignore_case(self, tmp_path):
        (tmp_path / "FOO.txt").touch()
        self._run("--ignore-case", "foo", "bar", str(tmp_path / "FOO.txt"), cwd=tmp_path)
        assert (tmp_path / "bar.txt").exists()


class TestPatternFile:
    """rgrename 互換のパターンファイル機能のテスト。"""

    def test_rename_tree_with_pattern_file(self, tmp_path: pathlib.Path) -> None:

        (tmp_path / "img_01.txt").touch()
        (tmp_path / "img_02.txt").touch()
        pf = tmp_path / "rules.txt"
        # `#`で始まる行は規則として読まない。
        pf.write_text("# 先頭のコメント行\n^img_\t\n", encoding="utf-8")
        rules = rename.load_pattern_file(pf)
        assert len(rules) == 1
        rename.rename_tree(tmp_path, rules)
        assert (tmp_path / "01.txt").exists()
        assert (tmp_path / "02.txt").exists()

    def test_rename_tree_files_only(self, tmp_path: pathlib.Path) -> None:

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

        (tmp_path / "img_001.txt").touch()
        pf = tmp_path / "rules.txt"
        pf.write_text(r"^img_(\d+)" + "\t" + r"page_$1" + "\n", encoding="utf-8")
        rules = rename.load_pattern_file(pf)
        rename.rename_tree(tmp_path, rules)
        assert (tmp_path / "page_001.txt").exists()

    def test_dollar_dollar_literal_and_double_digit(self, tmp_path: pathlib.Path) -> None:
        """`$$` がリテラル ``$``、`$10` などの 2 桁参照も解釈される。"""

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

        (tmp_path / "abc.txt").touch()
        pf = tmp_path / "rules.txt"
        # Python 流 \1 を書いてもキャプチャ参照にならず、リテラル ``\1`` のまま
        pf.write_text(r"^(a)" + "\t" + r"\1z" + "\n", encoding="utf-8")
        rules = rename.load_pattern_file(pf)
        rename.rename_tree(tmp_path, rules)
        assert (tmp_path / r"\1zbc.txt").exists()

    def test_rename_tree_dirs_only(self, tmp_path: pathlib.Path) -> None:

        sub = tmp_path / "sub_dir"
        sub.mkdir()
        (tmp_path / "sub_file.txt").touch()
        pf = tmp_path / "rules.txt"
        pf.write_text("^sub_\t\n", encoding="utf-8")
        rules = rename.load_pattern_file(pf)
        rename.rename_tree(tmp_path, rules, dirs_only=True)
        assert (tmp_path / "dir").exists()
        assert (tmp_path / "sub_file.txt").exists()  # ファイルは改名されない


class TestExitCode:
    """続行した改名の失敗を終了コードへ反映するテスト。"""

    @staticmethod
    def _run_main(monkeypatch: pytest.MonkeyPatch, *args: str) -> int | str | None:
        monkeypatch.setattr(sys, "argv", ["py-rename", *args])
        with pytest.raises(SystemExit) as exc_info:
            rename.main()
        return exc_info.value.code

    @staticmethod
    def _fail_rename_of(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
        real_rename = pathlib.Path.rename

        def flaky_rename(self: pathlib.Path, target: typing.Any, *args: typing.Any, **kwargs: typing.Any) -> pathlib.Path:
            if self.name == name:
                raise PermissionError("denied")
            return real_rename(self, target, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "rename", flaky_rename)

    def test_args_form_exit_code_one_on_failure(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """引数形: 1件の改名が失敗しても残りを改名し、終了コード1で終える。"""
        (tmp_path / "foo_a.txt").touch()
        (tmp_path / "foo_b.txt").touch()
        self._fail_rename_of(monkeypatch, "foo_a.txt")

        code = self._run_main(monkeypatch, "foo", "bar", str(tmp_path / "foo_a.txt"), str(tmp_path / "foo_b.txt"))

        assert code == 1
        assert (tmp_path / "foo_a.txt").exists()
        assert (tmp_path / "bar_b.txt").exists()

    def test_args_form_exit_code_zero_without_failure(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """引数形: 失敗が無ければ終了コード0で終える。"""
        (tmp_path / "foo_a.txt").touch()

        assert self._run_main(monkeypatch, "foo", "bar", str(tmp_path / "foo_a.txt")) == 0
        assert (tmp_path / "bar_a.txt").exists()

    def test_pattern_file_form_exit_code_one_on_failure(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """パターンファイル形: 1件の改名が失敗しても残りを改名し、終了コード1で終える。"""
        target = tmp_path / "target"
        target.mkdir()
        (target / "foo_a.txt").touch()
        (target / "foo_b.txt").touch()
        pf = tmp_path / "rules.txt"
        pf.write_text("^foo_\tbar_\n", encoding="utf-8")
        self._fail_rename_of(monkeypatch, "foo_a.txt")

        assert self._run_main(monkeypatch, "-f", str(pf), "-t", str(target)) == 1
        assert (target / "foo_a.txt").exists()
        assert (target / "bar_b.txt").exists()

    def test_pattern_file_form_exit_code_zero_without_failure(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """パターンファイル形: 失敗が無ければ終了コード0で終える。"""
        target = tmp_path / "target"
        target.mkdir()
        (target / "foo_a.txt").touch()
        pf = tmp_path / "rules.txt"
        pf.write_text("^foo_\tbar_\n", encoding="utf-8")

        assert self._run_main(monkeypatch, "-f", str(pf), "-t", str(target)) == 0
        assert (target / "bar_a.txt").exists()

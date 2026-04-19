"""pytools.claude_plans_viewer のテスト。"""

# 本モジュールはプライベート関数（`_list_files`・`_resolve_under_root`・`_markdown_to_html`・
# `_resolve_css_path`・`_read_css`）を単体でテストするため、protected-accessを一括で許可する。
# pylint: disable=protected-access

import os
import re
from pathlib import Path

import pytest

from pytools import claude_plans_viewer


class TestListFiles:
    """_list_files のテスト。"""

    def test_sorts_by_mtime_desc(self, tmp_path: Path):
        """mtime降順で返ること。"""
        old_path = tmp_path / "old.md"
        old_path.write_text("old", encoding="utf-8")
        os.utime(old_path, (1_000.0, 1_000.0))

        new_path = tmp_path / "new.md"
        new_path.write_text("new", encoding="utf-8")
        os.utime(new_path, (2_000.0, 2_000.0))

        entries = claude_plans_viewer._list_files(tmp_path)

        assert [e.path for e in entries] == ["new.md", "old.md"]
        # mtimeは`yyyy/MM/dd HH:mm`書式で整形される。
        pattern = re.compile(r"^\d{4}/\d{2}/\d{2} \d{2}:\d{2}$")
        for entry in entries:
            assert pattern.match(entry.mtime), entry.mtime
        # `_FileEntry`はサイズを保持しない。
        assert not hasattr(entries[0], "size")

    def test_includes_only_md(self, tmp_path: Path):
        """.md以外は含まず、サブディレクトリは再帰的に拾うこと。"""
        (tmp_path / "a.md").write_text("x", encoding="utf-8")
        (tmp_path / "b.txt").write_text("x", encoding="utf-8")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "c.md").write_text("x", encoding="utf-8")

        entries = claude_plans_viewer._list_files(tmp_path)

        assert sorted(e.path for e in entries) == ["a.md", "sub/c.md"]


class TestResolveUnderRoot:
    """_resolve_under_root のテスト。"""

    def test_valid_md_path(self, tmp_path: Path):
        """root配下の.mdを正常に解決する。"""
        target_path = tmp_path / "a.md"
        target_path.write_text("x", encoding="utf-8")

        result = claude_plans_viewer._resolve_under_root(tmp_path, "a.md")

        assert result == target_path.resolve()

    @pytest.mark.parametrize("rel", ["../outside.md", "sub/../../outside.md"])
    def test_rejects_traversal(self, tmp_path: Path, rel: str):
        """root外へ出るパスはNoneを返す。"""
        # root外の実体を作っても相対参照で抜けられないことを確認する。
        outside = tmp_path.parent / "outside.md"
        outside.write_text("x", encoding="utf-8")
        try:
            assert claude_plans_viewer._resolve_under_root(tmp_path, rel) is None
        finally:
            outside.unlink()

    def test_rejects_non_md(self, tmp_path: Path):
        """拡張子が.md以外のファイルはNoneを返す。"""
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")

        assert claude_plans_viewer._resolve_under_root(tmp_path, "a.txt") is None

    def test_rejects_missing(self, tmp_path: Path):
        """存在しないファイルはNoneを返す。"""
        assert claude_plans_viewer._resolve_under_root(tmp_path, "missing.md") is None


class TestMarkdownToHtml:
    """_markdown_to_html のテスト。"""

    def test_renders_basic_markdown(self):
        """見出し・コードブロック・表が反映される。"""
        src = "# title\n\n```\ncode\n```\n\n| a | b |\n| - | - |\n| 1 | 2 |\n"

        html = claude_plans_viewer._markdown_to_html(src)

        assert "<h1>title</h1>" in html
        assert "<pre><code>code\n</code></pre>" in html
        assert "<table>" in html
        assert "<th>a</th>" in html

    def test_escapes_raw_html(self):
        """raw HTMLタグは出力にそのまま現れず、エスケープされる。"""
        src = "# t\n\n<script>alert(1)</script>\n\n<img src=x onerror=y>\n"

        html = claude_plans_viewer._markdown_to_html(src)

        # 生タグが残らないこと（属性付きを含む広めの判定）
        assert "<script" not in html.lower()
        assert "<img" not in html.lower()
        # エスケープされた形で残ること
        assert "&lt;script&gt;" in html


class TestResolveCssPath:
    """_resolve_css_path のテスト。

    editable install前提でリポジトリ配下の`share/vscode/markdown.css`を返すことを確認する。
    本テストはdotfilesリポジトリ内で実行される前提で、配布CSSの所在を固定する。
    """

    def test_returns_repo_css(self):
        path = claude_plans_viewer._resolve_css_path()

        assert path is not None
        assert path.name == "markdown.css"
        assert path.is_file()
        assert path.parent.name == "vscode"
        assert path.parent.parent.name == "share"

    def test_read_css_nonempty(self):
        """_read_cssがCSS本文を返す（空でない）。"""
        css = claude_plans_viewer._read_css()

        assert css.strip()

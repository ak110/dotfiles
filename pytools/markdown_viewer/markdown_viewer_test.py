"""markdown_viewer統合テスト。"""

import pathlib
import tempfile
from urllib.parse import urlparse
from urllib.request import url2pathname

import pytest

from pytools import markdown_viewer
from pytools.markdown_viewer import _cli, _render


@pytest.mark.parametrize(
    ("filename", "content", "exit_code", "expected_substrs", "unexpected_substr"),
    [
        # 同値分割: 通常の.md拡張子
        ("sample.md", "# 見出し\n\n本文段落\n", 0, ("<h1>見出し</h1>",), None),
        # 同値分割: 拡張子なし（README等）
        ("README", "本文のみ", 0, ("<p>本文のみ</p>",), None),
        # 同値分割: 日本語ファイル名
        ("日本語名.md", "# 日本語タイトル", 0, ("<h1>日本語タイトル</h1>",), None),
        # 境界値: 空ファイル
        ("empty.md", "", 0, (), None),
        # 境界値: 1文字のMarkdown
        ("tiny.md", "x", 0, ("<p>x</p>",), None),
        # GFM拡張: 二重チルダを取り消し線へ変換する
        ("strikethrough.md", "通常 ~~取消~~\n", 0, ("<s>取消</s>",), None),
        # 表示契約: 裸URL・www・メールアドレスをリンクへ変換しない
        (
            "autolink.md",
            "https://example.com www.example.com user@example.com\n",
            0,
            ("https://example.com www.example.com user@example.com",),
            "<a href=",
        ),
        # GFM拡張: 明示リンクと自動リンク記法は維持する
        (
            "explicit-links.md",
            "[表示](https://example.com) <https://example.net>\n",
            0,
            (
                '<a href="https://example.com">表示</a>',
                '<a href="https://example.net">https://example.net</a>',
            ),
            None,
        ),
        # GFM拡張: 表を維持する
        (
            "table.md",
            "| 列 |\n| --- |\n| 値 |\n",
            0,
            ("<table>",),
            None,
        ),
        # セキュリティ境界: Raw HTMLをエスケープし、生のscriptタグを生成しない
        (
            "raw-html.md",
            "<script>alert(1)</script>\n",
            0,
            ("&lt;script&gt;alert(1)&lt;/script&gt;",),
            "<script",
        ),
        # 同値分割: 非存在ファイル
        ("__missing__.md", None, 1, (), None),
    ],
)
def test_markdown_viewer_integration(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    filename: str,
    content: str | None,
    exit_code: int,
    expected_substrs: tuple[str, ...],
    unexpected_substr: str | None,
) -> None:
    """公開インターフェース`main`経由でMarkdownレンダリング・HTML生成・ブラウザ起動を検査する。

    `webbrowser.open`は無効化して実ブラウザの立ち上げを抑制する。
    `tempfile.tempdir`を`tmp_path`配下へ差し替えて、生成された一時HTMLがテスト終了時に
    `tmp_path`ごと回収されるようにする。
    """
    # 一時HTMLの生成先をtmp_path配下へ閉じ込めてテスト間の残骸を避ける
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))

    source = tmp_path / filename
    if content is not None:
        source.write_text(content, encoding="utf-8")

    opened: list[str] = []

    def _fake_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(_cli.webbrowser, "open", _fake_open)

    ret = markdown_viewer.main([str(source)])
    assert ret == exit_code

    if exit_code == 0:
        # ブラウザに渡されたfile URIからパスを復元して出力ファイルの内容を検証する
        assert len(opened) == 1
        output = pathlib.Path(url2pathname(urlparse(opened[0]).path))
        assert output.is_file()
        document = output.read_text(encoding="utf-8")
        assert "<base " in document
        assert "<style>" in document
        # 入力ファイルの親ディレクトリが`<base href>`に埋め込まれていることを確認
        assert source.resolve().parent.as_uri() in document
        for expected_substr in expected_substrs:
            assert expected_substr in document
        if unexpected_substr:
            assert unexpected_substr.lower() not in document.lower()
    else:
        # 非存在ファイル指定時はstderrへエラーを出力してexit 1する
        assert not opened
        captured = capsys.readouterr()
        assert "見つかりません" in captured.err


def test_resolve_css_path() -> None:
    """resolve_css_pathがリポジトリ内の`share/vscode/markdown.css`を返すことを検査する。"""
    css_path = _render.resolve_css_path()
    assert css_path is not None
    assert css_path.name == "markdown.css"
    assert css_path.is_file()

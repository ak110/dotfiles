"""markdown_viewer統合テスト。"""

# pylint: disable=protected-access

import pathlib
import tempfile

import pytest

from pytools import markdown_viewer
from pytools.markdown_viewer import _cli, _render


@pytest.mark.parametrize(
    ("filename", "content", "exit_code", "expected_substr"),
    [
        # 同値分割: 通常の.md拡張子
        ("sample.md", "# 見出し\n\n本文段落\n", 0, "<h1>見出し</h1>"),
        # 同値分割: 拡張子なし（README等）
        ("README", "本文のみ", 0, "<p>本文のみ</p>"),
        # 同値分割: 日本語ファイル名
        ("日本語名.md", "# 日本語タイトル", 0, "<h1>日本語タイトル</h1>"),
        # 境界値: 空ファイル
        ("empty.md", "", 0, ""),
        # 境界値: 1文字のMarkdown
        ("tiny.md", "x", 0, "<p>x</p>"),
        # 同値分割: 非存在ファイル
        ("__missing__.md", None, 1, None),
    ],
)
def test_markdown_viewer_integration(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    filename: str,
    content: str | None,
    exit_code: int,
    expected_substr: str | None,
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
        # 出力先は入力絶対パスのSHA-256ハッシュで一意に定まるためテスト側から再計算できる
        output = _cli._output_path(source.resolve())
        assert opened == [output.as_uri()]
        assert output.is_file()
        document = output.read_text(encoding="utf-8")
        assert "<base " in document
        assert "<style>" in document
        # 入力ファイルの親ディレクトリが`<base href>`に埋め込まれていることを確認
        assert source.resolve().parent.as_uri() in document
        if expected_substr:
            assert expected_substr in document
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

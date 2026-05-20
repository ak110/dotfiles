"""MarkdownレンダリングとHTMLドキュメント組み立て用の純粋関数群。"""

import html
import pathlib

import markdown_it


def make_renderer() -> markdown_it.MarkdownIt:
    """Raw HTMLを無効化したMarkdownレンダラを返す。"""
    # CommonMarkプリセットは`html`オプション既定値が`True`でraw HTMLを通すため、
    # 明示的に`False`へ上書きしてXSS経路を塞ぐ。表拡張は別途`enable("table")`で有効化する。
    return markdown_it.MarkdownIt("commonmark", {"html": False}).enable("table")


def render_body(text: str, renderer: markdown_it.MarkdownIt | None = None) -> str:
    """Markdown文字列をHTML本文へ変換する。"""
    md = renderer if renderer is not None else make_renderer()
    return md.render(text)


def resolve_css_path() -> pathlib.Path | None:
    """リポジトリ内の`share/vscode/markdown.css`のパスを返す。見つからなければNone。

    `Path.home()`起点だとCI環境や`$HOME`と`~/dotfiles`が一致しない環境で破綻するため、
    本ファイルの位置を起点に解決する（リポジトリルートは2階層上）。editable installを前提とする。
    """
    candidate = pathlib.Path(__file__).resolve().parents[2] / "share" / "vscode" / "markdown.css"
    if candidate.is_file():
        return candidate
    return None


def build_html_document(*, body_html: str, title: str, base_uri: str, css_text: str) -> str:
    """HTML文書全体を組み立てる。

    Args:
        body_html: Markdownから変換済みのHTML本文。
        title: `<title>`へ埋め込むテキスト。
        base_uri: 相対パス解決用の`<base href>`。末尾`/`付きの`file:///...`形式を想定する。
        css_text: `<style>`タグへインライン埋め込むCSS。
    """
    return (
        "<!DOCTYPE html>\n"
        '<html lang="ja">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        f'<base href="{html.escape(base_uri, quote=True)}">\n'
        f"<title>{html.escape(title)}</title>\n"
        f"<style>\n{css_text}\n</style>\n"
        "</head>\n"
        "<body>\n"
        f"{body_html}\n"
        "</body>\n"
        "</html>\n"
    )

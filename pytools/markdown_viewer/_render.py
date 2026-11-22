import html
import pathlib

import markdown_it

from pytools._internal import common


def make_renderer() -> markdown_it.MarkdownIt:

    return markdown_it.MarkdownIt("gfm-like", {"html": False, "linkify": False})


def render_body(text: str, renderer: markdown_it.MarkdownIt | None = None) -> str:
    md = renderer if renderer is not None else make_renderer()
    return md.render(text)


def resolve_css_path() -> pathlib.Path | None:
    """リポジトリ内の`share/vscode/markdown.css`のパスを返す。見つからなければNone。

    `Path.home()`起点だとCI環境や`$HOME`と`~/dotfiles`が一致しない環境で破綻するため、
    `find_dotfiles_root()`で作業ツリーから解決する。editable installを前提とする。
    """
    dotfiles_root = common.find_dotfiles_root()
    if dotfiles_root is None:
        return None
    candidate = dotfiles_root / "share" / "vscode" / "markdown.css"
    if candidate.is_file():
        return candidate
    return None


def build_html_document(*, body_html: str, title: str, base_uri: str, css_text: str) -> str:
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

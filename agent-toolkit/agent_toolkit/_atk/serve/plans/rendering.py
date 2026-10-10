"""計画ファイルのMarkdownのHTML変換（コードブロックの構文強調を含む）と変換結果のキャッシュ。"""

from __future__ import annotations

# 他の処理では不要な依存を、使用時まで遅延する。旧Pythonでは通常のimportとなる。
__lazy_modules__ = {"pygments.lexers"}

import collections
import collections.abc
import html as html_lib
import typing

import markdown_it
import markdown_it.renderer
import markdown_it.token
import markdown_it.utils
import pygments
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

# --------------------------------------------------------------------------------------
# Markdownレンダリングとキャッシュ
# --------------------------------------------------------------------------------------
from agent_toolkit._atk.serve.plans.roots import (
    _PYGMENTS_CSS_CLASS,
    _PYGMENTS_FORMATTER,
    MARKDOWN_CACHE_MAX_BYTES,
    MARKDOWN_CACHE_MAX_ENTRIES,
)


def _highlight_code(code: str, name: str, _attrs: str) -> str:
    """markdown-itのフェンスコードブロックをPygmentsでハイライトする。

    言語指定なし・未知言語フェンスは空文字を返し、markdown-itが標準で行う描画に任せる。
    """
    if not name:
        return ""
    try:
        lexer = get_lexer_by_name(name, stripall=False)
    except ClassNotFound:
        return ""
    escaped_lang = html_lib.escape(name, quote=True)
    body = pygments.highlight(code, lexer, _PYGMENTS_FORMATTER).rstrip("\n")
    return f'<pre><code class="{_PYGMENTS_CSS_CLASS} language-{escaped_lang}">{body}\n</code></pre>\n'


def _render_fence(
    renderer: markdown_it.renderer.RendererHTML,
    tokens: typing.Sequence[markdown_it.token.Token],
    idx: int,
    options: markdown_it.utils.OptionsDict,
    env: collections.abc.MutableMapping[str, typing.Any],
) -> str:
    """MermaidとSVGのフェンスを専用HTML構造へ変換する。"""
    token = tokens[idx]
    info = token.info.strip() if token.info else ""
    name = info.split(maxsplit=1)[0].lower() if info else ""
    if name not in {"mermaid", "svg"}:
        return renderer.fence(tokens, idx, options, env)

    source = html_lib.escape(token.content)
    if name == "mermaid":
        return (
            '<figure class="diagram diagram-mermaid">\n'
            f'  <div class="diagram-output mermaid-output">{source}</div>\n'
            '  <details class="diagram-source"><summary>Mermaid原文</summary>'
            f"<pre>{source}</pre></details>\n"
            "</figure>\n"
        )
    return (
        '<figure class="diagram diagram-svg">\n'
        '  <img class="diagram-output svg-output" alt="SVG図">\n'
        '  <details class="diagram-source"><summary>SVG原文</summary>'
        f"<pre>{source}</pre></details>\n"
        "</figure>\n"
    )


def make_md_renderer() -> markdown_it.MarkdownIt:
    """Raw HTMLを無効化しPygmentsハイライトを注入したGFM相当のMarkdownレンダラを返す。

    GFM相当プリセットの表・取り消し線は維持し、誤リンクを防ぐため裸URLの自動リンクだけを無効化する。
    `html`も明示的に`False`へ上書きし、HTMLを解釈させずにXSSを防ぐ。
    `highlight`コールバックの戻り値はそのままHTMLとして埋め込まれるため、
    Pygmentsのエスケープ済み出力のみを返す。
    """
    renderer = markdown_it.MarkdownIt(
        "gfm-like",
        {"html": False, "highlight": _highlight_code, "linkify": False},
    )
    renderer.add_render_rule("fence", _render_fence)
    return renderer


def markdown_to_html(text: str, renderer: markdown_it.MarkdownIt | None = None) -> str:
    """Markdown文字列をHTMLへ変換する。"""
    md = renderer if renderer is not None else make_md_renderer()
    return md.render(text)


# キーは(host, source_id, path, 本文のSHA-256)。本文が変われば新しいエントリとなるため明示的な無効化は不要。
type MarkdownCacheKey = tuple[str, str, str, str]


class MarkdownCache:
    """Markdownレンダリング結果のLRUキャッシュ。

    キーへ取得済み本文のダイジェストを含めるため、更新時刻やwatch通知の遅延に左右されず本文と整合する。
    """

    def __init__(
        self,
        max_entries: int = MARKDOWN_CACHE_MAX_ENTRIES,
        max_bytes: int = MARKDOWN_CACHE_MAX_BYTES,
    ) -> None:
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        # OrderedDictで挿入順を保ち、`move_to_end`でLRU順に保つ。
        self._entries: collections.OrderedDict[MarkdownCacheKey, str] = collections.OrderedDict()
        self._total_bytes = 0

    def get(self, key: MarkdownCacheKey) -> str | None:
        """キャッシュ済みHTMLを返す。未保持ならNone。"""
        html = self._entries.get(key)
        if html is None:
            return None
        # アクセスのたびに末尾へ移して最近使用扱いにする。
        self._entries.move_to_end(key)
        return html

    def put(self, key: MarkdownCacheKey, html: str) -> None:
        """HTMLを保持し、上限超過分を古い順に破棄する。"""
        existing = self._entries.pop(key, None)
        if existing is not None:
            self._total_bytes -= len(existing.encode("utf-8"))
        size = len(html.encode("utf-8"))
        # 単一エントリが上限を超える場合は保持せずに諦める（次回はミスのまま再レンダリング）。
        if size > self._max_bytes:
            return
        self._entries[key] = html
        self._total_bytes += size
        self._evict_excess()

    def _evict_excess(self) -> None:
        while self._entries and (len(self._entries) > self._max_entries or self._total_bytes > self._max_bytes):
            _, evicted = self._entries.popitem(last=False)
            self._total_bytes -= len(evicted.encode("utf-8"))

    def __len__(self) -> int:
        return len(self._entries)

    def total_bytes(self) -> int:
        """テスト・観測用に現在の総バイト数を返す。"""
        return self._total_bytes

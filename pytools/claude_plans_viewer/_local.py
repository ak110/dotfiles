"""ローカルファイル探索・watchdog連携・Markdownレンダリング・CSS解決。"""

import asyncio
import collections
import collections.abc
import hashlib
import html as html_lib
import json
import os
import pathlib
import threading
import typing

import markdown_it
import markdown_it.renderer
import markdown_it.token
import markdown_it.utils
import platformdirs
import pygments
import watchdog.events
from pygments.formatters.html import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from pytools._internal.watchdog_events import WATCHED_EVENT_TYPES
from pytools.claude_plans_viewer import _assets, _state

# Pygmentsはmarkdown-itの`highlight`コールバックから呼ぶ。
# `nowrap=True`で`<span>`列のみを返し、markdown-itの既定`<pre><code>`ラッパー相当を
# `_highlight_code`側で組み立てる（言語クラスを付与しつつXSS耐性をPygmentsのエスケープに委ねるため）。
_PYGMENTS_FORMATTER = HtmlFormatter(nowrap=True, style="monokai")
_PYGMENTS_CSS_CLASS = "codehilite"


def _highlight_code(code: str, name: str, _attrs: str) -> str:
    """markdown-itのフェンスコードブロックをPygmentsでハイライトする。

    言語指定なし・未知言語フェンスは空文字を返し、markdown-it既定の素通し描画にフォールバックする。
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


# Markdownレンダリング結果LRUキャッシュの上限。
# エントリ数とバイト数の二重上限のうち、先に到達した側で古い順に削除する。
# 連続選択や前後ナビゲーションでヒットさせつつ、長時間運用でも有界に保つ値とする。
MARKDOWN_CACHE_MAX_ENTRIES = 128
MARKDOWN_CACHE_MAX_BYTES = 16 * 1024 * 1024
_CREATION_TIME_CACHE_DIR = pathlib.Path(platformdirs.user_cache_dir("claude-plans-viewer", appauthor=False)) / "creation-times"


def _is_watched_path(path: pathlib.Path, root: pathlib.Path) -> bool:
    """`path`が`.md`拡張子・`root`配下・非dotdirの全条件を満たすか判定する。"""
    if path.suffix != ".md":
        return False
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return not any(part.startswith(".") for part in rel.parts)


class PlansEventHandler(watchdog.events.FileSystemEventHandler):
    """watchdogのイベントを受信してSSE購読者へ通知するハンドラ。

    watchdogコールバックはwatchdog側のスレッドで実行されるため、
    asyncioループへ`run_coroutine_threadsafe`でブリッジする。
    """

    def __init__(self, root: pathlib.Path, state: _state.BroadcastState) -> None:
        super().__init__()
        self.root = root
        self.state = state

    @typing.override
    def on_any_event(self, event: watchdog.events.FileSystemEvent) -> None:
        """ファイルシステムイベントをフィルタリングして購読者へ通知する。"""
        # 読み取り由来イベント（`FileOpenedEvent`・`FileClosedNoWriteEvent`）は除外する。
        # これらを通過させると/api/fileのread_textがwatchdog経由でSSEを誘発するfeedback loopになる。
        if not isinstance(event, WATCHED_EVENT_TYPES):
            return
        # ディレクトリイベントは対象外
        if event.is_directory:
            return
        # src_pathはwatchdog型定義上bytes|strだが実行時はstr。str変換でPath型エラーを回避する
        src = pathlib.Path(str(event.src_path))
        # `FileMovedEvent`はsrc_pathとdest_pathの両方を確認する。
        # atomic-write保存（一時ファイルに書き込み後にrenameする保存方式）では
        # `FileMovedEvent(src_path="plan.md.tmp", dest_path="plan.md")`となり、
        # src_pathだけ参照すると.md以外として除外されて自動リロードが機能しない。
        if isinstance(event, watchdog.events.FileMovedEvent):
            dest = pathlib.Path(str(event.dest_path))
            if not (_is_watched_path(src, self.root) or _is_watched_path(dest, self.root)):
                return
        else:
            if not _is_watched_path(src, self.root):
                return
        loop = self.state.loop
        if loop is None:
            # 起動直後にループ参照が未設定のイベントは取りこぼしてよい（直後のイベントで再通知される）。
            return
        asyncio.run_coroutine_threadsafe(_state.schedule_broadcast(self.state), loop)


def make_md_renderer() -> markdown_it.MarkdownIt:
    """Raw HTMLを無効化しPygmentsハイライトを注入したGFM相当のMarkdownレンダラを返す。"""
    # GFM相当プリセットで表・取り消し線・裸URL自動リンクを有効化する。
    # `html`は明示的に`False`へ上書きしてXSS経路を塞ぐ。
    # `highlight`コールバックの戻り値はそのままHTMLとして埋め込まれるため、Pygmentsのエスケープ済み
    # 出力のみを返す（生のユーザー入力を経由させない）。
    renderer = markdown_it.MarkdownIt("gfm-like", {"html": False, "highlight": _highlight_code})
    renderer.add_render_rule("fence", _render_fence)
    return renderer


def markdown_to_html(text: str, renderer: markdown_it.MarkdownIt | None = None) -> str:
    """Markdown文字列をHTMLへ変換する。"""
    md = renderer if renderer is not None else make_md_renderer()
    return md.render(text)


# キャッシュキーは(host, path, mtime_epoch)。`mtime_epoch`がキーに含まれるため、
# ファイル更新時は自動的に新しいエントリとなり明示的な無効化は不要。
MarkdownCacheKey = tuple[str, str, float]


class MarkdownCache:
    """Markdownレンダリング結果のLRUキャッシュ。

    キーは`(host, path, mtime_epoch)`。リモート分は`fetch_remote_file`が本文と
    同時取得した`mtime_epoch`をそのまま使うことで、watch通知の遅延に左右されず整合する。
    `mtime_epoch`が`None`の場合、呼び出し側はキャッシュをバイパスする
    （本クラスは`None`を扱わない）。
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
        html = self._entries.get(key)
        if html is None:
            return None
        # アクセスのたびに末尾へ移して最近使用扱いにする。
        self._entries.move_to_end(key)
        return html

    def put(self, key: MarkdownCacheKey, html: str) -> None:
        # 既存キーは置換扱い。サイズ計算のため一旦削除してから挿入する。
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


def _cached_creation_time(host: str, rel: str, observed_mtime: float) -> float:
    """初回観測時の更新日時をホスト名と相対パスごとに永続化する。"""
    key = hashlib.sha256(f"{host}\0{rel}".encode()).hexdigest()
    cache_path = _CREATION_TIME_CACHE_DIR / f"{key}.json"
    cached: float | None = None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        if payload.get("host") == host and payload.get("path") == rel:
            value = payload.get("ctime_epoch")
            if isinstance(value, (int, float)):
                cached = float(value)
    except (FileNotFoundError, OSError, json.JSONDecodeError, AttributeError):
        pass
    creation_time = min(observed_mtime, cached) if cached is not None else observed_mtime
    if cached == creation_time:
        return creation_time
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_name(f".{cache_path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        temporary.write_text(
            json.dumps({"host": host, "path": rel, "ctime_epoch": creation_time}, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(cache_path)
    except OSError:
        return creation_time
    return creation_time


def _ctime_epoch(st: os.stat_result, host: str, rel: str) -> float:
    """作成日時をepoch秒で返す。

    `st_birthtime`（macOS・Windowsで実在し「作成時刻」を表す）を優先し、
    存在しないプラットフォームでは初回観測時の更新日時を永続キャッシュへ記録する。
    編集で変動する`st_ctime`を使わず、再起動後も同じ並び順を維持するためである。
    """
    birthtime = getattr(st, "st_birthtime", None)
    return float(birthtime) if birthtime is not None else _cached_creation_time(host, rel, float(st.st_mtime))


def local_host_info(root: pathlib.Path) -> dict[str, str]:
    """ローカルホストの`host_info`エントリ（`root`・`home`・`os_type`・`os_name`）を組み立てる。

    `_app.py`が起動時に`BroadcastState.host_info`へ登録し、`index()`のJS注入にも使う。
    `root`・`home`はクライアント側のパス結合と表記を統一するため常に`/`区切りへ正規化する。
    `home`はクライアント側`copySelectedPath`のチルダ表記変換の基準パスとして使う
    （`root`はplansディレクトリ等のroot直下パスでありホームディレクトリと一致しない場合があるため）。
    """
    home = str(pathlib.Path.home()).replace("\\", "/")
    return {
        "root": str(root).replace("\\", "/"),
        "home": home,
        "os_type": os.name,
        "os_name": os.name,
    }


def list_files(root: pathlib.Path, host: str) -> list[_state.FileEntry]:
    """`root`から`.md`ファイルを再帰的に探し、作成日時の降順で返す。

    `host`は各エントリの`host`フィールドへ埋め込むラベル（通常はサーバー実行ホスト名）。
    """
    collected: list[_state.FileEntry] = []
    for path in root.rglob("*.md"):
        if not path.is_file():
            continue
        st = path.stat()
        rel = path.relative_to(root).as_posix()
        item = {
            "path": rel,
            "name": path.name,
            "mtime_epoch": st.st_mtime,
            "ctime_epoch": _ctime_epoch(st, host, rel),
        }
        collected.append(_state.make_file_entry(host, item))
    collected.sort(key=lambda entry: entry.ctime_epoch, reverse=True)
    return collected


def search_files(root: pathlib.Path, query: str) -> set[str]:
    """本文へ検索語が部分一致するMarkdownファイルの相対パス集合を返す。"""
    needle = query.casefold()
    if not needle:
        return {path.relative_to(root).as_posix() for path in root.rglob("*.md") if path.is_file()}
    matched: set[str] = set()
    for path in root.rglob("*.md"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if needle in text.casefold():
            matched.add(path.relative_to(root).as_posix())
    return matched


def resolve_under_root(root: pathlib.Path, rel: str) -> pathlib.Path | None:
    """`rel`が`root`配下の`.md`ファイルを指す場合のみ絶対パスを返す。存在しない場合はNone。"""
    # シンボリックリンクを辿ってroot外へ出ないよう、resolve後のパスで範囲検査する。
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    if target.suffix != ".md" or not target.is_file():
        return None
    return target


def _resolve_css_path() -> pathlib.Path | None:
    """リポジトリ内の`share/vscode/markdown.css`のパスを返す。見つからなければNone。"""
    # editable install前提でこのスクリプトはリポジトリ配下に置かれる。
    # `pytools/claude_plans_viewer/_local.py`の位置を起点とするとリポジトリルートは2階層上。
    # `$HOME`と`~/dotfiles`が一致しないCIチェックアウトや別配置環境でも整合させるため、
    # `Path(__file__)`起点を一次解決経路とする。
    candidate = pathlib.Path(__file__).resolve().parents[2] / "share" / "vscode" / "markdown.css"
    if candidate.is_file():
        return candidate
    # フォールバック: pipインストール等で本ファイルがsite-packages配下にある場合、`~/dotfiles`配下を参照する。
    candidate = pathlib.Path.home() / "dotfiles" / "share" / "vscode" / "markdown.css"
    if candidate.is_file():
        return candidate
    return None


async def read_css() -> str:
    """配布物のCSSを読み込む。見つからなければフォールバックを返す。"""
    path = _resolve_css_path()
    if path is not None:
        # read_textはブロッキングI/Oのためスレッドプールで実行する。
        return await asyncio.to_thread(path.read_text, encoding="utf-8")
    return _assets.FALLBACK_CSS


def read_mermaid_bundle() -> str:
    """同梱したMermaidの単一ファイルbundleを読み込む。"""
    path = pathlib.Path(__file__).resolve().parent / "vendor" / "mermaid.min.js"
    return path.read_text(encoding="utf-8")


def read_pygments_css() -> str:
    """Pygmentsのスタイルシートを返す。

    pygmentsの基本ルール（`.codehilite { background: ...; color: ... }`）は除外し、
    トークン別カラールール（`.codehilite .k`等）のみを返す。
    背景と既定文字色はmarkdown.css側の`pre code`ルールへ委ね、
    `<pre>`の`#1e1e1e`背景上に異色矩形が出現する事象を防ぐ。
    """
    raw = _PYGMENTS_FORMATTER.get_style_defs(f".{_PYGMENTS_CSS_CLASS}")
    base_selector = f".{_PYGMENTS_CSS_CLASS}"
    kept: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{base_selector} {{") or stripped.startswith(f"{base_selector}{{"):
            continue
        kept.append(line)
    return "\n".join(kept)

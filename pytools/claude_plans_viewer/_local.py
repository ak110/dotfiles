"""ローカルファイル探索・watchdog連携・Markdownレンダリング・CSS解決。"""

import asyncio
import collections
import html as html_lib
import os
import pathlib
import typing

import markdown_it
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


# Markdownレンダリング結果LRUキャッシュの上限。
# エントリ数とバイト数の二重上限のうち、先に到達した側で古い順に削除する。
# 連続選択や前後ナビゲーションでヒットさせつつ、長時間運用でも有界に保つ値とする。
MARKDOWN_CACHE_MAX_ENTRIES = 128
MARKDOWN_CACHE_MAX_BYTES = 16 * 1024 * 1024


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
    """Raw HTMLを無効化しPygmentsハイライトを注入したMarkdownレンダラを返す。"""
    # CommonMarkプリセットは`html`オプションの既定値が`True`でraw HTMLを通すため、
    # 明示的に`False`へ上書きしてXSS経路を塞ぐ。表拡張は別途`enable("table")`で有効化する。
    # `highlight`コールバックの戻り値はそのままHTMLとして埋め込まれるため、Pygmentsのエスケープ済み
    # 出力のみを返す（生のユーザー入力を経由させない）。
    return markdown_it.MarkdownIt("commonmark", {"html": False, "highlight": _highlight_code}).enable("table")


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


def _ctime_epoch(st: os.stat_result) -> float:
    """作成日時をepoch秒で返す。

    `st_birthtime`（macOS・Windowsで実在し「作成時刻」を表す）を優先し、
    存在しないプラットフォーム（Linux等）では`st_ctime`（inode変更時刻）へフォールバックする。
    Linux上のfallback意味論は並列作業時のリネーム・権限変更で更新される制約があるが、
    実運用では作成時刻に近い値として許容する。
    """
    birthtime = getattr(st, "st_birthtime", None)
    return float(birthtime) if birthtime is not None else float(st.st_ctime)


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
        item = {
            "path": path.relative_to(root).as_posix(),
            "name": path.name,
            "mtime_epoch": st.st_mtime,
            "ctime_epoch": _ctime_epoch(st),
        }
        collected.append(_state.make_file_entry(host, item))
    collected.sort(key=lambda entry: entry.ctime_epoch, reverse=True)
    return collected


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

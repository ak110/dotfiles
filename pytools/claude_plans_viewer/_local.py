"""ローカルファイル探索・watchdog連携・Markdownレンダリング・CSS解決。

クラス・関数名は同一パッケージ内の兄弟モジュールから参照される前提のため、
underscore接頭辞を付けない（package-internalとして扱う）。
パッケージ外への公開可否は`__init__.py`の再export一覧で制御する。
"""

import asyncio
import collections
import datetime
import pathlib
import typing

import markdown_it
import watchdog.events

from pytools.claude_plans_viewer import _assets, _state

# Markdownレンダリング結果LRUキャッシュの上限。
# エントリ数とバイト数の二重上限のうち、先に到達した側で古い順に削除する。
# 連続選択や前後ナビゲーションでヒットさせつつ、長時間運用でも有界に保つ値とする。
MARKDOWN_CACHE_MAX_ENTRIES = 128
MARKDOWN_CACHE_MAX_BYTES = 16 * 1024 * 1024

# 読み取り由来の`FileOpenedEvent`・`FileClosedNoWriteEvent`は`/api/file`応答の`read_text`との間で
# feedback loopになるため除外する。`FileClosedEvent`は`IN_CLOSE_WRITE`（書き込み後クローズ）を表す。
WATCHED_EVENT_TYPES: tuple[type[watchdog.events.FileSystemEvent], ...] = (
    watchdog.events.FileCreatedEvent,
    watchdog.events.FileModifiedEvent,
    watchdog.events.FileDeletedEvent,
    watchdog.events.FileMovedEvent,
    watchdog.events.FileClosedEvent,
)


def is_watched_path(path: pathlib.Path, root: pathlib.Path) -> bool:
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
            if not (is_watched_path(src, self.root) or is_watched_path(dest, self.root)):
                return
        else:
            if not is_watched_path(src, self.root):
                return
        loop = self.state.loop
        if loop is None:
            # 起動直後にループ参照が未設定のイベントは取りこぼしてよい（直後のイベントで再通知される）。
            return
        asyncio.run_coroutine_threadsafe(_state.schedule_broadcast(self.state), loop)


def make_md_renderer() -> markdown_it.MarkdownIt:
    """Raw HTMLを無効化したMarkdownレンダラを返す。"""
    # CommonMarkプリセットは`html`オプションの既定値が`True`でraw HTMLを通すため、
    # 明示的に`False`へ上書きしてXSS経路を塞ぐ。表拡張は別途`enable("table")`で有効化する。
    return markdown_it.MarkdownIt("commonmark", {"html": False}).enable("table")


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
    同時取得した`mtime_epoch`をそのまま使うことで、watch通知の遅延と無関係に整合する。
    `mtime_epoch`が`None`の場合、呼び出し側はキャッシュを参照せずバイパスする
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


def list_files(root: pathlib.Path, host: str) -> list[_state.FileEntry]:
    """rootから`.md`ファイルを再帰的に探し、更新日時の降順で返す。

    `host`は各エントリの`host`フィールドへ埋め込むラベル（通常はサーバー実行ホスト名）。
    """
    tzinfo = datetime.datetime.now().astimezone().tzinfo
    collected: list[tuple[float, _state.FileEntry]] = []
    for path in root.rglob("*.md"):
        if not path.is_file():
            continue
        st = path.stat()
        rel = path.relative_to(root).as_posix()
        mtime = datetime.datetime.fromtimestamp(st.st_mtime, tz=tzinfo)
        entry = _state.FileEntry(
            host=host,
            path=rel,
            name=path.name,
            mtime=mtime.strftime("%Y/%m/%d %H:%M"),
            mtime_epoch=st.st_mtime,
        )
        collected.append((st.st_mtime, entry))
    collected.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _, entry in collected]


def resolve_under_root(root: pathlib.Path, rel: str) -> pathlib.Path | None:
    """`rel`が`root`配下の`.md`ファイルを指す場合のみ絶対パスを返す。"""
    # シンボリックリンクを辿ってroot外へ出ないよう、resolve後のパスで範囲検査する。
    target = (root / rel).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    if target.suffix != ".md" or not target.is_file():
        return None
    return target


def resolve_css_path() -> pathlib.Path | None:
    """リポジトリ内の`share/vscode/markdown.css`を返す。見つからなければNone。"""
    # dotfilesは通常~/dotfiles配下に置かれる。
    candidate = pathlib.Path.home() / "dotfiles" / "share" / "vscode" / "markdown.css"
    if candidate.is_file():
        return candidate
    # 念のためフォールバック。
    # editable installであればこのスクリプトがリポジトリ配下に置かれるため、こちらも解決できるはず。
    # `pytools/claude_plans_viewer/_local.py`の位置を起点とするとリポジトリルートは2階層上。
    candidate = pathlib.Path(__file__).resolve().parents[2] / "share" / "vscode" / "markdown.css"
    if candidate.is_file():
        return candidate
    return None


async def read_css() -> str:
    """配布物のCSSを読み込む。見つからなければフォールバックを返す。"""
    path = resolve_css_path()
    if path is not None:
        # read_textはブロッキングI/Oのためスレッドプールで実行する。
        return await asyncio.to_thread(path.read_text, encoding="utf-8")
    return _assets.FALLBACK_CSS

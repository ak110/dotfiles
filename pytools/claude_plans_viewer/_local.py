"""ローカルファイル探索・watchdog連携・Markdownレンダリング・CSS解決。"""

import asyncio
import collections
import collections.abc
import contextlib
import hashlib
import html as html_lib
import json
import os
import pathlib
import re
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

from pytools._internal import claude_common, file_lock
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
# 作成日時の永続インデックス。ホスト・root・相対パスの3項をキーとする単一JSONへ集約する。
# 同一ホスト上でリモートヘルパー（`_remote_helper.py`）も同じファイルを共有するため、
# キーと値の形式を両実装で一致させる。
_CREATION_TIME_INDEX_PATH = (
    pathlib.Path(platformdirs.user_cache_dir("claude-plans-viewer", appauthor=False)) / "creation-times" / "index.json"
)
# 旧形式（1エントリ1ファイル）のキャッシュ名。sha256 hexdigestと`.json`から成る。
_LEGACY_CACHE_NAME_RE = re.compile(r"^[0-9a-f]{64}\.json$")
# 旧実装が生成した一時ファイル名。`.<sha256 hexdigest>.json.<pid>.<スレッドID>.tmp`。
_LEGACY_TEMPORARY_NAME_RE = re.compile(r"^\.[0-9a-f]{64}\.json\.\d+\.\d+\.tmp$")


def is_target_path(path: pathlib.Path, root: pathlib.Path) -> bool:
    """`path`が`.md`拡張子・`root`配下・非dotdirの全条件を満たすか判定する。

    読取・検索・変更監視の3経路が同一の対象集合を返すよう、当該判定を1箇所へ集約する。
    計画本体`<stem>.md`と実装詳細側`<stem>.detail.md`の双方を真とする
    （detailは一覧だけから除外し、読取・検索・監視の対象には含める。`is_listed_path`が一覧専用の判定を持つ）。
    リモート側`_remote_helper.py`の`_is_target_path`と同一基準を保つ
    （同ファイルはSSH越しに単独実行されるためモジュールを共有できず、意図的に重複させている）。
    `root`自身がドット配下（`~/.claude/plans`など）でも通るよう、判定は`root`からの相対パスに対して行う。
    シンボリックリンクを解決してから相対化するため、`root`外を指すリンクは対象外となる。
    """
    if path.suffix != ".md":
        return False
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return not any(part.startswith(".") for part in rel.parts)


def is_listed_path(path: pathlib.Path, root: pathlib.Path) -> bool:
    """`path`が計画一覧の対象（`is_target_path`が真、かつ実装詳細側`.detail.md`ではない）かを判定する。

    一覧経路だけに使う。読取・検索・変更監視は`is_target_path`を使い、detailも対象へ含める。
    """
    return is_target_path(path, root) and not path.name.endswith(".detail.md")


def _is_watched_path(path: pathlib.Path, root: pathlib.Path) -> bool:
    """監視イベントの対象判定。読取・検索と同一の`is_target_path`を用いる（detailも対象へ含める）。"""
    return is_target_path(path, root)


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
    # GFM相当プリセットの表・取り消し線は維持し、誤リンクを防ぐため裸URLの自動リンクだけを無効化する。
    # `html`も明示的に`False`へ上書きしてXSS経路を塞ぐ。
    # `highlight`コールバックの戻り値はそのままHTMLとして埋め込まれるため、Pygmentsのエスケープ済み
    # 出力のみを返す（生のユーザー入力を経由させない）。
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


def _index_lock_path() -> pathlib.Path:
    """作成日時インデックスの排他ロックファイルのパス。"""
    return _CREATION_TIME_INDEX_PATH.with_name(_CREATION_TIME_INDEX_PATH.name + ".lock")


def _enter_index_lock(stack: contextlib.ExitStack) -> bool:
    """作成日時インデックスの排他ロックを`stack`へ登録する。取得できない場合は`False`を返す。

    キャッシュディレクトリを作成・書き込みできない環境ではロックファイルを開けず`OSError`となる。
    作成日時キャッシュの失敗で一覧機能を止めないため、当該例外は呼び出し元へ伝播させない。
    """
    try:
        stack.enter_context(file_lock.exclusive_file_lock(_index_lock_path()))
    except OSError:
        return False
    return True


def _index_key(host: str, root_key: str, rel: str) -> str:
    r"""インデックスのキー。`(host, root, rel)`を`\0`で連結した文字列のsha256 hexdigest。"""
    return hashlib.sha256(f"{host}\0{root_key}\0{rel}".encode()).hexdigest()


def _root_key(root: pathlib.Path) -> str:
    """インデックスのキーと値へ用いる`root`の正規化表記。"""
    return str(root.resolve()).replace("\\", "/")


def _entry_ctime(entry: typing.Any) -> float | None:
    """インデックスまたは旧形式のエントリから作成日時を取り出す。取得できない場合はNone。"""
    value = entry.get("ctime_epoch")
    return float(value) if isinstance(value, (int, float)) else None


def _load_index() -> dict[str, typing.Any]:
    """インデックスを読み込む。不在・読み取り失敗・形式不正はいずれも空として扱う。"""
    try:
        payload = json.loads(_CREATION_TIME_INDEX_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {key: value for key, value in payload.items() if isinstance(value, dict)}


def _load_legacy_entries() -> dict[tuple[str, str], tuple[float, pathlib.Path]]:
    """旧形式のキャッシュを`(host, 相対パス)`から作成日時と実ファイルへの対応として読み込む。

    旧形式は`root`を保持しないため、現在の走査対象と一致するものだけを移行対象にできる。
    """
    entries: dict[tuple[str, str], tuple[float, pathlib.Path]] = {}
    try:
        candidates = [path for path in _CREATION_TIME_INDEX_PATH.parent.iterdir() if _LEGACY_CACHE_NAME_RE.match(path.name)]
    except OSError:
        return entries
    for candidate in candidates:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        host = payload.get("host")
        rel = payload.get("path")
        ctime = _entry_ctime(payload)
        if isinstance(host, str) and isinstance(rel, str) and ctime is not None:
            entries[(host, rel)] = (ctime, candidate)
    return entries


def update_creation_time_index(host: str, root: pathlib.Path, observed: dict[str, float]) -> dict[str, float]:
    """走査結果の観測時刻をインデックスへ反映し、確定した作成日時を相対パスごとに返す。

    初回観測時の値を作成日時として保持することで、編集で変動する値に依らず並び順を維持する。
    同一の`(host, root)`に属し今回の走査に現れなかったキーは回収し、
    別の`root`に属するキーは維持する（`root`ごとに走査対象が異なるため）。
    旧形式は`host`と相対パスが今回の走査と一致するものだけを取り込み、
    インデックスの書き込みに成功した場合に限り取り込んだファイルを削除する。
    ロックを取得できない場合はインデックスの更新を諦め、観測値をそのまま返す。
    """
    root_key = _root_key(root)
    resolved: dict[str, float] = {}
    with contextlib.ExitStack() as stack:
        if not _enter_index_lock(stack):
            return dict(observed)
        index = _load_index()
        legacy = _load_legacy_entries()
        migrated: list[pathlib.Path] = []
        updated: dict[str, typing.Any] = {}
        for rel, observed_epoch in observed.items():
            key = _index_key(host, root_key, rel)
            cached = _entry_ctime(index.get(key, {}))
            if cached is None:
                legacy_entry = legacy.get((host, rel))
                if legacy_entry is not None:
                    cached, legacy_path = legacy_entry
                    migrated.append(legacy_path)
            creation = min(observed_epoch, cached) if cached is not None else observed_epoch
            resolved[rel] = creation
            updated[key] = {"host": host, "root": root_key, "path": rel, "ctime_epoch": creation}
        # インデックスを更新する経路は全て同じロックを保持するため、冒頭で読み込んだ内容へ直接反映する。
        for key, entry in list(index.items()):
            if key not in updated and entry.get("host") == host and entry.get("root") == root_key:
                del index[key]
        index.update(updated)
        if claude_common.atomic_write_json(_CREATION_TIME_INDEX_PATH, index):
            for legacy_path in migrated:
                with contextlib.suppress(OSError):
                    legacy_path.unlink()
    return resolved


def cleanup_creation_time_temporaries() -> None:
    """作成日時インデックスの残存一時ファイルをロック下で除去する。

    対象は`atomic_write_json`が生成する`index.json.<ランダム文字列>.tmp`と、
    旧実装が生成した`.<sha256 hexdigest>.json.<pid>.<スレッドID>.tmp`の2形式とする。
    書き込み途中のファイルを削除しないよう、除去はインデックスと同じロックの保持中に行う。
    ロックを取得できない場合は何もせずに返る。
    """
    directory = _CREATION_TIME_INDEX_PATH.parent
    if not directory.is_dir():
        return
    temporary_pattern = f"{_CREATION_TIME_INDEX_PATH.name}.*.tmp"
    with contextlib.ExitStack() as stack:
        if not _enter_index_lock(stack):
            return
        try:
            candidates = list(directory.iterdir())
        except OSError:
            return
        for candidate in candidates:
            if not candidate.match(temporary_pattern) and not _LEGACY_TEMPORARY_NAME_RE.match(candidate.name):
                continue
            with contextlib.suppress(OSError):
                candidate.unlink()


def _ctime_epoch(st: os.stat_result) -> float:
    """観測時点の作成日時候補をepoch秒で返す。

    `st_birthtime`（macOS・Windowsで実在し「作成時刻」を表す）を優先し、
    存在しないプラットフォームでは更新日時を用いる。
    初回観測時の値を保持する処理は`update_creation_time_index`が担う。
    編集で変動する`st_ctime`は用いない。
    """
    birthtime = getattr(st, "st_birthtime", None)
    return float(birthtime) if birthtime is not None else float(st.st_mtime)


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

    実装詳細側`<stem>.detail.md`は一覧から除外する（`is_listed_path`）。
    `host`は各エントリの`host`フィールドへ埋め込むラベル（通常はサーバー実行ホスト名）。
    """
    scanned: list[dict[str, typing.Any]] = []
    observed: dict[str, float] = {}
    for path in root.rglob("*.md"):
        if not path.is_file() or not is_listed_path(path, root):
            continue
        st = path.stat()
        rel = path.relative_to(root).as_posix()
        observed[rel] = _ctime_epoch(st)
        scanned.append({"path": rel, "name": path.name, "mtime_epoch": st.st_mtime})
    # 走査後に一度だけインデックスを更新し、同じ`(host, root)`の不在エントリを回収する。
    resolved = update_creation_time_index(host, root, observed)
    collected = [_state.make_file_entry(host, {**item, "ctime_epoch": resolved[item["path"]]}) for item in scanned]
    collected.sort(key=lambda entry: entry.ctime_epoch, reverse=True)
    return collected


def search_files(root: pathlib.Path, query: str) -> set[str]:
    """本文へ検索語が部分一致するMarkdownファイルの相対パス集合を返す。"""
    needle = query.casefold()
    if not needle:
        return {
            path.relative_to(root).as_posix() for path in root.rglob("*.md") if path.is_file() and is_target_path(path, root)
        }
    matched: set[str] = set()
    for path in root.rglob("*.md"):
        if not path.is_file() or not is_target_path(path, root):
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

"""Claude Codeの`~/.claude/plans/*.md`をブラウザで一覧・閲覧するローカルHTTPビューア。

SSHポートフォワード経由でWindows側のブラウザから参照することを想定し、
外部CDNに依存せずサーバー側でMarkdownをHTMLへ変換する。
Markdown→HTML変換はraw HTMLをエスケープする設定とし、
`~/.claude/plans/`配下の内容がスクリプトとして実行されないようにする。

設定値の優先順位は「CLI引数 > 環境変数 > 組み込み既定値」とし、
環境ごとの差分は環境変数で吸収できるようにしている。

- `CLAUDE_PLANS_VIEWER_ROOT`: Markdownのルートディレクトリ
- `CLAUDE_PLANS_VIEWER_HOST`: bindアドレス
- `CLAUDE_PLANS_VIEWER_PORT`: 待受ポート
"""

import argparse
import contextlib
import dataclasses
import datetime
import html
import http.server
import json
import logging
import os
import pathlib
import queue
import socket
import sys
import threading
import typing
import urllib.parse

import markdown_it
import watchdog.events
import watchdog.observers

logger = logging.getLogger(__name__)

# SSE購読者の集合。スレッドセーフ化のため _subscribers_lock で保護する。
_subscribers: set[queue.Queue[str]] = set()
_subscribers_lock = threading.Lock()

# debounce窓。watchdogは1回の書き込みで複数イベントを発火するため、時間窓で畳み込む。
_BROADCAST_DEBOUNCE_SEC = 0.3
_broadcast_timer: threading.Timer | None = None
_broadcast_timer_lock = threading.Lock()

# 読み取り由来の`FileOpenedEvent`・`FileClosedNoWriteEvent`は`/api/file`応答の`read_text`との間で
# feedback loopになるため除外する。`FileClosedEvent`は`IN_CLOSE_WRITE`（書き込み後クローズ）を表す。
_WATCHED_EVENT_TYPES: tuple[type[watchdog.events.FileSystemEvent], ...] = (
    watchdog.events.FileCreatedEvent,
    watchdog.events.FileModifiedEvent,
    watchdog.events.FileDeletedEvent,
    watchdog.events.FileMovedEvent,
    watchdog.events.FileClosedEvent,
)


def _subscribe() -> queue.Queue[str]:
    """SSE購読キューを生成して登録し返す。"""
    q: queue.Queue[str] = queue.Queue(maxsize=1)
    with _subscribers_lock:
        _subscribers.add(q)
    return q


def _unsubscribe(q: queue.Queue[str]) -> None:
    """購読キューを解除する。存在しない場合もエラーにしない。"""
    with _subscribers_lock:
        _subscribers.discard(q)


def _deliver_refresh() -> None:
    """全購読者へ`refresh`を配信する。

    キューがすでに満杯の場合は新規通知を破棄する。`_broadcast`のdebounce窓満了時に呼ばれる。
    """
    with _subscribers_lock:
        targets = list(_subscribers)
    for q in targets:
        with contextlib.suppress(queue.Full):
            q.put_nowait("refresh")


def _broadcast() -> None:
    """全購読者へ`refresh`を通知する（debounce付き）。

    watchdogは1回の書き込みで複数イベントを発火するため、時間窓（`_BROADCAST_DEBOUNCE_SEC`）で
    畳み込んで1回だけ配信する。タイマーがすでに起動中であれば即座に返る。
    """
    global _broadcast_timer  # noqa: PLW0603 # pylint: disable=global-statement
    with _broadcast_timer_lock:
        if _broadcast_timer is not None and _broadcast_timer.is_alive():
            return
        _broadcast_timer = threading.Timer(_BROADCAST_DEBOUNCE_SEC, _deliver_refresh)
        _broadcast_timer.daemon = True
        _broadcast_timer.start()


def _is_watched_path(path: pathlib.Path, root: pathlib.Path) -> bool:
    """`path`が`.md`拡張子・`root`配下・非dotdirの全条件を満たすか判定する。"""
    if path.suffix != ".md":
        return False
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return not any(part.startswith(".") for part in rel.parts)


class _PlansEventHandler(watchdog.events.FileSystemEventHandler):
    """watchdogのイベントを受けてSSE購読者へ通知するハンドラ。"""

    def __init__(self, root: pathlib.Path) -> None:
        super().__init__()
        self.root = root

    @typing.override
    def on_any_event(self, event: watchdog.events.FileSystemEvent) -> None:
        """ファイルシステムイベントをフィルタリングして購読者へ通知する。"""
        # 読み取り由来イベント（`FileOpenedEvent`・`FileClosedNoWriteEvent`）は除外する。
        # これらを通過させると/api/fileのread_textがwatchdog経由でSSEを誘発するfeedback loopになる。
        if not isinstance(event, _WATCHED_EVENT_TYPES):
            return
        # ディレクトリイベントは対象外
        if event.is_directory:
            return
        # src_pathはwatchdog型定義上bytes|strだが実行時はstr。str変換でPath型エラーを回避する
        src = pathlib.Path(str(event.src_path))
        # `FileMovedEvent`はsrc_pathとdest_pathの両方を確認する。
        # atomic-write保存（一時ファイルに書き込み後にrenameする保存方式）では
        # `FileMovedEvent(src_path="plan.md.tmp", dest_path="plan.md")`となり、
        # src_pathだけ見ると.md以外として除外されて自動リロードが機能しない。
        if isinstance(event, watchdog.events.FileMovedEvent):
            dest = pathlib.Path(str(event.dest_path))
            if not (_is_watched_path(src, self.root) or _is_watched_path(dest, self.root)):
                return
        else:
            if not _is_watched_path(src, self.root):
                return
        _broadcast()


_DEFAULT_ROOT = "~/.claude/plans"
_DEFAULT_HOST = "127.0.0.1"
# VSCodeリモート開発拡張はLinux側の待受ポートをWindows側へ自動転送するため、
# Windowsローカル実行時に既定値が衝突する。Windowsのみ別値へずらして回避する。
_DEFAULT_PORT = 28875 if sys.platform == "win32" else 28765

_ENV_ROOT = "CLAUDE_PLANS_VIEWER_ROOT"
_ENV_HOST = "CLAUDE_PLANS_VIEWER_HOST"
_ENV_PORT = "CLAUDE_PLANS_VIEWER_PORT"

# share/vscode/markdown.cssが見つからないときの最小フォールバック。
# editable install前提では使われない想定だが、非editable配布や移動時に備えて持たせる。
_FALLBACK_CSS = """\
body { font-family: system-ui, sans-serif; max-width: 860px; margin: 0 auto; padding: 2rem; color: #1a1a1a; }
pre { background: #1e1e1e; color: #d4d4d4; padding: 1rem; overflow: auto; border-radius: 8px; }
code { background: #f2f2f2; padding: 0.1em 0.3em; border-radius: 4px; }
table { border-collapse: collapse; }
th, td { border: 1px solid #d1d5db; padding: 6px 8px; }
"""

# タブ識別とPWAアイコンの双方でSSOTにするため、faviconはインラインSVGを単一定数で保持する。
# 図柄はtabler iconsのclipboard-list準拠。ベクターで配布するためPWAの192x192/512x512要件も1ファイルで満たせる。
_FAVICON_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#4f46e5"\
 stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M9 5H7a2 2 0 0 0 -2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2 -2V7a2 2 0 0 0 -2 -2h-2"/>
  <rect x="9" y="3" width="6" height="4" rx="2"/>
  <path d="M9 12h.01"/>
  <path d="M11 12h4"/>
  <path d="M9 16h.01"/>
  <path d="M11 16h4"/>
</svg>
"""

# PWAインストール可能性を満たすmanifest。iconsはSVG1件で192x192・512x512・anyを同時に宣言する
# （Chrome 93以降のSVG対応によりraster PNGを別途生成しなくてよい）。
_MANIFEST_JSON = """\
{
  "name": "Claude plans",
  "short_name": "Plans",
  "start_url": "/",
  "display": "standalone",
  "theme_color": "#4f46e5",
  "background_color": "#ffffff",
  "icons": [
    {
      "src": "/favicon.svg",
      "sizes": "192x192 512x512 any",
      "type": "image/svg+xml",
      "purpose": "any maskable"
    }
  ]
}
"""

# PWAインストール可能性判定を満たす最小のservice worker。
# オフライン動作は目標外のためキャッシュ戦略は持たず、fetchは既定のネットワーク動作に委ねる。
_SERVICE_WORKER_JS = """\
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
"""

# 左ペインにファイル一覧・右ペインにMarkdownプレビューを表示するSPA。
# Markdown→HTML変換はサーバー側で済ませて`/api/file`がHTMLを返すため、
# クライアント側はfetchした文字列をそのまま`<article>`へ挿入する。
_INDEX_HTML = """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude plans</title>
<link rel="icon" type="image/svg+xml" href="/favicon.svg">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#4f46e5">
<link rel="stylesheet" href="/static/markdown.css">
<style>
  html, body { height: 100%; }
  body { margin: 0; max-width: none; padding: 0; }
  #app { display: grid; grid-template-columns: 320px 1fr; height: 100vh; }
  aside {
    border-right: 1px solid #e6e6e6;
    overflow: auto;
    background: #f9fafb;
    padding: 0;
  }
  aside .toolbar {
    position: sticky;
    top: 0;
    background: #f9fafb;
    padding: 10px;
    border-bottom: 1px solid #e6e6e6;
  }
  aside .hostinfo {
    font-size: 11px;
    color: #6b7280;
    margin-bottom: 6px;
    word-break: break-all;
  }
  aside input {
    width: 100%;
    box-sizing: border-box;
    padding: 6px 8px;
    border: 1px solid #d1d5db;
    border-radius: 6px;
  }
  .file {
    padding: 10px 12px;
    border-bottom: 1px solid #e6e6e6;
    cursor: pointer;
  }
  .file:hover, .file.active { background: #eef2ff; }
  .name { font-size: 13px; font-weight: 600; word-break: break-all; }
  .meta { margin-top: 4px; font-size: 11px; color: #6b7280; }
  main { overflow: auto; padding: 2rem; box-sizing: border-box; }
  main article { max-width: 860px; margin: 0 auto; }
</style>
</head>
<body>
<div id="app">
  <aside>
    <div class="toolbar">
      <div class="hostinfo">__HOSTNAME__</div>
      <input id="filter" placeholder="filter...">
    </div>
    <div id="files"></div>
  </aside>
  <main><article id="preview">左の一覧からMarkdownを選択してください。</article></main>
</div>
<script>
let files = [];
let selectedPath = null;
let selectedMtime = null;

function renderFiles() {
  const q = document.getElementById("filter").value.toLowerCase();
  const root = document.getElementById("files");
  const aside = document.querySelector("aside");
  // 一覧再描画前にスクロール位置を退避し、再描画後に復元する
  const scrollTop = aside ? aside.scrollTop : 0;
  root.innerHTML = "";
  const frag = document.createDocumentFragment();
  for (const file of files) {
    if (!file.path.toLowerCase().includes(q)) continue;
    const item = document.createElement("div");
    item.className = "file" + (file.path === selectedPath ? " active" : "");
    item.onclick = () => openFile(file.path);
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = file.path;
    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = file.mtime;
    item.appendChild(name);
    item.appendChild(meta);
    frag.appendChild(item);
  }
  root.appendChild(frag);
  if (aside) aside.scrollTop = scrollTop;
}

async function refreshFiles() {
  const res = await fetch("/api/files");
  files = await res.json();
  renderFiles();
}

async function updatePreview() {
  if (!selectedPath) return;
  const main = document.querySelector("main");
  const scrollTop = main ? main.scrollTop : 0;
  const res = await fetch("/api/file?path=" + encodeURIComponent(selectedPath));
  if (!res.ok) {
    document.getElementById("preview").textContent = "読み込みに失敗しました: " + res.status;
    return;
  }
  document.getElementById("preview").innerHTML = await res.text();
  if (main) main.scrollTop = scrollTop;
}

async function openFile(path) {
  await refreshFiles();
  selectedPath = path;
  renderFiles();
  const main = document.querySelector("main");
  const res = await fetch("/api/file?path=" + encodeURIComponent(path));
  if (!res.ok) {
    document.getElementById("preview").textContent = "読み込みに失敗しました: " + res.status;
    if (main) main.scrollTop = 0;
    return;
  }
  document.getElementById("preview").innerHTML = await res.text();
  if (main) main.scrollTop = 0;
  document.title = path;
  const selected = files.find(f => f.path === path);
  selectedMtime = selected ? selected.mtime_epoch : null;
}

async function main() {
  await refreshFiles();
  if (files.length > 0) await openFile(files[0].path);

  const es = new EventSource("/api/events");
  es.onmessage = async () => {
    await refreshFiles();
    if (!selectedPath) return;
    const current = files.find(f => f.path === selectedPath);
    if (current && current.mtime_epoch !== selectedMtime) {
      selectedMtime = current.mtime_epoch;
      await updatePreview();
    }
  };
}

document.getElementById("filter").addEventListener("input", renderFiles);
main();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js");
}
</script>
</body>
</html>
"""


@dataclasses.dataclass(frozen=True, slots=True)
class _FileEntry:
    """/api/filesで返すエントリ。"""

    path: str
    name: str
    mtime: str
    mtime_epoch: float


def _main(argv: list[str] | None = None) -> int:
    """エントリーポイント。"""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)
    root = pathlib.Path(args.root).expanduser().resolve()
    if not root.is_dir():
        logger.error("ディレクトリが見つかりません: %s", root)
        return 1

    _PlansHandler.root = root
    _PlansHandler.renderer = _make_md_renderer()
    _PlansHandler.hostname = socket.gethostname()

    observer = watchdog.observers.Observer()
    observer.schedule(_PlansEventHandler(root), str(root), recursive=True)
    observer.start()
    try:
        with http.server.ThreadingHTTPServer((args.host, args.port), _PlansHandler) as server:
            logger.info("Serving %s at http://%s:%s/", root, args.host, args.port)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                logger.info("停止します")
    finally:
        observer.stop()
        observer.join()
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を解析する。

    各オプションの既定値は環境変数経由で解決する。
    優先順位は CLI引数 > 環境変数 > 組み込み既定値。
    """
    parser = argparse.ArgumentParser(description="Serve ~/.claude/plans Markdown via local HTTP.")
    parser.add_argument(
        "--root",
        default=os.environ.get(_ENV_ROOT, _DEFAULT_ROOT),
        help=f"Markdownのルートディレクトリ（環境変数 {_ENV_ROOT}、既定: {_DEFAULT_ROOT}）",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get(_ENV_HOST, _DEFAULT_HOST),
        help=f"bindアドレス（環境変数 {_ENV_HOST}、既定: {_DEFAULT_HOST}）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get(_ENV_PORT, _DEFAULT_PORT)),
        help=f"ポート（環境変数 {_ENV_PORT}、既定: {_DEFAULT_PORT}）",
    )
    return parser.parse_args(argv)


def _list_files(root: pathlib.Path) -> list[_FileEntry]:
    """rootから`.md`ファイルを再帰的に探し、更新日時の降順で返す。"""
    tzinfo = datetime.datetime.now().astimezone().tzinfo
    collected: list[tuple[float, _FileEntry]] = []
    for path in root.rglob("*.md"):
        if not path.is_file():
            continue
        stat = path.stat()
        rel = path.relative_to(root).as_posix()
        mtime = datetime.datetime.fromtimestamp(stat.st_mtime, tz=tzinfo)
        entry = _FileEntry(
            path=rel,
            name=path.name,
            mtime=mtime.strftime("%Y/%m/%d %H:%M"),
            mtime_epoch=stat.st_mtime,
        )
        collected.append((stat.st_mtime, entry))
    collected.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _, entry in collected]


def _make_md_renderer() -> markdown_it.MarkdownIt:
    """Raw HTMLを無効化したMarkdownレンダラを返す。"""
    # CommonMarkプリセットは`html`オプションの既定値が`True`でraw HTMLを通すため、
    # 明示的に`False`へ上書きしてXSS経路を塞ぐ。表拡張は別途`enable("table")`で有効化する。
    return markdown_it.MarkdownIt("commonmark", {"html": False}).enable("table")


def _markdown_to_html(text: str, renderer: markdown_it.MarkdownIt | None = None) -> str:
    """Markdown文字列をHTMLへ変換する。"""
    md = renderer if renderer is not None else _make_md_renderer()
    return md.render(text)


def _resolve_under_root(root: pathlib.Path, rel: str) -> pathlib.Path | None:
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


class _PlansHandler(http.server.BaseHTTPRequestHandler):
    """`~/.claude/plans/`配下のMarkdownを提供するHTTPハンドラ。"""

    root: pathlib.Path
    renderer: markdown_it.MarkdownIt
    hostname: str = ""

    def do_GET(self) -> None:  # noqa: N802  # BaseHTTPRequestHandlerの命名規約に合わせる
        """GETリクエストを振り分ける。"""
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/":
            index_html = _INDEX_HTML.replace("__HOSTNAME__", html.escape(self.hostname))
            self._send("text/html; charset=utf-8", index_html.encode("utf-8"))
            return

        if parsed.path == "/static/markdown.css":
            self._send("text/css; charset=utf-8", _read_css().encode("utf-8"))
            return

        if parsed.path == "/favicon.svg":
            self._send("image/svg+xml; charset=utf-8", _FAVICON_SVG.encode("utf-8"))
            return

        if parsed.path == "/manifest.webmanifest":
            self._send("application/manifest+json; charset=utf-8", _MANIFEST_JSON.encode("utf-8"))
            return

        if parsed.path == "/sw.js":
            self._send("application/javascript; charset=utf-8", _SERVICE_WORKER_JS.encode("utf-8"))
            return

        if parsed.path == "/api/files":
            entries = _list_files(self.root)
            body = json.dumps([dataclasses.asdict(e) for e in entries], ensure_ascii=False).encode("utf-8")
            self._send("application/json; charset=utf-8", body)
            return

        if parsed.path == "/api/events":
            # SSEエンドポイント。Content-Lengthは指定せずストリーミング応答する。
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = _subscribe()
            try:
                while True:
                    try:
                        msg = q.get(timeout=15)
                        self.wfile.write(f"data: {msg}\n\n".encode())
                    except queue.Empty:
                        # タイムアウト時はSSEコメントでkeepaliveを送る
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except OSError:
                # クライアント切断時の各種OSError（BrokenPipeError・ConnectionResetError・ECONNABORTED等）を一括で受ける
                pass
            finally:
                _unsubscribe(q)
            return

        if parsed.path == "/api/file":
            query = urllib.parse.parse_qs(parsed.query)
            rel_values = query.get("path", [])
            if not rel_values:
                self.send_error(400, "path is required")
                return
            target = _resolve_under_root(self.root, rel_values[0])
            if target is None:
                self.send_error(404)
                return
            text = target.read_text(encoding="utf-8", errors="replace")
            rendered = _markdown_to_html(text, self.renderer)
            self._send("text/html; charset=utf-8", rendered.encode("utf-8"))
            return

        self.send_error(404)

    def log_message(  # noqa: A002  # 基底の仮引数名`format`に合わせる
        self,
        format: str,  # pylint: disable=redefined-builtin
        *args: object,
    ) -> None:
        """アクセスログの標準出力を抑制する。"""
        del format, args

    def _send(self, content_type: str, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _read_css() -> str:
    """配布物のCSSを読み込む。見つからなければフォールバックを返す。"""
    path = _resolve_css_path()
    if path is not None:
        return path.read_text(encoding="utf-8")
    return _FALLBACK_CSS


def _resolve_css_path() -> pathlib.Path | None:
    """リポジトリ内の`share/vscode/markdown.css`を返す。見つからなければNone。"""
    # dotfilesは通常~/dotfiles配下に置かれる。
    candidate = pathlib.Path.home() / "dotfiles" / "share" / "vscode" / "markdown.css"
    if candidate.is_file():
        return candidate
    # 念のためフォールバック。
    # editable installであればこのスクリプトがリポジトリ配下に置かれるため、こちらも解決できるはず。
    candidate = pathlib.Path(__file__).resolve().parents[1] / "share" / "vscode" / "markdown.css"
    if candidate.is_file():
        return candidate
    return None


if __name__ == "__main__":
    sys.exit(_main())

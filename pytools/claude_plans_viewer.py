"""Claude Codeの`~/.claude/plans/*.md`をブラウザで一覧・閲覧するローカルHTTPビューア。

SSHポートフォワード経由でWindows側のブラウザから参照することを想定し、
外部CDNに依存せずサーバー側でMarkdownをHTMLへ変換する。
Markdown→HTML変換はraw HTMLをエスケープする設定とし、
`~/.claude/plans/`配下の内容がスクリプトとして実行されないようにする。
"""

import argparse
import dataclasses
import datetime
import http.server
import json
import logging
import pathlib
import sys
import urllib.parse

import markdown_it

logger = logging.getLogger(__name__)

_DEFAULT_ROOT = "~/.claude/plans"
_DEFAULT_HOST = "127.0.0.1"
# VSCodeリモート開発拡張はLinux側の待受ポートをWindows側へ自動転送するため、
# Windowsローカル実行時に既定値が衝突する。Windowsのみ別値へずらして回避する。
_DEFAULT_PORT = 28875 if sys.platform == "win32" else 28765

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
    <div class="toolbar"><input id="filter" placeholder="filter..."></div>
    <div id="files"></div>
  </aside>
  <main><article id="preview">左の一覧からMarkdownを選択してください。</article></main>
</div>
<script>
let files = [];
let selectedPath = null;

function renderFiles() {
  const q = document.getElementById("filter").value.toLowerCase();
  const root = document.getElementById("files");
  root.innerHTML = "";
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
    root.appendChild(item);
  }
}

async function refreshFiles() {
  const res = await fetch("/api/files");
  files = await res.json();
  renderFiles();
}

async function openFile(path) {
  await refreshFiles();
  selectedPath = path;
  renderFiles();
  const res = await fetch("/api/file?path=" + encodeURIComponent(path));
  if (!res.ok) {
    document.getElementById("preview").textContent = "読み込みに失敗しました: " + res.status;
    return;
  }
  document.getElementById("preview").innerHTML = await res.text();
  document.title = path;
}

async function main() {
  await refreshFiles();
  if (files.length > 0) await openFile(files[0].path);
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

    with http.server.ThreadingHTTPServer((args.host, args.port), _PlansHandler) as server:
        logger.info("Serving %s at http://%s:%s/", root, args.host, args.port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info("停止します")
    return 0


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を解析する。"""
    parser = argparse.ArgumentParser(description="Serve ~/.claude/plans Markdown via local HTTP.")
    parser.add_argument("--root", default=_DEFAULT_ROOT, help=f"Markdownのルートディレクトリ（既定: {_DEFAULT_ROOT}）")
    parser.add_argument("--host", default=_DEFAULT_HOST, help=f"bindアドレス（既定: {_DEFAULT_HOST}）")
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT, help=f"ポート（既定: {_DEFAULT_PORT}）")
    return parser.parse_args(argv)


def _list_files(root: pathlib.Path) -> list[_FileEntry]:
    """rootから`.md`ファイルを再帰的に探し、更新日時の降順で返す。"""
    tzinfo = datetime.datetime.now().astimezone().tzinfo
    # mtime_epochは並べ替え用。最終結果には含めない。
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

    def do_GET(self) -> None:  # noqa: N802  # BaseHTTPRequestHandlerの命名規約に合わせる
        """GETリクエストを振り分ける。"""
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path == "/":
            self._send("text/html; charset=utf-8", _INDEX_HTML.encode("utf-8"))
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
            html = _markdown_to_html(text, self.renderer)
            self._send("text/html; charset=utf-8", html.encode("utf-8"))
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

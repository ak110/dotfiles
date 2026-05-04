# PYTHON_ARGCOMPLETE_OK
# pylint: disable=too-many-lines
# 多ホスト対応のため埋め込みSPA・リモートヘルパースクリプト・APIハンドラなどを単一ファイルへ集約しており、
# モジュール分割よりも近接配置の見通しを優先している。
"""Claude Codeの`~/.claude/plans/*.md`をブラウザで一覧・閲覧するローカルHTTPビューア。

SSHポートフォワード経由でWindows側のブラウザから参照することを想定し、
外部CDNに依存せずサーバー側でMarkdownをHTMLへ変換する。
Markdown→HTML変換はraw HTMLをエスケープする設定とし、
`~/.claude/plans/`配下の内容がスクリプトとして実行されないようにする。

`--remote-host`を複数指定すると、SSH経由で各ホストの`~/.claude/plans/`を
watchdog経由で監視し、ローカル分と同じ左ペインへ統合表示する。
リモート側は`uv run --no-project --script -`でヘルパーを実行し、
`python`／`python3`のPATH差を吸収する。

設定値の優先順位は「CLI引数 > 環境変数 > 組み込み既定値」とし、
環境ごとの差分は環境変数で吸収できるようにしている。

- `CLAUDE_PLANS_VIEWER_ROOT`: Markdownのルートディレクトリ
- `CLAUDE_PLANS_VIEWER_HOST`: bindアドレス
- `CLAUDE_PLANS_VIEWER_PORT`: 待受ポート
- `CLAUDE_PLANS_VIEWER_REMOTE_HOSTS`: コロン区切りのSSH接続先一覧

リモート監視はwatchdogによるpush方式を採用する。
ポーリング方式は対象ファイル数が増えた場合や低リソースホストでのCPU/SSH接続コストが
懸念されるため、SSH越しに長時間watchプロセスを常駐させて差分イベントだけを配信する設計としている。
"""

import argparse
import asyncio
import asyncio.subprocess as _async_subprocess
import base64
import contextlib
import dataclasses
import datetime
import html
import json
import logging
import os
import pathlib
import random
import signal
import socket
import stat
import subprocess
import sys
import typing

import hypercorn.asyncio
import hypercorn.config
import markdown_it
import pytilpack.sse
import quart
import watchdog.events
import watchdog.observers

from pytools._internal.cli import enable_completion

logger = logging.getLogger(__name__)

# debounce窓。watchdogは1回の書き込みで複数イベントを発火するため、時間窓で畳み込む。
_BROADCAST_DEBOUNCE_SEC = 0.3

# 読み取り由来の`FileOpenedEvent`・`FileClosedNoWriteEvent`は`/api/file`応答の`read_text`との間で
# feedback loopになるため除外する。`FileClosedEvent`は`IN_CLOSE_WRITE`（書き込み後クローズ）を表す。
_WATCHED_EVENT_TYPES: tuple[type[watchdog.events.FileSystemEvent], ...] = (
    watchdog.events.FileCreatedEvent,
    watchdog.events.FileModifiedEvent,
    watchdog.events.FileDeletedEvent,
    watchdog.events.FileMovedEvent,
    watchdog.events.FileClosedEvent,
)


_DEFAULT_ROOT = "~/.claude/plans"
_DEFAULT_HOST = "127.0.0.1"
# VSCodeリモート開発拡張はLinux側の待受ポートをWindows側へ自動転送するため、
# Windowsローカル実行時に既定値が衝突する。Windowsのみ別値へずらして回避する。
_DEFAULT_PORT = 28875 if sys.platform == "win32" else 28765

_ENV_ROOT = "CLAUDE_PLANS_VIEWER_ROOT"
_ENV_HOST = "CLAUDE_PLANS_VIEWER_HOST"
_ENV_PORT = "CLAUDE_PLANS_VIEWER_PORT"
_ENV_REMOTE_HOSTS = "CLAUDE_PLANS_VIEWER_REMOTE_HOSTS"

# SSH接続時に共通付与するオプション。
# `BatchMode=yes`で鍵認証失敗時にパスワードプロンプトでハングしないようにする。
_SSH_BASE_OPTIONS = ("-o", "BatchMode=yes")

# `read`サブコマンド（同期SSH呼び出し）のタイムアウト秒。
# ネットワーク不通時の検知遅延と通常応答の余裕を兼ねた値。
# `watch`サブコマンドの長時間ストリームには適用しない。
_SSH_TIMEOUT_SEC = 30.0

# `watch`用のSSH追加オプション。
# 接続確立を5秒、ServerAliveの組合せでネットワーク途絶を最大30秒程度で検知する。
_SSH_WATCH_OPTIONS = (
    "-o",
    "ConnectTimeout=5",
    "-o",
    "ServerAliveInterval=10",
    "-o",
    "ServerAliveCountMax=3",
)

# `_RemoteWatcher`の再接続バックオフ。指数増加・上限・ジッタ係数を一箇所にまとめる。
_REMOTE_BACKOFF_INITIAL_SEC = 1.0
_REMOTE_BACKOFF_MAX_SEC = 30.0
_REMOTE_BACKOFF_JITTER_RANGE = (0.8, 1.2)

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
# 図柄はtabler iconsのclipboard-listに白い背景を追加したもの。
# ベクターで配布するためPWAの192x192/512x512要件も1ファイルで満たせる。
_FAVICON_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none"
  class="icon icon-tabler icons-tabler-outline icon-tabler-clipboard-list">
  <path stroke="none" d="M0 0h24v24H0z" fill="none"/>
  <!-- white outline / backing -->
  <g stroke="white" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" fill="white">
    <path d="M9 5h-2a2 2 0 0 0 -2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2 -2v-12a2 2 0 0 0 -2 -2h-2"/>
    <path d="M9 5a2 2 0 0 1 2 -2h2a2 2 0 0 1 2 2a2 2 0 0 1 -2 2h-2a2 2 0 0 1 -2 -2"/>
  </g>
  <!-- original stroke -->
  <g stroke="#4f46e5" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" fill="none">
    <path d="M9 5h-2a2 2 0 0 0 -2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2 -2v-12a2 2 0 0 0 -2 -2h-2"/>
    <path d="M9 5a2 2 0 0 1 2 -2h2a2 2 0 0 1 2 2a2 2 0 0 1 -2 2h-2a2 2 0 0 1 -2 -2"/>
    <path d="M9 12l.01 0"/>
    <path d="M13 12l2 0"/>
    <path d="M9 16l.01 0"/>
    <path d="M13 16l2 0"/>
  </g>
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
# Chrome 89以降はインストール可能性の必須要件からfetchハンドラが外れたうえ、
# Chrome 93以降は本ファイルのようなno-opのfetchハンドラを「不要」と警告する仕様に変わった
# （DevToolsコンソールに "no-op fetch handler" 系の警告が出る）。
# オフライン動作は目標外のためfetchリスナー自体を登録せず、ネットワーク動作はブラウザ既定に委ねる。
_SERVICE_WORKER_JS = """\
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
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
  .meta {
    margin-top: 4px;
    font-size: 11px;
    color: #6b7280;
    display: flex;
    justify-content: space-between;
    gap: 8px;
  }
  .meta .host { word-break: break-all; }
  .meta .mtime { white-space: nowrap; }
  /* ホスト名横の接続状態バッジ。connectedのときは表示しない。 */
  .host-badge {
    display: none;
    margin-left: 4px;
    padding: 0 4px;
    font-size: 10px;
    color: #4b5563;
    background: #f3f4f6;
    border: 1px solid #d1d5db;
    border-radius: 3px;
    vertical-align: baseline;
  }
  .host-badge.connecting, .host-badge.disconnected { display: inline-block; }
  main { overflow: auto; box-sizing: border-box; }
  main .toolbar {
    position: sticky;
    top: 0;
    z-index: 1;
    display: flex;
    justify-content: flex-end;
    padding: 8px 16px;
    background: #ffffff;
    border-bottom: 1px solid #e6e6e6;
  }
  main .toolbar button {
    padding: 6px 12px;
    font-size: 13px;
    background: #ffffff;
    border: 1px solid #d1d5db;
    border-radius: 6px;
    cursor: pointer;
  }
  main .toolbar button:hover:not(:disabled) { background: #f3f4f6; }
  main .toolbar button:disabled { color: #9ca3af; cursor: default; }
  main article { max-width: 860px; margin: 0 auto; padding: 2rem; box-sizing: border-box; }
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
  <main>
    <div class="toolbar">
      <button id="copy-btn" type="button" disabled>Markdownをコピー</button>
    </div>
    <article id="preview">左の一覧からMarkdownを選択してください。</article>
  </main>
</div>
<script>
let files = [];
// ホスト名とパスの組で一意に識別する。
let selectedHost = null;
let selectedPath = null;
let selectedMtime = null;
// ホスト別の接続状態。connected / connecting / disconnected。
let hostStatus = {};

const HOST_BADGE_LABELS = {
  connecting: "再接続中",
  disconnected: "切断中",
};

function fileKey(file) { return file.host + "\\u0000" + file.path; }

function fileQuery(host, path) {
  return "host=" + encodeURIComponent(host) + "&path=" + encodeURIComponent(path);
}

function isSelected(file) {
  return selectedHost === file.host && selectedPath === file.path;
}

function renderFiles() {
  const q = document.getElementById("filter").value.toLowerCase();
  const root = document.getElementById("files");
  const aside = document.querySelector("aside");
  // 一覧再描画前にスクロール位置を退避し、再描画後に復元する
  const scrollTop = aside ? aside.scrollTop : 0;
  root.innerHTML = "";
  const frag = document.createDocumentFragment();
  for (const file of files) {
    // ホスト名・パスのいずれかに部分一致するもののみ表示する
    const haystack = (file.host + " " + file.path).toLowerCase();
    if (!haystack.includes(q)) continue;
    const item = document.createElement("div");
    item.className = "file" + (isSelected(file) ? " active" : "");
    item.onclick = () => openFile(file.host, file.path);
    const name = document.createElement("div");
    name.className = "name";
    name.textContent = file.path;
    const meta = document.createElement("div");
    meta.className = "meta";
    const hostSpan = document.createElement("span");
    hostSpan.className = "host";
    hostSpan.textContent = file.host;
    const status = hostStatus[file.host];
    if (status === "connecting" || status === "disconnected") {
      const badge = document.createElement("span");
      badge.className = "host-badge " + status;
      badge.textContent = HOST_BADGE_LABELS[status];
      hostSpan.appendChild(badge);
    }
    const mtimeSpan = document.createElement("span");
    mtimeSpan.className = "mtime";
    mtimeSpan.textContent = file.mtime;
    meta.appendChild(hostSpan);
    meta.appendChild(mtimeSpan);
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

async function refreshHostStatus() {
  // SSE取りこぼし対策。接続時／再接続時に必ず一度ずつ呼ぶ。
  const res = await fetch("/api/host-status");
  if (res.ok) {
    hostStatus = await res.json();
  }
}

async function updatePreview() {
  if (!selectedPath || !selectedHost) return;
  const main = document.querySelector("main");
  const scrollTop = main ? main.scrollTop : 0;
  const res = await fetch("/api/file?" + fileQuery(selectedHost, selectedPath));
  if (!res.ok) {
    document.getElementById("preview").textContent = "読み込みに失敗しました: " + res.status;
    return;
  }
  document.getElementById("preview").innerHTML = await res.text();
  if (main) main.scrollTop = scrollTop;
}

async function openFile(host, path) {
  await refreshFiles();
  selectedHost = host;
  selectedPath = path;
  renderFiles();
  const main = document.querySelector("main");
  const res = await fetch("/api/file?" + fileQuery(host, path));
  if (!res.ok) {
    document.getElementById("preview").textContent = "読み込みに失敗しました: " + res.status;
    if (main) main.scrollTop = 0;
    return;
  }
  document.getElementById("preview").innerHTML = await res.text();
  if (main) main.scrollTop = 0;
  document.title = host + ": " + path;
  const selected = files.find(f => f.host === host && f.path === path);
  selectedMtime = selected ? selected.mtime_epoch : null;
  document.getElementById("copy-btn").disabled = false;
}

async function resyncFromServer() {
  await refreshFiles();
  if (!selectedPath || !selectedHost) return;
  const current = files.find(f => f.host === selectedHost && f.path === selectedPath);
  if (current && current.mtime_epoch !== selectedMtime) {
    selectedMtime = current.mtime_epoch;
    await updatePreview();
  }
}

async function copySelectedRaw() {
  if (!selectedPath || !selectedHost) return;
  const btn = document.getElementById("copy-btn");
  const originalLabel = btn.dataset.label || btn.textContent;
  btn.dataset.label = originalLabel;
  try {
    const res = await fetch("/api/raw?" + fileQuery(selectedHost, selectedPath));
    if (!res.ok) throw new Error("status " + res.status);
    const text = await res.text();
    await navigator.clipboard.writeText(text);
    btn.textContent = "コピーしました";
  } catch (e) {
    btn.textContent = "コピーに失敗しました";
  }
  setTimeout(() => { btn.textContent = originalLabel; }, 2000);
}

// SSE接続はpagehideで能動的にcloseする。
// 放置するとページ遷移時にブラウザがchunked転送終端マーカー無しでストリームを切断し、
// DevToolsコンソールに ERR_INCOMPLETE_CHUNKED_ENCODING が記録されるため。
// bfcache復帰時はpageshowのevent.persisted=trueで検出して再接続することで、
// バックフォワード遷移後も自動反映を維持する（beforeunloadはbfcacheを無効化するため避ける）。
let eventSource = null;

async function handleSseMessage(event) {
  // 旧形式（dataが"refresh"文字列固定）と新形式（JSON）を両対応する。
  // JSON解析失敗時もrefresh扱いで再同期する（パース不能なフレームを握り潰さない）。
  let payload = null;
  try {
    payload = JSON.parse(event.data);
  } catch (_) {
    payload = null;
  }
  if (payload && payload.type === "host-status") {
    hostStatus[payload.host] = payload.status;
    renderFiles();
    return;
  }
  await resyncFromServer();
}

function connectEvents() {
  const es = new EventSource("/api/events");
  // EventSourceは接続断後にブラウザが自動再接続を行うが、再接続中に発生したSSEイベントは
  // 取り逃される。初回／再接続のいずれでもonopen時にホスト状態とファイル一覧を強制再同期する。
  es.onopen = async () => {
    await refreshHostStatus();
    await resyncFromServer();
  };
  es.onmessage = handleSseMessage;
  return es;
}

async function main() {
  await refreshHostStatus();
  await refreshFiles();
  if (files.length > 0) await openFile(files[0].host, files[0].path);

  eventSource = connectEvents();
}

window.addEventListener("pagehide", () => {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
});

window.addEventListener("pageshow", (event) => {
  if (event.persisted && !eventSource) {
    eventSource = connectEvents();
  }
});

document.getElementById("filter").addEventListener("input", renderFiles);
document.getElementById("copy-btn").addEventListener("click", copySelectedRaw);
main();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js");
}
</script>
</body>
</html>
"""

# SSH経由でリモート側へstdin投入する小さなヘルパースクリプト。
# listとread操作は標準ライブラリのみで完結する。watchサブコマンドはwatchdogに依存し、
# リモート側のuv inline metadataで自動解決させる。
# 操作種別と引数（base64エンコードした相対パス）はSSHコマンドのargv経由で渡す
# （`uv run --script -`はstdinをスクリプト本文として消費するため、操作引数のstdin同居はできない）。
# raw文字列リテラルで保持し、エスケープシーケンスを内部Pythonの解釈に委ねる。
_REMOTE_HELPER_SCRIPT = r'''# /// script
# requires-python = ">=3.10"
# dependencies = ["watchdog>=6.0.0"]
# ///
"""claude_plans_viewerのリモートホスト側ヘルパー。

操作種別はargvで受け取る:
  - list           : ~/.claude/plans配下の.mdファイル一覧をJSON文字列でstdoutへ出力する
  - read <b64>     : 指定相対パスのファイル本文をbase64エンコードしてstdoutへ出力する
  - watch          : ~/.claude/plans配下をwatchdogで監視し、行区切りJSONをstdoutへ流す

watch サブコマンドのプロトコル（行区切りJSON）:
  - {"type":"snapshot","entries":[{"path":..., "name":..., "mtime_epoch":...}, ...]}
  - {"type":"upsert","path":..., "name":..., "mtime_epoch":...}
  - {"type":"deleted","path":...}
  - {"type":"ping"}  ※30秒間隔。SSH切断時のSIGPIPE誘発で生存確認とする

スクリプト本体はSSHのstdin経由で渡される（`uv run --no-project --script -`）。
"""
import base64
import json
import pathlib
import sys
import threading
import time

ROOT = pathlib.Path.home() / ".claude" / "plans"

# 生存確認pingの送信間隔（秒）。短すぎるとトラフィックが増え、長すぎると切断検知が遅れる。
_PING_INTERVAL_SEC = 30.0


def _is_target_path(path: pathlib.Path) -> bool:
    if path.suffix != ".md":
        return False
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        return False
    return not any(p.startswith(".") for p in rel.parts)


def _scan_entries() -> list[dict]:
    entries: list[dict] = []
    if not ROOT.is_dir():
        return entries
    for path in ROOT.rglob("*.md"):
        if not path.is_file():
            continue
        if not _is_target_path(path):
            continue
        st = path.stat()
        entries.append({
            "path": path.relative_to(ROOT).as_posix(),
            "name": path.name,
            "mtime_epoch": st.st_mtime,
        })
    return entries


def list_files() -> None:
    json.dump(_scan_entries(), sys.stdout, ensure_ascii=False)


def read_file(rel_b64: str) -> None:
    rel = base64.b64decode(rel_b64).decode("utf-8")
    rel_path = pathlib.PurePosixPath(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        raise ValueError("invalid relative path")
    target = (ROOT / rel).resolve()
    target.relative_to(ROOT.resolve())
    if target.suffix != ".md" or not target.is_file():
        raise FileNotFoundError(rel)
    sys.stdout.write(base64.b64encode(target.read_bytes()).decode("ascii"))


def _emit(payload: dict) -> None:
    # 1行JSONとして出力し、SSH切断時のSIGPIPEを即時に拾えるよう毎回フラッシュする。
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def watch_files() -> int:
    # watchdogはローカル側`_PlansEventHandler`と同等のフィルタを適用する。
    # 読み取り由来の`FileOpenedEvent`/`FileClosedNoWriteEvent`は除外し、
    # `FileMovedEvent`はatomic-write rename対応のためdest側も判定対象に含める。
    import watchdog.events
    import watchdog.observers

    watched_types = (
        watchdog.events.FileCreatedEvent,
        watchdog.events.FileModifiedEvent,
        watchdog.events.FileDeletedEvent,
        watchdog.events.FileMovedEvent,
        watchdog.events.FileClosedEvent,
    )

    # `~/.claude/plans`未作成のホストでも監視を開始できるよう、無ければ作成する。
    # 作成失敗時はsnapshot空・ping待機のみで継続する。
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        sys.stderr.write(f"warn: cannot create {ROOT}: {e}\n")

    stop_event = threading.Event()

    def ping_loop() -> None:
        while not stop_event.wait(_PING_INTERVAL_SEC):
            try:
                _emit({"type": "ping"})
            except BrokenPipeError:
                stop_event.set()
                return

    class Handler(watchdog.events.FileSystemEventHandler):
        def on_any_event(self, event):
            if not isinstance(event, watched_types):
                return
            if event.is_directory:
                return
            src = pathlib.Path(str(event.src_path))
            if isinstance(event, watchdog.events.FileMovedEvent):
                dest = pathlib.Path(str(event.dest_path))
                src_ok = _is_target_path(src)
                dest_ok = _is_target_path(dest)
                if not (src_ok or dest_ok):
                    return
                # rename経路でsrcのみ`.md`の場合は元パス側を削除扱い、
                # destが`.md`なら新パス側をupsertする。
                if src_ok and not dest_ok:
                    _emit({"type": "deleted", "path": src.relative_to(ROOT).as_posix()})
                    return
                target = dest if dest_ok else src
                self._emit_upsert(target)
                return
            if not _is_target_path(src):
                return
            if isinstance(event, watchdog.events.FileDeletedEvent):
                _emit({"type": "deleted", "path": src.relative_to(ROOT).as_posix()})
                return
            self._emit_upsert(src)

        @staticmethod
        def _emit_upsert(path: pathlib.Path) -> None:
            try:
                st = path.stat()
            except OSError as e:
                sys.stderr.write(f"warn: stat failed for {path}: {e}\n")
                return
            _emit({
                "type": "upsert",
                "path": path.relative_to(ROOT).as_posix(),
                "name": path.name,
                "mtime_epoch": st.st_mtime,
            })

    observer = watchdog.observers.Observer()
    if ROOT.is_dir():
        observer.schedule(Handler(), str(ROOT), recursive=True)
        observer.start()
    # observer起動後にsnapshotを出すことで、起動以前の変更取りこぼしを排除する。
    _emit({"type": "snapshot", "entries": _scan_entries()})

    ping_thread = threading.Thread(target=ping_loop, daemon=True)
    ping_thread.start()

    # SIGPIPEはping_loopが捕捉してstop_eventを通じて停止経路に乗せる。
    try:
        while not stop_event.is_set():
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        if observer.is_alive():
            observer.stop()
            observer.join()
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        sys.stderr.write("missing operation\n")
        return 2
    op = sys.argv[1]
    if op == "list":
        list_files()
        return 0
    if op == "read":
        if len(sys.argv) < 3:
            sys.stderr.write("missing path\n")
            return 2
        read_file(sys.argv[2])
        return 0
    if op == "watch":
        return watch_files()
    sys.stderr.write(f"unknown operation: {op}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
'''


# SSHランナーの抽象シグネチャ。テストではfake実装を差し込み、本番は`_default_ssh_runner`を使う。
# (host, op, args) -> stdout (UTF-8文字列)。失敗時は例外を送出する。
SshRunner = typing.Callable[[str, str, list[str]], typing.Awaitable[str]]


@dataclasses.dataclass(frozen=True, slots=True)
class _FileEntry:
    """/api/filesで返すエントリ。"""

    host: str
    path: str
    name: str
    mtime: str
    mtime_epoch: float


@dataclasses.dataclass(slots=True)
class _BroadcastState:
    """SSE購読者集合・debounce状態・リモートホストキャッシュ・接続状態を束ねる。

    Quartアプリの`app.config`に入れて保持することでモジュールレベルの可変状態を避ける。
    """

    subscribers: set[asyncio.Queue[str]] = dataclasses.field(default_factory=set)
    lock: asyncio.Lock = dataclasses.field(default_factory=asyncio.Lock)
    debounce_task: asyncio.Task[None] | None = None
    loop: asyncio.AbstractEventLoop | None = None
    # ホスト名 -> 最後に観測した_FileEntry一覧。リモートwatchで更新される。
    remote_files: dict[str, list[_FileEntry]] = dataclasses.field(default_factory=dict)
    # 起動中のリモートwatchタスク群。after_servingで一括キャンセルする。
    remote_tasks: list[asyncio.Task[None]] = dataclasses.field(default_factory=list)
    # ホスト名 -> "connected"|"connecting"|"disconnected"。
    # フロントエンドのサイドペインに切断バッジを表示するための状態。
    host_status: dict[str, str] = dataclasses.field(default_factory=dict)


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
    """watchdogのイベントを受けてSSE購読者へ通知するハンドラ。

    watchdogコールバックはwatchdog側のスレッドで実行されるため、
    asyncioループへ`run_coroutine_threadsafe`でブリッジする。
    """

    def __init__(self, root: pathlib.Path, state: _BroadcastState) -> None:
        super().__init__()
        self.root = root
        self.state = state

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
        loop = self.state.loop
        if loop is None:
            # 起動直後にループ参照が未設定のイベントは取りこぼしてよい（直後のイベントで再通知される）。
            return
        asyncio.run_coroutine_threadsafe(_schedule_broadcast(self.state), loop)


async def _schedule_broadcast(state: _BroadcastState) -> None:
    """debounce窓を使って`_deliver_refresh`を遅延実行する。

    既にdebounceタスクが走っている場合は何もしない。
    タイマー中に追加イベントを無視することで時間窓で畳み込む。
    """
    async with state.lock:
        if state.debounce_task is not None and not state.debounce_task.done():
            return
        state.debounce_task = asyncio.create_task(_debounced_deliver(state))


async def _debounced_deliver(state: _BroadcastState) -> None:
    """debounce窓満了後に全購読者へ`refresh`を配信する。"""
    await asyncio.sleep(_BROADCAST_DEBOUNCE_SEC)
    await _deliver_refresh(state)


# SSE配信メッセージ型: refreshはサイドペイン全体の再同期、host-statusはバッジ更新を意味する。
# 旧クライアント互換のためサーバー側はJSON文字列を1行で配信する（クライアントは`type`不在を
# refreshとみなすフォールバックを持つ）。
_SSE_REFRESH_PAYLOAD = json.dumps({"type": "refresh"}, ensure_ascii=False)


async def _deliver_refresh(state: _BroadcastState) -> None:
    """全購読者へ`{"type":"refresh"}`を配信する。

    キューがすでに満杯の場合は新規通知を破棄する（既に未配信の通知がある状態のため、
    クライアントは次に取り出した時点で最新化される）。
    """
    await _broadcast(state, _SSE_REFRESH_PAYLOAD)


async def _deliver_host_status(state: _BroadcastState, host: str, status: str) -> None:
    """全購読者へ`{"type":"host-status","host":...,"status":...}`を配信する。

    SSE経路で取りこぼした場合は接続時に`/api/host-status`から再同期できるため、
    `Queue`満杯時の破棄も許容する。
    """
    payload = json.dumps({"type": "host-status", "host": host, "status": status}, ensure_ascii=False)
    await _broadcast(state, payload)


async def _broadcast(state: _BroadcastState, payload: str) -> None:
    async with state.lock:
        targets = list(state.subscribers)
    for q in targets:
        with contextlib.suppress(asyncio.QueueFull):
            q.put_nowait(payload)


async def _default_ssh_runner(host: str, op: str, args: list[str]) -> str:
    """SSH経由でリモートヘルパーを実行し、stdoutをUTF-8文字列で返す。

    ヘルパースクリプト本体はstdinに、操作種別と引数はSSHコマンドのargvに渡す。
    `python`／`python3`のPATH不整合を吸収するため、リモート実行は常に`uv run --script -`で行う。
    `subprocess.run`はブロッキングのため`asyncio.to_thread`でラップする。
    """
    cmd = [
        "ssh",
        *_SSH_BASE_OPTIONS,
        host,
        "uv",
        "run",
        "--no-project",
        "--script",
        "-",
        op,
        *args,
    ]
    proc = await asyncio.to_thread(
        subprocess.run,
        cmd,
        input=_REMOTE_HELPER_SCRIPT.encode("utf-8"),
        capture_output=True,
        timeout=_SSH_TIMEOUT_SEC,
        check=True,
    )
    # capture_output=Trueかつtext未指定のため`stdout`は実行時bytes固定。型注釈はAnyのため明示する。
    assert isinstance(proc.stdout, bytes)
    return proc.stdout.decode("utf-8")


def _make_file_entry(host: str, item: typing.Mapping[str, typing.Any]) -> _FileEntry:
    """リモートヘルパー由来のdictを`_FileEntry`に変換する。

    snapshot/upsertの両方から共通に使う。
    """
    mtime_epoch = float(item["mtime_epoch"])
    tzinfo = datetime.datetime.now().astimezone().tzinfo
    mtime = datetime.datetime.fromtimestamp(mtime_epoch, tz=tzinfo)
    return _FileEntry(
        host=host,
        path=str(item["path"]),
        name=str(item["name"]),
        mtime=mtime.strftime("%Y/%m/%d %H:%M"),
        mtime_epoch=mtime_epoch,
    )


async def _fetch_remote_file(host: str, rel: str, ssh_runner: SshRunner) -> str:
    """リモートホストの指定ファイル本文を取得する（UTF-8文字列）。

    `read`サブコマンドは1回限りの同期SSH呼び出しのため、watch用の常駐ストリームとは別経路で
    既存`SshRunner`抽象を使う。テストでは`_FakeSshRunner`に差し替える。
    """
    rel_b64 = base64.b64encode(rel.encode("utf-8")).decode("ascii")
    raw = await ssh_runner(host, "read", [rel_b64])
    return base64.b64decode(raw).decode("utf-8", errors="replace")


# 行ジェネレーターのプロトコル。テストではメモリー上のリストから供給するため、
# `asyncio.subprocess.Process.stdout`に依存しないインターフェースを使う。
_LineSource = typing.AsyncIterator[str]


class _RemoteWatcher:
    """1ホスト分のwatch接続ライフサイクルを担うクラス。

    `run()`の流れ:
      1. host_statusを"connecting"へ更新しSSE配信
      2. SSH+stdinでリモートwatchを起動
      3. stdoutの行を読みつつ`_handle_event`でキャッシュとSSEを更新
      4. snapshotを受信したら"connected"へ遷移
      5. EOF・例外で"disconnected"へ遷移し、指数バックオフ後に再接続
    """

    def __init__(
        self,
        host: str,
        state: _BroadcastState,
        helper_script: str = _REMOTE_HELPER_SCRIPT,
    ) -> None:
        self.host = host
        self.state = state
        self._helper_script = helper_script
        # 長時間維持された接続が切れた後の再接続時にバックオフが最大値から始まらないよう、
        # snapshot受信（接続成功）時にリセットする。
        self._backoff = _REMOTE_BACKOFF_INITIAL_SEC

    async def run(self) -> None:
        """無限ループで接続→ストリーム処理→バックオフ→再接続を行う。

        `asyncio.CancelledError`は再送出してタスク終了させる。
        それ以外の例外は warning ログに残し、`disconnected`遷移後にバックオフ再試行する。
        """
        while True:
            await self._set_status("connecting")
            # pylintのE1101 no-memberが`asyncio.subprocess.Process`に対して誤検出されるため、
            # `import asyncio.subprocess as _async_subprocess`で別名importを介して参照する。
            proc: _async_subprocess.Process | None = None
            try:
                proc = await self._connect()
                assert proc.stdout is not None
                await self._process_stream(_iter_stream_lines(proc.stdout))
                await self._set_status("disconnected")
            except asyncio.CancelledError:
                if proc is not None:
                    await _terminate_process(proc)
                raise
            except Exception as e:  # noqa: BLE001
                # 接続失敗・JSON解析失敗・stat不能などをまとめて拾い、ホスト単位で再接続継続する。
                logger.warning("リモートwatch失敗 host=%s: %s", self.host, e)
                await self._set_status("disconnected")
            finally:
                if proc is not None:
                    await _terminate_process(proc)
            # 指数バックオフ（上限・±20%ジッタ）。リトライ上限なし。
            jittered = self._backoff * random.uniform(*_REMOTE_BACKOFF_JITTER_RANGE)
            await asyncio.sleep(jittered)
            self._backoff = min(self._backoff * 2, _REMOTE_BACKOFF_MAX_SEC)

    async def _connect(self) -> _async_subprocess.Process:
        cmd = [
            "ssh",
            *_SSH_BASE_OPTIONS,
            *_SSH_WATCH_OPTIONS,
            self.host,
            "uv",
            "run",
            "--no-project",
            "--script",
            "-",
            "watch",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert proc.stdin is not None
        proc.stdin.write(self._helper_script.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            await proc.stdin.wait_closed()
        return proc

    async def _process_stream(self, lines: _LineSource) -> None:
        """行ストリームを受け取り、type別にハンドラへ振り分ける。

        テスト容易性のため`_LineSource`を引数化し、本番は`_iter_stream_lines`を渡す。
        """
        async for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as e:
                logger.warning("リモートwatch JSON解析失敗 host=%s: %s line=%r", self.host, e, line)
                continue
            await self._handle_event(event)

    async def _handle_event(self, event: typing.Mapping[str, typing.Any]) -> None:
        kind = event.get("type")
        if kind == "snapshot":
            entries = [_make_file_entry(self.host, item) for item in event.get("entries", [])]
            async with self.state.lock:
                self.state.remote_files[self.host] = entries
            await self._set_status("connected")
            # 接続成功時にバックオフをリセットし、次回切断後の再接続を初期値から始める。
            self._backoff = _REMOTE_BACKOFF_INITIAL_SEC
            await _deliver_refresh(self.state)
            return
        if kind == "upsert":
            entry = _make_file_entry(self.host, event)
            async with self.state.lock:
                cached = self.state.remote_files.get(self.host, [])
                cached = [e for e in cached if e.path != entry.path]
                cached.append(entry)
                self.state.remote_files[self.host] = cached
            await _deliver_refresh(self.state)
            return
        if kind == "deleted":
            path = str(event.get("path", ""))
            async with self.state.lock:
                cached = self.state.remote_files.get(self.host, [])
                self.state.remote_files[self.host] = [e for e in cached if e.path != path]
            await _deliver_refresh(self.state)
            return
        if kind == "ping":
            return
        logger.warning("リモートwatch 未知のイベント host=%s type=%r", self.host, kind)

    async def _set_status(self, status: str) -> None:
        async with self.state.lock:
            previous = self.state.host_status.get(self.host)
            self.state.host_status[self.host] = status
        if previous != status:
            await _deliver_host_status(self.state, self.host, status)


async def _iter_stream_lines(stream: asyncio.StreamReader) -> typing.AsyncIterator[str]:
    """`StreamReader`から1行ずつ取り出す非同期イテレータ。

    `readline()`はEOFで空bytesを返すため、その時点で打ち切る。
    """
    while True:
        chunk = await stream.readline()
        if not chunk:
            return
        yield chunk.decode("utf-8", errors="replace")


async def _terminate_process(proc: _async_subprocess.Process) -> None:
    """watch用subprocessを後始末する。

    既に終了していれば何もしない。
    `terminate`後に短時間waitし、ゾンビ化を避ける。
    """
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        proc.terminate()
    with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(proc.wait(), timeout=2.0)


def _make_md_renderer() -> markdown_it.MarkdownIt:
    """Raw HTMLを無効化したMarkdownレンダラを返す。"""
    # CommonMarkプリセットは`html`オプションの既定値が`True`でraw HTMLを通すため、
    # 明示的に`False`へ上書きしてXSS経路を塞ぐ。表拡張は別途`enable("table")`で有効化する。
    return markdown_it.MarkdownIt("commonmark", {"html": False}).enable("table")


def _markdown_to_html(text: str, renderer: markdown_it.MarkdownIt | None = None) -> str:
    """Markdown文字列をHTMLへ変換する。"""
    md = renderer if renderer is not None else _make_md_renderer()
    return md.render(text)


def _list_files(root: pathlib.Path, host: str) -> list[_FileEntry]:
    """rootから`.md`ファイルを再帰的に探し、更新日時の降順で返す。

    `host`は各エントリの`host`フィールドへ埋め込むラベル（通常はサーバー実行ホスト名）。
    """
    tzinfo = datetime.datetime.now().astimezone().tzinfo
    collected: list[tuple[float, _FileEntry]] = []
    for path in root.rglob("*.md"):
        if not path.is_file():
            continue
        st = path.stat()
        rel = path.relative_to(root).as_posix()
        mtime = datetime.datetime.fromtimestamp(st.st_mtime, tz=tzinfo)
        entry = _FileEntry(
            host=host,
            path=rel,
            name=path.name,
            mtime=mtime.strftime("%Y/%m/%d %H:%M"),
            mtime_epoch=st.st_mtime,
        )
        collected.append((st.st_mtime, entry))
    collected.sort(key=lambda pair: pair[0], reverse=True)
    return [entry for _, entry in collected]


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


def _is_safe_remote_relpath(rel: str) -> bool:
    """SSHヘルパーへ渡す前に相対パスのトラバーサルを事前検証する。

    リモート側でも検証するが、サーバー側で先に弾くことで不要なSSH呼び出しを避け、
    ログにも危険な相対パスが残らないようにする。
    """
    if not rel or rel.startswith("/") or "\\" in rel:
        return False
    parts = pathlib.PurePosixPath(rel).parts
    if any(p in ("", "..") for p in parts):
        return False
    return rel.endswith(".md")


def _resolve_request_target(local_host: str, allowed_remote_hosts: set[str]) -> tuple[str, str] | quart.Response:
    """`/api/file`・`/api/raw`共通: hostとpathを取り出して許可リスト検証する。

    `host`未指定時は`local_host`を採用する。許可リスト外のhostは400で拒否する
    （サーバーが0.0.0.0等で公開された場合に、クライアントが任意SSH先へ
    接続試行を誘発できないようにするため）。
    """
    rel = quart.request.args.get("path")
    if not rel:
        return quart.Response("path is required", status=400)
    host = quart.request.args.get("host")
    if host is None:
        host = local_host
    if host != local_host and host not in allowed_remote_hosts:
        return quart.Response("unknown host", status=400)
    return host, rel


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


async def _read_css() -> str:
    """配布物のCSSを読み込む。見つからなければフォールバックを返す。"""
    path = _resolve_css_path()
    if path is not None:
        # read_textはブロッキングI/Oのためスレッドプールで実行する。
        return await asyncio.to_thread(path.read_text, encoding="utf-8")
    return _FALLBACK_CSS


async def _subscribe(state: _BroadcastState) -> asyncio.Queue[str]:
    """SSE購読キューを生成して登録し返す。"""
    q: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
    async with state.lock:
        state.subscribers.add(q)
    return q


async def _unsubscribe(state: _BroadcastState, q: asyncio.Queue[str]) -> None:
    """購読キューを解除する。存在しない場合もエラーにしない。"""
    async with state.lock:
        state.subscribers.discard(q)


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
    parser.add_argument(
        "--remote-host",
        action="append",
        default=None,
        metavar="HOST",
        help=(f"SSH経由で監視するリモートホスト（複数指定可、`user@host`形式可、環境変数 {_ENV_REMOTE_HOSTS} はコロン区切り）"),
    )
    enable_completion(parser)
    args = parser.parse_args(argv)
    # `action="append"`はCLI未指定時にNone固定のため、ここで環境変数→既定値の順に解決する。
    if args.remote_host is None:
        env_hosts = os.environ.get(_ENV_REMOTE_HOSTS, "")
        args.remote_host = [h for h in env_hosts.split(":") if h] if env_hosts else []
    return args


async def _serve(app: quart.Quart, host: str, port: int) -> None:
    """hypercornでQuartアプリを起動する。

    シグナル（SIGINT/SIGTERM/SIGHUP）とstdin EOF（非PTY SSH切断検知）を
    単一のshutdown_triggerに集約し、SSE接続中の体感遅延を抑えるため
    graceful_timeoutを1.0秒へ短縮する。
    """
    config = hypercorn.config.Config()
    config.bind = [f"{host}:{port}"]
    # アクセスログの標準出力抑制（既存実装の`log_message`抑制に相当）。
    config.accesslog = None
    # SSE generatorは`CancelledError`を捕捉して`finally`で`_unsubscribe`するため、
    # 短時間で打ち切ってもデータ整合性は保たれる。Ctrl+C後の体感遅延を1秒以内へ抑える。
    config.graceful_timeout = 1.0

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    # hypercorn既定のsignal処理はSIGINT/SIGTERM/SIGBREAKのみでSIGHUPを含まない。
    # remote-plans経由の主経路はstdin EOF監視だが、プロセス監視ツール等からSIGHUPが
    # 到達した場合にもgraceful shutdownできるよう、3種を統一のshutdown_triggerに集約する。
    for sig_name in ("SIGINT", "SIGTERM", "SIGHUP"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, shutdown_event.set)

    stdin_task = asyncio.create_task(_watch_stdin_eof(shutdown_event))

    async def shutdown_trigger() -> None:
        await shutdown_event.wait()

    try:
        await hypercorn.asyncio.serve(app, config, shutdown_trigger=shutdown_trigger)
    finally:
        stdin_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stdin_task


async def _watch_stdin_eof(shutdown_event: asyncio.Event) -> None:
    """SSH経由（非PTY）で起動された場合の切断検知。

    OpenSSHのsshdは非PTYセッションのチャンネル閉鎖時に子プロセスへSIGHUPを送らないため
    （`session.c`の`session_close_by_channel`はPTYのときのみ`session_pty_cleanup`を呼ぶ）、
    代替としてsshdがリモートコマンドのstdinに割り当てるパイプのEOFを監視する。

    対象はstdinがFIFO（パイプ）のときのみ。TTY（対話起動）やCHR（`/dev/null`等の
    バックグラウンド起動）では誤発火を避けるため早期returnする。
    """
    try:
        st = os.fstat(sys.stdin.fileno())
    except (OSError, AttributeError, ValueError):
        return
    if not stat.S_ISFIFO(st.st_mode):
        return
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    try:
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    except (OSError, ValueError, NotImplementedError):
        return
    try:
        while await reader.read(1024):
            pass
    finally:
        shutdown_event.set()


def _main(argv: list[str] | None = None) -> int:
    """エントリーポイント。"""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # hypercornは`hypercorn.error`に独自フォーマット付きハンドラーを設定するが、
    # `propagate`は既定でTrueのためrootの`basicConfig`ハンドラーへも伝搬し二重出力になる。
    # hypercorn側のフォーマット（タイムスタンプ・PID付き）を活かすため、伝搬を止める。
    logging.getLogger("hypercorn.error").propagate = False
    args = _parse_args(argv)
    root = pathlib.Path(args.root).expanduser().resolve()
    if not root.is_dir():
        logger.error("ディレクトリが見つかりません: %s", root)
        return 1

    try:
        app = create_app(
            root,
            remote_hosts=args.remote_host,
        )
    except ValueError as e:
        logger.error("設定エラー: %s", e)
        return 1
    state: _BroadcastState = app.config["PLANS_STATE"]

    observer = watchdog.observers.Observer()
    observer.schedule(_PlansEventHandler(root, state), str(root), recursive=True)
    observer.start()
    try:
        logger.info("Serving %s at http://%s:%s/", root, args.host, args.port)
        if args.remote_host:
            logger.info("Remote hosts: %s (watchdog)", ", ".join(args.remote_host))
        asyncio.run(_serve(app, args.host, args.port))
    finally:
        observer.stop()
        observer.join()
    return 0


def create_app(
    root: pathlib.Path,
    hostname: str | None = None,
    remote_hosts: list[str] | None = None,
    ssh_runner: SshRunner | None = None,
) -> quart.Quart:
    """Quartアプリを生成する。

    `root`はMarkdownの探索対象ディレクトリ（resolve済み絶対パス）。
    `hostname`はトップページとローカル分の`host`ラベルへ埋め込むホスト名。
    `None`のとき`socket.gethostname()`を使う。
    `remote_hosts`が空でない場合、各ホストへSSH越しにwatchを起動して差分イベントを配信する。
    `ssh_runner=None`のときは`_default_ssh_runner`を使う（`/api/file`/`/api/raw`の
    リモート参照経路でのみ使用する。watch経路は`_RemoteWatcher`が直接asyncio subprocessを起動する）。
    """
    app = quart.Quart(__name__)
    renderer = _make_md_renderer()
    state = _BroadcastState()
    resolved_hostname = hostname if hostname is not None else socket.gethostname()
    remote_host_list = list(remote_hosts) if remote_hosts else []
    if resolved_hostname in remote_host_list:
        # `remote_files`のキーが衝突しローカル/リモートが上書きし合うため、起動時に拒絶する。
        raise ValueError("local hostname conflicts with --remote-host")
    allowed_remote_hosts = set(remote_host_list)
    runner: SshRunner = ssh_runner if ssh_runner is not None else _default_ssh_runner

    # 初期接続状態を設定する。ローカルは常にconnected、リモートはconnecting開始。
    state.host_status[resolved_hostname] = "connected"
    for host in remote_host_list:
        state.host_status[host] = "connecting"

    # app.configに格納してモジュールレベルの可変状態を避ける。
    # ルートハンドラからは`quart.current_app.config`経由で参照する。
    app.config["PLANS_ROOT"] = root
    app.config["PLANS_RENDERER"] = renderer
    app.config["PLANS_STATE"] = state
    app.config["PLANS_HOSTNAME"] = resolved_hostname
    app.config["PLANS_REMOTE_HOSTS"] = remote_host_list
    app.config["PLANS_SSH_RUNNER"] = runner

    @app.before_serving
    async def _capture_loop() -> None:
        # watchdogスレッドからの配信ブリッジに必要なイベントループ参照を保持する。
        state.loop = asyncio.get_running_loop()
        # リモートwatchタスクを起動する。test_client経由ではbefore_serving自体が
        # 発火しないため、テスト側は`_RemoteWatcher`を直接駆動する。
        for host in remote_host_list:
            task = asyncio.create_task(_RemoteWatcher(host, state).run())
            state.remote_tasks.append(task)

    @app.after_serving
    async def _cancel_remote_tasks() -> None:
        for task in state.remote_tasks:
            task.cancel()
        for task in state.remote_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        state.remote_tasks.clear()

    @app.get("/")
    async def index() -> quart.Response:
        body = _INDEX_HTML.replace("__HOSTNAME__", html.escape(resolved_hostname))
        return quart.Response(body, content_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/static/markdown.css")
    async def markdown_css() -> quart.Response:
        return quart.Response(await _read_css(), content_type="text/css; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/favicon.svg")
    async def favicon() -> quart.Response:
        return quart.Response(_FAVICON_SVG, content_type="image/svg+xml; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/manifest.webmanifest")
    async def manifest() -> quart.Response:
        return quart.Response(
            _MANIFEST_JSON,
            content_type="application/manifest+json; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/sw.js")
    async def service_worker() -> quart.Response:
        return quart.Response(
            _SERVICE_WORKER_JS,
            content_type="application/javascript; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/api/host-status")
    async def api_host_status() -> quart.Response:
        # SPA起動時の初期同期用。SSE取りこぼし時の救済経路としても使う。
        async with state.lock:
            snapshot = dict(state.host_status)
        body = json.dumps(snapshot, ensure_ascii=False)
        return quart.Response(body, content_type="application/json; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/api/files")
    async def api_files() -> quart.Response:
        # ローカル一覧はリモート集約と並列実行できるよう`asyncio.to_thread`経由で取得する。
        local_entries = await asyncio.to_thread(_list_files, root, resolved_hostname)
        async with state.lock:
            remote_entries: list[_FileEntry] = []
            for cached in state.remote_files.values():
                remote_entries.extend(cached)
        merged = local_entries + remote_entries
        merged.sort(key=lambda e: e.mtime_epoch, reverse=True)
        body = json.dumps([dataclasses.asdict(e) for e in merged], ensure_ascii=False)
        return quart.Response(body, content_type="application/json; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/api/file")
    async def api_file() -> quart.Response:
        resolved = _resolve_request_target(resolved_hostname, allowed_remote_hosts)
        if isinstance(resolved, quart.Response):
            return resolved
        host, rel = resolved
        if host == resolved_hostname:
            target = _resolve_under_root(root, rel)
            if target is None:
                return quart.Response("not found", status=404)
            # read_textはブロッキングI/Oのためスレッドプールで実行する。
            text = await asyncio.to_thread(target.read_text, encoding="utf-8", errors="replace")
        else:
            if not _is_safe_remote_relpath(rel):
                return quart.Response("invalid path", status=400)
            try:
                text = await _fetch_remote_file(host, rel, runner)
            except Exception as e:  # noqa: BLE001
                logger.warning("リモートファイル取得失敗 host=%s path=%s: %s", host, rel, e)
                return quart.Response("not found", status=404)
        rendered = _markdown_to_html(text, renderer)
        return quart.Response(rendered, content_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/api/raw")
    async def api_raw() -> quart.Response:
        # クライアントのコピーボタン用に生Markdownを返す。`/api/file`はHTMLレンダリング結果を返すため
        # 経路を分離し、`Cache-Control`扱いやテストを単純に保つ。
        resolved = _resolve_request_target(resolved_hostname, allowed_remote_hosts)
        if isinstance(resolved, quart.Response):
            return resolved
        host, rel = resolved
        if host == resolved_hostname:
            target = _resolve_under_root(root, rel)
            if target is None:
                return quart.Response("not found", status=404)
            text = await asyncio.to_thread(target.read_text, encoding="utf-8", errors="replace")
        else:
            if not _is_safe_remote_relpath(rel):
                return quart.Response("invalid path", status=400)
            try:
                text = await _fetch_remote_file(host, rel, runner)
            except Exception as e:  # noqa: BLE001
                logger.warning("リモートファイル取得失敗 host=%s path=%s: %s", host, rel, e)
                return quart.Response("not found", status=404)
        return quart.Response(text, content_type="text/markdown; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/api/events")
    async def api_events() -> quart.Response:
        @pytilpack.sse.generator()
        async def generate() -> typing.AsyncGenerator[pytilpack.sse.SSE, None]:
            q = await _subscribe(state)
            try:
                while True:
                    msg = await q.get()
                    # 既存クライアント(EventSourceの`onmessage`)が受け取るよう、
                    # event名を付けずdataのみで配信する。
                    yield pytilpack.sse.SSE(data=msg)
            finally:
                await _unsubscribe(state, q)

        return quart.Response(
            generate(),
            content_type="text/event-stream",
            headers={"Cache-Control": "no-store", "Connection": "keep-alive"},
        )

    return app


if __name__ == "__main__":
    sys.exit(_main())

"""SPA・PWA・リモートヘルパー等のインライン資産。

`INDEX_HTML`は`<style>`と`<script>`を別定数（`_INDEX_CSS`・`_INDEX_JS`）として持ち、
モジュール読み込み時に1つの文字列へ組み立てる。
JSが伸びても1定数の見通しを維持できるようにするための分割。

定数名は同一パッケージ内の兄弟モジュールから参照される前提のため、
underscore接頭辞を付けない（package-internalとして扱う）。
パッケージ外への公開可否は`__init__.py`の再export一覧で制御する。
`_INDEX_CSS`・`_INDEX_JS`は`INDEX_HTML`組立時の中間値として本モジュール内でのみ使うため、
underscore接頭辞付きのまま残す。

リバースプロキシ越し配信に対応するため、SPAおよびmanifestの絶対パス参照は
`__BASE_PATH_HTML__` / `__BASE_PATH_JS__`プレースホルダーで保持し、
`_app.py`側でリクエスト時に`request.root_path`を厳格に検証してから埋め込む。
"""

# share/vscode/markdown.cssが見つからないときの最小フォールバック。
# editable install前提では使われない想定だが、非editable配布や移動時に備えて持たせる。
FALLBACK_CSS = """\
body { font-family: system-ui, sans-serif; max-width: 860px; margin: 0 auto; padding: 2rem; color: #1a1a1a; }
pre { background: #1e1e1e; color: #d4d4d4; padding: 1rem; overflow: auto; border-radius: 8px; }
code { background: #f2f2f2; padding: 0.1em 0.3em; border-radius: 4px; }
table { border-collapse: collapse; }
th, td { border: 1px solid #d1d5db; padding: 6px 8px; }
"""

# タブ識別とPWAアイコンの双方でSSOTにするため、faviconはインラインSVGを単一定数で保持する。
# 図柄はtabler iconsのclipboard-listに白い背景を追加したもの。
# ベクターで配布するためPWAの192x192/512x512要件も1ファイルで満たせる。
FAVICON_SVG = """\
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
# X-Forwarded-Prefixを尊重するため、URLは`base_path`を組み立てて返す関数として保持する
# （静的JSONの文字列置換にすると`json.dumps`相当のエスケープ漏れを起こしやすいため）。
def build_manifest(base_path: str) -> dict[str, object]:
    """指定`base_path`に基づくPWA manifest辞書を返す。

    `base_path`は`_app.safe_base_path`で正規化済みの安全な前置文字列を想定する
    （空文字列または先頭スラッシュ＋連続スラッシュを含まない値）。
    """
    return {
        "name": "Claude plans",
        "short_name": "Plans",
        "start_url": f"{base_path}/",
        "display": "standalone",
        "theme_color": "#4f46e5",
        "background_color": "#ffffff",
        "icons": [
            {
                "src": f"{base_path}/favicon.svg",
                "sizes": "192x192 512x512 any",
                "type": "image/svg+xml",
                "purpose": "any maskable",
            }
        ],
    }


# PWAインストール可能性判定を満たす最小のservice worker。
# Chrome 89以降はインストール可能性の必須要件からfetchハンドラが外れたうえ、
# Chrome 93以降は本ファイルのようなno-opのfetchハンドラを「不要」と警告する仕様に変わった
# （DevToolsコンソールに "no-op fetch handler" 系の警告が出る）。
# オフライン動作は目標外のためfetchリスナー自体を登録せず、ネットワーク動作はブラウザ既定に委ねる。
SERVICE_WORKER_JS = """\
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
"""

# 左ペインにファイル一覧・右ペインにMarkdownプレビューを表示するSPAのCSS部分。
# 768px以下ではドロワー化し、ハンバーガーボタンとオーバーレイで開閉する。
_INDEX_CSS = """\
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
    display: flex;
    align-items: center;
    gap: 6px;
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
  main .toolbar .spacer { flex: 1; }
  main .toolbar .nav-btn { min-width: 36px; padding: 6px 10px; }
  /* デスクトップ既定ではハンバーガーとモバイル専用メタを隠す。 */
  #menu-btn { display: none; }
  #meta-mobile { display: none; }
  #drawer-backdrop { display: none; }
  main article { max-width: 860px; margin: 0 auto; padding: 2rem; box-sizing: border-box; }
  /* モバイル幅（タブレット縦含む）では左ペインをドロワー化する。 */
  @media (max-width: 768px) {
    #app { grid-template-columns: 1fr; }
    aside {
      position: fixed;
      top: 0;
      left: 0;
      width: 280px;
      max-width: 85vw;
      height: 100vh;
      z-index: 20;
      transform: translateX(-100%);
      transition: transform 0.2s ease-out;
      box-shadow: 0 0 8px rgba(0, 0, 0, 0.15);
    }
    aside.open { transform: translateX(0); }
    #drawer-backdrop {
      display: none;
      position: fixed;
      top: 0;
      left: 0;
      width: 100vw;
      height: 100vh;
      background: rgba(0, 0, 0, 0.4);
      z-index: 10;
    }
    #drawer-backdrop.open { display: block; }
    #menu-btn { display: inline-flex; align-items: center; justify-content: center; }
    #meta-mobile {
      display: block;
      padding: 8px 16px;
      font-size: 11px;
      color: #6b7280;
      background: #f9fafb;
      border-bottom: 1px solid #e6e6e6;
      word-break: break-all;
    }
    #meta-mobile .meta-host { font-weight: 600; color: #374151; }
    #meta-mobile .meta-path { display: block; margin-top: 2px; }
    #meta-mobile .meta-mtime { display: inline-block; margin-left: 8px; }
    #meta-mobile.empty { display: none; }
  }
"""

# クライアントサイドJS。
# 主な責務:
# - ファイル一覧の取得・描画・フィルタ
# - 選択中ファイルのプレビュー表示・コピー
# - SSE経由のリアルタイム更新（refresh/host-status）
# - モバイル時のドロワー開閉と上部メタ表示
# - ↑↓ナビゲーションボタンによる前後ファイル移動
_INDEX_JS = """\
// `__BASE_PATH_JS__`は`_app.py`が`json.dumps`で文字列リテラルとして埋め込む。
// X-Forwarded-Prefix未設定または不正値時は空文字列で、すべてのfetch/EventSource/SW登録に前置する。
const BASE_PATH = __BASE_PATH_JS__;

let files = [];
// ホスト名とパスの組で一意に識別する。
let selectedHost = null;
let selectedPath = null;
let selectedMtime = null;
// ホスト別の接続状態。connected / connecting / disconnected。
let hostStatus = {};
// renderFilesが最後に描画したエントリ列（フィルタ適用後）。
// ↑↓ナビゲーションは選択中項目の前後インデックスをこの列から算出する。
let visibleFiles = [];

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

function isMobileViewport() {
  return window.matchMedia("(max-width: 768px)").matches;
}

function setDrawerOpen(open) {
  const aside = document.querySelector("aside");
  const backdrop = document.getElementById("drawer-backdrop");
  if (!aside || !backdrop) return;
  aside.classList.toggle("open", open);
  backdrop.classList.toggle("open", open);
}

function updateMetaMobile() {
  const block = document.getElementById("meta-mobile");
  if (!block) return;
  if (!selectedHost || !selectedPath) {
    block.classList.add("empty");
    block.textContent = "";
    return;
  }
  const selected = files.find(f => f.host === selectedHost && f.path === selectedPath);
  block.classList.remove("empty");
  block.innerHTML = "";
  const hostSpan = document.createElement("span");
  hostSpan.className = "meta-host";
  hostSpan.textContent = selectedHost;
  const status = hostStatus[selectedHost];
  if (status === "connecting" || status === "disconnected") {
    const badge = document.createElement("span");
    badge.className = "host-badge " + status;
    badge.textContent = HOST_BADGE_LABELS[status];
    hostSpan.appendChild(badge);
  }
  const mtimeSpan = document.createElement("span");
  mtimeSpan.className = "meta-mtime";
  mtimeSpan.textContent = selected ? selected.mtime : "";
  const pathSpan = document.createElement("span");
  pathSpan.className = "meta-path";
  pathSpan.textContent = selectedPath;
  block.appendChild(hostSpan);
  block.appendChild(mtimeSpan);
  block.appendChild(pathSpan);
}

function updateNavButtons() {
  const prevBtn = document.getElementById("prev-btn");
  const nextBtn = document.getElementById("next-btn");
  if (!prevBtn || !nextBtn) return;
  if (!selectedHost || !selectedPath || visibleFiles.length === 0) {
    prevBtn.disabled = true;
    nextBtn.disabled = true;
    return;
  }
  const idx = visibleFiles.findIndex(f => f.host === selectedHost && f.path === selectedPath);
  // 選択中項目がフィルタ範囲外に出ているときは前後とも非活性にする。
  if (idx < 0) {
    prevBtn.disabled = true;
    nextBtn.disabled = true;
    return;
  }
  prevBtn.disabled = idx <= 0;
  nextBtn.disabled = idx >= visibleFiles.length - 1;
}

function navigateRelative(delta) {
  if (!selectedHost || !selectedPath || visibleFiles.length === 0) return;
  const idx = visibleFiles.findIndex(f => f.host === selectedHost && f.path === selectedPath);
  if (idx < 0) return;
  const next = idx + delta;
  if (next < 0 || next >= visibleFiles.length) return;
  const target = visibleFiles[next];
  openFile(target.host, target.path);
}

function renderFiles() {
  const q = document.getElementById("filter").value.toLowerCase();
  const root = document.getElementById("files");
  const aside = document.querySelector("aside");
  // 一覧再描画前にスクロール位置を退避し、再描画後に復元する
  const scrollTop = aside ? aside.scrollTop : 0;
  root.innerHTML = "";
  const frag = document.createDocumentFragment();
  visibleFiles = [];
  for (const file of files) {
    // ホスト名・パスのいずれかに部分一致するもののみ表示する
    const haystack = (file.host + " " + file.path).toLowerCase();
    if (!haystack.includes(q)) continue;
    visibleFiles.push(file);
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
  updateNavButtons();
  updateMetaMobile();
}

async function refreshFiles() {
  const res = await fetch(BASE_PATH + "/api/files");
  files = await res.json();
  renderFiles();
}

async function refreshHostStatus() {
  // SSE取りこぼし対策。接続時／再接続時に必ず一度ずつ呼ぶ。
  const res = await fetch(BASE_PATH + "/api/host-status");
  if (res.ok) {
    hostStatus = await res.json();
  }
}

async function updatePreview() {
  if (!selectedPath || !selectedHost) return;
  const main = document.querySelector("main");
  const scrollTop = main ? main.scrollTop : 0;
  const res = await fetch(BASE_PATH + "/api/file?" + fileQuery(selectedHost, selectedPath));
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
  // モバイル時のドロワーを自動で閉じる（ファイル選択操作の延長として）。
  if (isMobileViewport()) setDrawerOpen(false);
  const main = document.querySelector("main");
  const res = await fetch(BASE_PATH + "/api/file?" + fileQuery(host, path));
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
  updateNavButtons();
  updateMetaMobile();
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
    const res = await fetch(BASE_PATH + "/api/raw?" + fileQuery(selectedHost, selectedPath));
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
  const es = new EventSource(BASE_PATH + "/api/events");
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
document.getElementById("prev-btn").addEventListener("click", () => navigateRelative(-1));
document.getElementById("next-btn").addEventListener("click", () => navigateRelative(1));
document.getElementById("menu-btn").addEventListener("click", () => {
  const aside = document.querySelector("aside");
  setDrawerOpen(!(aside && aside.classList.contains("open")));
});
document.getElementById("drawer-backdrop").addEventListener("click", () => setDrawerOpen(false));
main();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register(BASE_PATH + "/sw.js");
}
"""

# Markdown→HTML変換はサーバー側で済ませて`/api/file`がHTMLを返すため、
# クライアント側はfetchした文字列をそのまま`<article>`へ挿入する。
# `__HOSTNAME__`は`create_app`がhtml.escape済みのホスト名で置換する。
# `__BASE_PATH_HTML__`は`create_app`が`html.escape(quote=True)`済みのbase_pathで置換する。
INDEX_HTML = (
    """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude plans</title>
<link rel="icon" type="image/svg+xml" href="__BASE_PATH_HTML__/favicon.svg">
<link rel="manifest" href="__BASE_PATH_HTML__/manifest.webmanifest">
<meta name="theme-color" content="#4f46e5">
<link rel="stylesheet" href="__BASE_PATH_HTML__/static/markdown.css">
<style>
"""
    + _INDEX_CSS
    + """</style>
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
      <button id="menu-btn" type="button" aria-label="ファイル一覧を開く">&#9776;</button>
      <button id="copy-btn" type="button" disabled>Markdownをコピー</button>
      <span class="spacer"></span>
      <button id="prev-btn" class="nav-btn" type="button" aria-label="前のファイル" disabled>&uarr;</button>
      <button id="next-btn" class="nav-btn" type="button" aria-label="次のファイル" disabled>&darr;</button>
    </div>
    <div id="meta-mobile" class="empty"></div>
    <article id="preview">左の一覧からMarkdownを選択してください。</article>
  </main>
  <div id="drawer-backdrop"></div>
</div>
<script>
"""
    + _INDEX_JS
    + """</script>
</body>
</html>
"""
)

# SSH経由でリモート側へstdin投入する小さなヘルパースクリプト。
# listとread操作は標準ライブラリのみで完結する。watchサブコマンドはwatchdogに依存し、
# リモート側のuv inline metadataで自動解決させる。
# 操作種別と引数（base64エンコードした相対パス）はSSHコマンドのargv経由で渡す
# （`uv run --script -`はstdinをスクリプト本文として消費するため、操作引数のstdin同居はできない）。
# raw文字列リテラルで保持し、エスケープシーケンスを内部Pythonの解釈に委ねる。
REMOTE_HELPER_SCRIPT = r'''# /// script
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
    # watchdogはローカル側`PlansEventHandler`と同等のフィルタを適用する。
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

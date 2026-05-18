"""SPA・PWA等のインライン資産。"""

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
  /* 末尾の段階展開トリガー。`hidden`属性付与時は完全非表示、外したときは高さ1pxの不可視ブロックとして
      IntersectionObserverの可視化検出に使う。一覧の行高に影響を与えない。 */
  #files-sentinel { height: 1px; margin: 0; padding: 0; }
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
  main article { max-width: 860px; margin: 0 auto; padding: 1rem; box-sizing: border-box; }
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
# - タブ復帰時の強制再同期（バックグラウンドthrottling対策。`visibilitychange`/`focus`の2系統）
# - 大量件数時の段階展開描画（番兵要素を`IntersectionObserver`で監視し、表示上限を100件単位で拡張）
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
// renderFilesが最後に描画したエントリ列（フィルタ適用後の全件）。
// ↑↓ナビゲーションは選択中項目の前後インデックスをこの列から算出する。
// DOM化対象は先頭から`visibleLimit`件のみで、超過分は番兵IntersectionObserverで段階拡張する。
let visibleFiles = [];

// 一覧描画件数の初期上限と拡張ステップ。
// `~/.claude/plans/`が数百件規模に達するとフィルタ入力・スクロール・差分更新の比例コストが顕在化するため、
// 初期はフィルタ後の先頭100件のみDOM化し、末尾の番兵が可視化されるたびに100件ずつ拡張する。
const VISIBLE_FILES_INITIAL = 100;
const VISIBLE_FILES_STEP = 100;
let visibleLimit = VISIBLE_FILES_INITIAL;
let sentinelObserver = null;

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
  // 遷移先がDOM未描画領域なら、必要分まで表示上限をステップ単位で拡張してから再描画する。
  // 段階展開（先頭`VISIBLE_FILES_INITIAL`件のみDOM化）と↑↓ナビゲーションの整合を取るための処理。
  if (next >= visibleLimit) {
    const required = next + 1;
    visibleLimit = Math.ceil(required / VISIBLE_FILES_STEP) * VISIBLE_FILES_STEP;
    renderFiles();
  }
  const target = visibleFiles[next];
  openFile(target.host, target.path);
}

function createFileItem(file) {
  // 1ファイルエントリのDOMノードを生成する。差分更新時の追加経路から呼ぶ。
  const item = document.createElement("div");
  item.dataset.key = fileKey(file);
  const name = document.createElement("div");
  name.className = "name";
  const meta = document.createElement("div");
  meta.className = "meta";
  const hostSpan = document.createElement("span");
  hostSpan.className = "host";
  const mtimeSpan = document.createElement("span");
  mtimeSpan.className = "mtime";
  meta.appendChild(hostSpan);
  meta.appendChild(mtimeSpan);
  item.appendChild(name);
  item.appendChild(meta);
  item.addEventListener("click", () => openFile(file.host, file.path));
  return item;
}

function updateFileItem(item, file) {
  // 既存ノードのテキスト・クラス・バッジを最新値で上書きする。
  item.className = "file" + (isSelected(file) ? " active" : "");
  const name = item.querySelector(".name");
  if (name) name.textContent = file.path;
  const hostSpan = item.querySelector(".host");
  if (hostSpan) {
    hostSpan.textContent = file.host;
    const status = hostStatus[file.host];
    if (status === "connecting" || status === "disconnected") {
      const badge = document.createElement("span");
      badge.className = "host-badge " + status;
      badge.textContent = HOST_BADGE_LABELS[status];
      hostSpan.appendChild(badge);
    }
  }
  const mtimeSpan = item.querySelector(".mtime");
  if (mtimeSpan) mtimeSpan.textContent = file.mtime;
}

function renderFiles() {
  // ファイル一覧を差分更新する。innerHTMLの全消去ではなく既存ノードを再利用することで、
  // ファイル数が多い環境でのフィルタ入力遅延・スクロール位置のジャンプを抑える。
  // DOM化対象はフィルタ後の先頭`visibleLimit`件のみ。未描画分は末尾の番兵を`IntersectionObserver`で
  // 検出して段階拡張する（数百件規模の差分更新コストを抑えるため）。
  const q = document.getElementById("filter").value.toLowerCase();
  const root = document.getElementById("files");
  const sentinel = document.getElementById("files-sentinel");
  visibleFiles = [];
  for (const file of files) {
    // ホスト名・パスのいずれかに部分一致するもののみ表示する
    const haystack = (file.host + " " + file.path).toLowerCase();
    if (!haystack.includes(q)) continue;
    visibleFiles.push(file);
  }
  const renderCount = Math.min(visibleLimit, visibleFiles.length);
  // 既存ノードを`data-key`で索引化し、再利用候補とする。最終的に未参照のノードは削除する。
  const existing = new Map();
  for (const node of root.children) {
    const key = node.dataset.key;
    if (key) existing.set(key, node);
  }
  let cursor = root.firstChild;
  for (let i = 0; i < renderCount; i++) {
    const file = visibleFiles[i];
    const key = fileKey(file);
    let item = existing.get(key);
    if (item) {
      existing.delete(key);
    } else {
      item = createFileItem(file);
    }
    updateFileItem(item, file);
    if (cursor === item) {
      cursor = item.nextSibling;
    } else {
      // 期待位置へ並べ替える。`insertBefore`は同一ノードを移動できるため重複処理は不要。
      root.insertBefore(item, cursor);
    }
  }
  // 残った未使用ノードを削除する。
  for (const node of existing.values()) {
    node.remove();
  }
  // 番兵は未描画分が残る場合だけ表示する。`hidden`属性を付ければ`display: none`になり、
  // IntersectionObserverの`isIntersecting`通知も止まる。
  if (sentinel) {
    sentinel.hidden = renderCount >= visibleFiles.length;
  }
  updateNavButtons();
  updateMetaMobile();
}

function setupSentinelObserver() {
  // 末尾の番兵が可視範囲に入ったら表示上限を1ステップ拡張する。
  // `root`をaside（一覧をスクロールするコンテナー）に指定して、ビューポートではなく
  // スクロールコンテナー内での可視性を判定する。
  // `rootMargin`で末尾到達前に先読みし、スクロール停止前に拡張が完了するようにする。
  const sentinel = document.getElementById("files-sentinel");
  if (!sentinel || sentinelObserver) return;
  const aside = document.querySelector("aside");
  sentinelObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      if (visibleLimit >= visibleFiles.length) continue;
      visibleLimit += VISIBLE_FILES_STEP;
      renderFiles();
    }
  }, { root: aside || null, rootMargin: "400px 0px" });
  sentinelObserver.observe(sentinel);
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
  // ファイル一覧はSSE経由で常時同期されているため、選択操作のたびに/api/filesを再取得する必要はない。
  // 余分な往復を省いてプレビュー描画までのレイテンシーを下げる。
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
  setupSentinelObserver();

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

// 強制再同期の本体。ホスト別接続状態とファイル一覧を順に取り直し、即時に追従させる。
async function forceResync() {
  await refreshHostStatus();
  await resyncFromServer();
}

// バックグラウンドthrottling対策。Chromium系のバックグラウンドタブはタイマー・SSEコールバックを
// 抑制するため、`EventSource.onmessage`のみに依存するとタブ復帰時に蓄積イベントの処理が体感数秒ずれ込む。
// `visibilitychange`で`visible`化した瞬間（タブ可視性変化）と`window.focus`時
// （PWAウィンドウ単独でフォーカスのみ変動するケース）の2系統で`forceResync`を発火する。
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") {
    forceResync();
  }
});

window.addEventListener("focus", () => {
  forceResync();
});

document.getElementById("filter").addEventListener("input", () => {
  // フィルタ条件が変わったら表示上限を初期値へ戻し、先頭から100件のみ再描画する。
  // 段階展開によって伸びた上限を引きずると、フィルタ後の少数結果に対しても無駄な走査が残るため。
  visibleLimit = VISIBLE_FILES_INITIAL;
  renderFiles();
});
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
# `__BASE_PATH_HTML__`は`create_app`が`html.escape(quote=True)`済みのbase_pathで置換する。
INDEX_HTML = (
    """<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude plans</title>
<link rel="icon" type="image/svg+xml" href="__BASE_PATH_HTML__/favicon.svg">
<!-- Basic認証配下でもmanifestが取得できるようcrossorigin="use-credentials"を付与する。
     未指定だとブラウザはmanifestをno-credentialsで取得し、Apache等の認証で401になり
     PWAインストール条件を満たせない（W3C Web App Manifest仕様準拠）。 -->
<link rel="manifest" href="__BASE_PATH_HTML__/manifest.webmanifest" crossorigin="use-credentials">
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
      <input id="filter" placeholder="filter...">
    </div>
    <div id="files"></div>
    <!-- 段階展開トリガー。`hidden`属性は描画件数が未描画分を残すときだけ外れる。 -->
    <div id="files-sentinel" hidden></div>
  </aside>
  <main>
    <div class="toolbar">
      <button id="menu-btn" type="button" aria-label="ファイル一覧を開く">&#9776;</button>
      <span class="spacer"></span>
      <button id="copy-btn" type="button" disabled>Markdownをコピー</button>
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

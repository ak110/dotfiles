// 3画面は同じドキュメントへ順に読み込まれるため、トップレベルの宣言を即時実行関数で囲んで
// 画面ごとにスコープを閉じる。`window.__atkScreens`への登録だけを外部へ公開する。
// 内側の字下げは、囲む前後の差分を比較できるよう元のままとする。
(() => {
// ページロード時の初期値はサーバーが`plans.html`のJSONブロックへ埋め込む。
// 資産ファイルは要求ごとに変わらないため、要求ごとに変わる値だけをHTML側から受け取る。
// 画面の入れ替えでJSONブロックの内容も差し替わるため、`mount`のたびに読み直す。
// X-Forwarded-Prefix未設定または不正値時は空文字列で、すべてのfetch/EventSource/SW登録に前置する。
let BASE_PATH = "";
// ホスト名 -> 保存元ID -> {portable_root, home, os_type, os_name}。旧単一root形式の
// {root, home, os_type, os_name}も受理し、保存元IDは画面へ表示しない。
let ROOT_DIRS = {};
// `host_info_update`受信のたびに加算するカウンタ。`refreshHostInfo`がfetch中に発生した
// SSE更新を検出し、古いスナップショットで新しい状態を上書きしないようにするために使う。
let hostInfoEventCounter = 0;

let files = [];
// ホスト名・保存元ID・root相対パスの組で一意に識別する。
let selectedHost = null;
let selectedSource = "";
let selectedPath = null;
let selectedMtime = null;
// ホスト別の接続状態。connected / connecting / disconnected。
let hostStatus = {};
// ホスト・保存元別のroot警告。source IDは表示せず、利用者が復旧判断できる本文だけを表示する。
let rootStatus = {};
// renderFilesが最後に描画したエントリ列（フィルタ適用後の全件）。
// ↑↓ナビゲーションは選択中項目の前後インデックスをこの列から算出する。
// DOM化対象は先頭から`visibleLimit`件のみで、超過分は番兵IntersectionObserverで段階拡張する。
let visibleFiles = [];
let mermaidLoadPromise = null;
let previewGeneration = 0;
let previewObjectUrls = new Set();
let searchGeneration = 0;
let searchTimer = null;
let serverSearchKeys = null;
const SEARCH_DEBOUNCE_MS = 300;
// SSHフォールバック検索の直列化は全クライアント横断で働くため、別クライアント・別タブの検索で
// 自身の要求が打ち切られて409になることがある。自身にとっては最新世代の要求なので、
// 失敗表示にせず間隔を空けて再試行する。
const SEARCH_SUPERSEDED_RETRIES = 3;
const SEARCH_SUPERSEDED_RETRY_MS = 200;

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

function fileSource(file) { return file.source_id || file.source || ""; }

function fileKey(file) {
  const source = fileSource(file);
  return source ? file.host + "\\u0000" + source + "\\u0000" + file.path : file.host + "\\u0000" + file.path;
}

function fileQuery(host, path, source) {
  const sourceQuery = source ? "&source=" + encodeURIComponent(source) : "";
  return "host=" + encodeURIComponent(host) + "&path=" + encodeURIComponent(path) + sourceQuery;
}

function rootEntries(host) {
  const value = ROOT_DIRS[host];
  if (!value) return {};
  if (value.root || value.portable_root || value.source_id) {
    return {[value.source_id || ""]: value};
  }
  return value;
}

function rootInfo(host, source) {
  return rootEntries(host)[source || ""] || null;
}

function updateCopyPathButton(host, source) {
  if (!source) {
    // 旧単一root形式との互換。source IDが無い要求では従来のホスト判定を使う。
    document.getElementById("copy-path-btn").disabled = !(host in ROOT_DIRS);
    return;
  }
  document.getElementById("copy-path-btn").disabled = !rootInfo(host, source);
}

function isSelected(file) {
  return selectedHost === file.host && selectedSource === fileSource(file) && selectedPath === file.path;
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
  const selected = files.find(f => f.host === selectedHost && fileSource(f) === selectedSource && f.path === selectedPath);
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
  const ctimeSpan = document.createElement("span");
  ctimeSpan.className = "meta-ctime";
  ctimeSpan.textContent = selected ? selected.ctime : "";
  const pathSpan = document.createElement("span");
  pathSpan.className = "meta-path";
  pathSpan.textContent = selectedPath;
  block.appendChild(hostSpan);
  block.appendChild(ctimeSpan);
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
  const idx = visibleFiles.findIndex(f => isSelected(f));
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
  const idx = visibleFiles.findIndex(f => isSelected(f));
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
  openFile(target.host, target.path, fileSource(target));
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
  const ctimeSpan = document.createElement("span");
  ctimeSpan.className = "ctime";
  meta.appendChild(hostSpan);
  meta.appendChild(ctimeSpan);
  item.appendChild(name);
  item.appendChild(meta);
  item.addEventListener("click", () => openFile(file.host, file.path, fileSource(file)));
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
  const ctimeSpan = item.querySelector(".ctime");
  if (ctimeSpan) ctimeSpan.textContent = file.ctime;
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
    // ホスト名・パスのクライアント一致と、サーバーによる本文一致の和集合を表示する。
    const haystack = (file.host + " " + file.path).toLowerCase();
    const matchesText = serverSearchKeys !== null && serverSearchKeys.has(fileKey(file));
    if (!haystack.includes(q) && !matchesText) continue;
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
  renderRootWarnings();
}

function renderRootWarnings() {
  const block = document.getElementById("root-warnings");
  if (!block) return;
  block.innerHTML = "";
  const messages = [];
  for (const host of Object.keys(rootStatus)) {
    for (const source of Object.keys(rootStatus[host] || {})) {
      const status = rootStatus[host][source];
      if (status && status.status === "warning" && status.message) {
        messages.push("計画rootを利用できません: " + status.message);
      }
    }
  }
  for (const message of messages) {
    const item = document.createElement("div");
    item.textContent = message;
    block.appendChild(item);
  }
  block.hidden = messages.length === 0;
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
  const res = await fetch(BASE_PATH + "/api/plans/files");
  files = await res.json();
  renderFiles();
}

async function searchFullText(query, generation) {
  const status = document.getElementById("search-status");
  status.textContent = "検索中";
  try {
    let res = null;
    for (let attempt = 0; ; attempt++) {
      res = await fetch(BASE_PATH + "/api/plans/search?q=" + encodeURIComponent(query));
      if (generation !== searchGeneration) return;
      if (res.status !== 409 || attempt >= SEARCH_SUPERSEDED_RETRIES) break;
      await new Promise((resolve) => setTimeout(resolve, SEARCH_SUPERSEDED_RETRY_MS));
      if (generation !== searchGeneration) return;
    }
    if (!res.ok) throw new Error("status " + res.status);
    const matched = await res.json();
    if (generation !== searchGeneration) return;
    serverSearchKeys = new Set(matched.map(fileKey));
    status.textContent = "";
    renderFiles();
  } catch (_) {
    if (generation !== searchGeneration) return;
    serverSearchKeys = new Set();
    status.textContent = "検索に失敗しました";
    renderFiles();
  }
}

function scheduleFullTextSearch() {
  const query = document.getElementById("filter").value.trim();
  const generation = ++searchGeneration;
  if (searchTimer !== null) clearTimeout(searchTimer);
  serverSearchKeys = query ? new Set() : null;
  document.getElementById("search-status").textContent = "";
  visibleLimit = VISIBLE_FILES_INITIAL;
  renderFiles();
  if (!query) return;
  searchTimer = setTimeout(() => {
    searchTimer = null;
    searchFullText(query, generation);
  }, SEARCH_DEBOUNCE_MS);
}

async function refreshHostStatus() {
  // SSE取りこぼし対策。接続時／再接続時に必ず一度ずつ呼ぶ。
  const res = await fetch(BASE_PATH + "/api/plans/host-status");
  if (res.ok) {
    hostStatus = await res.json();
  }
}

// fetch中に届いたSSE更新の検出時、単発の見送りだと当該更新が別ホスト由来でも
// スナップショット全体を破棄してしまい、取りこぼし救済がその1回で終わらない場合に収束が
// 遅れる。カウンタが安定するまで最大でこの回数だけfetchを繰り返す。
const HOST_INFO_REFRESH_MAX_ATTEMPTS = 3;

async function refreshHostInfo() {
  // SSE取りこぼし対策。接続時／再接続時に必ず一度ずつ呼ぶ。
  // fetch開始前後でhostInfoEventCounterを比較し、変化していれば当該フェッチのスナップショットは
  // 新しいSSE更新より古い可能性があるため、カウンタが安定するまで取得し直す。上限到達時は
  // 適用を見送る。ROOT_DIRSはSSE側の処理で既に正しく更新済みであり、次回呼び出し時に整合を取る。
  for (let attempt = 0; attempt < HOST_INFO_REFRESH_MAX_ATTEMPTS; attempt++) {
    const counterBefore = hostInfoEventCounter;
    let res = await fetch(BASE_PATH + "/api/plans/root-info");
    if (!res.ok) res = await fetch(BASE_PATH + "/api/plans/host-info");
    if (!res.ok) return;
    const info = await res.json();
    if (hostInfoEventCounter !== counterBefore) continue;
    for (const host of Object.keys(ROOT_DIRS)) {
      if (!(host in info)) delete ROOT_DIRS[host];
    }
    Object.assign(ROOT_DIRS, info);
    if (selectedHost) updateCopyPathButton(selectedHost, selectedSource);
    return;
  }
}

async function refreshRootStatus() {
  const res = await fetch(BASE_PATH + "/api/plans/root-status");
  if (res.ok) {
    rootStatus = await res.json();
    renderRootWarnings();
  }
}

async function applyPreviewHtml(html, scrollTop, generation) {
  if (generation !== previewGeneration) return;
  revokePreviewObjectUrls();
  const preview = document.getElementById("preview");
  preview.innerHTML = html;
  await renderDiagrams(preview, generation);
  if (generation !== previewGeneration) return;
  const main = document.querySelector("main");
  if (main) main.scrollTop = scrollTop;
}

async function renderDiagrams(preview, generation) {
  renderSvgDiagrams(preview.querySelectorAll(".diagram-svg"), generation);
  await renderMermaidDiagrams(preview.querySelectorAll(".mermaid-output"), generation);
}

function loadMermaid() {
  if (mermaidLoadPromise) return mermaidLoadPromise;
  mermaidLoadPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = BASE_PATH + "/static/vendor/mermaid.min.js";
    script.onload = () => {
      window.mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
      });
      resolve(window.mermaid);
    };
    script.onerror = () => reject(new Error("Mermaidの読み込みに失敗しました"));
    document.head.appendChild(script);
  });
  return mermaidLoadPromise;
}

async function renderMermaidDiagrams(nodes, generation) {
  if (nodes.length === 0) return;
  let api;
  try {
    api = await loadMermaid();
  } catch (error) {
    for (const node of nodes) showDiagramError(node.closest("figure"), error.message);
    return;
  }
  if (generation !== previewGeneration) return;
  for (const node of nodes) {
    const source = node.textContent;
    try {
      await api.run({nodes: [node]});
      disableMermaidNavigation(node);
      if (generation !== previewGeneration) return;
    } catch (_) {
      node.textContent = source;
      showDiagramError(node.closest("figure"), "Mermaid図を描画できませんでした");
    }
  }
}

function disableMermaidNavigation(node) {
  for (const anchor of node.querySelectorAll("a")) {
    anchor.removeAttribute("href");
    anchor.removeAttribute("xlink:href");
    anchor.removeAttribute("target");
  }
}

function renderSvgDiagrams(figures, generation) {
  for (const figure of figures) {
    const source = figure.querySelector(".diagram-source pre").textContent;
    const image = figure.querySelector(".svg-output");
    const url = URL.createObjectURL(new Blob([source], {type: "image/svg+xml"}));
    previewObjectUrls.add(url);
    image.onload = () => {
      if (generation !== previewGeneration) URL.revokeObjectURL(url);
    };
    image.onerror = () => {
      if (generation === previewGeneration) {
        showDiagramError(figure, "SVG図を描画できませんでした");
      }
    };
    image.src = url;
  }
}

function revokePreviewObjectUrls() {
  for (const url of previewObjectUrls) URL.revokeObjectURL(url);
  previewObjectUrls.clear();
}

function showDiagramError(figure, message) {
  let error = figure.querySelector(".diagram-error");
  if (!error) {
    error = document.createElement("p");
    error.className = "diagram-error";
    figure.appendChild(error);
  }
  error.textContent = message;
  error.hidden = false;
}

async function updatePreview() {
  if (!selectedPath || !selectedHost) return;
  const main = document.querySelector("main");
  const scrollTop = main ? main.scrollTop : 0;
  const generation = ++previewGeneration;
  const res = await fetch(BASE_PATH + "/api/plans/file?" + fileQuery(selectedHost, selectedPath, selectedSource));
  if (generation !== previewGeneration) return;
  if (!res.ok) {
    document.getElementById("preview").textContent = "読み込みに失敗しました: " + res.status;
    return;
  }
  const html = await res.text();
  if (generation !== previewGeneration) return;
  await applyPreviewHtml(html, scrollTop, generation);
  if (generation !== previewGeneration) return;
}

async function openFile(host, path, source) {
  // ファイル一覧はSSE経由で常時同期されているため、選択操作のたびに/api/filesを再取得する必要はない。
  // 余分な往復を省いてプレビュー描画までのレイテンシーを下げる。
  selectedHost = host;
  selectedSource = source || "";
  selectedPath = path;
  document.title = host + ": " + path;
  const selected = source
    ? files.find(f => f.host === host && fileSource(f) === selectedSource && f.path === path)
    : files.find(f => f.host === host && f.path === path);
  if (selected) selectedSource = fileSource(selected);
  selectedMtime = selected ? selected.mtime_epoch : null;
  document.getElementById("copy-btn").disabled = false;
  // 選択rootの情報未取得（リモート接続確立前）はdisabled維持する。
  updateCopyPathButton(host, selectedSource);
  renderFiles();
  // モバイル時のドロワーを自動で閉じる（ファイル選択操作の延長として）。
  if (isMobileViewport()) setDrawerOpen(false);
  const main = document.querySelector("main");
  const generation = ++previewGeneration;
  const res = await fetch(BASE_PATH + "/api/plans/file?" + fileQuery(host, path, selectedSource));
  if (generation !== previewGeneration) return;
  if (!res.ok) {
    document.getElementById("preview").textContent = "読み込みに失敗しました: " + res.status;
    if (main) main.scrollTop = 0;
    return;
  }
  const html = await res.text();
  if (generation !== previewGeneration) return;
  await applyPreviewHtml(html, 0, generation);
}

async function resyncFromServer() {
  await refreshFiles();
  if (!selectedPath || !selectedHost) return;
  const current = files.find(f => isSelected(f));
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
    const res = await fetch(BASE_PATH + "/api/plans/raw?" + fileQuery(selectedHost, selectedPath, selectedSource));
    if (!res.ok) throw new Error("status " + res.status);
    const text = await res.text();
    await navigator.clipboard.writeText(text);
    btn.textContent = "コピーしました";
  } catch (e) {
    btn.textContent = "コピーに失敗しました";
  }
  setTimeout(() => { btn.textContent = originalLabel; }, 2000);
}

async function copySelectedPath() {
  if (!selectedPath || !selectedHost) return;
  const info = rootInfo(selectedHost, selectedSource);
  if (!info) return;
  const btn = document.getElementById("copy-path-btn");
  const originalLabel = btn.dataset.label || btn.textContent;
  btn.dataset.label = originalLabel;
  // ホスト種別に応じてチルダ表記（POSIX）または%USERPROFILE%表記（Windows）へ変換する。
  // 置換基準はinfo.homeとする（info.rootはplansディレクトリ等のroot直下パスであり
  // ホームディレクトリと一致しない場合があるため）。
  let absolutePath;
  if (info.portable_root) {
    absolutePath = info.portable_root + "/" + selectedPath;
  } else if (info.os_type === "nt") {
    const winRoot = info.root.split("/").join("\\\\");
    const winHome = info.home.split("/").join("\\\\");
    const winRelative = selectedPath.split("/").join("\\\\");
    absolutePath = (winRoot + "\\\\" + winRelative).replace(winHome, "%USERPROFILE%");
  } else {
    absolutePath = (info.root + "/" + selectedPath).replace(info.home, "~");
  }
  try {
    await navigator.clipboard.writeText(absolutePath);
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
  if (payload && payload.type === "host_info_update") {
    hostInfoEventCounter++;
    if (payload.info === null) {
      delete ROOT_DIRS[payload.host];
    } else {
      ROOT_DIRS[payload.host] = payload.info;
    }
    if (selectedHost === payload.host) {
      updateCopyPathButton(selectedHost, selectedSource);
    }
    return;
  }
  if (payload && payload.type === "root_info_update") {
    hostInfoEventCounter++;
    if (payload.info === null) {
      delete ROOT_DIRS[payload.host];
    } else {
      ROOT_DIRS[payload.host] = payload.info;
    }
    if (selectedHost === payload.host) updateCopyPathButton(selectedHost, selectedSource);
    return;
  }
  if (payload && payload.type === "root-status") {
    rootStatus[payload.host] = payload.status || {};
    renderRootWarnings();
    return;
  }
  await resyncFromServer();
}

function connectEvents() {
  const es = new EventSource(BASE_PATH + "/api/plans/events");
  // EventSourceは接続断後にブラウザが自動再接続するが、再接続中に発生したSSEイベントは
  // 取り逃される。初回／再接続のいずれでもonopen時にホスト状態とファイル一覧を強制再同期する。
  es.onopen = async () => {
    await refreshHostStatus();
    await refreshHostInfo();
    await refreshRootStatus();
    await resyncFromServer();
  };
  es.onmessage = handleSseMessage;
  return es;
}

// bfcache復帰後も自動反映を維持するため、`pagehide`で能動的にcloseし`pageshow`で再接続する。
function handlePageHide() {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
}

function handlePageShow(event) {
  if (event.persisted && !eventSource) {
    eventSource = connectEvents();
  }
}

// 強制再同期の本体。ホスト別接続状態とファイル一覧を順に取り直し、即時に追従させる。
async function forceResync() {
  await refreshHostStatus();
  await refreshHostInfo();
  await refreshRootStatus();
  await resyncFromServer();
}

// バックグラウンドthrottling対策。Chromium系のバックグラウンドタブはタイマー・SSEコールバックを
// 抑制するため、`EventSource.onmessage`のみに依存するとタブ復帰時に蓄積イベントの処理が体感数秒ずれ込む。
// `visibilitychange`で`visible`化した瞬間（タブ可視性変化）と`window.focus`時
// （PWAウィンドウ単独でフォーカスのみ変動するケース）の2系統で`forceResync`を発火する。
function handleVisibilityChange() {
  if (document.visibilityState === "visible") {
    forceResync();
  }
}

function handleWindowFocus() {
  forceResync();
}

function bindScreenEvents() {
  document.getElementById("filter").addEventListener("input", () => {
    // フィルタ条件が変わったら表示上限を初期値へ戻し、先頭から100件のみ再描画する。
    // 段階展開によって伸びた上限を引きずると、フィルタ後の少数結果に対しても無駄な走査が残るため。
    scheduleFullTextSearch();
  });
  document.getElementById("copy-btn").addEventListener("click", copySelectedRaw);
  document.getElementById("copy-path-btn").addEventListener("click", copySelectedPath);
  document.getElementById("prev-btn").addEventListener("click", () => navigateRelative(-1));
  document.getElementById("next-btn").addEventListener("click", () => navigateRelative(1));
  document.getElementById("menu-btn").addEventListener("click", () => {
    const aside = document.querySelector("aside");
    setDrawerOpen(!(aside && aside.classList.contains("open")));
  });
  document.getElementById("drawer-backdrop").addEventListener("click", () => setDrawerOpen(false));
  document.getElementById("preview").addEventListener("click", (event) => {
    // 付属計画は計画一覧に載らないため、サーバーが本文へ付与したリンクだけが選択経路になる。
    // 本文は表示のたびに差し替わるので、個別ノードではなく親要素への委譲で受け取る。
    const link = event.target.closest("a[data-plan-path]");
    if (!link) return;
    event.preventDefault();
    openFile(selectedHost, link.dataset.planPath, selectedSource);
  });
}

async function mount() {
  const bootstrap = JSON.parse(document.getElementById("plans-bootstrap").textContent);
  BASE_PATH = bootstrap.base_path;
  ROOT_DIRS = bootstrap.root_dirs;
  bindScreenEvents();
  window.addEventListener("pagehide", handlePageHide);
  window.addEventListener("pageshow", handlePageShow);
  window.addEventListener("focus", handleWindowFocus);
  document.addEventListener("visibilitychange", handleVisibilityChange);

  await refreshHostStatus();
  await refreshHostInfo();
  await refreshRootStatus();
  await refreshFiles();
  if (files.length > 0) await openFile(files[0].host, files[0].path, fileSource(files[0]));
  setupSentinelObserver();

  eventSource = connectEvents();
}

function unmount() {
  window.removeEventListener("pagehide", handlePageHide);
  window.removeEventListener("pageshow", handlePageShow);
  window.removeEventListener("focus", handleWindowFocus);
  document.removeEventListener("visibilitychange", handleVisibilityChange);
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  if (sentinelObserver) {
    sentinelObserver.disconnect();
    sentinelObserver = null;
  }
  if (searchTimer !== null) {
    clearTimeout(searchTimer);
    searchTimer = null;
  }
  revokePreviewObjectUrls();
  files = [];
  visibleFiles = [];
  visibleLimit = VISIBLE_FILES_INITIAL;
  serverSearchKeys = null;
  hostStatus = {};
  rootStatus = {};
  selectedHost = null;
  selectedSource = "";
  selectedPath = null;
  selectedMtime = null;
}

window.__atkScreens = window.__atkScreens || {};
window.__atkScreens.plans = {mount, unmount};
})();

// 3画面（ワークアイテム・計画ファイル・セッション）が同じ役割で使う処理と設定値。
// 画面ごとの差（取得するAPI、一覧の項目の描画、画面固有の操作）は、各画面が引数の関数として渡す。

// 要求ごとに変わる値はHTMLへ埋め込んだJSONブロックから読む。資産ファイルは要求ごとに変わらないためである。
const bootstrap = readBootstrap();

// X-Forwarded-Prefix未設定または不正値時は空文字列で、すべてのfetch/EventSourceに前置する。
export const BASE_PATH = typeof bootstrap.base_path === "string" ? bootstrap.base_path : "";

function readBootstrap() {
  const element = document.getElementById("serve-bootstrap");
  return element ? JSON.parse(element.textContent) : {};
}

const DEFAULT_STALL_MS = 45000;

// SSEの無通信を判定する時間。サーバーがheartbeat間隔の3回分として埋め込む。
function stallMs() {
  const value = bootstrap.stall_ms;
  return Number.isFinite(value) && value > 0 ? value : DEFAULT_STALL_MS;
}

// SSEを購読する。接続の確立、heartbeat、通知のいずれも届かない時間が閾値を超えたら再接続する。
// サーバーのheartbeatはスクリプトから観測できる名前付きイベント`heartbeat`で届く。
// コメント行のheartbeatは`EventSource`がスクリプトへ渡さないため、接続が途中で止まっても検知できない。
// `handlers`は`open`・`message`・`error`と、画面が受け取る名前付きイベント名から処理への対応を持つ。
// 再接続した接続でも同じ処理を登録し、確立時の`open`で各画面が再同期する。
// bfcacheへ入る前に接続を閉じ、復帰時に再接続する。ページ遷移時にブラウザーがストリームを終端なしで
// 切断するとコンソールにERR_INCOMPLETE_CHUNKED_ENCODINGが残るためである。`beforeunload`はbfcacheを無効にするため使わない。
export function connectEvents(path, handlers) {
  const url = BASE_PATH + path;
  const limit = stallMs();
  let source = null;
  let lastSeen = 0;
  let timer = null;
  const touch = () => { lastSeen = Date.now(); };

  function open() {
    source = new EventSource(url);
    touch();
    source.addEventListener("heartbeat", touch);
    for (const [name, handler] of Object.entries(handlers)) {
      source.addEventListener(name, (event) => {
        if (name !== "error") touch();
        handler(event);
      });
    }
  }

  function check() {
    if (!source) return;
    if (source.readyState === EventSource.CLOSED || Date.now() - lastSeen > limit) {
      source.close();
      open();
    }
  }

  function close() {
    clearInterval(timer);
    timer = null;
    if (source) source.close();
    source = null;
  }

  function start() {
    if (source) return;
    open();
    timer = setInterval(check, Math.max(100, Math.min(5000, limit / 3)));
  }

  start();
  globalThis.addEventListener?.("pagehide", close);
  globalThis.addEventListener?.("pageshow", (event) => { if (event.persisted) start(); });
  return {close, reopen: start};
}

// 名前の無い`message`イベントの本文をJSONとして解析し、`type`ごとの処理へ振り分ける。
// 解析できない本文と処理を持たない種類は`fallback`へ渡し、`fallback`が無ければ処理しない。
export function handleSseMessage(event, handlers, fallback = null) {
  let payload = null;
  try {
    payload = JSON.parse(event.data);
  } catch (_) {
    payload = null;
  }
  const handler = payload ? handlers[payload.type] : undefined;
  if (handler) return handler(payload);
  return fallback ? fallback(payload) : undefined;
}

// 選択中の項目と候補が同じ項目かを返す。選択が持つ全ての識別項目の一致で判定する。
export function isSelected(selection, candidate) {
  return Boolean(selection) && Object.keys(selection).every((key) => selection[key] === candidate[key]);
}

// 一覧の前後へ移動するボタンを、表示中の一覧での選択位置から有効・無効にする。
// 選択が表示中の一覧に無い場合は両方を無効にする。
export function updateNavButtons(previousButton, nextButton, items, isCurrent) {
  const index = items.findIndex(isCurrent);
  previousButton.disabled = index <= 0;
  nextButton.disabled = index < 0 || index >= items.length - 1;
}

// 表示中の一覧で選択位置から`delta`だけ離れた項目を`open`へ渡す。選択が一覧に無い場合と範囲外は何もしない。
export function navigateRelative(items, isCurrent, delta, open) {
  const index = items.findIndex(isCurrent);
  if (index < 0) return;
  const position = index + delta;
  if (position < 0 || position >= items.length) return;
  open(items[position], position);
}

// 一覧のコンテナーを`items`の順へ差分更新する。`render(item, existing)`は既存の項目のノード
// （無ければ`null`）を受け取り、表示するノードを返す。既存のノードを再利用すると、項目が多い一覧でも
// 入力中のフィルターやスクロール位置を乱さない。`data-key`を持たない子（番兵など）は対象にしない。
export function renderList(container, items, keyOf, render) {
  const existing = new Map();
  for (const node of container.children) {
    if (node.dataset.key) existing.set(node.dataset.key, node);
  }
  let cursor = container.firstChild;
  for (const item of items) {
    const key = keyOf(item);
    const reused = existing.get(key) ?? null;
    existing.delete(key);
    const node = render(item, reused);
    node.dataset.key = key;
    if (reused && reused !== node) {
      if (reused === cursor) cursor = reused.nextSibling;
      reused.remove();
    }
    if (node === cursor) cursor = node.nextSibling;
    else container.insertBefore(node, cursor);
  }
  for (const node of existing.values()) node.remove();
}

// 警告の行を表示する。行が無ければ警告欄を隠す。
export function renderWarnings(element, lines) {
  element.replaceChildren(...lines.map((line) => {
    const row = document.createElement("div");
    row.textContent = line;
    return row;
  }));
  element.hidden = lines.length === 0;
}

// 計画ファイル画面とセッション画面の左ペインを、狭い幅でドロワーとして開閉する。
export function setDrawerOpen(screenName, open) {
  const screen = document.getElementById(`screen-${screenName}`);
  const button = document.getElementById(`${screenName}-menu-btn`);
  const sidebar = screen.querySelector(`#${screenName}-app > aside`);
  screen.classList.toggle("drawer-open", open);
  button.setAttribute("aria-expanded", String(open));
  sidebar.inert = window.matchMedia("(max-width: 768px)").matches && !open;
  if (open) document.getElementById(`${screenName}-filter`).focus();
  else if (screen.contains(document.activeElement) && sidebar.contains(document.activeElement)) button.focus();
}

// タブが表示された状態へ戻った時点で`resync`を呼ぶ。`focus`が真ならウィンドウのフォーカス時にも呼ぶ。
// バックグラウンドのタブは通知の処理が遅れ、表示が古いまま残るためである。
// タブの表示とウィンドウのフォーカスは別々に変わる（PWAのウィンドウはフォーカスだけが変わる）。
export function resyncWhenVisible(resync, {focus = true} = {}) {
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void resync();
  });
  if (focus) globalThis.addEventListener?.("focus", () => { void resync(); });
}

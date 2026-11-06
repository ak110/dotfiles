// 3画面（ワークアイテム・計画ファイル・セッション）が同じ役割で使う処理と設定値。
// 画面ごとの差（取得するAPI、一覧の項目の描画、画面固有の操作）は、各画面が引数の関数として渡す。

// 要求ごとに変わる値はHTMLへ埋め込んだJSONブロックから読む。資産ファイルは要求ごとに変わらないためである。
const bootstrap = readBootstrap();

// X-Forwarded-Prefixの値。未設定または不正な値の場合は空文字列とする。全てのfetchとEventSourceのURLの先頭へ付ける。
export const BASE_PATH = typeof bootstrap.base_path === "string" ? bootstrap.base_path : "";
export const SERVE_HOST = bootstrap.host_name || location.hostname;
export const WI_ROOT = bootstrap.wi_root || "WI保存元";

// 理由の文言は変えずに、先頭へ画面・ホスト・対象を添える。呼び出す画面は対象の表示名を渡す。
export function diagnostic(screen, host, target, reason) {
  return `［${screen} / ${host} / ${target}］${reason}`;
}

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

// 操作の所要を区間別にサーバーの記録（`serve/timings.jsonl`）へ送る。遅い操作の区間を後から判別するためである。
// 取得から本文の受信まで（fetch_ms）と、受信後に画面へ反映されるまで（render_ms）を分け、要求IDでサーバーの記録と対応付ける。
// 本文・検索語・入力値は送らない（URLはqueryを除いたパスだけを使う）。
const TIMING_FLUSH_MS = 5000;
const TIMING_BUFFER_LIMIT = 50;
const pendingTimings = [];
let timingTimer = null;

export function newRequestId() {
  const random = globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  return random.replace(/-/g, "").slice(0, 16);
}

function flushTimings({beacon = false} = {}) {
  if (timingTimer !== null) {
    clearTimeout(timingTimer);
    timingTimer = null;
  }
  if (pendingTimings.length === 0) return;
  const body = JSON.stringify({records: pendingTimings.splice(0, pendingTimings.length)});
  const url = BASE_PATH + "/api/timings";
  // 画面を閉じる時も送れる`sendBeacon`を優先し、使えない環境では通常の送信へ切り替える。記録の失敗は画面へ出さない。
  if ((beacon || typeof fetch !== "function") && navigator.sendBeacon?.(url, new Blob([body], {type: "application/json"}))) return;
  if (typeof fetch === "function") {
    void fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body, keepalive: true}).catch(() => {});
  }
}

export function recordTiming(record) {
  pendingTimings.push(record);
  if (pendingTimings.length >= TIMING_BUFFER_LIMIT) flushTimings();
  else if (timingTimer === null) timingTimer = setTimeout(() => flushTimings(), TIMING_FLUSH_MS);
}

globalThis.addEventListener?.("pagehide", () => flushTimings({beacon: true}));

// 本文の受信後、呼出し側の同期的な描画を終えた次の描画フレームの時刻で`done`を呼ぶ。
function afterRender(done) {
  setTimeout(() => {
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(() => done(performance.now()));
    else done(performance.now());
  }, 0);
}

// 操作の開始から結果の表示までを1件で記録する。呼出し側は結果の表示（ダイアログの開閉、一覧の再描画、
// 非同期の図の描画など）を終えてから`end`を呼び、次の描画フレームで所要を確定する。操作内の取得へ`timing`を
// 渡すと取得単位の記録に`operation_id`が付き、操作全体とサーバーの要求記録（`request_id`）を対応付けられる。
// 変更通知のように実行待ちを経た操作は、`startedAt`へ受信時刻を渡して待ち時間を含め、`queued_ms`にも分けて残す。
export function beginOperation(screen, operation, {startedAt, ...fields} = {}) {
  const operationId = newRequestId();
  const now = performance.now();
  const started = Number.isFinite(startedAt) ? startedAt : now;
  let ended = false;
  return {
    timing: {operation_id: operationId, ...(fields.trigger ? {trigger: fields.trigger} : {})},
    end(outcome = "ok") {
      if (ended) return;
      ended = true;
      afterRender((renderedAt) => recordTiming({
        screen, operation, kind: "operation", operation_id: operationId, outcome, ...fields,
        ...(now > started ? {queued_ms: now - started} : {}), total_ms: renderedAt - started,
      }));
    },
  };
}

// 所要を記録する`fetch`。`timing.operation`を省くとqueryを除いたパスを操作名にする。
// `timing.trigger`は契機（`click`・`sse`など）、`timing.notify_delay_ms`は変更通知の配信から受信まで、
// `timing.operation_id`は`beginOperation`が返す操作の識別子を渡す。
export async function timedFetch(screen, url, options = {}, timing = {}) {
  const requestId = newRequestId();
  const headers = {...(options.headers || {}), "X-Atk-Request-Id": requestId};
  const started = performance.now();
  const path = String(url).replace(BASE_PATH, "").split("?")[0];
  const base = {
    screen,
    operation: timing.operation || `${options.method || "GET"} ${path}`,
    route: path,
    request_id: requestId,
    ...(timing.trigger ? {trigger: timing.trigger} : {}),
    ...(timing.operation_id ? {operation_id: timing.operation_id} : {}),
    ...(Number.isFinite(timing.notify_delay_ms) ? {notify_delay_ms: timing.notify_delay_ms} : {}),
  };
  let response;
  try {
    response = await fetch(url, {...options, headers});
  } catch (error) {
    // 画面が不要になった要求を中止した場合は、通信の失敗と区別して記録する。
    const outcome = error?.name === "AbortError" ? "aborted" : "network-error";
    recordTiming({...base, outcome, total_ms: performance.now() - started});
    throw error;
  }
  let recorded = false;
  const finish = (bodyAt) => {
    if (recorded) return;
    recorded = true;
    afterRender((renderedAt) => recordTiming({
      ...base, status: response.status, fetch_ms: bodyAt - started, render_ms: renderedAt - bodyAt, total_ms: renderedAt - started,
    }));
  };
  let wrapped = false;
  for (const method of ["json", "text", "blob"]) {
    if (typeof response[method] !== "function") continue;
    const original = response[method].bind(response);
    try {
      response[method] = async () => {
        try {
          return await original();
        } finally {
          finish(performance.now());
        }
      };
      wrapped = true;
    } catch (_) {
      // 書き換えられない応答は本文の受信時刻を測れないため、ヘッダー受信の時点で記録する。
    }
  }
  if (!wrapped) finish(performance.now());
  return response;
}

// 変更通知の本文から配信時刻を読み、受信までの経過ミリ秒を返す。読めなければ`undefined`。
export function notifyDelayMs(event) {
  try {
    const published = JSON.parse(event?.data ?? "{}").published_at_ms;
    return Number.isFinite(published) ? Date.now() - published : undefined;
  } catch (_) {
    return undefined;
  }
}

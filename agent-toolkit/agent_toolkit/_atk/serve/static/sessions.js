// 3画面は同じドキュメントへ順に読み込まれるため、トップレベルの宣言を即時実行関数で囲んで
// 画面ごとにスコープを閉じる。`window.__atkScreens`への登録だけを外部へ公開する。
// 内側の字下げは、囲む前後の差分を比較できるよう元のままとする。
(() => {
// セッション画面。左ペインで保存済み記録を選び、右ペインへ発話を時系列に表示する。
// ページロード時の初期値は単一HTMLのJSONブロックへ埋め込み、初回の`init`で読み取る。
// X-Forwarded-Prefix未設定または不正値時は空文字列で、すべてのfetch/EventSourceに前置する。
let BASE_PATH = "";

const ENGINE_LABELS = { claude: "Claude Code", codex: "Codex" };
const KIND_LABELS = {
  user: "ユーザー",
  developer: "開発者",
  assistant: "アシスタント",
  thinking: "思考・要約",
  tool_call: "ツール呼び出し",
  tool_result: "ツール結果",
  compact_boundary: "コンテキスト圧縮",
};

let sessions = [];
let sessionRoots = [];
let selected = null;
let queryText = "";
let visibleLimit = 100;
let filterTimer = null;
const expandedKeys = new Set();
let visibleSessions = [];
// サブエージェントの記録は左ペインの一覧に現れないため、呼び出し元の記録を古い順に保持して戻れるようにする。
let parentTrail = [];
// 初期化後は文書とともに維持するSSE購読。
let eventSource = null;
// 詳細の取得の世代。選択の切り替えや再取得の後に届いた古い応答を反映しないために使う。
let detailGeneration = 0;
// 右ペインへ描画済みのイベント件数。「さらに100件表示」で増え、追記の反映後も保つ。
let renderedCount = 100;
const DETAIL_PAGE_SIZE = 100;
// 右ペインに表示中の記録と、利用者が末尾より上を読んでいる間に届いた未読のイベント件数。
let currentDetail = null;
let unseenEventCount = 0;
// 末尾からこの距離以内を読んでいれば末尾を読んでいるとみなし、追記に合わせて末尾へ追従する。
const FOLLOW_TAIL_PX = 48;

// 画面DOMの参照は初回の`init`で確定する。
let listEl = null;
let warningsEl = null;
let detailEl = null;
let detailTitleEl = null;
let detailUsageEl = null;
let filterEl = null;

function readBasePath() {
  const bootstrap = document.getElementById("sessions-bootstrap");
  if (bootstrap) BASE_PATH = JSON.parse(bootstrap.textContent).base_path;
}

function formatTime(value) {
  if (!value) return "不明";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const pad = (n) => String(n).padStart(2, "0");
  return `${parsed.getFullYear()}/${pad(parsed.getMonth() + 1)}/${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}

function setDrawerOpen(open) {
  window.__atkDrawer.set("sessions", open);
}

function sessionKey(entry) {
  return JSON.stringify([entry.host, entry.engine, entry.path]);
}

function matchesFilter(entry) {
  if (!queryText) return true;
  const haystack = [entry.host, entry.cwd, entry.first_user_message, entry.session_id, entry.path]
    .filter((value) => typeof value === "string")
    .join(" ")
    .toLowerCase();
  return haystack.includes(queryText);
}

function treeRows() {
  const byKey = new Map(sessions.map(entry => [sessionKey(entry), entry]));
  const children = new Map();
  const roots = [];
  for (const entry of sessions) {
    const parent = entry.parent_path && byKey.get(JSON.stringify([entry.host, entry.engine, entry.parent_path]));
    if (!parent || parent === entry) roots.push(entry);
    else {
      const key = sessionKey(parent);
      if (!children.has(key)) children.set(key, []);
      children.get(key).push(entry);
    }
  }
  const hasMatch = (entry, visiting = new Set()) => {
    const key = sessionKey(entry);
    if (visiting.has(key)) return false;
    visiting.add(key);
    return matchesFilter(entry) || (children.get(key) || []).some(child => hasMatch(child, new Set(visiting)));
  };
  const rows = [];
  const walk = (entry, level, visiting = new Set()) => {
    const key = sessionKey(entry);
    if (visiting.has(key) || !hasMatch(entry)) return;
    visiting.add(key);
    rows.push({entry, level, childCount: (children.get(key) || []).length});
    if (expandedKeys.has(key) || queryText) {
      for (const child of children.get(key) || []) walk(child, level + 1, new Set(visiting));
    }
  };
  for (const root of roots) walk(root, 1);
  return rows;
}

function renderList() {
  const rows = treeRows();
  visibleSessions = rows.map(row => row.entry);
  const existing = new Map([...listEl.children].filter(node => node.dataset.key).map(node => [node.dataset.key, node]));
  let cursor = listEl.firstChild;
  for (const {entry, level, childCount} of rows.slice(0, visibleLimit)) {
    const key = sessionKey(entry);
    let row = existing.get(key);
    if (row) existing.delete(key);
    else {
      row = document.createElement("div");
      row.className = "session-tree-row";
      row.dataset.key = key;
    }
    row.setAttribute("role", "treeitem");
    row.setAttribute("aria-level", String(level));
    row.style.paddingInlineStart = `${(level - 1) * 16}px`;
    let item = row.querySelector(".session-item");
    if (!item) {
      item = document.createElement("button");
      item.type = "button";
      item.className = "session-item pane-item";
    }
    item.dataset.host = entry.host;
    item.dataset.engine = entry.engine;
    item.dataset.path = entry.path;
    if (selected && sessionKey(selected) === key) item.setAttribute("aria-current", "true");
    else item.removeAttribute("aria-current");

    const cwd = document.createElement("div");
    cwd.className = "session-cwd pane-item-title";
    cwd.textContent = entry.cwd || "(作業ディレクトリ不明)";

    const meta = document.createElement("div");
    meta.className = "session-meta pane-item-meta";
    const host = document.createElement("span");
    host.textContent = entry.host;
    const startedAt = document.createElement("span");
    startedAt.textContent = formatTime(entry.started_at);
    meta.append(host, startedAt);
    item.replaceChildren(cwd, meta);

    if (entry.warning) {
      const warning = document.createElement("div");
      warning.className = "session-warning";
      warning.textContent = entry.warning;
      item.append(warning);
    }
    // 子の無い行にも同じ幅の開閉欄を置き、子の有無で項目の開始位置がずれないようにする。
    // 子の無い行の「−」は展開できる子が無いことを示す表示であり、操作対象にしない。
    let toggle = row.querySelector(".session-tree-toggle, .session-tree-leaf");
    if (childCount) {
      if (!toggle || toggle.tagName !== "BUTTON") {
        toggle?.remove();
        toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "session-tree-toggle";
        toggle.addEventListener("click", () => {
          if (expandedKeys.has(key)) expandedKeys.delete(key);
          else expandedKeys.add(key);
          renderList();
          listEl.querySelector(`[data-key="${CSS.escape(key)}"] .session-tree-toggle`)?.focus();
        });
        row.insertBefore(toggle, row.firstChild);
      }
      toggle.textContent = expandedKeys.has(key) ? "−" : "+";
      toggle.setAttribute("aria-label", `${entry.session_id}の子セッションを${expandedKeys.has(key) ? "折り畳む" : "展開する"}`);
      toggle.setAttribute("aria-expanded", String(expandedKeys.has(key)));
    } else if (!toggle || toggle.tagName === "BUTTON") {
      toggle?.remove();
      toggle = document.createElement("span");
      toggle.className = "session-tree-leaf";
      toggle.textContent = "−";
      toggle.setAttribute("aria-hidden", "true");
      row.insertBefore(toggle, row.firstChild);
    }
    if (item.parentNode !== row) row.append(item);
    if (row === cursor) cursor = row.nextSibling;
    else listEl.insertBefore(row, cursor);
  }
  for (const node of existing.values()) node.remove();
  document.getElementById("sessions-empty").hidden = rows.length !== 0;
  const empty = document.getElementById("sessions-empty");
  empty.querySelector("p").textContent = sessions.length === 0
    ? `ユーザーの発話を含むセッション記録はまだありません。発話が保存された記録は自動で一覧へ加わります。対象root: ${sessionRoots.join("、")}`
    : "一致するセッションはありません。";
  empty.querySelector("button").hidden = !queryText;
  document.getElementById("sessions-sentinel").hidden = rows.length <= visibleLimit;
  updateNavButtons();
}

function updateNavButtons() {
  const visible = visibleSessions;
  const index = selected
    ? visible.findIndex((entry) => entry.host === selected.host && entry.engine === selected.engine && entry.path === selected.path)
    : -1;
  document.getElementById("sessions-prev-btn").disabled = index <= 0;
  document.getElementById("sessions-next-btn").disabled = index < 0 || index >= visible.length - 1;
}

function navigateRelative(delta) {
  const visible = visibleSessions;
  const index = selected
    ? visible.findIndex((entry) => entry.host === selected.host && entry.engine === selected.engine && entry.path === selected.path)
    : -1;
  const target = visible[index + delta];
  if (target) openSession(target.host, target.engine, target.path);
}

function showWarnings(lines) {
  warningsEl.hidden = lines.length === 0;
  warningsEl.replaceChildren();
  for (const line of lines) {
    const row = document.createElement("div");
    row.textContent = line;
    warningsEl.append(row);
  }
}

function renderWarnings(warnings) {
  showWarnings((warnings || []).map((warning) => `${warning.host}: ${warning.reason}`));
}

async function loadList() {
  try {
    const response = await (fetch(BASE_PATH + "/api/sessions/list"));
    if (!response.ok) throw new Error(`一覧を取得できません (${response.status})`);
    const payload = await (response.json());
    const previousKeys = new Set(sessions.map(sessionKey));
    sessions = payload.sessions || [];
    const currentKeys = new Set(sessions.map(sessionKey));
    for (const key of expandedKeys) if (!currentKeys.has(key)) expandedKeys.delete(key);
    if (selected && previousKeys.has(sessionKey(selected)) && !currentKeys.has(sessionKey(selected))) {
      selected = null;
      parentTrail = [];
      detailTitleEl.textContent = "";
      detailUsageEl.textContent = "";
      detailEl.textContent = "選択したセッション記録は見つかりません。";
      if (location.pathname.endsWith("/sessions") && location.search) history.replaceState({atkSession: true}, "", location.pathname);
    }
    sessionRoots = payload.roots || [];
    renderWarnings(payload.warnings);
    document.getElementById("sessions-list-error").hidden = true;
    renderList();
  } catch (error) {
    const box = document.getElementById("sessions-list-error");
    box.replaceChildren();
    const message = document.createElement("span");
    message.textContent = `セッション一覧を取得できません: ${error.message} `;
    const retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = "再読み込み";
    retry.addEventListener("click", loadList);
    box.append(message, retry);
    box.hidden = false;
  }
}

function usageText(usage) {
  if (!usage) return "";
  const parts = [];
  for (const [key, label] of [["input_tokens", "入力"], ["output_tokens", "出力"]]) {
    const value = usage[key];
    parts.push(`${label}: ${value === null || value === undefined ? "取得不能" : value.toLocaleString()}`);
  }
  return parts.join(" / ");
}

function appendUnavailable(parent, label) {
  const span = document.createElement("span");
  span.className = "unavailable";
  span.textContent = label;
  parent.append(span);
}

function toolInputSummary(event) {
  let input = event.detail?.input;
  if (typeof input === "string") {
    try {
      input = JSON.parse(input);
    } catch (_) {
      return "";
    }
  }
  if (!input || typeof input !== "object" || Array.isArray(input)) return "";
  if (typeof input.command === "string") return input.command.split(/\r?\n/, 1)[0];
  if (typeof input.file_path === "string") return input.file_path;
  return Object.values(input).find((value) => typeof value === "string") || "";
}

function renderEvent(event, index) {
  const block = document.createElement("details");
  block.className = `event kind-${event.kind}`;
  block.dataset.eventIndex = String(index);
  block.open = event.kind === "user" || event.kind === "assistant" || event.kind === "developer";
  if (!block.open) {
    block.dataset.exclusiveEvent = "true";
    block.addEventListener("toggle", () => {
      if (!block.open) return;
      for (const other of detailEl.querySelectorAll('details[data-exclusive-event="true"]')) {
        if (other !== block) other.open = false;
      }
    });
  }

  const summary = document.createElement("summary");
  const kind = document.createElement("span");
  kind.className = "event-kind";
  kind.textContent = KIND_LABELS[event.kind] || event.kind;
  const time = document.createElement("span");
  time.className = "event-time";
  time.textContent = event.timestamp ? formatTime(event.timestamp) : "時刻なし";
  summary.append(kind, time);
  if (event.name) {
    const name = document.createElement("span");
    name.textContent = event.name;
    summary.append(name);
  }
  if (event.kind === "tool_call") {
    const input = toolInputSummary(event);
    if (input) {
      const inputSummary = document.createElement("span");
      inputSummary.className = "event-input-summary";
      inputSummary.textContent = input;
      summary.append(inputSummary);
    }
  }
  block.append(summary);

  const body = document.createElement("pre");
  if (typeof event.text === "string" && event.text !== "") {
    body.textContent = event.text;
  } else if (event.detail !== null && event.detail !== undefined) {
    body.textContent = JSON.stringify(event.detail, null, 2);
  } else {
    appendUnavailable(body, "本文は記録に含まれていません");
  }
  block.append(body);
  return block;
}

function renderSubagents(detail) {
  const container = document.createElement("div");
  container.className = "subagents";
  const heading = document.createElement("h2");
  heading.textContent = "サブエージェント";
  container.append(heading);
  for (const subagent of detail.subagents) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "subagent-item";
    // 起動の深さを字下げで表す。`parent_agent_id`は深さ2以上の記録にしか現れないため使わない。
    const depth = Number.isInteger(subagent.spawn_depth) ? subagent.spawn_depth : 0;
    item.style.paddingLeft = `${10 + depth * 14}px`;
    const type = document.createElement("span");
    type.className = "subagent-type";
    type.textContent = subagent.agent_type || "(種別不明)";
    const description = document.createElement("span");
    description.className = "subagent-description";
    description.textContent = subagent.description || "";
    item.append(type, description);
    if (subagent.path) {
      item.addEventListener("click", () => openSession(detail.host, "claude", subagent.path, [...parentTrail, selected]));
    } else {
      // 記録本体が残っていない項目は開けないため、選択できない表示とする。
      item.disabled = true;
    }
    container.append(item);
  }
  return container;
}

function renderBack() {
  const trail = parentTrail;
  const parent = trail[trail.length - 1];
  const button = document.createElement("button");
  button.type = "button";
  button.className = "detail-back button-secondary";
  button.textContent = "呼び出し元の記録へ戻る";
  button.addEventListener("click", () => openSession(parent.host, parent.engine, parent.path, trail.slice(0, -1)));
  return button;
}

// 追記の反映で描き直す前に、展開中のイベント、表示件数、スクロール位置を控える。
// イベントは記録の先頭からの順で並び、追記で既存の位置は変わらないため、位置を識別に使う。
function captureDetailState() {
  const openStates = new Map();
  for (const block of detailEl.querySelectorAll("details[data-event-index]")) {
    openStates.set(block.dataset.eventIndex, block.open);
  }
  return {openStates, renderedCount, scrollTop: detailEl.parentElement.scrollTop};
}

function renderDetail(detail, preserved = null) {
  currentDetail = detail;
  detailTitleEl.textContent = `${ENGINE_LABELS[detail.engine] || detail.engine} / ${detail.host} / ${detail.project || "(プロジェクト不明)"}`;
  detailUsageEl.textContent = usageText(detail.usage);
  // 子要素だけを差し替えるため、本文装飾を担う`#detail`自身のmarkdown-bodyクラスは保持される。
  detailEl.replaceChildren();

  if (parentTrail.length > 0) {
    detailEl.append(renderBack());
  }

  const meta = document.createElement("div");
  meta.className = "secondary-text";
  meta.textContent = `識別子: ${detail.session_id} / 開始: ${detail.started_at ? formatTime(detail.started_at) : "取得不能"}`;
  detailEl.append(meta);

  if (Array.isArray(detail.subagents) && detail.subagents.length > 0) {
    detailEl.append(renderSubagents(detail));
  } else if (detail.subagents_unavailable) {
    // サブエージェントが無い場合は何も表示しないため、有無を判定できなかったことは明示して区別する。
    const note = document.createElement("div");
    note.className = "secondary-text";
    appendUnavailable(note, "サブエージェントの一覧を取得できません（リモートホストのdotfilesを更新すると表示されます）");
    detailEl.append(note);
  }

  if (detail.broken_lines > 0) {
    const broken = document.createElement("div");
    broken.className = "session-warning";
    broken.textContent = `解析できない行が${detail.broken_lines}件あります`;
    detailEl.append(broken);
  }

  renderedCount = preserved ? preserved.renderedCount : DETAIL_PAGE_SIZE;
  detail.events.slice(0, renderedCount).forEach((event, index) => {
    const block = renderEvent(event, index);
    const open = preserved?.openStates.get(String(index));
    if (open !== undefined) block.open = open;
    detailEl.append(block);
  });
  if (detail.events.length > renderedCount) {
    const more = document.createElement("button");
    more.type = "button";
    more.textContent = "さらに100件表示";
    more.addEventListener("click", () => {
      detail.events.slice(renderedCount, renderedCount + DETAIL_PAGE_SIZE).forEach((event, offset) => {
        detailEl.insertBefore(renderEvent(event, renderedCount + offset), more);
      });
      renderedCount += DETAIL_PAGE_SIZE;
      more.hidden = renderedCount >= detail.events.length;
    });
    detailEl.append(more);
  }

  if (detail.truncated_events > 0) {
    const truncated = document.createElement("div");
    truncated.className = "secondary-text";
    truncated.textContent = `表示上限を超えた${detail.truncated_events}件は表示していません`;
    detailEl.append(truncated);
  }
  if (preserved) detailEl.parentElement.scrollTop = preserved.scrollTop;
}

function isSelected(host, engine, path) {
  return Boolean(selected) && selected.host === host && selected.engine === engine && selected.path === path;
}

function detailScroller() {
  return detailEl.parentElement;
}

// 描画済みのイベントが記録の末尾までそろい、スクロールが末尾近くにある状態を「末尾を読んでいる」とする。
function isReadingTail() {
  if (!currentDetail || renderedCount < currentDetail.events.length) return false;
  const scroller = detailScroller();
  return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight <= FOLLOW_TAIL_PX;
}

// 末尾より上を読んでいる間に届いたイベントを件数で知らせ、末尾へ移る操作を示す。
function updateNewEventsNotice() {
  const button = document.getElementById("sessions-new-events-btn");
  button.hidden = unseenEventCount === 0;
  button.textContent = unseenEventCount === 0 ? "" : `新しいイベントが${unseenEventCount}件あります。末尾へ移動`;
}

function clearNewEventsNotice() {
  unseenEventCount = 0;
  updateNewEventsNotice();
}

// 未描画のイベントも描画してから末尾へ移る。
function jumpToLatestEvents() {
  if (currentDetail && renderedCount < currentDetail.events.length) {
    renderDetail(currentDetail, {...captureDetailState(), renderedCount: currentDetail.events.length});
  }
  const scroller = detailScroller();
  scroller.scrollTop = scroller.scrollHeight;
  clearNewEventsNotice();
}

// 選択中の記録を取り直し、表示状態を保ったまま描き直す。取得に失敗した場合は現在の表示を残す。
// 末尾を読んでいた場合は追記の末尾へ追従し、それ以外は位置を保って新着の件数を通知欄へ表示する。
async function refreshSelectedDetail() {
  if (!selected) return;
  const {host, engine, path} = selected;
  const generation = ++detailGeneration;
  try {
    const response = await fetch(`${BASE_PATH}/api/sessions/detail?${new URLSearchParams({host, engine, path}).toString()}`);
    if (!response.ok) return;
    const detail = await response.json();
    if (generation !== detailGeneration || !isSelected(host, engine, path)) return;
    const previousCount = currentDetail ? currentDetail.events.length : detail.events.length;
    const readingTail = isReadingTail();
    renderDetail(detail, {...captureDetailState(), renderedCount: readingTail ? detail.events.length : renderedCount});
    const added = detail.events.length - previousCount;
    if (added <= 0) return;
    if (readingTail) {
      const scroller = detailScroller();
      scroller.scrollTop = scroller.scrollHeight;
    } else {
      unseenEventCount += added;
      updateNewEventsNotice();
    }
  } catch (_) {
    // 一時的な取得失敗では現在の表示を残し、次の通知か再接続で取り直す。
  }
}

// `trail`は開こうとする記録の呼び出し元を古い順に並べる。左ペインから選んだ記録には呼び出し元が無いため既定は空とする。
async function openSession(host, engine, path, trail = [], updateUrl = true) {
  selected = { host, engine, path };
  if (updateUrl && location.pathname.endsWith("/sessions")) {
    const url = new URL(location.href);
    url.search = new URLSearchParams({host, engine, path}).toString();
    history.pushState({atkSession: true}, "", url);
  }
  parentTrail = trail;
  currentDetail = null;
  clearNewEventsNotice();
  renderList();
  detailEl.replaceChildren();
  detailTitleEl.textContent = "読み込み中...";
  const query = new URLSearchParams({ host, engine, path });
  const generation = ++detailGeneration;
  try {
    const response = await (fetch(`${BASE_PATH}/api/sessions/detail?${query.toString()}`));
    if (!response.ok) throw new Error(`記録を取得できません (${response.status})`);
    const detail = await (response.json());
    if (generation !== detailGeneration) return;
    renderDetail(detail);
    detailEl.parentElement.scrollTop = 0;
  } catch (error) {
    if (generation !== detailGeneration) return;
    detailTitleEl.textContent = "";
    detailEl.replaceChildren();
    const alert = document.createElement("div");
    alert.setAttribute("role", "alert");
    alert.textContent = `セッションの詳細を取得できません: ${error.message} `;
    const retry = document.createElement("button");
    retry.type = "button";
    retry.textContent = "再読み込み";
    retry.addEventListener("click", () => openSession(host, engine, path, trail, false));
    alert.append(retry);
    detailEl.append(alert);
  }
  setDrawerOpen(false);
}

// 通知は2種類ある。`refresh`は一覧の再取得を促し、選択中の記録も取り直す。
// `record`は記録1件の更新を示し、選択中の記録と一致する場合だけ右ペインを取り直す。
function handleSseMessage(event) {
  let message = null;
  try {
    message = JSON.parse(event.data);
  } catch (_) {
    return;
  }
  if (message?.type === "refresh") {
    void resyncFromServer();
  } else if (message?.type === "record" && isSelected(message.host, message.engine, message.path)) {
    void refreshSelectedDetail();
  }
}

// 確立時（初回・自動再接続・無通信後の再接続）は、接続していない間の変更を取り込むため一覧と選択中の記録を取り直す。
// 初回の確立は`init`の初期取得の直後に起きるため取り直しを省く。
function subscribeEvents() {
  let firstOpen = true;
  eventSource = window.__atkSse.connect(BASE_PATH + "/api/sessions/events", {
    open: () => {
      if (firstOpen) {
        firstOpen = false;
        return;
      }
      void resyncFromServer();
    },
    message: handleSseMessage,
  });
}

async function resyncFromServer() {
  await loadList();
  await refreshSelectedDetail();
}

async function init() {
  readBasePath();
  listEl = document.getElementById("sessions");
  warningsEl = document.getElementById("warnings");
  detailEl = document.getElementById("detail");
  detailTitleEl = document.getElementById("detail-title");
  detailUsageEl = document.getElementById("detail-usage");
  filterEl = document.getElementById("sessions-filter");
  filterEl.addEventListener("input", () => {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(() => {
      queryText = filterEl.value.trim().toLowerCase();
      visibleLimit = 100;
      renderList();
    }, 300);
  });
  listEl.addEventListener("click", (event) => {
    const item = event.target.closest(".session-item");
    if (!item) return;
    openSession(item.dataset.host, item.dataset.engine, item.dataset.path);
  });
  document.getElementById("sessions-menu-btn").addEventListener("click", () => {
    setDrawerOpen(!document.getElementById("screen-sessions").classList.contains("drawer-open"));
  });
  document.getElementById("sessions-drawer-backdrop").addEventListener("click", () => setDrawerOpen(false));
  document.getElementById("sessions-clear-filter").addEventListener("click", () => {
    filterEl.value = "";
    queryText = "";
    visibleLimit = 100;
    renderList();
    filterEl.focus();
  });
  new IntersectionObserver(entries => {
    if (entries.some(entry => entry.isIntersecting) && visibleLimit < treeRows().length) {
      visibleLimit += 100;
      renderList();
    }
  }, {root: document.querySelector("#sessions-app > aside"), rootMargin: "400px"})
    .observe(document.getElementById("sessions-sentinel"));
  window.addEventListener("popstate", () => {
    if (!location.pathname.endsWith("/sessions")) return;
    const params = new URLSearchParams(location.search);
    const entry = sessions.find(item => item.host === params.get("host") && item.engine === params.get("engine") && item.path === params.get("path"));
    if (entry) void openSession(entry.host, entry.engine, entry.path, [], false);
  });
  document.getElementById("sessions-new-events-btn").addEventListener("click", jumpToLatestEvents);
  detailScroller().addEventListener("scroll", () => {
    if (unseenEventCount > 0 && isReadingTail()) clearNewEventsNotice();
  }, {passive: true});
  document.getElementById("sessions-prev-btn").addEventListener("click", () => navigateRelative(-1));
  document.getElementById("sessions-next-btn").addEventListener("click", () => navigateRelative(1));
  await loadList();
  const params = new URLSearchParams(location.search);
  const fromUrl = sessions.find(item => item.host === params.get("host") && item.engine === params.get("engine") && item.path === params.get("path"));
  const mobile = window.matchMedia("(max-width: 768px)").matches;
  if (fromUrl) {
    await openSession(fromUrl.host, fromUrl.engine, fromUrl.path, [], false);
  } else if (!selected && sessions.length > 0 && !mobile) {
    await openSession(sessions[0].host, sessions[0].engine, sessions[0].path, [], false);
  }
  setDrawerOpen(false);
  subscribeEvents();
  // タブが表示された状態へ戻った時点で取り直す。バックグラウンドのタブでは通知の処理が遅れるためである。
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") void resyncFromServer();
  });
  // bfcacheへ入る前に接続を閉じ、復帰時に再接続する。再接続した接続の確立時に取り直す。
  window.addEventListener("pagehide", () => { eventSource?.close(); });
  window.addEventListener("pageshow", (event) => { if (event.persisted) eventSource?.reopen(); });
}

window.__atkScreens = window.__atkScreens || {};
window.__atkScreens.sessions = {init};
})();

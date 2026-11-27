import {readFailure} from "./common.js";
// セッション画面。左ペインで保存済み記録を選び、右ペインへ発話を時系列に表示する。
import {
  BASE_PATH, SERVE_HOST, diagnostic, connectEvents, handleSseMessage, isSelected, navigateRelative, renderList, renderWarnings,
  resyncWhenVisible, updateNavButtons, timedFetch, beginOperation, recordUpdateSuccess, setUpdateError, createReadRecovery, setDisplayedReadError
} from "./common.js";
import {decorateWiReferences} from "./wi_reference.js";
import {createSessionDetail} from "./sessions_detail.js";
import {registerScreen} from "./shell.js";

const ENGINE_LABELS = { claude: "Claude Code", codex: "Codex" };

function sessionDiagnostic(entry, reason) {
  return diagnostic("セッション", entry.host, `${ENGINE_LABELS[entry.engine] || entry.engine} / ${entry.path}`, reason);
}
let sessions = [];
let listLoaded = false;
let selected = null;
let queryText = "";
let visibleLimit = 100;
let filterTimer = null;
let fullTextMatches = null;
let fullTextRequest = null;
let fullTextStatus = "";
let searchInterrupted = false;
const expandedKeys = new Set();
let visibleSessions = [];
// 画面DOMの参照は初回の`init`で確定する。
let listEl = null;
let warningsEl = null;
let filterEl = null;

function formatTime(value) {
  if (!value) return "不明";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const pad = (n) => String(n).padStart(2, "0");
  return `${parsed.getFullYear()}/${pad(parsed.getMonth() + 1)}/${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}

function sessionKey(entry) {
  return JSON.stringify([entry.host, entry.engine, entry.path]);
}

function matchesFilter(entry) {
  if (!queryText) return true;
  if (fullTextMatches) return fullTextMatches.has(sessionKey(entry));
  return matchesMetadata(entry);
}

function matchesMetadata(entry) {
  const haystack = [entry.host, entry.cwd, entry.first_user_message, entry.session_id, entry.path, entry.label]
    .filter((value) => typeof value === "string")
    .join(" ")
    .toLowerCase();
  return haystack.includes(queryText);
}

function treeRows() {
  // 親と子の実行系（Claude CodeとCodex）は異なり得るため、親はホストとパスで検索し、子の実行系を検索条件に含めない。
  // 記録のパスはホスト内で一意である。
  const byPath = new Map(sessions.map(entry => [JSON.stringify([entry.host, entry.path]), entry]));
  const children = new Map();
  const roots = [];
  for (const entry of sessions) {
    const parent = entry.parent_path && byPath.get(JSON.stringify([entry.host, entry.parent_path]));
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

function isSelectedSession(entry) {
  return isSelected(selected, entry);
}

function showSessionList({focus = false} = {}) {
  document.getElementById("sessions-app").dataset.view = "list";
  if (focus) (listEl.querySelector('[aria-current="true"]') ?? filterEl).focus();
}

function renderSessions() {
  const rows = treeRows();
  visibleSessions = rows.map(row => row.entry);
  renderList(listEl, rows.slice(0, visibleLimit), ({entry}) => sessionKey(entry), ({entry, level, childCount}, existing) => {
    const key = sessionKey(entry);
    let row = existing;
    if (!row) {
      row = document.createElement("div");
      row.className = "session-tree-row";
    }
    row.setAttribute("role", "listitem");
    row.setAttribute("aria-level", String(level));
    row.style.setProperty("--session-depth", String(level - 1));
    let item = row.querySelector(".session-item");
    if (!item) {
      item = document.createElement("button");
      item.type = "button";
      item.className = "session-item pane-item";
    }
    item.dataset.host = entry.host;
    item.dataset.engine = entry.engine;
    item.dataset.path = entry.path;
    if (isSelectedSession(entry)) item.setAttribute("aria-current", "true");
    else item.removeAttribute("aria-current");

    const cwd = document.createElement("div");
    cwd.className = "session-cwd pane-item-title";
    const labelText = entry.label;
    row.querySelector(":scope > .session-cwd")?.remove();
    item.removeAttribute("aria-label");
    cwd.textContent = labelText || entry.cwd || "作業ディレクトリ不明";
    item.title = [labelText, entry.cwd].filter(Boolean).join(" — ");

    const meta = document.createElement("div");
    meta.className = "session-meta pane-item-meta";
    const host = document.createElement("span");
    host.textContent = [labelText && entry.cwd, entry.host].filter(Boolean).join(" · ");
    const startedAt = document.createElement("span");
    startedAt.textContent = formatTime(entry.started_at);
    meta.append(host, startedAt);
    item.replaceChildren(cwd, meta);

    if (entry.warning) {
      const warning = document.createElement("div");
      warning.className = "session-warning";
      warning.textContent = sessionDiagnostic(entry, entry.warning);
      item.append(warning);
    }
    let toggle = row.querySelector(".session-tree-toggle, .session-tree-leaf");
    if (childCount) {
      if (!toggle || toggle.tagName !== "BUTTON") {
        toggle?.remove();
        toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "session-tree-toggle browse-icon-action";
        toggle.innerHTML = '<svg class="app-icon" aria-hidden="true"><use href="#icon-chevron-down"></use></svg>';
        toggle.addEventListener("click", () => {
          if (expandedKeys.has(key)) expandedKeys.delete(key);
          else expandedKeys.add(key);
          renderSessions();
          listEl.querySelector(`[data-key="${CSS.escape(key)}"] .session-tree-toggle`)?.focus();
        });
        row.append(toggle);
      }
      const expanded = expandedKeys.has(key) || Boolean(queryText);
      const label = expanded ? "子セッションを折り畳む" : "子セッションを展開する";
      toggle.setAttribute("aria-label", label + ": " + cwd.textContent);
      toggle.dataset.tooltip = label;
      toggle.setAttribute("aria-expanded", String(expanded));
      toggle.hidden = Boolean(queryText);
    } else if (!toggle || toggle.tagName === "BUTTON") {
      toggle?.remove();
      toggle = document.createElement("span");
      toggle.className = "session-tree-leaf";
      toggle.setAttribute("aria-hidden", "true");
      row.append(toggle);
    }
    if (item.parentNode !== row) row.insertBefore(item, toggle);
    if (labelText) {
      decorateWiReferences(cwd, {beforeLink: () => {
        item.before(cwd);
        const action = document.createElement("span");
        action.className = "pane-item-title";
        action.textContent = "セッションを開く";
        item.prepend(action);
        item.setAttribute("aria-label", `${action.textContent}: ${labelText}`);
      }});
    }
    return row;
  });
  document.getElementById("sessions-empty").hidden = !listLoaded || rows.length !== 0;
  const empty = document.getElementById("sessions-empty");
  const searchStatus = document.getElementById("sessions-search-status");
  searchStatus.textContent = fullTextStatus;
  searchStatus.hidden = !fullTextStatus;
  document.getElementById("sessions-restart-search").hidden = !searchInterrupted;
  if (fullTextStatus) empty.hidden = true;
  empty.querySelector("p").textContent = sessions.length === 0
    ? "ユーザーの発話を含むセッション記録はまだありません。発話が保存された記録は自動で一覧へ加わります。"
    : "一致するセッションはありません。";
  empty.querySelector("button").hidden = !queryText;
  document.getElementById("sessions-sentinel").hidden = rows.length <= visibleLimit;
  updateNavButtons(
    document.getElementById("sessions-prev-btn"), document.getElementById("sessions-next-btn"),
    visibleSessions, isSelectedSession
  );
}

function openRelativeSession(delta) {
  navigateRelative(visibleSessions, isSelectedSession, delta, (target) => openSession(target.host, target.engine, target.path));
}

// 取得中に再び呼ばれた処理は新しい要求を並行して発行せず、進行中の取得の完了後に1回だけ取り直す。
// `refresh`通知は新しい記録の作成ごとに届き、一覧の取得より短い間隔で続くことがある。
// 要求を重ねるとサーバーで同じ走査が重なり、他の画面の応答も待たされる。
// 呼び出し元へは取り直しまで含めた完了を返す。
// `operation`を渡すと、1回の実行を契機の受信から表示完了までの1件の操作として記録する。まとめた要求は
// 最初の受信時刻から測り、実行待ちの時間を含める。`run`へは操作の`timing`（無ければ呼出し側の値）を渡す。
function coalesced(run, operation = null) {
  let inFlight = null;
  let pending = null;
  return ({trigger = "resync", notify_delay_ms: notifyDelay, timing = {}} = {}) => {
    if (!pending) pending = {receivedAt: performance.now(), trigger, notifyDelay, timing};
    if (inFlight) return inFlight;
    inFlight = (async () => {
      try {
        while (pending) {
          const current = pending;
          pending = null;
          const measured = operation ? beginOperation("sessions", operation, {
            startedAt: current.receivedAt, trigger: current.trigger,
            ...(Number.isFinite(current.notifyDelay) ? {notify_delay_ms: current.notifyDelay} : {}),
          }) : null;
          let outcome = "ok";
          try {
            await run(measured ? measured.timing : current.timing);
          } catch (error) {
            outcome = "error";
            throw error;
          } finally {
            measured?.end(outcome);
          }
        }
      } finally {
        inFlight = null;
      }
    })();
    return inFlight;
  };
}

// 通知本文の配信時刻から受信までの経過ミリ秒を返す。読めなければ`undefined`。
function payloadNotifyDelay(payload) {
  return Number.isFinite(payload?.published_at_ms) ? Date.now() - payload.published_at_ms : undefined;
}

const loadList = coalesced(fetchList);
let remoteRetry;
const remoteFailures = new Map();

function showListWarnings(warnings) {
  const failed = new Set(warnings.filter(warning => warning.retryable === "true").map(warning => warning.host));
  renderWarnings(warningsEl, warnings.filter(warning => warning.retryable !== "true").map(warning =>
    diagnostic("セッション", warning.host, warning.path || warning.root || "記録一覧", warning.reason)));
  clearTimeout(remoteRetry);
  for (const host of remoteFailures.keys()) {
    if (!failed.has(host)) {
      remoteFailures.delete(host);
      recordUpdateSuccess("sessions", `host:${host}`);
    }
  }
  if (!failed.size) return;
  let delay = 30_000;
  for (const host of failed) {
    const attempts = (remoteFailures.get(host) ?? 0) + 1;
    remoteFailures.set(host, attempts);
    delay = Math.min(delay, 1500 * 2 ** Math.min(5, attempts - 1));
    if (attempts >= 2) {
      setUpdateError("sessions", `host:${host}`, `${host}のセッション一覧を取得できません。`, loadList);
    }
  }
  remoteRetry = setTimeout(() => void loadList(), delay);
}

async function fetchList(timing = {}) {
  try {
    const response = await (timedFetch("sessions", BASE_PATH + "/api/sessions/list", {}, timing));
    if (!response.ok) throw new Error(`一覧を取得できません (${response.status})`);
    const payload = await (response.json());
    const previousKeys = new Set(sessions.map(sessionKey));
    const hadMetadataMatch = queryText && sessions.some(matchesMetadata);
    const warnings = payload.warnings || [];
    showListWarnings(warnings);
    const failed = new Set(warnings.filter(warning => warning.retryable !== undefined).map(warning => warning.host));
    sessions = [...(payload.sessions || []), ...sessions.filter(entry => failed.has(entry.host))];
    const hasMetadataMatch = queryText && sessions.some(matchesMetadata);
    if (!searchInterrupted && hasMetadataMatch) cancelFullTextSearch();
    const currentKeys = new Set(sessions.map(sessionKey));
    const listChanged = currentKeys.size !== previousKeys.size || [...currentKeys].some(key => !previousKeys.has(key));
    for (const key of expandedKeys) if (!currentKeys.has(key)) expandedKeys.delete(key);
    if (selected && previousKeys.has(sessionKey(selected)) && !currentKeys.has(sessionKey(selected))) {
      const message = sessionDiagnostic(selected, "選択したセッション記録は見つかりません。");
      selected = null;
      detail.clear(message);
      document.getElementById("sessions-selection-status").textContent = message;
      showSessionList({focus: window.matchMedia("(max-width: 768px)").matches && document.querySelector("#sessions-app main").contains(document.activeElement)});
      if (location.pathname.endsWith("/sessions") && location.search) history.replaceState({atkSession: true}, "", location.pathname);
    }
    listLoaded = true;
    recordUpdateSuccess("sessions", "list");
    const searched = fullTextMatches !== null || fullTextRequest !== null;
    if (!searchInterrupted && queryText && !hasMetadataMatch && (hadMetadataMatch || (searched && listChanged))
        && queryText === filterEl.value.trim().toLowerCase()) {
      void applySessionFilter();
    } else {
      renderSessions();
    }
  } catch (error) {
    if (error.name !== "AbortError") setDisplayedReadError("sessions", "list", "セッション一覧を取得できません。", loadList);
  } finally {
    document.getElementById("sessions-list-loading").hidden = true;
  }
}

// 通知は2種類ある。`refresh`は一覧の再取得を促し、選択中の記録も取り直す。
// `record`は記録1件の更新を示す。選択中の記録自身と、親の集計へ含める子孫の更新を取り込む。
// JSONとして解析できない本文と他の種類は処理しない。
function changesSelectedFamily(message) {
  if (!selected || detail.isChild()) return false;
  const current = sessions.find(item => isSelected(selected, item));
  if (!current || current.parent_path) return false;
  let changed = sessions.find(item => isSelected({host: message.host, engine: message.engine, path: message.path}, item));
  const seen = new Set();
  while (changed?.parent_path && !seen.has(changed.path)) {
    if (changed.host === selected.host && changed.parent_path === selected.path) return true;
    seen.add(changed.path);
    changed = sessions.find(item => item.host === changed.host && item.path === changed.parent_path);
  }
  return false;
}

const detail = createSessionDetail({ENGINE_LABELS, sessionDiagnostic, formatTime, getSessions: () => sessions, onSelect: (value) => {
  selected = value;
  document.getElementById("sessions-app").dataset.view = "detail";
  document.getElementById("sessions-selection-status").textContent = "";
  renderSessions();
}});
const openSession = (...args) => detail.openSession(...args);
const refreshSelectedDetail = (...args) => detail.refreshSelectedDetail(...args);

const refreshFromRecord = coalesced(refreshSelectedDetail, "record-reload");
const SSE_MESSAGE_HANDLERS = {
  refresh: (payload) => { void resyncFromServer({trigger: "sse", notify_delay_ms: payloadNotifyDelay(payload)}); },
  record: (message) => {
    if (isSelected(selected, message) || changesSelectedFamily(message)) {
      void refreshFromRecord({trigger: "sse", notify_delay_ms: payloadNotifyDelay(message)});
    }
  },
};

const INITIAL_SUBSCRIBE_WAIT_MS = 300;
let initialLoadStarted = false;
let resolveFirstSubscription = () => {};
const firstSubscription = new Promise(resolve => { resolveFirstSubscription = resolve; });

function subscribeEvents() {
  // HTTPのopenより後に購読キューが登録されるため、登録の合図を初期取得の同期点にする。
  // 上限後の遅い登録と再接続では、購読していない間の変更を取り直す。
  connectEvents("/api/sessions/events", {
    subscribed: () => {
      if (!initialLoadStarted) {
        resolveFirstSubscription();
        return;
      }
      void resyncFromServer();
    },
    message: (event) => handleSseMessage(event, SSE_MESSAGE_HANDLERS),
  });
}

const resyncFromServer = coalesced(async (timing) => {
  await loadList({timing});
  await refreshSelectedDetail(timing);
}, "external-reload");

function cancelFullTextSearch() {
  fullTextRequest?.abort();
  fullTextRequest = null;
  fullTextMatches = null;
  fullTextStatus = "";
  searchInterrupted = false;
}

function stopSearchOnLeave() {
  clearTimeout(filterTimer);
  const input = filterEl.value.trim().toLowerCase();
  if (!fullTextRequest && input === queryText) return;
  fullTextRequest?.abort();
  fullTextRequest = null;
  if (input !== queryText) fullTextMatches = null;
  queryText = input;
  searchInterrupted = Boolean(input);
  fullTextStatus = searchInterrupted ? "検索を中断しました。" : "";
  setUpdateError("sessions", "search");
  renderSessions();
}

async function applySessionFilter() {
  cancelFullTextSearch();
  setUpdateError("sessions", "search");
  queryText = filterEl.value.trim().toLowerCase();
  visibleLimit = 100;
  if (!queryText || sessions.some(matchesFilter)) {
    renderSessions();
    return;
  }
  const request = new AbortController();
  fullTextRequest = request;
  fullTextStatus = "セッション本文を検索しています。";
  renderSessions();
  let targets;
  const matches = new Set();
  const unavailable = new Set();
  const run = createReadRecovery(request.signal, async failures => {
    try {
      const response = await timedFetch("sessions", `${BASE_PATH}/api/sessions/search`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({query: queryText, ...(targets ? {retry: targets} : {})}),
        signal: request.signal,
      });
      if (!response.ok) {
        if (readFailure(response.status) === "temporary") throw new Error();
        fullTextStatus = "セッションを検索できません。";
      } else {
        const payload = await response.json();
        if (fullTextRequest !== request) return false;
        for (const match of payload.matches) matches.add(JSON.stringify(match));
        for (const host of payload.unavailable ?? []) unavailable.add(host);
        fullTextMatches = matches;
        fullTextStatus = unavailable.size || (payload.incomplete && !payload.retry) ?
          "検索できない記録があります。" : "";
        targets = payload.retry;
        if (targets && Object.keys(targets).length) {
          if (failures) setUpdateError("sessions", "search", "一部のセッションを検索できません。", run);
          renderSessions();
          return true;
        }
      }
      setUpdateError("sessions", "search");
      fullTextRequest = null;
      renderSessions();
      return false;
    } catch {
      if (fullTextRequest !== request) return false;
      setUpdateError("sessions", "search", "セッションを検索できません。", run);
      renderSessions();
      return true;
    }
  });
  await run();
}

function selectedRecordFromUrl() {
  if (!location.pathname.endsWith("/sessions")) return null;
  const params = new URLSearchParams(location.search);
  const [host, engine, path] = ["host", "engine", "path"].map(key => params.get(key));
  return host && ["claude", "codex"].includes(engine) && path ? {host, engine, path} : null;
}

async function init() {
  listEl = document.getElementById("sessions");
  warningsEl = document.getElementById("warnings");
  detail.init();
  filterEl = document.getElementById("sessions-filter");
  document.addEventListener("atk-screen-change", () => {
    if (document.body.dataset.screen !== "sessions") stopSearchOnLeave();
  });
  document.getElementById("sessions-restart-search").addEventListener("click", () => {
    filterEl.focus();
    void applySessionFilter();
  });
  filterEl.addEventListener("input", () => {
    clearTimeout(filterTimer);
    cancelFullTextSearch();
    filterTimer = setTimeout(() => void applySessionFilter(), 300);
  });
  listEl.addEventListener("click", (event) => {
    const item = event.target.closest(".session-item");
    if (!item) return;
    openSession(item.dataset.host, item.dataset.engine, item.dataset.path);
  });
  document.getElementById("sessions-menu-btn").addEventListener("click", () => showSessionList({focus: true}));
  document.getElementById("sessions-clear-filter").addEventListener("click", () => {
    filterEl.value = "";
    clearTimeout(filterTimer);
    void applySessionFilter();
    filterEl.focus();
  });
  new IntersectionObserver(entries => {
    if (entries.some(entry => entry.isIntersecting) && visibleLimit < treeRows().length) {
      visibleLimit += 100;
      renderSessions();
    }
  }, {root: document.querySelector("#sessions-app > aside"), rootMargin: "400px"})
    .observe(document.getElementById("sessions-sentinel"));
  window.addEventListener("popstate", () => {
    const entry = selectedRecordFromUrl();
    if (entry && !isSelected(entry, selected ?? {})) void openSession(entry.host, entry.engine, entry.path, [], false);
    else if (!entry && location.pathname === `${BASE_PATH}/sessions`) {
      showSessionList({focus: matchMedia("(max-width: 768px)").matches});
    }
  });
  document.getElementById("sessions-prev-btn").addEventListener("click", () => openRelativeSession(-1));
  document.getElementById("sessions-next-btn").addEventListener("click", () => openRelativeSession(1));
  const fromUrl = selectedRecordFromUrl();
  subscribeEvents();
  await Promise.race([firstSubscription, new Promise(resolve => setTimeout(resolve, INITIAL_SUBSCRIBE_WAIT_MS))]);
  initialLoadStarted = true;
  const initialList = loadList();
  const explicitDetail = fromUrl && !selected
    ? openSession(fromUrl.host, fromUrl.engine, fromUrl.path, [], false, false, initialList) : null;
  await initialList;
  const mobile = window.matchMedia("(max-width: 768px)").matches;
  if (explicitDetail) await explicitDetail;
  else if (!selected && sessions.length > 0 && !mobile) {
    await openSession(sessions[0].host, sessions[0].engine, sessions[0].path, [], false, false);
  }
  resyncWhenVisible(resyncFromServer, {focus: false});
}

registerScreen("sessions", {init});

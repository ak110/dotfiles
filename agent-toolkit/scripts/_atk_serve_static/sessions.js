// セッション画面。左ペインで保存済み記録を選び、右ペインへ発話を時系列に表示する。
// ページロード時の初期値はサーバーが`sessions.html`のJSONブロックへ埋め込む。
const BOOTSTRAP = JSON.parse(document.getElementById("sessions-bootstrap").textContent);
// X-Forwarded-Prefix未設定または不正値時は空文字列で、すべてのfetch/EventSourceに前置する。
const BASE_PATH = BOOTSTRAP.base_path;

const ENGINE_LABELS = { claude: "Claude Code", codex: "Codex" };
const KIND_LABELS = {
  user: "ユーザー",
  assistant: "アシスタント",
  thinking: "思考・要約",
  tool_call: "ツール呼び出し",
  tool_result: "ツール結果",
  compact_boundary: "コンテキスト圧縮",
};

let sessions = [];
let selected = null;
let queryText = "";
let enabledEngines = new Set(["claude", "codex"]);

const listEl = document.getElementById("sessions");
const warningsEl = document.getElementById("warnings");
const listStatusEl = document.getElementById("list-status");
const detailEl = document.getElementById("detail");
const detailTitleEl = document.getElementById("detail-title");
const detailUsageEl = document.getElementById("detail-usage");
const hostStatusEl = document.getElementById("host-status");
const filterEl = document.getElementById("filter");

function formatTime(value) {
  if (!value) return "不明";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const pad = (n) => String(n).padStart(2, "0");
  return `${parsed.getFullYear()}/${pad(parsed.getMonth() + 1)}/${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}

function matchesFilter(entry) {
  if (!enabledEngines.has(entry.engine)) return false;
  if (!queryText) return true;
  const haystack = [entry.host, entry.project, entry.session_id, entry.path]
    .filter((value) => typeof value === "string")
    .join(" ")
    .toLowerCase();
  return haystack.includes(queryText);
}

function renderList() {
  const visible = sessions.filter(matchesFilter);
  listEl.replaceChildren();
  for (const entry of visible) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "session-item";
    item.dataset.host = entry.host;
    item.dataset.engine = entry.engine;
    item.dataset.path = entry.path;
    if (selected && selected.host === entry.host && selected.path === entry.path) {
      item.setAttribute("aria-current", "true");
    }

    const line = document.createElement("div");
    line.className = "session-line";
    const badge = document.createElement("span");
    badge.className = `engine-badge ${entry.engine}`;
    badge.textContent = ENGINE_LABELS[entry.engine] || entry.engine;
    const project = document.createElement("span");
    project.className = "session-project";
    project.textContent = entry.project || "(プロジェクト不明)";
    line.append(badge, project);

    const meta = document.createElement("div");
    meta.className = "session-meta";
    meta.textContent = `${entry.host} / ${formatTime(entry.updated_at)} / ${entry.session_id}`;
    item.append(line, meta);

    if (entry.warning) {
      const warning = document.createElement("div");
      warning.className = "session-warning";
      warning.textContent = entry.warning;
      item.append(warning);
    }
    listEl.append(item);
  }
  listStatusEl.textContent = `${visible.length}件 / 全${sessions.length}件`;
}

function renderWarnings(warnings) {
  if (!warnings || warnings.length === 0) {
    warningsEl.hidden = true;
    warningsEl.replaceChildren();
    return;
  }
  warningsEl.hidden = false;
  warningsEl.replaceChildren();
  for (const warning of warnings) {
    const line = document.createElement("div");
    line.textContent = `${warning.host}: ${warning.reason}`;
    warningsEl.append(line);
  }
}

async function loadList() {
  try {
    const response = await fetch(BASE_PATH + "/api/sessions/list");
    if (!response.ok) throw new Error(`一覧を取得できません (${response.status})`);
    const payload = await response.json();
    sessions = payload.sessions || [];
    renderWarnings(payload.warnings);
    renderList();
  } catch (error) {
    listStatusEl.textContent = String(error);
  }
}

async function loadHostStatus() {
  try {
    const response = await fetch(BASE_PATH + "/api/sessions/host-status");
    if (!response.ok) return;
    const payload = await response.json();
    const parts = Object.entries(payload.hosts || {}).map(([host, status]) => `${host}: ${status}`);
    hostStatusEl.textContent = parts.join(" / ");
  } catch (error) {
    hostStatusEl.textContent = String(error);
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

function renderEvent(event) {
  const block = document.createElement("details");
  block.className = `event kind-${event.kind}`;
  block.open = event.kind === "user" || event.kind === "assistant";

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

function renderDetail(detail) {
  detailTitleEl.textContent = `${ENGINE_LABELS[detail.engine] || detail.engine} / ${detail.host} / ${detail.project || "(プロジェクト不明)"}`;
  detailUsageEl.textContent = usageText(detail.usage);
  detailEl.replaceChildren();

  const meta = document.createElement("div");
  meta.className = "secondary-text";
  meta.textContent = `識別子: ${detail.session_id} / 開始: ${detail.started_at ? formatTime(detail.started_at) : "取得不能"}`;
  detailEl.append(meta);

  if (Array.isArray(detail.subagents) && detail.subagents.length > 0) {
    const subagents = document.createElement("details");
    subagents.className = "event kind-subagent";
    const summary = document.createElement("summary");
    summary.textContent = `サブエージェント ${detail.subagents.length}件`;
    const body = document.createElement("pre");
    body.textContent = JSON.stringify(detail.subagents, null, 2);
    subagents.append(summary, body);
    detailEl.append(subagents);
  } else if (detail.subagents === null) {
    const note = document.createElement("div");
    note.className = "secondary-text";
    appendUnavailable(note, "サブエージェントの親子関係は記録に含まれていません");
    detailEl.append(note);
  }

  if (detail.broken_lines > 0) {
    const broken = document.createElement("div");
    broken.className = "session-warning";
    broken.textContent = `解析できない行が${detail.broken_lines}件あります`;
    detailEl.append(broken);
  }

  for (const event of detail.events) {
    detailEl.append(renderEvent(event));
  }

  if (detail.truncated_events > 0) {
    const truncated = document.createElement("div");
    truncated.className = "secondary-text";
    truncated.textContent = `表示上限を超えた${detail.truncated_events}件は表示していません`;
    detailEl.append(truncated);
  }
}

async function openSession(host, engine, path) {
  selected = { host, engine, path };
  renderList();
  detailEl.replaceChildren();
  detailTitleEl.textContent = "読み込み中...";
  const query = new URLSearchParams({ host, engine, path });
  try {
    const response = await fetch(`${BASE_PATH}/api/sessions/detail?${query.toString()}`);
    if (!response.ok) throw new Error(`記録を取得できません (${response.status})`);
    renderDetail(await response.json());
  } catch (error) {
    detailTitleEl.textContent = "";
    detailEl.textContent = String(error);
  }
  document.body.classList.remove("drawer-open");
}

function subscribeEvents() {
  const source = new EventSource(BASE_PATH + "/api/sessions/events");
  source.onmessage = () => {
    loadList();
  };
  source.onerror = () => {
    // EventSourceはブラウザが自動再接続する。切断中の一覧は次の再接続で更新される。
  };
}

function main() {
  filterEl.addEventListener("input", () => {
    queryText = filterEl.value.trim().toLowerCase();
    renderList();
  });
  for (const checkbox of document.querySelectorAll(".engine-filter")) {
    checkbox.addEventListener("change", () => {
      enabledEngines = new Set(
        Array.from(document.querySelectorAll(".engine-filter"))
          .filter((element) => element.checked)
          .map((element) => element.value)
      );
      renderList();
    });
  }
  listEl.addEventListener("click", (event) => {
    const item = event.target.closest(".session-item");
    if (!item) return;
    openSession(item.dataset.host, item.dataset.engine, item.dataset.path);
  });
  document.getElementById("menu-btn").addEventListener("click", () => {
    document.body.classList.toggle("drawer-open");
  });
  document.getElementById("drawer-backdrop").addEventListener("click", () => {
    document.body.classList.remove("drawer-open");
  });
  loadList();
  loadHostStatus();
  subscribeEvents();
}

main();

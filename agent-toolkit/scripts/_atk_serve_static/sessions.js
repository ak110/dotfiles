// 3画面は同じドキュメントへ順に読み込まれるため、トップレベルの宣言を即時実行関数で囲んで
// 画面ごとにスコープを閉じる。`window.__atkScreens`への登録だけを外部へ公開する。
// 内側の字下げは、囲む前後の差分を比較できるよう元のままとする。
(() => {
// セッション画面。左ペインで保存済み記録を選び、右ペインへ発話を時系列に表示する。
// ページロード時の初期値はサーバーが`sessions.html`のJSONブロックへ埋め込む。
// 画面の入れ替えでJSONブロックの内容も差し替わるため、`mount`のたびに読み直す。
// X-Forwarded-Prefix未設定または不正値時は空文字列で、すべてのfetch/EventSourceに前置する。
let BASE_PATH = "";

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
// サブエージェントの記録は左ペインの一覧に現れないため、呼び出し元の記録を古い順に保持して戻れるようにする。
let parentTrail = [];
// SSE購読。`unmount`で閉じるため保持する。
let eventSource = null;
let isCurrentMount = () => false;

// 画面の入れ替えでDOMごと差し替わるため、参照は`mount`のたびに取り直す。
let listEl = null;
let warningsEl = null;
let detailEl = null;
let detailTitleEl = null;
let detailUsageEl = null;
let filterEl = null;

function formatTime(value) {
  if (!value) return "不明";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const pad = (n) => String(n).padStart(2, "0");
  return `${parsed.getFullYear()}/${pad(parsed.getMonth() + 1)}/${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}

function matchesFilter(entry) {
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
    const project = document.createElement("span");
    project.className = "session-project";
    project.textContent = entry.project || "(プロジェクト不明)";
    line.append(project);

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
  const currentMount = isCurrentMount;
  try {
    const response = await currentMount.wait(fetch(BASE_PATH + "/api/sessions/list"));
    if (!currentMount()) return;
    if (!response.ok) throw new Error(`一覧を取得できません (${response.status})`);
    const payload = await currentMount.wait(response.json());
    if (!currentMount()) return;
    sessions = payload.sessions || [];
    renderWarnings(payload.warnings);
    renderList();
  } catch (error) {
    if (!currentMount()) return;
    showWarnings([String(error)]);
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

function renderDetail(detail) {
  detailTitleEl.textContent = `${ENGINE_LABELS[detail.engine] || detail.engine} / ${detail.host} / ${detail.project || "(プロジェクト不明)"}`;
  detailUsageEl.textContent = usageText(detail.usage);
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

// `trail`は開こうとする記録の呼び出し元を古い順に並べる。左ペインから選んだ記録には呼び出し元が無いため既定は空とする。
async function openSession(host, engine, path, trail = []) {
  const currentMount = isCurrentMount;
  selected = { host, engine, path };
  parentTrail = trail;
  renderList();
  detailEl.replaceChildren();
  detailTitleEl.textContent = "読み込み中...";
  const query = new URLSearchParams({ host, engine, path });
  try {
    const response = await currentMount.wait(fetch(`${BASE_PATH}/api/sessions/detail?${query.toString()}`));
    if (!currentMount()) return;
    if (!response.ok) throw new Error(`記録を取得できません (${response.status})`);
    const detail = await currentMount.wait(response.json());
    if (!currentMount()) return;
    renderDetail(detail);
  } catch (error) {
    if (!currentMount()) return;
    detailTitleEl.textContent = "";
    detailEl.textContent = String(error);
  }
  document.body.classList.remove("drawer-open");
}

function subscribeEvents() {
  const currentMount = isCurrentMount;
  eventSource = new EventSource(BASE_PATH + "/api/sessions/events");
  eventSource.onmessage = () => {
    if (currentMount()) loadList();
  };
  eventSource.onerror = () => {
    // EventSourceはブラウザが自動再接続する。切断中の一覧は次の再接続で更新される。
  };
}

function mount(currentMount) {
  isCurrentMount = currentMount;
  BASE_PATH = JSON.parse(document.getElementById("sessions-bootstrap").textContent).base_path;
  listEl = document.getElementById("sessions");
  warningsEl = document.getElementById("warnings");
  detailEl = document.getElementById("detail");
  detailTitleEl = document.getElementById("detail-title");
  detailUsageEl = document.getElementById("detail-usage");
  filterEl = document.getElementById("filter");
  sessions = [];
  selected = null;
  queryText = "";
  parentTrail = [];
  filterEl.addEventListener("input", () => {
    queryText = filterEl.value.trim().toLowerCase();
    renderList();
  });
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
  subscribeEvents();
}

function unmount() {
  isCurrentMount = () => false;
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
}

window.__atkScreens = window.__atkScreens || {};
window.__atkScreens.sessions = {mount, unmount};
})();

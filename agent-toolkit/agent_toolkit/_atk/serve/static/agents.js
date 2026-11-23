import {BASE_PATH} from "./common.js";
import {registerScreen} from "./shell.js";

const active = () => document.body.dataset.screen === "agents";
const checkbox = document.getElementById("agents-terminated");
const hosts = document.getElementById("agents-hosts");
const error = document.getElementById("agents-error");

async function fetchJson(url) {
  const response = await fetch(BASE_PATH + url);
  if (!response.ok) throw new Error(`一覧の取得に失敗しました（HTTP ${response.status}）。`);
  return response.json();
}

function agentRow(agent, label) {
  const row = document.createElement("li");
  row.dataset.agentId = agent.id;
  const target = agent.record ? document.createElement("a") : document.createElement("span");
  if (agent.record) target.href = BASE_PATH + "/sessions?" + new URLSearchParams(agent.record);
  target.textContent = label;
  const state = document.createElement("span");
  state.className = "agent-status";
  state.textContent = agent.status;
  const rootDetails = agent.root ? [
    agent.name || (agent.engine ? `${agent.engine}（モデル・推論の深さは不明）` : "エンジン・モデルは不明"),
    agent.elapsed || "経過時間は不明",
    "行動・文脈使用量は不明",
  ] : [agent.name, agent.action];
  row.append(state, " ", target, " ", [...rootDetails, agent.record_error].filter(Boolean).join(" · "));
  if (agent.running || agent.status === "稼働中") row.dataset.running = "true";
  return row;
}

function renderRoots(content, roots) {
  const focused = content.contains(document.activeElement) ? document.activeElement.closest("[data-agent-id]") : null;
  if (!roots.length) {
    content.replaceChildren("稼働中の会話・委譲先はありません。");
    if (focused) checkbox.focus({preventScroll: true});
    return;
  }
  const list = document.createElement("ul");
  for (const root of roots) {
    const item = agentRow(root, root.id);
    if (root.children.length) {
      const children = document.createElement("ul");
      for (const child of root.children) children.append(agentRow(child, child.tree_label));
      item.append(children);
    }
    list.append(item);
  }
  content.replaceChildren(list);
  if (focused) {
    const sameTarget = Array.from(list.querySelectorAll("[data-agent-id]"))
      .find(row => row.dataset.agentId === focused.dataset.agentId);
    (sameTarget?.querySelector(":scope > a") ?? checkbox).focus({preventScroll: true});
  }
}

function watchHost(content, host) {
  let pending = false;
  let timer;
  async function refresh() {
    clearTimeout(timer);
    if (pending || !active()) return;
    pending = true;
    const params = new URLSearchParams({terminated: checkbox.checked ? "1" : "0"});
    if (host !== null) params.set("host", host);
    try {
      const payload = await fetchJson("/api/agents/list?" + params);
      if (payload.error) content.textContent = payload.error;
      else renderRoots(content, payload.roots);
    } catch (cause) {
      content.textContent = cause.message;
    } finally {
      pending = false;
      if (active()) timer = setTimeout(refresh, 2000);
    }
  }
  checkbox.addEventListener("change", refresh);
  document.addEventListener("atk-screen-change", refresh);
  void refresh();
}

async function init() {
  if (!active() || hosts.dataset.initialized) return;
  hosts.dataset.initialized = "true";
  try {
    const data = await fetchJson("/api/agents/hosts");
    error.hidden = true;
    for (const hostname of [null, ...data.remote]) {
      const section = document.createElement("section");
      const heading = document.createElement("h2");
      heading.textContent = hostname ?? `${data.local}（ローカル）`;
      const content = document.createElement("div");
      content.textContent = "読み込み中…";
      section.append(heading, content);
      hosts.append(section);
      watchHost(content, hostname);
    }
  } catch (cause) {
    error.textContent = cause.message;
    error.hidden = false;
    delete hosts.dataset.initialized;
  }
}
registerScreen("agents", {init: () => {
  document.addEventListener("atk-screen-change", init);
  return init();
}});

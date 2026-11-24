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

function agentText(className, value) {
  const span = document.createElement("span");
  span.className = className;
  span.textContent = value;
  return span;
}

function agentRow(agent, depth) {
  const item = document.createElement("li");
  item.dataset.agentId = agent.id;
  const row = document.createElement("div");
  row.className = "agent-row";
  row.style.setProperty("--agent-depth", depth);
  const identity = document.createElement("div");
  identity.className = "agent-identity";
  const target = agent.record ? document.createElement("a") : document.createElement("span");
  if (agent.record) target.href = BASE_PATH + "/sessions?" + new URLSearchParams(agent.record);
  target.textContent = agent.id;
  identity.append(target);
  const name = agent.name || agent.engine;
  if (name) identity.append(agentText("agent-name", name));
  row.append(identity);
  for (const [field, label] of [["status", "状態"], ["elapsed", "経過時間"], ["usage", "文脈使用量"]]) {
    if (!agent[field]) continue;
    const cell = document.createElement("span");
    cell.className = `agent-${field}`;
    cell.append(agentText("visually-hidden", `${label}: `), agentText("agent-value", agent[field]));
    row.append(cell);
  }
  const action = [agent.action, agent.retry_detail, agent.retry_count].filter(Boolean).join(" · ");
  if (action) identity.append(agentText("agent-action", action));
  if (agent.record_error) identity.append(agentText("agent-record-note", agent.record_error));
  if (agent.running || agent.status === "稼働中") row.dataset.running = "true";
  item.append(row);
  return item;
}

function agentList() {
  const list = document.createElement("ul");
  list.className = "agent-list";
  list.setAttribute("role", "list");
  return list;
}

function appendRoot(list, root) {
  const item = agentRow(root, 0);
  list.append(item);
  const ancestors = [item];
  for (const child of root.children) {
    const depth = Number.isInteger(child.depth) && child.depth > 0 ? Math.min(child.depth, ancestors.length) : 1;
    const parent = ancestors[depth - 1];
    let children = parent.querySelector(":scope > ul");
    if (!children) {
      children = agentList();
      parent.append(children);
    }
    const childItem = agentRow(child, depth);
    children.append(childItem);
    ancestors.length = depth;
    ancestors.push(childItem);
  }
}

function renderRoots(content, roots) {
  const focused = content.contains(document.activeElement) ? document.activeElement.closest("[data-agent-id]") : null;
  if (!roots.length) {
    content.replaceChildren("稼働中の会話・委譲先はありません。");
    if (focused) checkbox.focus({preventScroll: true});
    return;
  }
  const list = agentList();
  for (const root of roots) appendRoot(list, root);
  content.replaceChildren(list);
  if (focused) {
    const sameTarget = Array.from(list.querySelectorAll("[data-agent-id]"))
      .find(row => row.dataset.agentId === focused.dataset.agentId);
    (sameTarget?.querySelector(":scope > .agent-row > .agent-identity > a") ?? checkbox).focus({preventScroll: true});
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
      heading.className = "agent-host-heading";
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

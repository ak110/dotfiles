import {decorateWiReferences} from "./wi_reference.js";
import {setUpdateError, setDisplayedReadError, BASE_PATH, recoverRead} from "./common.js";
import {registerScreen} from "./shell.js";

const active = () => document.body.dataset.screen === "agents";
const checkbox = document.getElementById("agents-terminated");
const hosts = document.getElementById("agents-hosts");

async function fetchJson(url, signal) {
  const response = await recoverRead("agents", BASE_PATH + url, {signal});
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
  row.className = "agent-row list-row";
  row.style.setProperty("--agent-depth", depth);
  const identity = document.createElement("div");
  identity.className = "agent-identity";
  const target = agent.record ? document.createElement("a") : document.createElement("span");
  if (agent.record) target.href = BASE_PATH + "/sessions?" + new URLSearchParams(agent.record);
  target.textContent = agent.id;
  const name = agent.name || agent.engine;
  const caption = agentText("text-clip", "");
  caption.title = [agent.id, name].filter(Boolean).join(" · ");
  caption.append(target);
  if (name) caption.append(` · ${name}`);
  const action = agent.action?.startsWith("commandExecution:")
    ? agent.action.match(/(?:^|\s)command=([\s\S]*)$/)?.[1] || "コマンドを実行中"
    : agent.action;
  const subline = [action, agent.retry_detail, agent.retry_count, agent.record_error].filter(Boolean).join(" · ");
  const actionText = agentText("agent-action text-clip", subline);
  actionText.title = subline;
  identity.append(caption, actionText);
  row.append(identity);
  const state = {"稼働中": "running", "終了": "terminated", "不明": "unknown"}[agent.status] || agent.status;
  for (const [field, label, value] of [
    ["status", "状態", state], ["elapsed", "経過時間", agent.elapsed], ["usage", "文脈使用量", agent.usage],
  ]) {
    const cell = document.createElement("span");
    cell.className = `agent-${field} text-clip`;
    if (value) {
      cell.title = value;
      const valueNode = agentText(field === "status" ? "agent-value status-chip" : "agent-value", value);
      if (field === "status") valueNode.dataset.state = state;
      cell.append(agentText("visually-hidden", `${label}: `), valueNode);
    }
    row.append(cell);
  }
  item.append(row);
  decorateWiReferences(row);
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
    (sameTarget?.querySelector(":scope > .agent-row > .agent-identity a") ?? checkbox).focus({preventScroll: true});
  }
}

function watchHost(content, host) {
  let pending = false;
  let timer;
  let failed = false;
  let generation = 0;
  let current;
  let controller;
  async function refresh() {
    clearTimeout(timer);
    if (pending || !active()) return;
    pending = true;
    const token = generation;
    controller = new AbortController();
    const params = new URLSearchParams({terminated: checkbox.checked ? "1" : "0"});
    if (host !== null) params.set("host", host);
    try {
      const payload = await fetchJson("/api/agents/list?" + params, controller.signal);
      if (payload.error) throw new Error(payload.error);
      if (!active() || token !== generation) return;
      renderRoots(content, payload.roots);
      failed = false;
      setUpdateError("agents", host);
    } catch {
      if (!active() || token !== generation) return;
      failed = true;
      if (content.textContent === "読み込み中…") content.textContent = "";
      setDisplayedReadError("agents", host, `${host ?? "ローカル"}のエージェント一覧を取得できません。`, refresh);
    } finally {
      pending = false;
      if (active() && token !== generation) void refresh();
      else if (active() && !failed) timer = setTimeout(refresh, 2000);
    }
  }
  function change() {
    const selection = active() ? checkbox.checked : null;
    if (selection !== current) {
      current = selection;
      generation += 1;
      controller?.abort();
      failed = false;
      setUpdateError("agents", host);
    }
    void refresh();
  }
  checkbox.addEventListener("change", change);
  document.addEventListener("atk-screen-change", change);
  change();
}

async function init() {
  if (!active() || hosts.dataset.initialized) return;
  hosts.dataset.initialized = "true";
  try {
    const data = await fetchJson("/api/agents/hosts");
    setUpdateError("agents", "hosts");
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
    setDisplayedReadError("agents", "hosts", cause.message, init);
    delete hosts.dataset.initialized;
  }
}
registerScreen("agents", {init: () => {
  document.addEventListener("atk-screen-change", init);
  return init();
}});

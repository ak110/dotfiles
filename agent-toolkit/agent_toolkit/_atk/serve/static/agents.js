import {decorateWiReferences} from "./wi_reference.js";
import {recordUpdateSuccess, setUpdateError, setDisplayedReadError, BASE_PATH, recoverRead} from "./common.js";
import {registerScreen} from "./shell.js";

const checkbox = document.getElementById("agents-terminated");
const hosts = document.getElementById("agents-hosts");
const overview = document.getElementById("agents-overview");
const attention = document.getElementById("agents-attention");
const hostStates = [];
const statusLabels = {
  running: "稼働中", terminated: "終了", completed: "完了",
  failed: "失敗", interrupted: "中断", expired: "期限切れ", unknown: "不明",
};

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

function agentProgress(label) {
  const progress = document.createElement("progress");
  progress.className = "monitor-progress";
  progress.setAttribute("aria-label", label);
  return progress;
}

function statusOf(agent) {
  const state = {"稼働中": "running", "終了": "terminated", "不明": "unknown"}[agent.status] ?? agent.status;
  return Object.hasOwn(statusLabels, state) ? state : "unknown";
}

function attentionReasons(agent, state) {
  const action = agent.action ?? "";
  const waiting = action.startsWith("利用上限の解除待ち");
  const retry = !waiting && (action.startsWith("API再試行") || agent.retry_detail || agent.retry_count);
  return [
    ...(state === "unknown" ? ["状態不明"] : []),
    ...(["failed", "interrupted", "expired"].includes(state) ? [statusLabels[state]] : []),
    ...(waiting ? ["利用上限の解除待ち"] : retry ? ["再試行"] : []),
    ...(agent.record_error && !agent.record_unsupported ? ["記録を開けない"] : []),
  ];
}

function issueCounts(roots) {
  const counts = new Map();
  for (const root of roots) {
    for (const agent of [root, ...root.children]) {
      for (const reason of attentionReasons(agent, statusOf(agent))) counts.set(reason, (counts.get(reason) ?? 0) + 1);
    }
  }
  return [...counts].map(([reason, count]) => `${reason} ${count}件`);
}

function renderOverview() {
  if (!overview || !attention) return;
  const issues = hostStates.filter(host => host.failed || host.issues?.length);
  const message = issues.length ? `${issues.length}ホストで確認が必要です。` :
    hostStates.some(host => host.loading) ? "" : "表示中の一覧に、確認が必要な状態はありません。";
  if (overview.textContent !== message) overview.textContent = message;
  for (const host of hostStates) {
    const description = [host.failed ? "一覧を取得できません" : "", ...(host.issues ?? [])].filter(Boolean).join(" · ");
    if (!description && host.notice.contains(document.activeElement)) host.heading.focus({preventScroll: true});
    host.notice.hidden = !description;
    if (host.noticeText.textContent !== description) host.noticeText.textContent = description;
  }
  attention.hidden = issues.length === 0;
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
  const caption = agentText("agent-caption text-clip", "");
  caption.title = [agent.id, name].filter(Boolean).join(" · ");
  target.classList.add("agent-name");
  if (name) caption.append(agentText("agent-display-name", name), " ");
  caption.append(target);
  decorateWiReferences(caption);
  const action = agent.action?.startsWith("commandExecution:")
    ? agent.action.match(/(?:^|\s)command=([\s\S]*)$/)?.[1] || "コマンドを実行中"
    : agent.action;
  const subline = [action, agent.retry_detail, agent.retry_count, agent.record_error].filter(Boolean).join(" · ");
  identity.append(caption);
  if (subline) {
    const details = document.createElement("details");
    details.className = "agent-action-detail";
    const summary = document.createElement("summary");
    summary.append(agentText("agent-action secondary-text text-clip", subline));
    const fullText = document.createElement("p");
    fullText.textContent = subline;
    decorateWiReferences(fullText);
    details.append(summary, fullText);
    identity.append(details);
  }
  row.append(identity);
  const state = statusOf(agent);
  const issues = attentionReasons(agent, state);
  if (issues.length) row.dataset.attention = "";
  const statusDetail = issues.filter(issue => issue !== "状態不明" && issue !== statusLabels[state]);
  for (const [field, label, value] of [
    ["status", "状態", statusLabels[state]],
    ["elapsed", "経過時間", agent.elapsed], ["usage", "文脈使用量", agent.usage],
  ]) {
    const cell = document.createElement("span");
    cell.className = `agent-${field} text-clip`;
    if (value) {
      cell.title = value;
      const valueNode = agentText(field === "status" ? "agent-value status-chip" : "agent-value", value);
      if (field === "status") valueNode.dataset.state = state === "completed" ? "terminated" : state;
      cell.append(agentText("agent-field-label secondary-text", `${label}: `), valueNode);
    }
    if (field === "status" && statusDetail.length) {
      cell.append(agentText("agent-status-detail", statusDetail.join(" · ")));
    }
    row.append(cell);
  }
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
  const activeElement = document.activeElement;
  const focused = content.contains(activeElement) ? activeElement.closest("[data-agent-id]") : null;
  const opened = new Set([...content.querySelectorAll(".agent-action-detail[open]")].map(
    detail => detail.closest("[data-agent-id]").dataset.agentId,
  ));
  if (!roots.length) {
    content.replaceChildren(agentText("empty-state", "稼働中の会話・委譲先はありません。"));
    if (focused) checkbox.focus({preventScroll: true});
    return;
  }
  const list = agentList();
  for (const root of roots) appendRoot(list, root);
  let sameTarget;
  for (const item of list.querySelectorAll("[data-agent-id]")) {
    if (opened.has(item.dataset.agentId)) {
      const detail = item.querySelector(":scope > .agent-row .agent-action-detail");
      if (detail) detail.open = true;
    }
    if (item.dataset.agentId === focused?.dataset.agentId) sameTarget = item.querySelector(":scope > .agent-row");
  }
  content.replaceChildren(list);
  if (focused) {
    const target = activeElement.tagName === "SUMMARY" ? sameTarget?.querySelector("summary") :
      [...(sameTarget?.querySelectorAll("a") ?? [])].find(link => link.href === activeElement.href);
    (target ?? sameTarget?.querySelector(".agent-name[href], summary") ?? checkbox).focus({preventScroll: true});
  }
}

function watchHost(content, host, hostState) {
  let pending = false;
  let timer;
  let failed = false;
  let hasSnapshot = false;
  let generation = 0;
  let current;
  let controller;
  let snapshot;
  async function refresh() {
    clearTimeout(timer);
    if (pending) return;
    pending = true;
    const token = generation;
    controller = new AbortController();
    const params = new URLSearchParams({terminated: checkbox.checked ? "1" : "0"});
    if (host !== null) params.set("host", host);
    try {
      const payload = await fetchJson("/api/agents/list?" + params, controller.signal);
      if (payload.error) throw new Error(payload.error);
      if (token !== generation) return;
      hasSnapshot = true;
      const next = JSON.stringify(payload.roots);
      if (snapshot !== next) {
        renderRoots(content, payload.roots);
        snapshot = next;
      }
      failed = false;
      hostState.loading = false;
      hostState.failed = false;
      hostState.issues = issueCounts(payload.roots);
      renderOverview();
      recordUpdateSuccess("agents", host);
    } catch {
      if (token !== generation) return;
      failed = true;
      if (!hasSnapshot) content.replaceChildren(agentText("secondary-text", "一覧は未取得です。"));
      hostState.loading = false;
      hostState.failed = true;
      renderOverview();
      setDisplayedReadError("agents", host, `${host ?? "ローカル"}のエージェント一覧を取得できません。`, refresh);
    } finally {
      pending = false;
      if (token !== generation) void refresh();
      else if (!failed) {
        const foreground = document.body.dataset.screen === "agents" && !document.hidden;
        timer = setTimeout(refresh, foreground ? 2000 : 30_000);
      }
    }
  }
  function change() {
    const selection = checkbox.checked;
    if (selection !== current) {
      current = selection;
      generation += 1;
      controller?.abort();
      failed = false;
      setUpdateError("agents", host);
      hostState.loading = true;
      hostState.failed = false;
      renderOverview();
    }
    void refresh();
  }
  checkbox.addEventListener("change", change);
  change();
}

async function init() {
  if (hosts.dataset.initialized) return;
  hosts.dataset.initialized = "true";
  if (overview) overview.textContent = "";
  const hostProgress = agentProgress("ホスト一覧を取得中");
  hosts.append(hostProgress);
  try {
    const data = await fetchJson("/api/agents/hosts");
    recordUpdateSuccess("agents", "hosts");
    for (const hostname of [null, ...data.remote]) {
      const section = document.createElement("section");
      const heading = document.createElement("h4");
      heading.className = "agent-host-heading section-heading";
      heading.textContent = hostname ?? `${data.local}（ローカル）`;
      heading.tabIndex = -1;
      const content = document.createElement("div");
      content.append(agentProgress(`${heading.textContent}のエージェント一覧を取得中`));
      section.append(heading, content);
      hosts.append(section);
      const notice = document.createElement("li");
      notice.hidden = true;
      const jump = document.createElement("button");
      jump.className = "button-text";
      jump.type = "button";
      jump.textContent = heading.textContent;
      jump.addEventListener("click", () => heading.focus());
      const noticeText = agentText("", "");
      notice.append(jump, noticeText);
      attention?.append(notice);
      const hostState = {heading, notice, noticeText, loading: true};
      hostStates.push(hostState);
      watchHost(content, hostname, hostState);
    }
  } catch (cause) {
    if (overview) overview.textContent = "ホスト一覧は未取得です。";
    setDisplayedReadError("agents", "hosts", cause.message, init);
    delete hosts.dataset.initialized;
  } finally {
    hostProgress.remove();
  }
}
registerScreen("agents", {init});

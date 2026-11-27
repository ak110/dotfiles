import {readFailure} from "./common.js";
import {BASE_PATH, recordUpdateSuccess, setUpdateError, setDisplayedReadError, recoverRead} from "./common.js";
import {formatLocalMinute} from "./reset_permissions.js";
import {registerScreen} from "./shell.js";
import {createSettingsView, savedSettingMessage, settingName} from "./settings_view.js";

const status = document.getElementById("settings-status");
const content = document.getElementById("settings-content");
const hostPicker = document.getElementById("settings-host");
const hostProgress = document.createElement("progress");
hostProgress.id = "settings-host-progress";
hostProgress.className = "settings-host-progress";
hostProgress.setAttribute("aria-label", "設定の取得");
hostProgress.hidden = true;
hostPicker.after(hostProgress);
const fastState = document.getElementById("settings-fast-state");
const fastDetail = document.getElementById("settings-fast-detail");
const fastReason = document.getElementById("settings-fast-reason");
const shared = document.getElementById("settings-shared");
const SHARED_SCOPE = '[data-scope="shared"]';
const HOST_EDITOR = `[data-editor]:not(${SHARED_SCOPE} *)`;
function inSharedScope(node) {
  return Boolean(node.closest(SHARED_SCOPE));
}
const comparison = document.getElementById("settings-comparison");
const search = document.getElementById("settings-search");
let localData;
const navigation = document.getElementById("settings-nav");
let host = "";
let activeGroup = "models";
let busy = false;
let loading = false;
let fetching = false;
let loadGeneration = 0;
let displayedData;
let openPermissionsOnLoad = false;
const hostDrafts = new Map();
const conflicts = new Map();
const {button, element, renderComparison, renderHost, renderShared, renderSharedComparison, showGroup, getActionControl} = createSettingsView({
  content, shared, comparison, hostPicker, navigation,
  state: {
    get host() { return host; },
    get localData() { return localData; },
    get confirmedComparisonFailures() { return confirmedComparisonFailures; },
    get activeGroup() { return activeGroup; },
    set activeGroup(value) { activeGroup = value; },
  },
  operate, saveShared, migrateShared, selectHost, filterSettings, updateUnsaved, updateWriteControls, setWriteUnavailable,
  rememberSettingFocus, restoreSettingFocus, editorFields,
});

async function request(payload, {background = false, retry = false, target = host, observeUsage = false, selection} = {}) {
  const parameters = new URLSearchParams();
  if (target) parameters.set("host", target);
  if (background) parameters.set("background", "1");
  if (observeUsage) parameters.set("observe_usage", "1");
  if (retry) parameters.set("retry", "1");
  if (selection) {
    parameters.set("selection", "1");
    if (selection.after) parameters.set("after", selection.after);
    if (selection.wait) parameters.set("wait", "1");
  }
  const query = parameters.size ? "?" + parameters : "";
  const url = BASE_PATH + "/api/settings" + query;
  const response = background && !payload ? await recoverRead("settings", url, {}, observeUsage ? 115_000 : undefined) : await fetch(url, payload ? {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
  } : {signal: AbortSignal.timeout(45_000)});
  const data = await response.json();
  if (!response.ok) {
    const error = new Error([data.error, data.next_action].filter(Boolean).join(" "));
    error.status = response.status;
    throw error;
  }
  return data;
}

function editorFields(editor) {
  return Array.from(editor.querySelectorAll("input, select, textarea")).filter(input => input.closest("[data-editor]") === editor);
}

function rememberEditors(savedSlot) {
  return new Map(Array.from(content.querySelectorAll(HOST_EDITOR), editor => [
    editor.dataset.editor,
    {
      open: editor.open,
      settingDetailsOpen: editor.querySelector(".settings-setting-details")?.open,
      directAccountOpen: editor.querySelector("[data-permission-direct]")?.closest("details")?.open,
      values: editor.dataset.editor === savedSlot ? [] : editorFields(editor).map(input => {
        const instant = input.dataset.instantDisplay === input.value ? input.dataset.instant : "";
        if (editor.open && input.matches("[data-permission-account]") &&
            (input.value || editor.querySelector("[data-permission-direct]")?.value)) {
          return {value: input.value, instant};
        }
        if (input.value === input.dataset.initialValue && (!instant || instant === input.dataset.initialInstant)) return null;
        return {value: input.value, instant};
      }),
    },
  ]));
}

function revealSettingResult(output, message, stayedAtOrigin, failed = false) {
  if (!stayedAtOrigin || !output?.isConnected || output.closest("[hidden], details:not([open])")) {
    const active = document.activeElement;
    const scroller = content.closest("main");
    const field = content.contains(active) && active.matches("input, select, textarea");
    const before = field ? active.getBoundingClientRect() : null;
    const bounds = scroller.getBoundingClientRect();
    status.textContent = message;
    if (before && before.top < bounds.bottom && before.bottom > bounds.top) {
      active.scrollIntoView({block: "nearest", inline: "nearest"});
    }
    return;
  }
  status.textContent = "";
  output.setAttribute("role", failed ? "alert" : "status");
  output.textContent = message;
  output.tabIndex = -1;
  output.focus({preventScroll: true});
  output.scrollIntoView({block: "nearest"});
}

function rememberSettingFocus(active) {
  if (navigation.contains(active)) return {group: active.dataset.group};
  if (content.contains(active) && active.dataset.migrationFocus) return {migration: active.dataset.migrationFocus};
  const editor = active.closest("[data-editor]");
  const label = active.labels?.[0]?.textContent;
  if (!editor || !content.contains(active)) return null;
  if (active.matches("summary") && active.parentElement === editor) {
    return {editor: editor.dataset.editor, shared: inSharedScope(editor), summary: true};
  }
  const sharedAction = active.dataset.sharedConflictAction;
  if (sharedAction) return {editor: editor.dataset.editor, shared: true, sharedAction};
  if (!label) return null;
  return {
    editor: editor.dataset.editor,
    shared: inSharedScope(editor),
    label,
    start: active.selectionStart,
    end: active.selectionEnd,
    direction: active.selectionDirection,
  };
}

function restoreSettingFocus(focus) {
  if (!focus) return;
  if (focus.migration) {
    const target = content.querySelector(`[data-migration-focus="${CSS.escape(focus.migration)}"]`);
    if (target && !target.closest("[hidden]")) target.focus({preventScroll: true});
    return;
  }
  if (focus.group) {
    navigation.querySelector(`[data-group="${CSS.escape(focus.group)}"]`)?.focus({preventScroll: true});
    return;
  }
  const slot = `[data-editor="${CSS.escape(focus.editor)}"]`;
  const editor = content.querySelector(focus.shared ? `${SHARED_SCOPE} ${slot}` : `${HOST_EDITOR}${slot}`);
  if (!editor || editor.closest("[hidden]")) return;
  if (focus.summary) {
    if (editor !== document.activeElement?.parentElement) {
      editor.querySelector(":scope > summary")?.focus({preventScroll: true});
    }
    return;
  }
  if (focus.sharedAction) {
    editor.querySelector(`[data-shared-conflict-action="${CSS.escape(focus.sharedAction)}"]`)?.focus({preventScroll: true});
    return;
  }
  const fields = editorFields(editor).filter(field => field.labels[0]?.textContent === focus.label);
  const [target] = fields;
  if (fields.length !== 1 || target === document.activeElement || target.closest("[hidden]")) return;
  for (let details = target.closest("details:not([open])"); details; details = details.closest("details:not([open])")) {
    details.open = true;
  }
  target.focus({preventScroll: true});
  if (focus.start != null && target.selectionStart != null) target.setSelectionRange(focus.start, focus.end, focus.direction);
}

function saveErrorMessage(error, saved) {
  return saved ? "保存済みです。最新の値を取得できなかったため、設定画面を再読み込みして確認してください。" : error.message;
}

function startSaving() {
  const control = getActionControl();
  const label = control?.getAttribute("aria-label");
  control?.setAttribute("data-saving", "");
  control?.setAttribute("aria-label", `${label ?? control.textContent}: 保存中`);
  return () => {
    control?.removeAttribute("data-saving");
    if (label == null) control?.removeAttribute("aria-label");
    else control?.setAttribute("aria-label", label);
  };
}

async function operate(payload, output, submittedEditor = null) {
  if (busy || loading || staleSnapshotHost === host || conflicts.has(output.closest("[data-editor]")?.dataset.editor)) return;
  const trigger = document.activeElement;
  const stopSaving = startSaving();
  const stayedAtOrigin = () => document.activeElement === trigger || document.activeElement === document.body;
  busy = true;
  hostPicker.disabled = true;
  const before = displayedData;
  const oldEditor = output.closest("[data-editor]");
  const editorKey = oldEditor?.dataset.editor;
  status.textContent = "";
  output.textContent = "";
  updateWriteControls();
  let saved = false;
  try {
    await request(host && before?.fetched_at ? {...payload, settings_revision: before.fetched_at} : payload);
    saved = true;
    const data = await request();
    const stayed = stayedAtOrigin();
    const focus = stayed ? null : rememberSettingFocus(document.activeElement);
    const scroll = content.closest("main");
    const scrollTop = scroll.scrollTop;
    const submittedKey = submittedEditor?.dataset.editor ?? editorKey;
    const drafts = rememberEditors(submittedKey);
    if (submittedKey === "usage_reset_permissions:") drafts.delete(submittedKey);
    for (const {key, old} of conflicts.values()) data.settings[key] = old;
    render(data);
    const savedEditor = restoreEditors(drafts, editorKey);
    renderConflicts();
    updateUnsaved();
    const notice = savedEditor?.querySelector("[data-settings-result]");
    scroll.scrollTop = scrollTop;
    restoreSettingFocus(focus);
    revealSettingResult(notice, savedSettingMessage(payload, before, data), stayed);
  } catch (error) {
    if (host && (saved || error.status === 409)) {
      staleSnapshotHost = host;
      setDisplayedReadError("settings", "refresh", "最新の設定を取得し直してから保存してください。", () => loadHost(true, true));
    }
    revealSettingResult(output, saveErrorMessage(error, saved), stayedAtOrigin(), true);
  } finally {
    stopSaving();
    busy = false;
    hostPicker.disabled = false;
    updateWriteControls();
  }
}

function restoreEditors(drafts, editorKey) {
  let savedEditor;
  for (const editor of content.querySelectorAll(HOST_EDITOR)) {
    const previous = drafts.get(editor.dataset.editor);
    if (previous?.open) editor.open = true;
    const settingDetails = editor.querySelector(".settings-setting-details");
    if (settingDetails && previous?.settingDetailsOpen) settingDetails.open = true;
    if (editor.dataset.editor === "temporary" && previous?.values[0] != null) {
      const key = editor.querySelector("select");
      key.value = previous.values[0].value;
      key.dispatchEvent(new Event("change"));
    }
    editorFields(editor).forEach((input, index) => {
      const draft = previous?.values[index];
      if (!draft) return;
      if (input.matches("[data-permission-account]") && input.options.length &&
          !Array.from(input.options).some(option => option.value === draft.value)) {
        input.add(new Option(draft.value ? "編集中のアカウント" : "直接入力", draft.value));
        input.parentElement.hidden = false;
      }
      input.value = draft.value;
      if (draft.instant) {
        input.dataset.instant = draft.instant;
        input.dataset.instantDisplay = draft.value;
      }
      if (input.hasAttribute("data-draft-trigger")) input.dispatchEvent(new Event("change"));
    });
    const directAccount = editor.querySelector("[data-permission-direct]")?.closest("details");
    if (directAccount && previous?.directAccountOpen) directAccount.open = true;
    if (editor.dataset.editor === editorKey) savedEditor = editor;
  }
  return savedEditor;
}

async function migrateShared(payload, output) {
  if (busy || loading || staleSnapshotHost === host || migrationReadFailed) return null;
  busy = true;
  hostPicker.disabled = true;
  const stopSaving = startSaving();
  updateWriteControls();
  const target = host;
  status.textContent = "";
  output.textContent = "";
  try {
    const query = target ? "?" + new URLSearchParams({host: target}) : "";
    const response = await fetch(BASE_PATH + "/api/settings/shared-migration" + query, {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error([data.error, data.next_action].filter(Boolean).join(" "));
    status.textContent = "";
    if (payload.action !== "apply") return data;
    busy = false;
    await loadHost();
    const message = "選んだ値を共有先に保存しました。";
    revealSettingResult(null, message, true);
    return null;
  } catch (error) {
    status.textContent = "";
    revealSettingResult(output, error.message, true, true);
    return null;
  } finally {
    stopSaving();
    busy = false;
    hostPicker.disabled = false;
    updateWriteControls();
  }
}

async function saveShared(payload, output) {
  if (busy || (host && loading) || staleSnapshotHost === host ||
      output.closest("[data-setting]")?.querySelector(".settings-shared-conflict")) return;
  const trigger = document.activeElement;
  const stopSaving = startSaving();
  const before = localData;
  const editorKey = output.closest("[data-editor]")?.dataset.editor;
  const stayedAtOrigin = () => document.activeElement === trigger || document.activeElement === document.body;
  busy = true;
  hostPicker.disabled = true;
  status.textContent = "";
  output.textContent = "";
  updateWriteControls();
  let saved = false;
  try {
    await request(payload, {target: ""});
    saved = true;
    const updated = await request(undefined, {target: ""});
    const stayed = stayedAtOrigin();
    localData = updated;
    renderComparison(displayedData ?? updated);
    renderShared(output.closest("[data-setting]")?.dataset.setting, editorKey);
    filterSettings();
    const notice = content.querySelector(`${SHARED_SCOPE} [data-editor="${CSS.escape(editorKey ?? "")}"] [data-settings-result]`);
    revealSettingResult(notice, savedSettingMessage(payload, before, updated), stayed);
  } catch (error) {
    revealSettingResult(output, saveErrorMessage(error, saved), stayedAtOrigin(), true);
  } finally {
    stopSaving();
    busy = false;
    hostPicker.disabled = false;
    updateWriteControls();
    updateUnsaved();
  }
}

let comparisonRetry;
let confirmedComparisonFailures = new Set();
function showAutomaticErrors(data, retried = false) {
  if (!data.automatic_errors) return;
  const failures = Object.entries(data.automatic_errors);
  confirmedComparisonFailures = new Set(
    failures.filter(([name]) => retried || confirmedComparisonFailures.has(name)).map(([name]) => name)
  );
  renderSharedComparison();
  const announced = failures.filter(([name]) => name !== host && confirmedComparisonFailures.has(name));
  if (announced.length) {
    setDisplayedReadError("settings", "automatic", announced.map(([name, reason]) => `${name}: ${reason}`).join(" / "), retryComparison);
  } else setUpdateError("settings", "automatic");
  const unconfirmed = failures.some(([name]) => !confirmedComparisonFailures.has(name));
  if (!unconfirmed) {
    clearTimeout(comparisonRetry);
    comparisonRetry = undefined;
  } else if (comparisonRetry === undefined && document.body.dataset.screen === "settings") {
    comparisonRetry = setTimeout(() => {
      comparisonRetry = undefined;
      if (document.body.dataset.screen === "settings") void retryComparison();
    }, 1500);
  }
}

async function retryComparison() {
  try {
    localData = await request(undefined, {background: true, retry: true, target: ""});
    renderShared();
    renderComparison(localData);
    setUpdateError("settings", "comparison");
    showAutomaticErrors(localData, true);
    filterSettings();
  } catch (error) {
    setDisplayedReadError("settings", "comparison", error.message, retryComparison);
  }
}

function filterSettings() {
  const needle = search.value.trim().toLowerCase();
  let count = 0;
  for (const editor of content.querySelectorAll("[data-setting]")) {
    editor.hidden = !!needle && !editor.dataset.search.includes(needle);
    if (!editor.hidden) count++;
  }
  showGroup(Boolean(needle));
  document.getElementById("settings-search-clear").hidden = !needle;
  document.getElementById("settings-no-matches").hidden = !needle || !displayedData || count > 0;
}

function draftLabel(slot) {
  if (slot.startsWith("setting:")) return settingName(slot.slice("setting:".length));
  if (slot.startsWith("preset:")) return `プリセット: ${slot.slice("preset:".length)}`;
  if (slot.startsWith("repository:")) return `リポジトリ: ${slot.slice("repository:".length)}`;
  return slot === "temporary" ? "一時指定" : slot;
}

function updateUnsaved() {
  const list = document.getElementById("settings-unsaved");
  const drafts = [...content.querySelectorAll("[data-editor]")]
    .filter(editor => editorFields(editor).some(input => input.value !== input.dataset.initialValue));
  const other = [...hostDrafts.entries()].filter(([name]) => name !== host).flatMap(([name, {editors}]) =>
    [...editors].filter(([, draft]) => draft.values.some(Boolean)).map(([slot]) => [name, slot]));
  const changed = new Set(drafts);
  for (const editor of content.querySelectorAll("[data-editor]")) {
    const unsaved = changed.has(editor);
    editor.toggleAttribute("data-unsaved", unsaved);
    const label = editor.querySelector(":scope > summary .settings-draft-state");
    if (label) label.hidden = !unsaved;
  }
  const count = drafts.length + other.length;
  list.replaceChildren();
  if (!count) {
    window.removeEventListener("beforeunload", warnOnUnload);
    return;
  }
  window.addEventListener("beforeunload", warnOnUnload);
  list.append(element("p", `未保存の変更: ${count}件`));
  for (const editor of drafts) list.append(button(`${editor.dataset.editorLabel ?? draftLabel(editor.dataset.editor)}へ移動`, () => {
    const group = editor.closest("section[id^=settings-group-]");
    if (group) activeGroup = group.id.replace("settings-group-", "");
    search.value = "";
    filterSettings();
    for (let detail = editor.closest("details:not([open])"); detail; detail = detail.closest("details:not([open])")) {
      detail.open = true;
    }
    editorFields(editor)[0]?.focus();
  }));
  for (const [name, slot] of other) list.append(button(`${name || "ローカル"}の設定を開く（${draftLabel(slot)}が未保存）`, () => selectHost(name)));
  list.append(button("未保存の変更を取り消す", () => {
    for (const editor of drafts) {
      for (const input of editorFields(editor)) input.value = input.dataset.initialValue;
      const result = (editor.closest("[data-setting]") ?? editor).querySelector("[data-settings-result]");
      if (result?.getAttribute("role") === "alert") result.textContent = "";
    }
    hostDrafts.clear();
    updateUnsaved();
  }));
}

search.addEventListener("input", filterSettings);
document.getElementById("settings-search-clear").addEventListener("click", () => {
  search.value = "";
  filterSettings();
  search.focus();
});
content.addEventListener("input", updateUnsaved);
function warnOnUnload(event) {
  event.preventDefault();
  event.returnValue = "";
}
document.addEventListener("atk-screen-will-change", event => {
  if (document.body.dataset.screen === "settings" && document.getElementById("settings-unsaved").hasChildNodes() &&
      !window.confirm("未保存の設定があります。この画面を離れますか？")) event.preventDefault();
});

function renderFastStart(data, failed = false) {
  const start = data?.codex_fast_start;
  const known = start && typeof start === "object" && ["通常", "高速"].includes(start.speed);
  const scope = host || "ローカル";
  let result;
  let detail;
  if (known) {
    const minute = Number.isFinite(Date.parse(start.at)) ? formatLocalMinute(start.at).replace("T", " ") : "";
    if (start.source === "今回確認") {
      result = start.speed;
      detail = [minute, start.reason].filter(Boolean).join("、");
    } else if (start.source === "直前の成功判定") {
      result = "前回の判定を使用";
      detail = [minute, `使用した速度: ${start.speed}`, start.reason].filter(Boolean).join("、");
    } else if (start.source === "確認不能") {
      result = "自動判定は未確認";
      detail = [minute, `この起動で使った速度: ${start.speed}`, start.reason].filter(Boolean).join("、");
      detail += "。次の起動後に判定を確認してください。";
    } else {
      result = "判定根拠の記録なし";
      detail = [minute, `記録上の速度: ${start.speed}`, start.reason].filter(Boolean).join("、");
    }
  } else if (start === null) {
    result = "記録なし";
    detail = "このホストで自動判定を使ってCodexを開始すると、結果を確認できます。";
  } else if (failed) {
    result = "取得できませんでした";
    detail = "設定を再取得すると、判定の記録も確認できます。";
  } else if (data) {
    result = "判定を取得できません";
    detail = data.legacy ? "接続先のatkを更新してください。" : "このホストは判定の記録を返していません。";
  } else {
    result = "未取得";
    detail = "";
  }
  if (data && failed) {
    result += "（前回取得）";
    detail += " 最新の記録は、設定を再取得すると確認できます。";
  }
  fastState.textContent = `Codexの起動時判定（${scope}）: ${result}`;
  fastReason.textContent = detail;
}

function render(data) {
  displayedData = data;
  renderFastStart(data);
  if (!host) {
    localData = data;
  } else if (localData && data.comparison) {
    localData = {...localData, comparison: data.comparison};
  }
  renderComparison(data);
  renderHost(data);
}

function syncWriteControl(control) {
  control.disabled = control.hasAttribute("data-write-blocked") || control.hasAttribute("data-write-unavailable");
}

function setWriteUnavailable(control, unavailable) {
  control.toggleAttribute("data-write-unavailable", unavailable);
  syncWriteControl(control);
}

let migrationReadFailed = false;

function updateWriteControls() {
  for (const control of content.querySelectorAll("[data-observed-deadline]")) {
    control.toggleAttribute("data-write-blocked", Boolean(host) && (loading || staleSnapshotHost === host));
    syncWriteControl(control);
  }
  const awaitingMigrationRead = loading || migrationReadFailed || staleSnapshotHost === host;
  for (const control of content.querySelectorAll("[data-shared-migration] button")) {
    control.disabled = control.hasAttribute("data-saving");
    if (busy || awaitingMigrationRead) control.setAttribute("aria-disabled", "true");
    else control.removeAttribute("aria-disabled");
    if (awaitingMigrationRead) control.setAttribute("aria-describedby", "settings-migration-wait");
    else control.removeAttribute("aria-describedby");
  }
  const migrationWait = content.querySelector("[data-migration-wait]");
  if (migrationWait) migrationWait.hidden = !awaitingMigrationRead;
  for (const control of content.querySelectorAll("button:not([data-conflict-action])")) {
    if (control.hasAttribute("data-local-edit") && !inSharedScope(control)) continue;
    const editor = control.closest("[data-editor]");
    if (!editor) continue;
    const setting = control.closest("[data-setting]") || editor;
    const blocked = busy || (host && loading) || staleSnapshotHost === host || setting.querySelector(".settings-shared-conflict") ||
      (!inSharedScope(control) && (loading || conflicts.has(setting.dataset.editor)));
    if (blocked) {
      control.dataset.writeBlocked = "";
      const reason = loading ? (hostProgress.hidden ? "settings-status" : hostProgress.id) : setting.querySelector("[data-conflict]")?.id;
      if (reason) control.setAttribute("aria-describedby", reason);
      else control.removeAttribute("aria-describedby");
    } else if (control.hasAttribute("data-write-blocked")) {
      control.removeAttribute("data-write-blocked");
      control.removeAttribute("aria-describedby");
    }
    syncWriteControl(control);
  }
}

function resolveConflict(slot, keepDraft) {
  const conflict = conflicts.get(slot);
  if (!conflict) return;
  const drafts = rememberEditors();
  conflicts.delete(slot);
  if (!keepDraft) {
    const setting = drafts.get(slot);
    if (setting) drafts.set(slot, {...setting, values: []});
    for (const key of drafts.keys()) if (key.startsWith(conflict.key + ":")) drafts.delete(key);
    displayedData.settings[conflict.key] = conflict.latest;
    render(displayedData);
    restoreEditors(drafts);
    renderConflicts();
    updateUnsaved();
  } else {
    content.querySelector(`${HOST_EDITOR}[data-editor="${CSS.escape(slot)}"] [data-conflict]`)?.remove();
    updateWriteControls();
  }
}

function renderConflicts() {
  for (const [slot, {key, latest}] of conflicts) {
    const editor = content.querySelector(`${HOST_EDITOR}[data-editor="${CSS.escape(slot)}"]`);
    if (!editor) continue;
    const notice = element("div", "", "notice notice-warning");
    notice.dataset.conflict = "";
    notice.id = `settings-conflict-${key}`;
    const latestValue = key === "usage_reset_permissions" ?
      JSON.parse(latest.saved).map(entry => `${entry.id} · ${entry.count}回 · ${entry.from}〜${entry.until}`).join(" / ") || "許可なし" :
      latest.saved;
    notice.append(
      element("p", "取得中に保存値が変更されました。"),
      element("p", `最新の保存値: ${latestValue}`),
    );
    for (const [label, keepDraft] of [["保存値を使う", false], ["入力を残す", true]]) {
      const choice = button(label, () => resolveConflict(slot, keepDraft));
      choice.dataset.conflictAction = "";
      notice.append(choice);
    }
    editor.append(notice);
  }
  updateWriteControls();
}

let observingHost;
let staleSnapshotHost;
async function observeUsage() {
  if (document.body.dataset.screen !== "settings" || busy || fetching) return;
  const target = host;
  if (observingHost === target || (target && !displayedData?.can_observe_usage)) return;
  const generation = loadGeneration;
  observingHost = target;
  if (target) {
    staleSnapshotHost = target;
    updateWriteControls();
  }
  try {
    for (const delay of [0, 1500]) {
      if (delay) await new Promise(resolve => setTimeout(resolve, delay));
      if (host !== target || generation !== loadGeneration) return;
      try {
        const data = await request(undefined, {background: true, target, observeUsage: true});
        if (host !== target || generation !== loadGeneration || busy || document.body.dataset.screen !== "settings") return;
        const failed = data.reset_editor?.refresh_failed;
        if (failed && !delay && !target) continue;
        const changed = JSON.stringify(data.reset_editor) !== JSON.stringify(displayedData?.reset_editor);
        if (target || changed) applyHostSnapshot(data, displayedData, rememberEditors());
        if (!failed) {
          setUpdateError("settings", "usage");
          return;
        }
      } catch {
        if (target && host === target && generation === loadGeneration) {
          staleSnapshotHost = target;
          updateWriteControls();
        }
      }
    }
    if (host === target && document.body.dataset.screen === "settings") {
      setDisplayedReadError("settings", "usage", "利用枠を取得できません。", observeUsage);
    }
  } finally {
    if (observingHost === target) observingHost = undefined;
  }
}

function applyHostSnapshot(data, previous, drafts) {
  const scroll = content.closest("main");
  const scrollTop = scroll.scrollTop;
  const focus = rememberSettingFocus(document.activeElement);
  const rendered = {...data, settings: {...data.settings}};
  for (const [key, latest] of Object.entries(data.settings)) {
    const slot = `setting:${key}`;
    const old = previous?.settings[key];
    const edited = Array.from(drafts).some(([name, draft]) =>
      (name === slot || name.startsWith(key + ":")) && draft.values.some(Boolean));
    if (conflicts.has(slot)) {
      const conflict = conflicts.get(slot);
      conflicts.set(slot, {...conflict, latest});
      rendered.settings[key] = conflict.old;
      continue;
    }
    if (!old || latest.read_only || old.saved === latest.saved || !edited) continue;
    conflicts.set(slot, {key, old, latest});
    rendered.settings[key] = old;
  }
  render(rendered);
  restoreEditors(drafts);
  applyPermissionsShortcut();
  renderConflicts();
  updateUnsaved();
  scroll.scrollTop = scrollTop;
  staleSnapshotHost = undefined;
  migrationReadFailed = false;
  loading = false;
  updateWriteControls();
  restoreSettingFocus(focus);
  recordUpdateSuccess("settings", "refresh");
  showAutomaticErrors(data);
  const legacy = data.legacy || data.automatic_unsupported?.includes(host);
  status.textContent = legacy ? "旧バージョンのatkです。実効値を表示しています。一部の値の編集にはSSH先のatkの更新が必要です。" : "";
}

async function loadHost(background = false, retry = false) {
  if (busy || (background && fetching)) return;
  const generation = ++loadGeneration;
  fetching = true;
  loading = true;
  hostProgress.hidden = false;
  updateWriteControls();
  if (!background) {
    if (!host || !displayedData) status.textContent = "";
    if (!displayedData) renderFastStart();
  }
  let selectedLegacy = false;
  try {
    const displayedHost = host;
    const savedDrafts = hostDrafts.get(displayedHost);
    const editingSnapshot = displayedData ?? savedDrafts?.snapshot;
    if (background && displayedHost && !retry) {
      const local = await request(undefined, {background: true, target: ""});
      if (generation !== loadGeneration) return;
      localData = local;
      renderShared();
      renderComparison(local);
      filterSettings();
      showAutomaticErrors(local);
      if (displayedData?.legacy || local.automatic_unsupported?.includes(displayedHost)) {
        loading = false;
        updateWriteControls();
        return;
      }
    }
    let data;
    if (displayedHost && (!background || retry)) {
      data = await request(undefined, {retry, target: displayedHost, selection: {after: editingSnapshot?.fetched_at}});
      if (generation !== loadGeneration) return;
      if (data.settings_state && data.settings_state !== "ready" && data.settings && !displayedData) {
        render(data);
        restoreEditors(savedDrafts?.editors ?? new Map());
        updateUnsaved();
        updateWriteControls();
      }
      if (data.settings_state === "pending") {
        if (displayedData) status.textContent = "前回取得した設定を表示中。最新の設定を取得しています。";
        data = await request(undefined, {target: displayedHost, selection: {wait: true}});
        if (generation !== loadGeneration) return;
      }
      if (data.settings_state === "legacy" || data.settings_state === "error") {
        showAutomaticErrors(data);
        if (localData && data.comparison) {
          localData = {...localData, comparison: data.comparison};
          renderSharedComparison();
        }
        selectedLegacy = data.settings_state === "legacy";
        throw new Error("");
      }
    } else {
      try {
        data = await request(undefined, {background, retry, target: displayedHost});
      } catch (error) {
        const transient = readFailure(error.status) === "temporary";
        if (retry || !transient || generation !== loadGeneration) throw error;
        await new Promise(resolve => setTimeout(resolve, 1500));
        if (generation !== loadGeneration) return;
        data = await request(undefined, {background: true, retry: Boolean(displayedHost), target: displayedHost});
      }
      if (generation !== loadGeneration) return;
    }
    const drafts = displayedData ? rememberEditors() : savedDrafts?.editors ?? new Map();
    applyHostSnapshot(data, editingSnapshot ?? displayedData, drafts);
  } catch (error) {
    if (generation !== loadGeneration) return;
    if (host) staleSnapshotHost = host;
    migrationReadFailed = true;
    loading = Boolean(host) && Boolean(displayedData);
    renderFastStart(displayedData, true);
    hostProgress.hidden = true;
    updateWriteControls();
    const snapshotStatus = loading ? "前回取得した設定を表示中。保存するには再取得してください。" : "";
    const hostStatus = selectedLegacy ? "SSH先のatkが旧バージョンです。設定を確認するにはSSH先のatkを更新してください。" : "";
    const messageStatus = [hostStatus, snapshotStatus].filter(Boolean).join(" ");
    if (status.textContent !== messageStatus) status.textContent = messageStatus;
    const message = host ? [`${host}の設定を取得できません。`, error.message].filter(Boolean).join(" ") : error.message;
    setDisplayedReadError("settings", "refresh", message, () => loadHost(true, true), () => loadHost(false, true));
  } finally {
    if (generation === loadGeneration) {
      fetching = false;
      hostProgress.hidden = true;
      hostPicker.disabled = false;
    }
  }
}

function applyPermissionsShortcut() {
  if (!openPermissionsOnLoad || host) return;
  const editor = content.querySelector('[data-setting="usage_reset_permissions"]');
  if (!editor) return;
  editor.open = true;
  openPermissionsOnLoad = false;
}

function restoreSettingsFromUrl() {
  if (document.body.dataset.screen !== "settings") return;
  const query = new URLSearchParams(location.search);
  if (query.get("group") === "usage") activeGroup = "usage";
  openPermissionsOnLoad = query.get("group") === "usage" && query.get("host") === "";
  if (openPermissionsOnLoad) search.value = "";
  if (query.get("host") === "" && host) {
    selectHost("");
    return;
  }
  if (displayedData && query.get("group") === "usage") {
    filterSettings();
    applyPermissionsShortcut();
  }
}

async function refreshSettings() {
  if (document.hidden || busy || fetching) return;
  const refresh = loadHost(true);
  const generation = loadGeneration;
  await refresh;
  prepareRemotes();
  if (generation === loadGeneration && content.querySelector('details[data-editor^="usage_reset_permissions:"][open]')) {
    await observeUsage();
  }
}

let preparation;
function prepareRemotes() {
  if (preparation || !localData?.hosts?.length) return;
  preparation = fetch(BASE_PATH + "/api/settings/prepare", {method: "POST"}).then(response => {
    if (!response.ok) preparation = undefined;
  }, () => { preparation = undefined; });
}

function init() {
  // pyfltr: allow-comment /settings の直ロードではサーバー側で先行取得を始めている。
  if (document.body.dataset.screen === "settings") preparation = Promise.resolve();
  restoreSettingsFromUrl();
  window.addEventListener("popstate", restoreSettingsFromUrl);
  setInterval(() => void refreshSettings(), 30_000);
  return loadHost().then(prepareRemotes);
}

function selectHost(selected) {
  if (busy) return;
  if (displayedData) hostDrafts.set(host, {snapshot: displayedData, editors: rememberEditors()});
  setUpdateError("settings", "refresh");
  setUpdateError("settings", "usage");
  host = selected;
  hostPicker.value = host;
  const cached = hostDrafts.get(host);
  displayedData = undefined;
  fastDetail.open = false;
  renderFastStart();
  conflicts.clear();
  loading = true;
  staleSnapshotHost = host || undefined;
  if (host && cached) {
    render({...cached.snapshot, comparison: localData?.comparison});
    restoreEditors(cached.editors);
    status.textContent = "前回取得した設定を表示中。最新の設定を取得しています。";
  } else {
    renderHost({settings: {}, paths: {}, hosts: localData?.hosts, loadingHost: true});
  }
  updateUnsaved();
  void loadHost();
}

hostPicker.addEventListener("change", () => selectHost(hostPicker.value));

registerScreen("settings", {init});

content.addEventListener("click", event => {
  if (event.target.closest('details[data-editor^="usage_reset_permissions:"] > summary')) {
    setTimeout(() => void observeUsage(), 0);
  }
});

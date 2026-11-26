import {readFailure} from "./common.js";
import {BASE_PATH, setUpdateError, setDisplayedReadError, recoverRead} from "./common.js";
import {formatLocalMinute} from "./reset_permissions.js";
import {registerScreen} from "./shell.js";
import {createSettingsView} from "./settings_view.js";

const status = document.getElementById("settings-status");
const content = document.getElementById("settings-content");
const hostPicker = document.getElementById("settings-host");
const fastState = document.getElementById("settings-fast-state");
const fastDetail = document.getElementById("settings-fast-detail");
const fastReason = document.getElementById("settings-fast-reason");
const shared = document.getElementById("settings-shared");
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
let refreshTimer;
const hostDrafts = new Map();
const conflicts = new Map();
const {button, element, renderComparison, renderHost, renderShared, renderSharedComparison, showGroup} = createSettingsView({
  content, shared, comparison, hostPicker, navigation,
  state: {
    get host() { return host; },
    get localData() { return localData; },
    get confirmedComparisonFailures() { return confirmedComparisonFailures; },
    get activeGroup() { return activeGroup; },
    set activeGroup(value) { activeGroup = value; },
  },
  operate, saveShared, selectHost, filterSettings, updateUnsaved, updateWriteControls, setWriteUnavailable,
  rememberSettingFocus, restoreSettingFocus, editorFields,
});

async function request(payload, cached = false, background = false, retry = false, target = host, observeUsage = false) {
  const parameters = new URLSearchParams();
  if (target) parameters.set("host", target);
  if (cached) parameters.set("cached", "1");
  if (background) parameters.set("background", "1");
  if (observeUsage) parameters.set("observe_usage", "1");
  if (retry) parameters.set("retry", "1");
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
  return Array.from(editor.querySelectorAll("input, select")).filter(input => input.closest("[data-editor]") === editor);
}

function rememberEditors(savedSlot) {
  return new Map(Array.from(content.querySelectorAll("[data-editor]"), editor => [
    editor.dataset.editor,
    {
      open: editor.open,
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

function revealSettingResult(output, message, stayedAtOrigin) {
  if (!stayedAtOrigin || !output?.isConnected || output.closest("[hidden], details:not([open])")) {
    const active = document.activeElement;
    const scroller = content.closest("main");
    const field = content.contains(active) && active.matches("input, select");
    const before = field ? active.getBoundingClientRect() : null;
    const bounds = scroller.getBoundingClientRect();
    status.textContent = message;
    if (before && before.top < bounds.bottom && before.bottom > bounds.top) {
      active.scrollIntoView({block: "nearest", inline: "nearest"});
    }
    return;
  }
  status.textContent = "";
  output.textContent = message;
  output.tabIndex = -1;
  output.focus({preventScroll: true});
  output.scrollIntoView({block: "nearest"});
}

function rememberSettingFocus(active) {
  if (navigation.contains(active)) return {group: active.dataset.group};
  const editor = active.closest("[data-editor]");
  const label = active.labels?.[0]?.textContent;
  if (!editor || (!shared.contains(active) && !content.contains(active))) return null;
  const sharedAction = active.dataset.sharedConflictAction;
  if (sharedAction) return {editor: editor.dataset.editor, shared: true, sharedAction};
  if (!label) return null;
  return {
    editor: editor.dataset.editor,
    shared: shared.contains(editor),
    label,
    start: active.selectionStart,
    end: active.selectionEnd,
    direction: active.selectionDirection,
  };
}

function restoreSettingFocus(focus) {
  if (!focus) return;
  if (focus.group) {
    navigation.querySelector(`[data-group="${CSS.escape(focus.group)}"]`)?.focus({preventScroll: true});
    return;
  }
  const editor = (focus.shared ? shared : content).querySelector(`[data-editor="${CSS.escape(focus.editor)}"]`);
  if (!editor) return;
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

async function operate(payload, output, submittedEditor = null) {
  if (busy || loading || staleSnapshotHost === host || conflicts.has(output.closest("[data-editor]")?.dataset.editor)) return;
  const trigger = document.activeElement;
  const stayedAtOrigin = () => document.activeElement === trigger || document.activeElement === document.body;
  busy = true;
  hostPicker.disabled = true;
  const oldEditor = output.closest("[data-editor]");
  const editorKey = oldEditor?.dataset.editor;
  status.textContent = "設定を保存中…";
  output.textContent = "";
  updateWriteControls();
  try {
    await request(payload);
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
    revealSettingResult(notice, "設定を保存しました。", stayed);
  } catch (error) {
    revealSettingResult(output, error.message, stayedAtOrigin());
  } finally {
    busy = false;
    hostPicker.disabled = false;
    updateWriteControls();
  }
}

function restoreEditors(drafts, editorKey) {
  let savedEditor;
  for (const editor of content.querySelectorAll("[data-editor]")) {
    const previous = drafts.get(editor.dataset.editor);
    if (previous?.open) editor.open = true;
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

async function saveShared(payload, output) {
  if (busy || staleSnapshotHost === host || output.closest("[data-setting]")?.querySelector(".settings-shared-conflict")) return;
  busy = true;
  updateWriteControls();
  try {
    await request(payload, false, false, false, "");
    const updated = await request(undefined, false, false, false, "");
    localData = updated;
    renderComparison(displayedData ?? updated);
    renderShared(output.closest("[data-setting]")?.dataset.setting);
    const notice = shared.querySelector(`[data-setting="${CSS.escape(payload.key ?? "")}"] [data-settings-result]`);
    if (notice) revealSettingResult(notice, "設定を保存しました。", true);
  } catch (error) {
    revealSettingResult(output, error.message, true);
  } finally {
    busy = false;
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
    localData = await request(undefined, false, true, true, "");
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
  for (const root of [shared, content]) {
    for (const editor of root.querySelectorAll("[data-setting]")) {
      editor.hidden = !!needle && !editor.dataset.search.includes(needle);
      if (!editor.hidden) count++;
    }
    for (const group of root.querySelectorAll("section")) {
      if (needle) group.hidden = !group.querySelector("[data-setting]:not([hidden])");
      else if (root === shared) group.hidden = false;
    }
  }
  if (!needle) showGroup();
  document.getElementById("settings-search-clear").hidden = !needle;
  document.getElementById("settings-no-matches").hidden = !needle || count > 0;
}

function updateUnsaved() {
  const list = document.getElementById("settings-unsaved");
  const drafts = [...shared.querySelectorAll("[data-editor]"), ...content.querySelectorAll("[data-editor]")]
    .filter(editor => editorFields(editor).some(input => input.value !== input.dataset.initialValue));
  const other = [...hostDrafts.entries()].filter(([name]) => name !== host).flatMap(([name, {editors}]) =>
    [...editors].filter(([, draft]) => draft.values.some(Boolean)).map(([slot]) => [name, slot]));
  const count = drafts.length + other.length;
  list.replaceChildren();
  if (!count) {
    window.removeEventListener("beforeunload", warnOnUnload);
    return;
  }
  window.addEventListener("beforeunload", warnOnUnload);
  list.append(element("p", `未保存の変更: ${count}件`));
  for (const editor of drafts) list.append(button(editor.dataset.editor, () => {
    const group = editor.closest("section[id^=settings-group-]");
    if (group) activeGroup = group.id.replace("settings-group-", "");
    search.value = "";
    filterSettings();
    if (editor.tagName === "DETAILS") editor.open = true;
    editorFields(editor)[0]?.focus();
  }));
  for (const [name, slot] of other) list.append(button(`${name || "ローカル"}: ${slot}`, () => selectHost(name)));
  list.append(button("未保存の変更を取り消す", () => {
    for (const editor of drafts) for (const input of editorFields(editor)) input.value = input.dataset.initialValue;
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
for (const root of [shared, content]) root.addEventListener("input", updateUnsaved);
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
  const known = start && typeof start === "object" && typeof start.speed === "string" && typeof start.source === "string";
  const scope = host || "ローカル";
  let result = failed ? "取得できませんでした" : "読み込み中";
  if (data) {
    result = known ? `${start.speed}（${start.source}）` :
      start === null ? "記録なし" : "このホストからは取得できません";
  }
  if (data && failed) result += "（前回取得）";
  fastState.textContent = `Codexの起動時判定（${scope}）: ${result}`;
  if (known) {
    const minute = Number.isFinite(Date.parse(start.at)) ? formatLocalMinute(start.at).replace("T", " ") : "";
    fastReason.textContent = [minute, start.reason].filter(Boolean).join("、");
  } else {
    fastReason.textContent = result;
  }
}

function render(data) {
  displayedData = data;
  renderFastStart(data);
  if (!host) {
    localData = data;
    renderShared();
  } else if (localData && data.comparison) {
    localData = {...localData, comparison: data.comparison};
    renderSharedComparison();
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

function updateWriteControls() {
  for (const control of content.querySelectorAll("[data-observed-deadline]")) {
    control.toggleAttribute("data-write-blocked", Boolean(host) && (loading || staleSnapshotHost === host));
    syncWriteControl(control);
  }
  for (const control of [...content.querySelectorAll("button:not([data-conflict-action], [data-local-edit])"), ...shared.querySelectorAll("button:not([data-conflict-action])")]) {
    const editor = control.closest("[data-editor]");
    if (!editor) continue;
    const setting = control.closest("[data-setting]") || editor;
    const blocked = busy || staleSnapshotHost === host || setting.querySelector(".settings-shared-conflict") ||
      (!shared.contains(control) && (loading || conflicts.has(setting.dataset.editor)));
    if (blocked) {
      control.dataset.writeBlocked = "";
      const reason = busy || loading ? "settings-status" : setting.querySelector("[data-conflict]")?.id;
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
    for (const key of drafts.keys()) if (key === slot || key.startsWith(conflict.key + ":")) drafts.delete(key);
    displayedData.settings[conflict.key] = conflict.latest;
    render(displayedData);
    restoreEditors(drafts);
    renderConflicts();
    updateUnsaved();
  } else {
    content.querySelector(`[data-editor="${CSS.escape(slot)}"] [data-conflict]`)?.remove();
    updateWriteControls();
  }
}

function renderConflicts() {
  for (const [slot, {key, latest}] of conflicts) {
    const editor = content.querySelector(`[data-editor="${CSS.escape(slot)}"]`);
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
  let generation = loadGeneration;
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
        const data = await request(undefined, false, true, false, target, true);
        if (host !== target || generation !== loadGeneration || busy || document.body.dataset.screen !== "settings") return;
        const failed = data.reset_editor?.refresh_failed;
        if (failed && !delay && !target) continue;
        const changed = JSON.stringify(data.reset_editor) !== JSON.stringify(displayedData?.reset_editor);
        if (target || changed) {
          await loadHost(true);
          generation = loadGeneration;
        }
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

async function loadHost(background = false, retry = false) {
  if (busy || (background && fetching)) return;
  const generation = ++loadGeneration;
  fetching = true;
  loading = true;
  updateWriteControls();
  if (!background) {
    status.textContent = "読み込み中…";
    if (!displayedData) renderFastStart();
  }
  try {
    const displayedHost = host;
    const savedDrafts = hostDrafts.get(displayedHost);
    const editingSnapshot = displayedData ?? savedDrafts?.snapshot;
    if (background && displayedHost) {
      const local = await request(undefined, false, true, retry, "");
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
    let initial;
    try {
      initial = await request(undefined, !background && !retry && Boolean(host) && !displayedData, background, retry);
    } catch (error) {
      const transient = readFailure(error.status) === "temporary";
      if (retry || !transient || generation !== loadGeneration) throw error;
      await new Promise(resolve => setTimeout(resolve, 1500));
      if (generation !== loadGeneration) return;
      initial = await request(undefined, false, true, Boolean(displayedHost), displayedHost);
    }
    if (generation !== loadGeneration) return;
    const scroll = content.closest("main");
    const scrollTop = scroll.scrollTop;
    const prior = content.hasChildNodes() ? rememberEditors() : savedDrafts?.editors ?? new Map();
    let data = initial;
    if (initial.cached) {
      render(initial);
      restoreEditors(prior);
      status.textContent = "更新中。最新の値が届くまで保存できません。";
      data = await request(undefined, false, true, true, displayedHost);
      if (generation !== loadGeneration) return;
    }
    const drafts = initial.cached ? rememberEditors() : prior;
    const previous = editingSnapshot ?? (initial.cached ? initial : undefined);
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
    renderConflicts();
    updateUnsaved();
    scroll.scrollTop = scrollTop;
    restoreSettingFocus(focus);
    staleSnapshotHost = undefined;
    loading = false;
    updateWriteControls();
    setUpdateError("settings", "refresh");
    showAutomaticErrors(data);
    status.textContent = data.legacy || data.automatic_unsupported?.includes(host) ? "旧版のatkです。実効値を表示しています。一部の値の編集にはSSH先のatkの更新が必要です。" : "";
  } catch (error) {
    if (generation !== loadGeneration) return;
    if (host) staleSnapshotHost = host;
    loading = Boolean(host) && Boolean(displayedData);
    renderFastStart(displayedData, true);
    updateWriteControls();
    status.textContent = loading ? "最新の値が届くまで保存できません。" : "";
    setDisplayedReadError("settings", "refresh", host ? `${host}の設定を取得できません。` : error.message, () => loadHost(true, true), () => loadHost(false, true));
  } finally {
    if (generation === loadGeneration) {
      fetching = false;
      hostPicker.disabled = false;
    }
  }
}

async function enter() {
  clearInterval(refreshTimer);
  if (document.body.dataset.screen !== "settings") return;
  refreshTimer = setInterval(() => {
    if (content.querySelector('details[data-editor^="usage_reset_permissions:"][open]')) void observeUsage();
    else void loadHost(true);
  }, 30_000);
  const query = new URLSearchParams(location.search);
  if (query.get("group") === "usage") activeGroup = "usage";
  if (query.get("host") === "" && host) {
    selectHost("");
    return;
  }
  if (content.hasChildNodes()) {
    showGroup();
    return;
  }
  await loadHost();
}

function selectHost(selected) {
  if (busy) return;
  if (content.hasChildNodes()) hostDrafts.set(host, {snapshot: displayedData, editors: rememberEditors()});
  setUpdateError("settings", "refresh");
  setUpdateError("settings", "usage");
  host = selected;
  hostPicker.value = host;
  displayedData = undefined;
  fastDetail.open = false;
  renderFastStart();
  conflicts.clear();
  navigation.replaceChildren();
  content.replaceChildren();
  updateUnsaved();
  void loadHost();
}

hostPicker.addEventListener("change", () => selectHost(hostPicker.value));

registerScreen("settings", {init: () => {
  document.addEventListener("atk-screen-change", enter);
  return enter();
}});

content.addEventListener("click", event => {
  if (event.target.closest('details[data-editor^="usage_reset_permissions:"] > summary')) {
    setTimeout(() => void observeUsage(), 0);
  }
});

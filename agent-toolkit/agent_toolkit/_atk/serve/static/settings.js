import {BASE_PATH, setUpdateError} from "./common.js";
import {registerScreen} from "./shell.js";
import {formatLocalMinute, renderResetPermissions} from "./reset_permissions.js";

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
let fieldCount = 0;
const guide = "https://github.com/ak110/dotfiles/blob/master/docs/guide/";

function element(tag, text = "", className = "") {
  const result = document.createElement(tag);
  result.textContent = text;
  if (className) result.className = className;
  return result;
}

function section(parent, title, url) {
  const target = element("section");
  const heading = element("h2", title);
  heading.className = "page-heading";
  target.append(heading);
  if (url) {
    const link = element("a", "ガイドを読む");
    link.href = guide + url;
    target.append(link);
  }
  parent.append(target);
  return target;
}

function button(text, action) {
  const result = element("button", text);
  result.type = "button";
  result.addEventListener("click", action);
  return result;
}

const FAST_MODE_LABELS = {auto: "自動判定（auto）", true: "高速（true）", false: "通常（false）"};
function sharedScope(syncs) {
  return syncs
    ? "保存先: 同じprivate-notesを使うホストで共有されます。このホストだけ変えるには一時指定を使ってください。"
    : "保存先: このホストのprivate-notes。同期先が未設定のため、他のホストには反映されません。";
}

function field(parent, label, value = "", choices, type = "text", labels = {}) {
  const wrapper = element("div");
  wrapper.className = "settings-field";
  const input = element(choices ? "select" : "input");
  for (const choice of choices ?? []) input.append(new Option(labels[choice] ?? choice, choice));
  if (!choices) input.type = type;
  input.id = `settings-field-${++fieldCount}`;
  const caption = element("label", label);
  caption.htmlFor = input.id;
  input.value = value;
  input.dataset.initialValue = input.value;
  wrapper.append(caption, input);
  parent.append(wrapper);
  return input;
}

async function request(payload, cached = false, background = false, retry = false, target = host) {
  const parameters = new URLSearchParams();
  if (target) parameters.set("host", target);
  if (cached) parameters.set("cached", "1");
  if (background) parameters.set("background", "1");
  if (retry) parameters.set("retry", "1");
  const query = parameters.size ? "?" + parameters : "";
  const response = await fetch(BASE_PATH + "/api/settings" + query, payload ? {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
  } : undefined);
  const data = await response.json();
  if (!response.ok) throw new Error([data.error, data.next_action].filter(Boolean).join(" "));
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
      values: editor.dataset.editor === savedSlot ? [] : editorFields(editor).map(input => {
        const instant = input.dataset.instantDisplay === input.value ? input.dataset.instant : "";
        if (input.value === input.dataset.initialValue && (!instant || instant === input.dataset.initialInstant)) return null;
        return {value: input.value, instant};
      }),
    },
  ]));
}

function settingResult() {
  const output = element("p");
  output.dataset.settingsResult = "";
  output.setAttribute("role", "alert");
  return output;
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
  if (!editor) return null;
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
  if (fields.length !== 1 || target.closest("[hidden]")) return;
  for (let details = target.closest("details:not([open])"); details; details = details.closest("details:not([open])")) {
    details.open = true;
  }
  target.focus({preventScroll: true});
  if (focus.start != null && target.selectionStart != null) target.setSelectionRange(focus.start, focus.end, focus.direction);
}

async function operate(payload, output, submittedEditor = null) {
  if (busy || loading || conflicts.has(output.closest("[data-editor]")?.dataset.editor)) return;
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
      input.value = draft.value;
      if (draft.instant) {
        input.dataset.instant = draft.instant;
        input.dataset.instantDisplay = draft.value;
      }
      if (input.hasAttribute("data-draft-trigger")) input.dispatchEvent(new Event("change"));
    });
    if (editor.dataset.editor === editorKey) savedEditor = editor;
  }
  return savedEditor;
}

function form(parent, perform) {
  const result = element("form");
  result.addEventListener("submit", event => {
    event.preventDefault();
    perform();
  });
  parent.append(result);
  return result;
}

function submit(form, label) {
  const control = element("button", label);
  control.type = "submit";
  const actions = element("div", "", "form-actions");
  actions.append(control);
  form.append(actions);
  return control;
}

function keyEditor(parent, key, item, data, save = operate) {
  const listen = data.listen ?? {};
  const block = element("div");
  block.dataset.setting = key;
  block.dataset.search = `${key} ${item.description ?? ""}`.toLowerCase();
  block.dataset.editor = `setting:${key}`;
  block.className = "settings-row";
  const description = element("div");
  description.append(element("h3", key));
  block.append(description);
  const timing = key.startsWith("serve_") || key === "remote_hosts" ? "次回のatk serve起動" :
    key === "dotfiles_mode" ? "次のdotfiles更新" : "";
  if (item.description) description.append(element("p", item.description));
  const source = item.source ? `（出所: ${item.source}）` : "";
  const effective = key === "usage_reset_permissions" ? `${JSON.parse(item.effective || "[]").length}件の許可` :
    item.effective || "未指定";
  description.append(element("p", `実効値: ${effective}${source}`));
  if (item.read_only || (key === "usage_reset_permissions" && !data.reset_editor && host)) {
    description.append(element("p", key === "usage_reset_permissions" && host
      ? "このホストのatkを更新すると画面で許可を編集できます。CLIで設定するには atk config set usage_reset_permissions を使います。"
      : "読み取り専用"));
    parent.append(block);
    return;
  }
  if (item.shared) description.append(element("p", sharedScope(item.shared_sync)));
  if (timing) description.append(element("p", key === "remote_hosts" ?
    "反映: 設定のホスト切替は保存後、計画とセッションは次回のatk serve起動" : `反映: ${timing}`));
  if (item.source === "一時指定" || item.source?.startsWith("AGENT_TOOLKIT_CONFIG_")) {
    description.append(element("p", "保存値を変更しても、この上書きが解除されるまで実効値は変わりません。"));
  }
  if (listen[key] && (String(listen[key][0]) !== item.effective || listen[key][1] !== "atk config")) {
    const [value, source] = listen[key];
    description.append(element("p", `実行中の待受: ${value}（出所: ${source}）、保存値: ${item.saved}。`));
    if (source !== "atk config") description.append(element("p", "同じ指定で再起動する場合、保存値は使われません。"));
  }
  const controls = element("div");
  block.append(controls);
  const output = settingResult();
  if (key === "usage_reset_permissions") {
    renderResetPermissions(controls, JSON.parse(item.saved), data.reset_editor,
      (entries, editor) => save({action: "set", key, value: JSON.stringify(entries)}, output, editor),
      {element, field, form, button, submit, setWriteUnavailable});
  } else if (item.array) {
    const list = element("ul");
    for (const value of JSON.parse(item.saved)) {
      const row = element("li", value + " ");
      row.append(button(`${value}を削除`, () => save({action: "array-remove", key, value}, output)));
      list.append(row);
    }
    controls.append(list);
    const editor = form(controls, () => save({action: "array-add", key, value: input.value}, output));
    const input = field(editor, "追加するホスト");
    input.required = true;
    submit(editor, "追加");
  } else if (key !== "repositories") {
    const editor = form(controls, () => save({action: "set", key, value: input.value}, output));
    const input = field(editor, "保存値", item.saved, item.choices, "text", key === "codex_fast_mode" ? FAST_MODE_LABELS : undefined);
    if (key === "serve_port") input.inputMode = "numeric";
    if (!item.choices && item.format) {
      const hint = element("small", `形式: ${item.format}。${item.default === undefined ? "" : ` 初期値: ${item.default || "未指定"}`}`);
      hint.id = `${input.id}-format`;
      hint.className = "settings-format";
      input.parentElement.append(hint);
      input.setAttribute("aria-describedby", `${hint.id} ${input.id}-result`);
    }
    output.id = `${input.id}-result`;
    submit(editor, "保存");
  }
  controls.append(output);
  parent.append(block);
}

function repositoryEditor(parent, repo = "", attributes = {}) {
  const box = element("details");
  box.dataset.editor = `repository:${repo}`;
  box.append(element("summary", repo || "リポジトリを追加"));
  const identifier = repo ? null : field(box, "リポジトリID・URL・ローカルパス");
  const edit = field(box, "編集可否", attributes.edit ?? "deny", ["allow", "awi", "deny"]);
  const path = field(box, "ワークツリー", attributes.path);
  const loop = field(box, "process-loopの対象", String(attributes.process_loop ?? false), ["true", "false"]);
  const platforms = field(box, "検証OS（カンマ区切り）", (attributes.platforms ?? ["linux", "windows"]).join(","));
  const branches = field(box, "系統（カンマ区切り）", (attributes.branches ?? []).join(","));
  const defaultBranch = field(box, "省略時の系統", attributes.default_branch);
  const mergeFrom = field(box, "リリース前に取り込む系統（対象=取込元、カンマ区切り）",
    Object.entries(attributes.merge_from ?? {}).map(([target, source]) => `${target}=${source}`).join(","));
  const output = settingResult();
  box.append(button(repo ? "属性を保存" : "追加", () => {
    const data = {
      action: "repo", operation: repo ? "set" : "add", repo: repo || identifier.value,
      edit: edit.value, process_loop: loop.value === "true", platforms: platforms.value,
    };
    if (repo || path.value) data.path = path.value;
    if (branches.value !== (attributes.branches ?? []).join(",")) data.branches = branches.value;
    if (defaultBranch.value && branches.value) data.default_branch = defaultBranch.value;
    if (mergeFrom.value || Object.keys(attributes.merge_from ?? {}).length) data.merge_from = mergeFrom.value;
    void operate(data, output);
  }));
  if (repo) box.append(button("登録を削除", () => {
    if (window.confirm(`${repo}の登録を削除しますか？`)) void operate({action: "repo", operation: "remove", repo}, output);
  }));
  box.append(output);
  parent.append(box);
}

function sameSharedSetting(data, key) {
  const here = localData?.settings[key];
  const there = data.settings[key];
  return Boolean(localData?.shared_identity && data.shared_identity === localData.shared_identity &&
    here?.shared && !here.migration_pending && there?.shared && !there.migration_pending);
}

function groupId(key) {
  return key.endsWith("_model") ? "models" : (key.startsWith("serve_") || key === "remote_hosts") ? "serve" :
    key === "repositories" ? "repos" : /usage|reset/.test(key) ? "usage" :
    /connections|provider|env_file/.test(key) ? "connections" : "other";
}

const groupTitles = {models: "工程別モデル", connections: "接続", usage: "利用枠", other: "その他の設定とパス"};

async function saveShared(payload, output) {
  if (busy || output.closest("[data-setting]")?.querySelector(".settings-shared-conflict")) return;
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

function renderShared(savedKey) {
  const data = localData;
  if (!data) return;
  const focus = rememberSettingFocus(document.activeElement);
  const old = new Map([...shared.querySelectorAll("[data-setting]")].map(node => [node.dataset.setting, node]));
  const note = element("p", "保存先: private-notesの共有設定ファイル");
  const nodes = [note];
  const sections = new Map();
  for (const [key, item] of Object.entries(data.settings)) {
    if (!item.shared) continue;
    const group = groupId(key);
    if (!sections.has(group)) {
      const node = element("section");
      node.append(element("h3", groupTitles[group] ?? "その他の設定"));
      sections.set(group, node);
      nodes.push(node);
    }
    const target = sections.get(group);
    const prior = old.get(key);
    if (prior && key !== savedKey && editorFields(prior).some(field => field.value !== field.dataset.initialValue)) {
      target.append(prior);
      const field = editorFields(prior)[0];
      prior.querySelector(".settings-shared-conflict")?.remove();
      if (field.dataset.initialValue !== item.saved) {
        const conflict = element("p", `共有の保存値が変わりました。最新の保存値: ${item.saved}`);
        conflict.className = "settings-shared-conflict notice notice-warning";
        conflict.setAttribute("role", "alert");
        const resolve = keep => {
          field.dataset.initialValue = item.saved;
          if (!keep) field.value = item.saved;
          conflict.remove();
          updateWriteControls();
          updateUnsaved();
        };
        const preserve = button("入力を残す", () => resolve(true));
        const replace = button("保存値を使う", () => resolve(false));
        preserve.dataset.conflictAction = "";
        replace.dataset.conflictAction = "";
        preserve.dataset.sharedConflictAction = "keep";
        replace.dataset.sharedConflictAction = "replace";
        conflict.append(" ", preserve, " ", replace);
        prior.append(conflict);
      }
    } else {
      keyEditor(target, key, item, data, saveShared);
    }
  }
  if (sections.size === 0) nodes.push(element("p", "共有設定はありません。"));
  for (const [preset, values] of Object.entries(data.presets ?? {})) {
    if (!Object.keys(values).length || !Object.keys(values).every(key => data.settings[key]?.shared)) continue;
    const detail = element("details");
    detail.append(element("summary", preset));
    for (const [key, value] of Object.entries(values)) detail.append(element("p", `${key}: ${value}`));
    const output = settingResult();
    const actions = element("div", "", "form-actions");
    actions.append(button("適用", () => saveShared({action: "preset", preset}, output)));
    detail.append(actions, output);
    nodes.push(detail);
  }
  for (const [name, peer] of Object.entries(data.comparison ?? {})) {
    const common = Object.keys(data.settings).filter(key => sameSharedSetting(peer, key));
    const summary = peer.state === "pending" ? "未取得" : peer.state === "error" ? "取得失敗" :
      common.length === 0 ? "共有設定の一致を確認できません" :
      common.some(key => data.settings[key].saved !== peer.settings[key].saved) ? "値が異なります（同期の遅れの可能性）" :
      "同じ共有設定を使用";
    note.append(element("span", ` · ${name}: ${summary}`));
  }
  shared.replaceChildren(...nodes);
  if (focus?.shared) restoreSettingFocus(focus);
}

function showAutomaticErrors(data) {
  if (!data.automatic_errors) return;
  const failures = Object.entries(data.automatic_errors).map(([name, reason]) => `${name}: ${reason}`);
  setUpdateError("settings", "automatic", failures.join(" / "), retryComparison);
}

async function retryComparison() {
  try {
    localData = await request(undefined, false, true, true, "");
    renderShared();
    renderComparison(localData);
    setUpdateError("settings", "comparison");
    showAutomaticErrors(localData);
    filterSettings();
  } catch (error) {
    setUpdateError("settings", "comparison", error.message, retryComparison);
  }
}

function renderComparison(data) {
  const hosts = [ ["", {state: "ready", ...localData}], ...Object.entries(data.comparison ?? localData?.comparison ?? {}) ];
  const table = element("table");
  const headers = table.createTHead().insertRow();
  const label = element("th", "設定");
  label.scope = "col";
  headers.append(label);
  for (const [name, info] of hosts) {
    const header = element("th");
    header.scope = "col";
    header.append(button(name || "ローカル", () => selectHost(name)));
    if (info.state === "pending" || info.state === "error") {
      header.append(element("p", info.state === "pending" ? "未取得" : "取得失敗"),
        button("再取得", retryComparison));
    } else if (info.state === "legacy") header.append(element("p", "旧版のため自動取得なし"));
    headers.append(header);
  }
  const body = table.createTBody();
  for (const key of new Set(hosts.flatMap(([, value]) => Object.keys(value.settings ?? {})))) {
    const values = hosts.map(([name, info]) => {
      if (name ? sameSharedSetting(info, key) : info.settings?.[key]?.shared) return null;
      return info.settings?.[key]?.saved ?? null;
    });
    if (values.every(value => value == null)) continue;
    const row = body.insertRow();
    const heading = element("th", key);
    heading.scope = "row";
    row.append(heading);
    for (const value of values) {
      const cell = row.insertCell();
      cell.textContent = value == null ? "—" : String(value);
      if (value != null && value !== values[0]) cell.className = "settings-different";
    }
  }
  comparison.replaceChildren(table);
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

function renderFastStart(data) {
  const start = data?.codex_fast_start;
  const known = start && typeof start === "object" && typeof start.speed === "string" && typeof start.source === "string";
  const scope = host || "ローカル";
  let result = "読み込み中";
  if (data) {
    result = known ? `${start.speed}（${start.source}）` :
      start === null ? "記録なし" : "このホストからは取得できません";
  }
  fastState.textContent = `Codexの起動時判定（${scope}）: ${result}`;
  fastDetail.hidden = !known;
  if (known) {
    const minute = Number.isFinite(Date.parse(start.at)) ? formatLocalMinute(start.at).replace("T", " ") : "";
    fastReason.textContent = [minute, start.reason].filter(Boolean).join("、");
  } else {
    fastReason.textContent = "";
    fastDetail.open = false;
  }
}

function render(data) {
  displayedData = data;
  renderFastStart(data);
  if (!host) { localData = data; renderShared(); }
  renderComparison(data);
  const root = document.createDocumentFragment();
  const groups = {
    models: section(root, "工程別モデル", "claude-code-models.md#工程別モデル設定"),
    connections: section(root, "接続", "claude-code-connections.md#接続の使い分け"),
    usage: section(root, "利用枠", "claude-code-models.md#利用枠とreset許可"),
    repos: section(root, "リポジトリ", "claude-code-wi.md#処理対象と検証os"),
    serve: section(root, "atk serve", "claude-code-serve.md#atk-serveの画面"),
    other: section(root, "その他の設定とパス", "claude-code-settings.md#atk-config"),
    temporary: section(root, "一時指定", "claude-code-connections.md#緊急時だけ従量課金を許可する"),
  };
  const candidates = element("details");
  candidates.append(element("summary", "モデル候補の例"));
  for (const [engine, models] of Object.entries(data.model_candidates ?? {})) {
    candidates.append(element("p", `${engine}: ${models.join(", ")}`));
  }
  if (data.model_candidates) groups.models.append(candidates);
  const settings = Object.entries(data.settings).sort(([left], [right]) =>
    Number(right === "remote_hosts") - Number(left === "remote_hosts"));
  for (const [key, item] of settings) {
    if ((!host && item.shared) || (host && sameSharedSetting(data, key))) continue;
    const group = groups[groupId(key)];
    if (key !== "repositories" || !data.repositories) keyEditor(group, key, item, data);
  }
  for (const [key, value] of Object.entries(data.paths)) groups.other.append(element("p", `${key}: ${value}`));
  for (const [repo, attributes] of Object.entries(data.repositories ?? {})) repositoryEditor(groups.repos, repo, attributes);
  if (data.repositories) repositoryEditor(groups.repos);
  if (data.model_order_policy) groups.usage.append(element("p", data.model_order_policy));
  const usageLink = element("a", "利用枠の使用率とリセット権を見る");
  usageLink.href = BASE_PATH + "/usage";
  const usageHelp = element("p");
  usageHelp.append(usageLink);
  groups.usage.append(usageHelp);
  if (data.temporary) renderTemporary(groups.temporary, data);
  else groups.temporary.remove();
  const presets = groups.models;
  if (data.presets) presets.append(element("h3", "プリセット"));
  for (const [preset, values] of Object.entries(data.presets ?? {})) {
    const item = element("details");
    item.dataset.editor = `preset:${preset}`;
    item.append(element("summary", preset));
    if (Object.keys(values).some(name => !data.settings[name] || data.settings[name].read_only)) continue;
    if (Object.keys(values).every(name => host ? sameSharedSetting(data, name) : data.settings[name].shared)) continue;
    const sharedSetting = Object.keys(values).find(name => data.settings[name].shared);
    if (sharedSetting) item.append(element("p", sharedScope(data.settings[sharedSetting].shared_sync)));
    for (const [name, presetValue] of Object.entries(values)) {
      item.append(element("p", `${name}: 現在 ${data.settings[name].effective} → 保存 ${presetValue}`));
    }
    const output = settingResult();
    const actions = element("div", "", "form-actions");
    actions.append(button("適用", () => operate({action: "preset", preset}, output)));
    item.append(actions, output);
    presets.append(item);
  }
  navigation.replaceChildren();
  for (const [id, group] of Object.entries(groups)) {
    if (!group.parentNode) continue;
    group.id = `settings-group-${id}`;
    const control = button(group.querySelector("h2").textContent, () => {
      activeGroup = id;
      showGroup();
      content.closest("main").scrollTop = 0;
    });
    control.dataset.group = id;
    control.setAttribute("aria-controls", group.id);
    navigation.append(control);
  }
  content.replaceChildren(root);
  if (!document.getElementById(`settings-group-${activeGroup}`)) activeGroup = "models";
  showGroup();

  const options = [new Option("ローカル", ""), ...(data.hosts ?? []).map(name => new Option(name, name))];
  hostPicker.replaceChildren(...options);
  hostPicker.value = host;
  filterSettings();
  updateWriteControls();
}

function showGroup() {
  for (const control of navigation.querySelectorAll("button")) {
    const selected = control.dataset.group === activeGroup;
    document.getElementById(control.getAttribute("aria-controls")).hidden = !selected;
    if (selected) control.setAttribute("aria-current", "page");
    else control.removeAttribute("aria-current");
  }
}

function renderTemporary(temporary, data) {
  temporary.dataset.editor = "temporary";
  temporary.append(element("p", "保存値は変わりません。期限を空欄にすると解除するまで有効です。"));
  const entries = Object.entries(data.temporary.values);
  if (entries.length) {
    const current = element("ul");
    for (const [name, value] of entries) current.append(element("li", `${name}: ${value}`));
    temporary.append(current);
    if (data.temporary.until) temporary.append(element("p", `有効期限: ${new Date(data.temporary.until).toLocaleString()}`));
  }
  const keys = Object.keys(data.settings).filter(key => data.settings[key].temporary && !data.settings[key].read_only);
  if (!keys.length) return;
  const output = settingResult();
  const editor = form(temporary, () => {
    void operate({action: "temporary", key: key.value, value: value.value,
      until: until.value ? new Date(until.value).toISOString() : null}, output);
  });
  const key = field(editor, "キー", keys[0], keys);
  function valueField(parent) {
    const setting = data.settings[key.value];
    return field(parent, "値", setting.effective, setting.choices, "text",
      key.value === "codex_fast_mode" ? FAST_MODE_LABELS : undefined);
  }
  let value = valueField(editor);
  key.addEventListener("change", () => {
    const replacement = document.createDocumentFragment();
    const next = valueField(replacement);
    value.parentElement.replaceWith(replacement);
    value = next;
  });
  const until = field(editor, "期限（このブラウザーの時刻）", "", null, "datetime-local");
  submit(editor, "一時指定を保存");
  if (entries.length) temporary.append(button("一時指定を全解除", () => operate({action: "temporary-clear"}, output)));
  temporary.append(output);
}

function syncWriteControl(control) {
  control.disabled = control.hasAttribute("data-write-blocked") || control.hasAttribute("data-write-unavailable");
}

function setWriteUnavailable(control, unavailable) {
  control.toggleAttribute("data-write-unavailable", unavailable);
  syncWriteControl(control);
}

function updateWriteControls() {
  for (const control of [...content.querySelectorAll("button:not([data-conflict-action], [data-local-edit])"), ...shared.querySelectorAll("button:not([data-conflict-action])")]) {
    const editor = control.closest("[data-editor]");
    if (!editor) continue;
    const setting = control.closest("[data-setting]") || editor;
    const blocked = busy || setting.querySelector(".settings-shared-conflict") ||
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

async function loadHost(background = false, retry = false) {
  if (busy || (background && fetching)) return;
  const generation = ++loadGeneration;
  fetching = true;
  loading = true;
  updateWriteControls();
  if (!background) status.textContent = "読み込み中…";
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
    const initial = await request(undefined, !background && Boolean(host) && !displayedData, background, retry && !displayedHost);
    if (generation !== loadGeneration) return;
    const focus = rememberSettingFocus(document.activeElement);
    const scroll = content.closest("main");
    const scrollTop = scroll.scrollTop;
    const prior = content.hasChildNodes() ? rememberEditors() : savedDrafts?.editors ?? new Map();
    let data = initial;
    if (initial.cached) {
      render(initial);
      restoreEditors(prior);
      status.textContent = "更新中。最新の値が届くまで保存できません。";
      data = await request();
      if (generation !== loadGeneration) return;
    }
    const drafts = initial.cached ? rememberEditors() : prior;
    const previous = editingSnapshot ?? (initial.cached ? initial : undefined);
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
    loading = false;
    updateWriteControls();
    setUpdateError("settings", "refresh");
    showAutomaticErrors(data);
    status.textContent = data.legacy || data.automatic_unsupported?.includes(host) ? "旧版のatkです。実効値を表示しています。一部の値の編集にはSSH先のatkの更新が必要です。" : "";
  } catch (error) {
    if (generation !== loadGeneration) return;
    loading = Boolean(host) && Boolean(displayedData);
    updateWriteControls();
    status.textContent = loading ? "最新の値が届くまで保存できません。" : "";
    setUpdateError("settings", "refresh", host ? `${host}: ${error.message}` : error.message, () => loadHost(background, background));
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
  refreshTimer = setInterval(() => void loadHost(true), 30_000);
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
  host = selected;
  hostPicker.value = host;
  displayedData = undefined;
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

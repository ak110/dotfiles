import {BASE_PATH, setUpdateError} from "./common.js";
import {registerScreen} from "./shell.js";
import {formatLocalMinute, renderResetPermissions} from "./reset_permissions.js";

const status = document.getElementById("settings-status");
const content = document.getElementById("settings-content");
const hostPicker = document.getElementById("settings-host");
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

function element(tag, text = "") {
  const result = document.createElement(tag);
  result.textContent = text;
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

async function request(payload, cached = false, background = false, retry = false) {
  const parameters = new URLSearchParams();
  if (host) parameters.set("host", host);
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
  if (!editor || !label) return null;
  return {
    editor: editor.dataset.editor,
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
  const editor = content.querySelector(`[data-editor="${CSS.escape(focus.editor)}"]`);
  if (!editor) return;
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
  form.append(control);
  return control;
}

function keyEditor(parent, key, item, data) {
  const listen = data.listen ?? {};
  const block = element("div");
  block.dataset.setting = key;
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
  if (key === "codex_fast_mode") {
    description.append(element("p", "実効値は採用中の設定です。自動判定で実際に使う速度は接続先と提供期間で決まります。"));
  }
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
      (entries, editor) => operate({action: "set", key, value: JSON.stringify(entries)}, output, editor),
      {element, field, form, button, submit, setWriteUnavailable});
  } else if (item.array) {
    const list = element("ul");
    for (const value of JSON.parse(item.saved)) {
      const row = element("li", value + " ");
      row.append(button(`${value}を削除`, () => operate({action: "array-remove", key, value}, output)));
      list.append(row);
    }
    controls.append(list);
    const editor = form(controls, () => operate({action: "array-add", key, value: input.value}, output));
    const input = field(editor, "追加するホスト");
    input.required = true;
    submit(editor, "追加");
  } else if (key !== "repositories") {
    const editor = form(controls, () => operate({action: "set", key, value: input.value}, output));
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

function render(data) {
  displayedData = data;
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
    const group = key.endsWith("_model") ? groups.models : (key.startsWith("serve_") || key === "remote_hosts") ? groups.serve :
      key === "repositories" ? groups.repos : /usage|reset/.test(key) ? groups.usage :
      /connections|provider|env_file/.test(key) ? groups.connections : groups.other;
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
    const sharedSetting = Object.keys(values).find(name => data.settings[name].shared);
    if (sharedSetting) item.append(element("p", sharedScope(data.settings[sharedSetting].shared_sync)));
    for (const [name, presetValue] of Object.entries(values)) {
      item.append(element("p", `${name}: 現在 ${data.settings[name].effective} → 保存 ${presetValue}`));
    }
    const output = settingResult();
    item.append(button("適用", () => operate({action: "preset", preset}, output)), output);
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
  for (const control of content.querySelectorAll("button:not([data-conflict-action], [data-local-edit])")) {
    const editor = control.closest("[data-editor]");
    if (!editor) continue;
    const setting = control.closest("[data-setting]") || editor;
    const blocked = busy || loading || conflicts.has(setting.dataset.editor);
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
  } else {
    content.querySelector(`[data-editor="${CSS.escape(slot)}"] [data-conflict]`)?.remove();
    updateWriteControls();
  }
}

function renderConflicts() {
  for (const [slot, {key, latest}] of conflicts) {
    const editor = content.querySelector(`[data-editor="${CSS.escape(slot)}"]`);
    if (!editor) continue;
    const notice = element("div");
    notice.dataset.conflict = "";
    notice.id = `settings-conflict-${key}`;
    const latestValue = key === "usage_reset_permissions" ?
      JSON.parse(latest.saved).map(entry => `${entry.id} · ${entry.count}回 · ${entry.from}〜${entry.until}`).join(" / ") || "許可なし" :
      latest.saved;
    notice.append(
      element("p", "取得中に保存値が変更されました。入力欄は編集中の値です。保存する前にどちらを使うか選んでください。"),
      element("p", `最新の保存値: ${latestValue}`),
    );
    for (const [label, keepDraft] of [["最新の値を取り込む", false], ["自分の入力で上書き", true]]) {
      const choice = button(label, () => resolveConflict(slot, keepDraft));
      choice.dataset.conflictAction = "";
      notice.append(choice);
    }
    editor.append(notice);
  }
  updateWriteControls();
}

async function loadHost(background = false, retry = false) {
  if (busy || (background && (fetching || displayedData?.legacy || displayedData?.automatic_unsupported?.includes(host)))) return;
  const generation = ++loadGeneration;
  fetching = true;
  loading = true;
  updateWriteControls();
  if (!background) status.textContent = "読み込み中…";
  try {
    const displayedHost = host;
    const savedDrafts = hostDrafts.get(displayedHost);
    const editingSnapshot = displayedData ?? savedDrafts?.snapshot;
    const initial = await request(undefined, !background && Boolean(host) && !displayedData, background, retry);
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
    scroll.scrollTop = scrollTop;
    restoreSettingFocus(focus);
    loading = false;
    updateWriteControls();
    const failures = Object.entries(data.automatic_errors ?? {}).map(([name, reason]) => `${name}: ${reason}`);
    setUpdateError("settings", "refresh", failures.join(" / "), () => loadHost(true, true));
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
  conflicts.clear();
  navigation.replaceChildren();
  content.replaceChildren();
  void loadHost();
}

hostPicker.addEventListener("change", () => selectHost(hostPicker.value));

registerScreen("settings", {init: () => {
  document.addEventListener("atk-screen-change", enter);
  return enter();
}});

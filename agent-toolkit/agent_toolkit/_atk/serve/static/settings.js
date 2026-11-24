import {BASE_PATH} from "./common.js";
import {registerScreen} from "./shell.js";

const status = document.getElementById("settings-status");
const content = document.getElementById("settings-content");
const hostPicker = document.getElementById("settings-host");
const navigation = document.getElementById("settings-nav");
let host = "";
let activeGroup = "models";
let busy = false;
const hostDrafts = new Map();
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

function field(parent, label, value = "", choices, type = "text") {
  const wrapper = element("div");
  wrapper.className = "settings-field";
  const input = element(choices ? "select" : "input");
  for (const choice of choices ?? []) input.append(new Option(choice, choice));
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

async function request(payload) {
  const query = host ? "?" + new URLSearchParams({host}) : "";
  const response = await fetch(BASE_PATH + "/api/settings" + query, payload ? {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
  } : undefined);
  const data = await response.json();
  if (!response.ok) throw new Error([data.error, data.next_action].filter(Boolean).join(" "));
  return data;
}

function rememberEditors(savedSlot) {
  return new Map(Array.from(content.querySelectorAll("[data-editor]"), editor => [
    editor.dataset.editor,
    {
      open: editor.open,
      values: editor.dataset.editor === savedSlot ? [] : Array.from(editor.querySelectorAll("input, select"), input =>
        input.value === input.dataset.initialValue ? null : input.value),
    },
  ]));
}

async function operate(payload, output) {
  if (busy) return;
  busy = true;
  hostPicker.disabled = true;
  const oldEditor = output.closest("[data-editor]");
  const editorKey = oldEditor?.dataset.editor;
  const scroll = content.closest("main");
  const scrollTop = scroll.scrollTop;
  try {
    await request(payload);
    const drafts = rememberEditors(editorKey);
    await refresh();
    const savedEditor = restoreEditors(drafts, editorKey);
    const message = "設定を保存しました。";
    status.textContent = message;
    const notice = savedEditor?.querySelector("[role=alert]");
    scroll.scrollTop = scrollTop;
    if (notice && !notice.closest("[hidden]")) {
      notice.textContent = message;
      notice.tabIndex = -1;
      notice.focus({preventScroll: true});
      notice.scrollIntoView({block: "nearest"});
    }
  } catch (error) {
    (output.isConnected ? output : status).textContent = error.message;
  } finally {
    busy = false;
    hostPicker.disabled = false;
  }
}

function restoreEditors(drafts, editorKey) {
  let savedEditor;
  for (const editor of content.querySelectorAll("[data-editor]")) {
    const previous = drafts.get(editor.dataset.editor);
    if (previous?.open) editor.open = true;
    editor.querySelectorAll("input, select").forEach((input, index) => {
      const draft = previous?.values[index];
      if (draft != null) input.value = draft;
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
}

function keyEditor(parent, key, item, listen) {
  const block = element("div");
  block.dataset.setting = key;
  block.dataset.editor = `setting:${key}`;
  block.append(element("h3", key));
  const timing = key.startsWith("serve_") || key === "remote_hosts" ? "次回のatk serve起動" :
    key === "dotfiles_mode" ? "次のdotfiles更新" : "";
  if (item.description) block.append(element("p", item.description));
  const source = item.source ? `（出所: ${item.source}）` : "";
  block.append(element("p", `実効値: ${item.effective || "未指定"}${source}`));
  if (item.read_only) {
    block.append(element("p", "読み取り専用"));
    parent.append(block);
    return;
  }
  if (timing) block.append(element("p", key === "remote_hosts" ?
    "反映: 設定のホスト切替は保存後、計画とセッションは次回のatk serve起動" : `反映: ${timing}`));
  if (item.source === "一時指定" || item.source?.startsWith("AGENT_TOOLKIT_CONFIG_")) {
    block.append(element("p", "保存値を変更しても、この上書きが解除されるまで実効値は変わりません。"));
  }
  if (listen[key] && (String(listen[key][0]) !== item.effective || listen[key][1] !== "atk config")) {
    const [value, source] = listen[key];
    block.append(element("p", `実行中の待受: ${value}（出所: ${source}）、保存値: ${item.saved}。`));
    if (source !== "atk config") block.append(element("p", "同じ指定で再起動する場合、保存値は使われません。"));
  }
  const output = element("p");
  output.setAttribute("role", "alert");
  if (key === "usage_reset_permissions" || key === "claude_reset_inventory") {
    resetPermissionsEditor(block, key, item, output);
  } else if (item.array) {
    const list = element("ul");
    for (const value of JSON.parse(item.saved)) {
      const row = element("li", value + " ");
      row.append(button(`${value}を削除`, () => operate({action: "array-remove", key, value}, output)));
      list.append(row);
    }
    block.append(list);
    const editor = form(block, () => operate({action: "array-add", key, value: input.value}, output));
    const input = field(editor, "追加するホスト");
    input.required = true;
    submit(editor, "追加");
  } else if (key !== "repositories") {
    const editor = form(block, () => operate({action: "set", key, value: input.value}, output));
    const input = field(editor, "保存値", item.saved, item.choices);
    if (key === "serve_port") input.inputMode = "numeric";
    if (!item.choices && item.format) {
      const hint = element("small", `形式: ${item.format}。${item.default === undefined ? "" : ` 初期値: ${item.default || "未指定"}`}`);
      hint.id = `${input.id}-format`;
      editor.prepend(hint);
      input.setAttribute("aria-describedby", `${hint.id} ${input.id}-result`);
    }
    output.id = `${input.id}-result`;
    submit(editor, "保存");
  }
  block.append(output);
  parent.append(block);
}

function dateTimeValue(instant) {
  if (!instant) return "";
  const date = new Date(instant);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function resetPermissionsEditor(parent, key, item, output) {
  const entries = JSON.parse(item.saved);
  const save = entries => operate({action: "set", key, value: JSON.stringify(entries)}, output);
  const fields = [
    ["id", "許可ID", "text"],
    ["engine", "エンジン", "text", ["claude", "codex"]],
    ["account", "アカウント", "text"],
    ["scope", "範囲", "text"],
    ["count", "回数", "number"],
    ["from", "開始（このブラウザーの時刻）", "datetime-local"],
    ["until", "終了（このブラウザーの時刻）", "datetime-local"],
  ];
  for (const [index, entry] of [...entries, {}].entries()) {
    const box = element("details");
    box.dataset.editor = `${key}:${entry.id ?? ""}`;
    box.append(element("summary", entry.id ?? "許可を追加"));
    const editor = form(box, () => {
      const updated = {...entry};
      for (const [name, input] of inputs) {
        updated[name] = input.type === "datetime-local"
          ? (entry[name] && input.value === input.dataset.initialValue ? entry[name] : new Date(input.value).toISOString())
          : input.type === "number" ? input.valueAsNumber : input.value;
      }
      const result = entries.slice();
      result[index] = updated;
      void save(result);
    });
    const inputs = fields.map(([name, label, type, choices]) => {
      const value = type === "datetime-local" ? dateTimeValue(entry[name]) : entry[name] ?? choices?.[0] ?? "";
      const input = field(editor, label, value, choices, type);
      input.required = true;
      if (type === "number") input.min = "0";
      return [name, input];
    });
    submit(editor, entry.id ? "保存" : "追加");
    if (entry.id) box.append(button("許可を削除", () => save(entries.filter((_, i) => i !== index))));
    parent.append(box);
  }
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
  const output = element("p");
  output.setAttribute("role", "alert");
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
    if (key !== "repositories" || !data.repositories) keyEditor(group, key, item, data.listen ?? {});
  }
  for (const [key, value] of Object.entries(data.paths)) groups.other.append(element("p", `${key}: ${value}`));
  for (const [repo, attributes] of Object.entries(data.repositories ?? {})) repositoryEditor(groups.repos, repo, attributes);
  if (data.repositories) repositoryEditor(groups.repos);
  if (data.model_order_policy) groups.usage.append(element("p", data.model_order_policy));
  for (const quota of data.usage ?? []) {
    const recovery = quota.resets_at == null ? "不明" : new Date(quota.resets_at * 1000).toLocaleString();
    groups.usage.append(element("p", `${quota.engine} · ${quota.window_id}: 使用率 ${quota.used_percent ?? "不明"}、回復: ${recovery}`));
  }
  if (data.usage && !data.usage.length) groups.usage.append(element("p", "利用枠の観測値はありません。"));
  if (data.temporary) renderTemporary(groups.temporary, data);
  else groups.temporary.remove();
  const presets = groups.models;
  if (data.presets) presets.append(element("h3", "プリセット"));
  for (const [preset, values] of Object.entries(data.presets ?? {})) {
    const item = element("details");
    item.dataset.editor = `preset:${preset}`;
    item.append(element("summary", preset));
    if (Object.keys(values).some(name => !data.settings[name] || data.settings[name].read_only)) continue;
    for (const [name, presetValue] of Object.entries(values)) {
      item.append(element("p", `${name}: 現在 ${data.settings[name].effective} → 保存 ${presetValue}`));
    }
    const output = element("p");
    output.setAttribute("role", "alert");
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
  const output = element("p");
  output.setAttribute("role", "alert");
  const editor = form(temporary, () => {
    void operate({action: "temporary", key: key.value, value: value.value,
      until: until.value ? new Date(until.value).toISOString() : null}, output);
  });
  const key = field(editor, "キー", keys[0], keys);
  const value = field(editor, "値", data.settings[key.value].effective);
  key.addEventListener("change", () => { value.value = data.settings[key.value].effective; });
  const until = field(editor, "期限（このブラウザーの時刻）", "", null, "datetime-local");
  submit(editor, "一時指定を保存");
  if (entries.length) temporary.append(button("一時指定を全解除", () => operate({action: "temporary-clear"}, output)));
  temporary.append(output);
}

async function refresh() {
  const data = await request();
  render(data);
  return data;
}

async function loadHost() {
  if (busy) return;
  busy = true;
  hostPicker.disabled = true;
  navigation.replaceChildren();
  content.replaceChildren();
  status.textContent = "読み込み中…";
  try {
    const data = await refresh();
    restoreEditors(hostDrafts.get(host) ?? new Map());
    status.textContent = data.legacy ? "旧版のatkです。実効値を表示しています。一部の値の編集にはSSH先のatkの更新が必要です。" : "";
  } catch (error) {
    status.textContent = error.message;
    status.append(" ", button("再試行", loadHost));
  } finally {
    busy = false;
    hostPicker.disabled = false;
  }
}

async function enter() {
  if (document.body.dataset.screen !== "settings" || content.hasChildNodes()) return;
  await loadHost();
}

hostPicker.addEventListener("change", () => {
  if (busy) return;
  if (content.hasChildNodes()) hostDrafts.set(host, rememberEditors());
  host = hostPicker.value;
  void loadHost();
});

registerScreen("settings", {init: () => {
  document.addEventListener("atk-screen-change", enter);
  return enter();
}});

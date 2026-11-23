import {BASE_PATH} from "./common.js";
import {registerScreen} from "./shell.js";

const status = document.getElementById("settings-status");
const content = document.getElementById("settings-content");
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

function field(parent, label, value = "", choices) {
  const wrapper = element("label");
  const input = element(choices ? "select" : "input");
  for (const choice of choices ?? []) input.append(new Option(choice, choice));
  input.value = value;
  input.dataset.initialValue = input.value;
  input.setAttribute("aria-label", label);
  wrapper.append(element("span", label), input);
  parent.append(wrapper);
  return input;
}

async function request(payload) {
  const response = await fetch(BASE_PATH + "/api/settings", payload ? {
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
  const oldEditor = output.closest("[data-editor]");
  const editorKey = oldEditor?.dataset.editor;
  const scroll = content.closest("main");
  const scrollTop = scroll.scrollTop;
  try {
    await request(payload);
    const drafts = rememberEditors(editorKey);
    await refresh();
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
    const message = "設定を保存しました。";
    status.textContent = message;
    const notice = savedEditor?.querySelector("[role=alert]");
    scroll.scrollTop = scrollTop;
    if (notice) {
      notice.textContent = message;
      notice.tabIndex = -1;
      notice.focus({preventScroll: true});
      notice.scrollIntoView({block: "nearest"});
    }
  } catch (error) {
    output.textContent = error.message;
  }
}

function keyEditor(parent, key, item, listen) {
  const block = element("div");
  block.dataset.setting = key;
  block.dataset.editor = `setting:${key}`;
  block.append(element("h3", key));
  const timing = key.startsWith("serve_") ? "次回のatk serve起動" :
    key === "dotfiles_mode" ? "次のdotfiles更新" :
    "次にこの設定を使う処理（稼働中の処理は継続）";
  block.append(element("p", `${item.description}。初期値: ${item.default || "未指定"}。形式: ${item.format}。反映: ${timing}。`));
  block.append(element("p", `実効値: ${item.effective || "未指定"}（出所: ${item.source}）。`));
  if (item.source === "一時指定" || item.source.startsWith("AGENT_TOOLKIT_CONFIG_")) {
    block.append(element("p", "保存値を変更しても、この上書きが解除されるまで実効値は変わりません。"));
  }
  if (listen[key]) {
    const [value, source] = listen[key];
    block.append(element("p", `実行中の待受: ${value}（出所: ${source}）、保存値: ${item.saved}。`));
    if (source !== "atk config") block.append(element("p", "同じ指定で再起動する場合、保存値は使われません。"));
  }
  const output = element("p");
  output.setAttribute("role", "alert");
  if (item.array) {
    const list = element("ul");
    for (const value of JSON.parse(item.saved)) {
      const row = element("li", value + " ");
      row.append(button(`${value}を削除`, () => operate({action: "array-remove", key, value}, output)));
      list.append(row);
    }
    block.append(list);
    const input = field(block, "追加するホスト");
    block.append(button("追加", () => operate({action: "array-add", key, value: input.value}, output)));
  } else if (key !== "repositories") {
    const input = field(block, "保存値", item.saved, item.choices);
    block.append(button("保存", () => operate({action: "set", key, value: input.value}, output)));
  }
  block.append(output);
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
  };
  const candidates = element("details");
  candidates.append(element("summary", "モデル候補の例"));
  for (const [engine, models] of Object.entries(data.model_candidates)) {
    candidates.append(element("p", `${engine}: ${models.join(", ")}`));
  }
  groups.models.append(candidates);
  for (const [key, item] of Object.entries(data.settings)) {
    const group = key.endsWith("_model") ? groups.models : key.startsWith("serve_") ? groups.serve :
      key === "repositories" ? groups.repos : /usage|reset/.test(key) ? groups.usage :
      /connections|provider|env_file/.test(key) ? groups.connections : groups.other;
    keyEditor(group, key, item, data.listen);
  }
  for (const [key, value] of Object.entries(data.paths)) groups.other.append(element("p", `${key}: ${value}`));
  for (const [repo, attributes] of Object.entries(data.repositories)) repositoryEditor(groups.repos, repo, attributes);
  repositoryEditor(groups.repos);
  groups.usage.append(element("p", data.model_order_policy));
  for (const quota of data.usage) {
    const recovery = quota.resets_at == null ? "不明" : new Date(quota.resets_at * 1000).toLocaleString();
    groups.usage.append(element("p", `${quota.engine} · ${quota.window_id}: 使用率 ${quota.used_percent ?? "不明"}、回復: ${recovery}`));
  }
  if (!data.usage.length) groups.usage.append(element("p", "利用枠の観測値はありません。"));
  const temporary = section(root, "一時指定", "claude-code-connections.md#緊急時だけ従量課金を許可する");
  temporary.dataset.editor = "temporary";
  temporary.append(element("p", "保存値を変えず、指定した期限まで有効にします。環境変数が優先されます。"));
  temporary.append(element("pre", JSON.stringify(data.temporary, null, 2)));
  const keys = Object.keys(data.settings).filter(key => data.settings[key].temporary);
  const key = field(temporary, "キー", keys[0], keys);
  const value = field(temporary, "値", data.settings[key.value].effective);
  key.addEventListener("change", () => {
    value.value = data.settings[key.value].effective;
  });
  const until = field(temporary, "期限（タイムゾーン付きISO 8601）");
  const temporaryOutput = element("p");
  temporaryOutput.setAttribute("role", "alert");
  temporary.append(
    button("一時指定を保存", () => operate({action: "temporary", key: key.value, value: value.value, until: until.value}, temporaryOutput)),
    button("一時指定を全解除", () => operate({action: "temporary-clear"}, temporaryOutput)),
    temporaryOutput,
  );
  const presets = section(root, "プリセット", "claude-code-models.md#モデル候補の保存");
  presets.append(element("p", "適用すると下記の工程別モデルを一括で保存します。"));
  for (const [preset, values] of Object.entries(data.presets)) {
    const item = element("details");
    item.dataset.editor = `preset:${preset}`;
    item.append(element("summary", preset));
    for (const [name, presetValue] of Object.entries(values)) {
      item.append(element("p", `${name}: 現在 ${data.settings[name].effective} → 保存 ${presetValue}`));
    }
    const output = element("p");
    output.setAttribute("role", "alert");
    item.append(button("適用", () => operate({action: "preset", preset}, output)), output);
    presets.append(item);
  }
  content.replaceChildren(root);
}

async function refresh() {
  render(await request());
}

async function enter() {
  if (document.body.dataset.screen !== "settings" || content.hasChildNodes()) return;
  status.textContent = "読み込み中…";
  try {
    await refresh();
    status.textContent = "";
  } catch (error) {
    status.textContent = error.message;
  }
}

registerScreen("settings", {init: () => {
  document.addEventListener("atk-screen-change", enter);
  return enter();
}});

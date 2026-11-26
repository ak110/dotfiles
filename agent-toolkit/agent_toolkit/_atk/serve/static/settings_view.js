import {BASE_PATH} from "./common.js";
import {renderResetPermissions} from "./reset_permissions.js";

export function createSettingsView({
  content, shared, comparison, hostPicker, navigation, state,
  operate, saveShared, selectHost, filterSettings, updateUnsaved, updateWriteControls, setWriteUnavailable,
  rememberSettingFocus, restoreSettingFocus, editorFields,
}) {
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

  function settingResult() {
    const output = element("p");
    output.dataset.settingsResult = "";
    output.setAttribute("role", "alert");
    return output;
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
    if (item.read_only || (key === "usage_reset_permissions" && !data.reset_editor && state.host)) {
      description.append(element("p", key === "usage_reset_permissions" && state.host
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
    const here = state.localData?.settings[key];
    const there = data.settings[key];
    return Boolean(state.localData?.shared_identity && data.shared_identity === state.localData.shared_identity &&
      here?.shared && !here.migration_pending && there?.shared && !there.migration_pending);
  }

  function groupId(key) {
    return key.endsWith("_model") ? "models" : (key.startsWith("serve_") || key === "remote_hosts") ? "serve" :
      key === "repositories" ? "repos" : /usage|reset/.test(key) ? "usage" :
      /connections|provider|env_file/.test(key) ? "connections" : "other";
  }

  const groupTitles = {models: "工程別モデル", connections: "接続", usage: "利用枠", other: "その他の設定とパス"};

  function renderShared(savedKey) {
    const data = state.localData;
    if (!data) return;
    const focus = rememberSettingFocus(document.activeElement);
    const old = new Map([...shared.querySelectorAll("[data-setting]")].map(node => [node.dataset.setting, node]));
    const note = element("p");
    note.dataset.sharedComparison = "";
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
    shared.replaceChildren(...nodes);
    renderSharedComparison();
    if (focus?.shared) restoreSettingFocus(focus);
  }

  function renderSharedComparison() {
    const note = shared.querySelector("[data-shared-comparison]");
    const data = state.localData;
    if (!note || !data) return;
    note.replaceChildren("保存先: private-notesの共有設定ファイル");
    for (const [name, peer] of Object.entries(data.comparison ?? {})) {
      const common = Object.keys(data.settings).filter(key => sameSharedSetting(peer, key));
      const failed = peer.state === "error" && state.confirmedComparisonFailures.has(name);
      if (!failed && !Object.keys(peer.settings ?? {}).length && ["pending", "error"].includes(peer.state)) continue;
      const summary = failed ? "取得失敗" :
        peer.state === "legacy" ? "旧版のため自動取得なし" :
        common.length === 0 ? "共有設定の一致を確認できません" :
        common.some(key => data.settings[key].saved !== peer.settings[key].saved) ? "値が異なります（同期の遅れの可能性）" :
        "同じ共有設定を使用";
      note.append(element("span", ` · ${name}: ${summary}`));
    }

  }

  function renderComparison(data) {
    const hosts = [ ["", {state: "ready", ...state.localData}], ...Object.entries(data.comparison ?? state.localData?.comparison ?? {}) ];
    const table = element("table");
    const headers = table.createTHead().insertRow();
    const label = element("th", "設定");
    label.scope = "col";
    headers.append(label);
    for (const [name, info] of hosts) {
      const header = element("th");
      header.scope = "col";
      header.append(button(name || "ローカル", () => selectHost(name)));
      if (info.state === "legacy") header.append(element("p", "旧版のため自動取得なし"));
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

  function renderHost(data) {
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
      if ((!state.host && item.shared) || (state.host && sameSharedSetting(data, key))) continue;
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
      if (Object.keys(values).every(name => state.host ? sameSharedSetting(data, name) : data.settings[name].shared)) continue;
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
        state.activeGroup = id;
        showGroup();
        content.closest("main").scrollTop = 0;
      });
      control.dataset.group = id;
      control.setAttribute("aria-controls", group.id);
      navigation.append(control);
    }
    content.replaceChildren(root);
    if (!document.getElementById(`settings-group-${state.activeGroup}`)) state.activeGroup = "models";
    showGroup();

    const options = [new Option("ローカル", ""), ...(data.hosts ?? []).map(name => new Option(name, name))];
    hostPicker.replaceChildren(...options);
    hostPicker.value = state.host;
    filterSettings();
    updateWriteControls();
  }

  function showGroup() {
    for (const control of navigation.querySelectorAll("button")) {
      const selected = control.dataset.group === state.activeGroup;
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

  return {button, element, renderComparison, renderHost, renderShared, renderSharedComparison, showGroup};
}

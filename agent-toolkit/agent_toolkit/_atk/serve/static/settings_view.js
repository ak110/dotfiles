import {BASE_PATH} from "./common.js";
import {renderResetPermissions} from "./reset_permissions.js";

const SETTING_NAMES = {
  execute_model: "実装のモデル", review_model: "レビューのモデル", review_fast_model: "一括置換後レビューのモデル",
  chores_model: "補助作業のモデル", chores_fast_model: "軽量な補助作業のモデル",
  orchestrate_model: "統括のモデル", write_model: "文書執筆のモデル",
  codex_fast_mode: "Codexの高速モード", usage_reset_permissions: "利用枠のリセット許可",
  codex_credits_provider: "Codexの有限クレジットの接続先", codex_payg_provider: "Codexの従量課金の接続先",
  claude_orchestrator_connections: "Claudeの統括で使う接続順", claude_worker_connections: "Claudeの委譲先で使う接続順",
  codex_orchestrator_connections: "Codexの統括で使う接続順", codex_worker_connections: "Codexの委譲先で使う接続順",
  claude_usage_limit_wait: "Claudeの利用枠の待機条件", codex_usage_limit_wait: "Codexの利用枠の待機条件",
  session_review_auto_start: "会話の振り返りを自動で開始", weekly_research_x_api: "調査で有償のX APIを使用",
  dotfiles_mode: "dotfilesの配布範囲", agents_concurrency: "委譲先の同時実行数",
  serve_port: "待受ポート", serve_host: "待受アドレス", remote_hosts: "接続先ホスト",
  serve_web_push: "ブラウザーを閉じている間の通知", serve_web_push_details: "通知に含める作業情報",
  serve_plans_root: "計画の保存先", repositories: "リポジトリの登録",
};

function displayName(names, key) {
  return Object.hasOwn(names, key) ? names[key] : key;
}

export function settingName(key) {
  return displayName(SETTING_NAMES, key);
}

const FAST_MODE_LABELS = {auto: "自動判定", true: "高速", false: "通常"};
const BOOLEAN_LABELS = {true: "有効", false: "無効"};

function choiceLabels(key, item) {
  if (key === "codex_fast_mode") return FAST_MODE_LABELS;
  return item.choices?.length === 2 && item.choices.includes("true") && item.choices.includes("false") ? BOOLEAN_LABELS : {};
}

function valueLabel(key, item, value) {
  if (!value) return "未指定";
  if (key === "usage_reset_permissions") return `${JSON.parse(value).length}件の許可`;
  if (item.array) return JSON.parse(value).join("、") || "なし";
  return displayName(choiceLabels(key, item), value);
}

function reflection(key, item, data) {
  const {source} = item;
  if (source === "一時指定" || source?.startsWith("AGENT_TOOLKIT_CONFIG_")) return "上書きの解除後";
  if (data.listen?.[key] && data.listen[key][1] !== "atk config") return "起動時の指定を外して再起動した後";
  if (key === "remote_hosts") return "設定は保存後、計画・セッションは次回起動時";
  if (key.startsWith("serve_")) return "次回のatk serve起動時";
  if (key === "dotfiles_mode") return "次のdotfiles更新時";
  return null;
}

export function savedSettingMessage(payload, before, after) {
  const {key} = payload;
  const old = before?.settings[key];
  const item = after.settings[key];
  if (!old || !item || payload.action === "reset-reissue") return "設定を保存しました。";
  const from = valueLabel(key, old, old.saved);
  const to = valueLabel(key, item, item.saved);
  const change = from === to ? to : `${from} → ${to}`;
  const target = item.shared ? (item.shared_sync ? "共有" : "このホストの共有ファイル") : "このホスト";
  const timing = reflection(key, item, after) ?? "次にこの設定を使うとき";
  if (key === "usage_reset_permissions" && from === to && old.saved !== item.saved) {
    return `許可を更新しました（${target}）。反映: ${timing}。`;
  }
  return `保存済み（${target}）: ${change}。反映: ${timing}。`;
}

export function createSettingsView({
  content, shared, comparison, hostPicker, navigation, state,
  operate, saveShared, migrateShared, selectHost, filterSettings, updateUnsaved, updateWriteControls, setWriteUnavailable,
  rememberSettingFocus, restoreSettingFocus, editorFields,
}) {
  let fieldCount = 0;
  let actionControl;

  function performAction(control, action) {
    actionControl = control;
    try {
      return action();
    } finally {
      actionControl = undefined;
    }
  }
  const guide = "https://github.com/ak110/dotfiles/blob/master/docs/guide/";

  function element(tag, text = "", className = "") {
    const result = document.createElement(tag);
    result.textContent = text;
    if (className) result.className = className;
    return result;
  }

  function section(parent, title, url) {
    const target = element("section");
    const heading = element("h3", title, "page-heading");
    target.append(heading);
    if (url) {
      const link = element("a", "ガイドを読む", "settings-guide");
      link.href = guide + url;
      target.append(link);
    }
    parent.append(target);
    return target;
  }

  function button(text, action) {
    const result = element("button", text);
    result.type = "button";
    result.addEventListener("click", () => performAction(result, action));
    return result;
  }

  const categoryPicker = document.getElementById("settings-group-picker");
  const categoryLinks = document.getElementById("settings-group-links");
  const migrationShortcut = document.getElementById("settings-migration-shortcut");

  const compactCategories = matchMedia("(max-width: 48rem)");
  compactCategories.addEventListener("change", () => updateCategories());
  let categoryOptions = [];
  let currentCategory = "";
  function updateCategories() {
    categoryPicker.replaceChildren(...(compactCategories.matches ? categoryOptions : []));
    categoryPicker.value = currentCategory;
    migrationShortcut.hidden = !compactCategories.matches || currentCategory === "migration" ||
      !categoryLinks.querySelector("[data-needs-choice]");
  }
  categoryPicker.addEventListener("change", () => chooseGroup(categoryPicker.value));
  migrationShortcut.addEventListener("click", () => {
    chooseGroup("migration");
    const heading = document.querySelector("#settings-group-migration h3");
    heading.tabIndex = -1;
    heading.focus();
  });

  function chooseGroup(id) {
    state.activeGroup = id;
    showGroup();
    content.closest("main").scrollTop = 0;
  }

  function sharedScope(syncs) {
    return syncs
      ? "保存先: 同じprivate-notesを使うホストで共有されます。このホストだけ変えるには一時指定を使ってください。"
      : "保存先: このホストのprivate-notes。同期先が未設定のため、他のホストには反映されません。";
  }

  function field(parent, label, value = "", choices, type = "text", labels = {}) {
    const wrapper = element("div");
    wrapper.className = "settings-field";
    const input = element(choices ? "select" : type === "multiline" ? "textarea" : "input");
    for (const choice of choices ?? []) input.append(new Option(labels[choice] ?? choice, choice));
    if (!choices && type !== "multiline") input.type = type;
    if (type === "multiline") input.rows = 3;
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
    output.setAttribute("role", "status");
    return output;
  }

  function form(parent, perform) {
    const result = element("form");
    result.addEventListener("submit", event => {
      event.preventDefault();
      performAction(event.submitter, perform);
    });
    parent.append(result);
    return result;
  }

  function submit(form, label) {
    const control = element("button", label, "button-primary");
    control.type = "submit";
    const actions = element("div", "", "form-actions");
    actions.append(control);
    form.append(actions);
    return control;
  }

  function keyEditor(parent, key, item, data, save = operate) {
    const listen = data.listen ?? {};
    const block = element("details", "", "settings-row");
    const title = settingName(key);
    block.dataset.setting = key;
    block.dataset.search = `${title} ${key} ${item.description ?? ""}`.toLowerCase();
    block.dataset.editor = `setting:${key}`;
    block.dataset.editorLabel = title;
    const summary = element("summary");
    const overview = element("span", "", "settings-row-overview");
    const name = element("span", "", "settings-row-label");
    name.append(element("strong", title));
    const unsaved = element("span", "未保存", "settings-draft-state secondary-text");
    unsaved.hidden = true;
    name.append(unsaved);
    const readOnly = item.read_only || (key === "usage_reset_permissions" && !data.reset_editor && state.host);
    if (item.invalid_local) name.append(element("span", "設定値に問題あり", "secondary-text"));
    else if (readOnly) name.append(element("span", "読み取り専用", "secondary-text"));
    const current = element("span", "", "settings-current-values");
    const labels = choiceLabels(key, item);
    const running = listen[key];
    const differs = item.saved !== item.effective;
    const hasDistinctRunningValue = running && String(running[0]) !== item.effective;
    function appendPreview(label, value, showLabel) {
      const line = element("span", "", "settings-value-line");
      line.append(element("span", label, showLabel ? "secondary-text" : "visually-hidden"),
        element("span", valueLabel(key, item, value)));
      current.append(line);
    }
    if (hasDistinctRunningValue) {
      appendPreview("実行中: ", String(running[0]), true);
      const nextLabel = running[1] === "atk config" ? "次回起動: " : "起動時の指定を外すと: ";
      appendPreview(nextLabel, item.effective, true);
    } else {
      appendPreview("実効値: ", item.effective, differs);
    }
    if (differs) appendPreview("保存済み: ", item.saved, true);
    overview.append(name, current);
    summary.append(overview);
    block.append(summary);
    const description = element("div", "", "settings-setting-info");
    const details = element("details", "", "settings-setting-details secondary-text");
    details.append(element("summary", "形式・出所"));
    if (title !== key) details.append(element("code", key, "setting-key"));
    if (item.description) details.append(element("p", item.description, "setting-description"));
    if (item.source) details.append(element("p", `出所: ${item.source}`));
    if (running) details.append(element("p", `実行中の待受: ${running[0]}（${running[1]}）`));
    if (item.format) details.append(element("p", `形式: ${item.format}`));
    if (item.default !== undefined) details.append(element("p", `初期値: ${item.default || "未指定"}`));
    if (item.shared) details.append(element("p", sharedScope(item.shared_sync)));
    if (item.invalid_local) {
      description.append(element("p", "この端末のconfig.jsonに無効な値があります。共有値を変更する前に、端末の設定ファイルを修正してください。", "notice notice-warning"));
      description.append(details);
      block.append(description);
      parent.append(block);
      return block;
    }
    if (readOnly) {
      if (key === "usage_reset_permissions" && state.host) {
        description.append(element("p", "このホストのatkを更新すると画面で許可を編集できます。CLIで設定するには atk config set usage_reset_permissions を使います。"));
      }
      description.append(details);
      block.append(description);
      parent.append(block);
      return block;
    }
    const controls = element("div", "", "settings-setting-controls");
    block.append(controls);
    const output = settingResult();
    if (key === "usage_reset_permissions") {
      renderResetPermissions(controls, JSON.parse(item.saved), data.reset_editor,
        (entries, editor) => save({action: "set", key, value: JSON.stringify(entries)}, output, editor),
        {element, field, form, button, submit, setWriteUnavailable},
        data.reset_reissue, (id, count) => save({action: "reset-reissue", key, id, count}, output));
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
      editor.classList.add("settings-scalar-form");
      const input = field(editor, "保存値", item.saved, item.choices,
        !item.choices && item.saved?.length > (key.endsWith("_model") ? 30 : 48) ? "multiline" : "text", labels);
      if (key === "serve_port") input.inputMode = "numeric";
      output.id = `${input.id}-result`;
      input.setAttribute("aria-describedby", output.id);
      submit(editor, "保存");
    }
    controls.append(output);
    const timing = reflection(key, item, data);
    if (timing) controls.append(element("p", `反映: ${timing}`, "secondary-text settings-reflection"));
    controls.append(details);
    parent.append(block);
    return block;
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
      const data = {action: "repo", operation: repo ? "set" : "add", repo: repo || identifier.value};
      if (!repo || edit.value !== attributes.edit) data.edit = edit.value;
      if (loop.value !== String(attributes.process_loop ?? false)) data.process_loop = loop.value === "true";
      if (platforms.value !== (attributes.platforms ?? ["linux", "windows"]).join(",")) data.platforms = platforms.value;
      if (path.value !== (attributes.path ?? "")) data.path = path.value;
      if (branches.value !== (attributes.branches ?? []).join(",")) data.branches = branches.value;
      if (defaultBranch.value !== (attributes.default_branch ?? "") && branches.value) data.default_branch = defaultBranch.value;
      if (mergeFrom.value !== Object.entries(attributes.merge_from ?? {}).map(([target, source]) => `${target}=${source}`).join(",")) {
        data.merge_from = mergeFrom.value;
      }
      void operate(data, output);
    }));
    if (repo) box.append(button("登録を削除", () => {
      if (window.confirm(`${repo}の登録を削除しますか？`)) void operate({action: "repo", operation: "remove", repo}, output);
    }));
    box.append(output);
    parent.append(box);
  }

  function shownValue(value) {
    if (value == null) return "未保存";
    return typeof value === "string" ? value : JSON.stringify(value);
  }

  // 画面の定期更新で描き直しても、確認済みの差と選択を残す。 pyfltr: allow-comment
  const migrationPlans = new Map();
  function migrationPanel(parent, data) {
    const migration = data.shared_migration;
    const available = migration?.plan;
    const pending = migration?.pending ??
      Object.entries(data.settings).filter(([, item]) => item.migration_pending).map(([key]) => key);
    const host = state.host;
    if (!available && !pending.length && !migration?.sync_failed) {
      migrationPlans.delete(host);
      return false;
    }
    const target = element("div");
    target.dataset.sharedMigration = "";
    parent.append(target);
    if (migration?.sync_failed) {
      target.append(element("p", "共有先を確認できません。確認できていない値は端末に残っています。", "notice notice-warning"));
      if (!available) return true;
    }
    if (!available) {
      target.append(element("p", "この端末のatkは旧版です。設定の競合を解決するにはatkを更新してください。"));
      return true;
    }
    if (data.shared_identity && state.localData?.shared_identity && data.shared_identity !== state.localData.shared_identity) {
      target.append(element("p", "ここで選んだ値は、この端末の共有先に保存します。"));
    }
    if (migrationPlans.get(host)?.plan.revision !== available.revision) {
      migrationPlans.set(host, {plan: available, choices: {}});
    }
    const output = settingResult();
    target.append(output);
    renderPlan(target, migrationPlans.get(host), data, output);
    return true;
  }

  function migrationItemName(item) {
    if (item.repo) {
      const names = {edit: "編集可否", platforms: "検証OS", branches: "作業する系統", github_issues: "GitHubの取り込み"};
      const field = item.fields?.[0];
      return field ? `${item.repo} · ${displayName(names, field)}` : item.repo;
    }
    const names = {codex_connections: "Codexの接続順と接続先", claude_connections: "Claudeの接続順"};
    return Object.hasOwn(names, item.key) ? names[item.key] : settingName(item.key);
  }

  function migrationRepair(item, data) {
    if (item.repo || item.key === "repositories") {
      return "この端末のconfig.jsonで、リポジトリの登録を修正してください。";
    }
    if ((item.fields ?? [item.key]).some(key => data.settings[key]?.invalid_local)) {
      return "まずこの端末のconfig.jsonで無効な値だけを修正してください。";
    }
    return "共有先のatk-config.jsonで、エラーに示された保存値を確認してください。";
  }

  function renderPlan(target, {plan, choices}, data, output) {
    target.querySelector("[data-migration-plan]")?.remove();
    const box = element("form");
    box.dataset.migrationPlan = "";
    box.setAttribute("aria-label", "共有設定の選択");
    for (const item of plan.items.filter(item => item.kind === "different")) {
      const group = element("fieldset");
      group.append(element("legend", migrationItemName(item)));
      for (const [choice, label, value] of [
        ["local", "この端末の値", item.local], ["shared", "共有の値", item.shared ?? item.shared_effective],
      ]) {
        const wrapper = element("label", "", "settings-migration-option");
        const radio = element("input");
        radio.type = "radio";
        radio.name = `migration-${item.key}`;
        radio.dataset.migrationFocus = `${item.key}:${choice}`;
        radio.value = choice;
        radio.required = true;
        radio.checked = choices[item.key] === choice;
        radio.addEventListener("change", () => { choices[item.key] = choice; });
        wrapper.append(radio, element("span", `${label}: ${shownValue(value)}`));
        group.append(wrapper);
      }
      if (item.loosens_edit) group.append(element("p", "共有の値はこの端末の編集制限より緩い値です。", "notice notice-warning"));
      box.append(group);
    }
    if (plan.required.length) box.append(element("p", "選んだ値は同じ共有先を使う全ての端末に反映されます。"));
    for (const item of plan.items.filter(item => item.error)) {
      box.append(element("p", `${migrationItemName(item)}: ${item.error} ${migrationRepair(item, data)}`, "notice notice-warning"));
    }
    if (plan.required.length) {
      const apply = element("button", "選んだ値を保存", "button-primary");
      apply.type = "submit";
      apply.dataset.migrationFocus = "save";
      const wait = element("p", "最新の共有設定を確認できるまで保存できません。", "notice");
      wait.id = "settings-migration-wait";
      wait.dataset.migrationWait = "";
      wait.hidden = true;
      box.append(wait);
      box.addEventListener("submit", async event => {
        event.preventDefault();
        if (apply.getAttribute("aria-disabled") === "true" || plan.required.some(key => !choices[key])) return;
        await performAction(apply, () => migrateShared({action: "apply", revision: plan.revision, choices}, output));
      });
      const actions = element("div", "", "form-actions");
      actions.append(apply);
      box.append(actions);
    }
    target.insertBefore(box, output);
  }

  function sameSharedSetting(data, key) {
    const here = state.localData?.settings[key];
    const there = data.settings?.[key];
    return Boolean(state.localData?.shared_identity && data.shared_identity === state.localData.shared_identity &&
      here?.shared && !here.migration_pending && there?.shared && !there.migration_pending);
  }

  function groupId(key) {
    return key.endsWith("_model") ? "models" : (key.startsWith("serve_") || key === "remote_hosts") ? "serve" :
      key === "repositories" ? "repos" : /usage|reset/.test(key) ? "usage" :
      /connections|provider|env_file/.test(key) ? "connections" : "other";
  }

  let sharedEditors = new Map();

  function scope(group, name) {
    return content.querySelector(`[data-scope="${name}"][data-group="${group}"]`);
  }

  function addScope(group, id, name, title) {
    const target = element("div", "", "settings-scope");
    target.dataset.scope = name;
    target.dataset.group = id;
    const header = element("div", "", "settings-scope-heading");
    const label = element("p", title, "section-heading");
    const valueColumn = element("span", "設定の値", "settings-value-heading secondary-text");
    valueColumn.setAttribute("aria-hidden", "true");
    header.append(label, valueColumn);
    label.id = `settings-scope-${id}-${name}`;
    target.setAttribute("role", "group");
    target.setAttribute("aria-labelledby", label.id);
    target.append(header);
    group.append(target);
    return target;
  }

  function renderShared(savedKey, savedEditorKey) {
    const data = state.localData;
    if (!data || !content.querySelector("[data-scope=\"shared\"]")) return;
    const focus = rememberSettingFocus(document.activeElement);
    const old = sharedEditors;
    sharedEditors = new Map();
    for (const target of content.querySelectorAll("[data-scope=\"shared\"]")) target.replaceChildren(target.firstChild);
    const note = element("p");
    note.dataset.sharedComparison = "";
    for (const [key, item] of Object.entries(data.settings)) {
      if (!item.shared) continue;
      const target = scope(groupId(key), "shared");
      const prior = old.get(key);
      if (prior && !item.invalid_local && key !== savedKey &&
          editorFields(prior).some(field => field.value !== field.dataset.initialValue)) {
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
        sharedEditors.set(key, prior);
      } else {
        const editor = keyEditor(target, key, item, data, saveShared);
        editor.open = prior?.open ?? false;
        const details = editor.querySelector(".settings-setting-details");
        if (details && prior?.querySelector(".settings-setting-details")?.open) details.open = true;
        sharedEditors.set(key, editor);
      }
    }
    const sharedPresets = scope("models", "shared");
    for (const [preset, values] of Object.entries(data.presets ?? {})) {
      if (!Object.keys(values).length || !Object.keys(values).every(key => data.settings[key]?.shared)) continue;
      const detail = element("details");
      detail.dataset.editor = `preset:${preset}`;
      detail.open = detail.dataset.editor === savedEditorKey;
      detail.append(element("summary", preset));
      for (const [key, value] of Object.entries(values)) detail.append(element("p", `${key}: ${value}`));
      const output = settingResult();
      const actions = element("div", "", "form-actions");
      actions.append(button("適用", () => saveShared({action: "preset", preset}, output)));
      detail.append(actions, output);
      sharedPresets?.append(detail);
    }
    shared.replaceChildren(note);
    renderSharedComparison();
    if (focus?.shared) restoreSettingFocus(focus);
  }

  function renderSharedComparison() {
    const note = shared.querySelector("[data-shared-comparison]");
    const data = state.localData;
    if (!note || !data) return;
    const hasShared = Object.values(data.settings).some(item => item.shared);
    note.replaceChildren(...(hasShared ? ["共有の保存先: private-notes"] : []));
    for (const [name, peer] of Object.entries(data.comparison ?? {})) {
      const common = Object.keys(data.settings).filter(key => sameSharedSetting(peer, key));
      const failed = peer.state === "error" && state.confirmedComparisonFailures.has(name);
      if (peer.state === "ready" && !hasShared) continue;
      if (!failed && !Object.keys(peer.settings ?? {}).length && ["pending", "error"].includes(peer.state)) continue;
      const different = common.filter(key => data.settings[key].saved !== peer.settings[key].saved);
      const summary = failed ? "取得失敗" :
        peer.state === "legacy" ? "旧版のため自動取得なし" :
        peer.state !== "ready" || common.length === 0 ? "共有先の一致は未確認" :
        different.length ? `共有値に差あり（${different.map(settingName).join("、")}）` :
        "共有値は一致";
      const separator = note.hasChildNodes() ? " · " : "";
      note.append(element("span", `${separator}${name}: ${summary}`));
    }
    for (const old of shared.querySelectorAll("[data-provider-warning]")) old.remove();
    const warnings = [["ローカル", data.provider_warnings ?? []],
      ...Object.entries(data.comparison ?? {}).map(([name, peer]) => [name, peer.provider_warnings ?? []])];
    for (const [name, list] of warnings) {
      for (const warning of list) {
        const line = element("p", `${name}: ${warning.message}`, "notice notice-warning");
        line.dataset.providerWarning = name;
        note.after(line);
      }
    }

  }

  function comparisonValue(key, setting, info) {
    if (!setting) return info.state === "ready" ? "未設定" : "未取得";
    const value = String(setting.saved ?? "");
    if (!value || value === "{}") return "未設定";
    if (value === "[]") return "なし";
    if (key === "codex_fast_mode") return displayName({auto: "自動判定", true: "高速", false: "通常"}, value);
    if (key.endsWith("_connections")) {
      const labels = {subscription: "定額契約", credits: "クレジット", payg: "従量課金"};
      return value.split(",").map(name => displayName(labels, name.trim())).join(" → ");
    }
    if (key.endsWith("_usage_limit_wait")) {
      return displayName({never: "待たない", "five-hour": "5時間枠の回復を待つ", always: "回復を待つ"}, value);
    }
    return displayName({true: "有効", false: "無効"}, value);
  }

  function renderComparison(data) {
    const prior = comparison.querySelector(".settings-comparison-scroll");
    const scrollLeft = prior?.scrollLeft ?? 0;
    const active = document.activeElement;
    const focusedHost = comparison.contains(active) ? active.dataset.comparisonHost : undefined;
    const hosts = [ ["", {state: "ready", ...state.localData}], ...Object.entries(data.comparison ?? state.localData?.comparison ?? {}) ];
    if (hosts.length === 1) {
      comparison.replaceChildren(element("p", "比較できるホストがありません。接続先ホストを追加すると比較できます。"));
      if (active === prior || focusedHost !== undefined) {
        comparison.closest("details")?.querySelector("summary")?.focus({preventScroll: true});
      }
      return;
    }
    const table = element("table");
    table.createCaption().textContent = "ホストごとの保存値";
    const headers = table.createTHead().insertRow();
    const label = element("th", "設定");
    label.scope = "col";
    headers.append(label);
    for (const [name, info] of hosts) {
      const header = element("th", name || "ローカル");
      header.scope = "col";
      if (info.state === "legacy") header.append(element("p", "旧版のため自動取得なし"));
      headers.append(header);
    }
    const body = table.createTBody();
    for (const key of new Set(hosts.flatMap(([, value]) => Object.keys(value.settings ?? {})))) {
      const values = hosts.map(([name, info]) => {
        if (name ? sameSharedSetting(info, key) : info.settings?.[key]?.shared) return null;
        return {setting: info.settings?.[key], info};
      });
      if (values.every(value => !value || !value.setting)) continue;
      const different = new Set(values.filter(value => value?.setting).map(({setting}) => setting.saved)).size > 1;
      const row = body.insertRow();
      row.toggleAttribute("data-different", different);
      const heading = element("th", settingName(key));
      heading.scope = "row";
      if (different) heading.append(" ", element("span", "相違あり", "status-chip"));
      row.append(heading);
      for (const [index, value] of values.entries()) {
        const cell = row.insertCell();
        const displayed = value ? comparisonValue(key, value.setting, value.info) : "共有設定を使用";
        for (const [part, segment] of displayed.split(/(?<=[,/])/u).entries()) {
          if (part > 0) cell.append(element("wbr"));
          cell.append(segment);
        }
        if (different && value?.setting && value.setting.saved !== values[0]?.setting?.saved) {
          cell.className = "settings-different";
        }
      }
    }
    const actions = element("div", "", "form-actions");
    let restoreFocus;
    for (const [name] of hosts) {
      const open = button(`${name || "ローカル"}の設定を開く`, () => selectHost(name));
      open.dataset.comparisonHost = name;
      if (name === focusedHost) restoreFocus = open;
      actions.append(open);
    }
    const scroll = element("div", "", "settings-comparison-scroll");
    scroll.tabIndex = 0;
    scroll.setAttribute("role", "region");
    scroll.setAttribute("aria-label", "ホストごとの保存値");
    scroll.append(table);
    comparison.replaceChildren(scroll, actions);
    scroll.scrollLeft = scrollLeft;
    if (active === prior) scroll.focus({preventScroll: true});
    else restoreFocus?.focus({preventScroll: true});
  }

  function renderHost(data) {
    const root = document.createDocumentFragment();
    const groups = {
      migration: section(root, "共有設定の選択", "claude-code-settings.md#共有設定への移行"),
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
    if (data.loadingHost || !migrationPanel(groups.migration, data)) groups.migration.remove();
    for (const warning of data.provider_warnings ?? []) {
      groups.connections.append(element("p", `${state.host || "ローカル"}: ${warning.message}`, "notice notice-warning"));
    }
    const settings = Object.entries(data.settings).sort(([left], [right]) =>
      Number(right === "remote_hosts") - Number(left === "remote_hosts"));
    const hostTitle = `このホストだけ（${state.host || "ローカル"}）`;
    const scopes = {};
    for (const id of ["models", "connections", "usage", "repos", "serve", "other"]) {
      addScope(groups[id], id, "shared", "全ホスト共通");
      scopes[id] = addScope(groups[id], id, "host", hostTitle);
    }
    for (const [key, item] of settings) {
      if ((!state.host && item.shared) || (state.host && sameSharedSetting(data, key))) continue;
      if (key !== "repositories" || !data.repositories) keyEditor(scopes[groupId(key)], key, item, data);
    }
    for (const [key, value] of Object.entries(data.paths)) scopes.other.append(element("p", `${key}: ${value}`));
    for (const [repo, attributes] of Object.entries(data.repositories ?? {})) {
      if (!attributes.path && data.shared_migration && /^[^/]+\/[^/]/.test(repo) && !repo.startsWith("/")) {
        const missing = element("p", `${repo}: この端末には未登録。atk config repos set ${repo} --path <clone>`);
        missing.dataset.repositoryUnlinked = repo;
        groups.repos.append(missing);
      }
      repositoryEditor(groups.repos, repo, attributes);
    }
    if (data.repositories) repositoryEditor(groups.repos);
    if (data.model_order_policy) groups.usage.append(element("p", data.model_order_policy));
    const usageLink = element("a", "利用枠の使用率とリセット権を見る");
    usageLink.href = BASE_PATH + "/usage";
    groups.usage.append(usageLink);
    if (data.temporary) renderTemporary(groups.temporary, data);
    else groups.temporary.remove();
    const presets = scopes.models;
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
    content.replaceChildren(root);
    renderShared();
    categoryLinks.replaceChildren();
    for (const [id, group] of Object.entries(groups)) {
      if (!group.parentNode) continue;
      group.id = `settings-group-${id}`;
      const control = button(group.querySelector("h3").textContent, () => chooseGroup(id));
      control.classList.add("choice-button");
      control.dataset.group = id;
      control.toggleAttribute("data-needs-choice", id === "migration" && Boolean(data.shared_migration?.plan?.required?.length));
      control.setAttribute("aria-controls", group.id);
      categoryLinks.append(control);
    }
    const options = [new Option("ローカル", ""), ...(data.hosts ?? []).map(name => new Option(name, name))];
    hostPicker.replaceChildren(...options);
    hostPicker.value = state.host;
    filterSettings();
    updateWriteControls();
  }

  function showGroup(searching = false) {
    const controls = [...categoryLinks.querySelectorAll("button")];
    const groups = controls.map(control => document.getElementById(control.getAttribute("aria-controls")));
    for (const target of content.querySelectorAll("[data-scope]")) {
      target.hidden = searching ? !target.querySelector("[data-setting]:not([hidden])") : target.children.length === 1;
    }
    const available = groups.map(group => [...group.children].some(child =>
      !child.hidden && !child.matches(".page-heading, .settings-guide")));
    const active = controls.some((control, index) => available[index] && control.dataset.group === state.activeGroup) ?
      state.activeGroup : controls[available.indexOf(true)]?.dataset.group;
    for (const [index, control] of controls.entries()) {
      const selected = !searching && available[index] && control.dataset.group === active;
      control.hidden = searching || !available[index];
      groups[index].hidden = searching ? !groups[index].querySelector("[data-setting]:not([hidden])") : !selected;
      if (selected) control.setAttribute("aria-current", "page");
      else control.removeAttribute("aria-current");
    }
    categoryOptions = controls.flatMap((button, index) =>
      available[index] ? [new Option(button.textContent, button.dataset.group)] : []);
    currentCategory = active ?? "";
    updateCategories();
    navigation.hidden = searching || !available.some(Boolean);
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
      return field(parent, "値", setting.effective, setting.choices, "text", choiceLabels(key.value, setting));
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

  return {button, element, renderComparison, renderHost, renderShared, renderSharedComparison, showGroup,
    getActionControl: () => actionControl};
}

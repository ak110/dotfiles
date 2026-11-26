import {DAY, claudeResetScopeName, dateJst, forecast, quotaName} from "./usage_display.js";

const ENGINES = {codex: "Codex", claude: "Claude"};
const INVENTORY_HELP = "このアカウントのリセット権は未登録です。";
const finite = Number.isFinite;
const seconds = instant => Date.parse(instant) / 1000;

export function formatLocalMinute(instant) {
  if (!instant) return "";
  const date = new Date(instant);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

function takeInstant(input) {
  if (input.value === input.dataset.instantDisplay) return input.dataset.instant || "";
  return input.value ? new Date(input.value).toISOString() : "";
}

function fillInstant(input, time) {
  const instant = finite(time) ? new Date(time * 1000).toISOString() : "";
  input.value = formatLocalMinute(instant);
  input.dataset.instantDisplay = input.value;
  input.dataset.instant = instant;
}

function permitId(engine, now) {
  const date = new Date(now * 1000);
  const [day, minute] = formatLocalMinute(date).split("T");
  const stamp = day.replaceAll("-", "") + "-" + minute.replace(":", "") + String(date.getSeconds()).padStart(2, "0");
  const random = crypto.getRandomValues(new Uint32Array(1))[0].toString(16).padStart(8, "0");
  return `${engine}-${stamp}-${random}`;
}

function planLabel(engine, account) {
  const plan = account?.plan;
  return ENGINES[engine] + (plan && !/^unknown(?::|$)/.test(plan) ? `（${plan}）` : "");
}

function knownAccount(account) {
  return typeof account === "string" && account !== "" && !/^unknown(?::|$)/.test(account);
}

function weeklyWindow(engine, scope, model) {
  if (model.failed_engines?.includes(engine)) return null;
  if (engine === "codex") {
    const weekly = model.codex_weekly;
    if (!weekly || scope !== "codex") return null;
    const observed = model.windows.find(window => window.engine === "codex" &&
      window.window_id === weekly.window_id && window.resets_at === weekly.resets_at);
    return {...observed, ...weekly, engine, duration_seconds: 7 * DAY};
  }
  return model.windows
    .filter(window => window.engine === "claude" && window.duration_seconds === 7 * DAY &&
      (scope === "all" ? window.window_id === "weekly_all" : window.aliases?.includes(scope)))
    .reduce((newest, window) => !newest || window.observed_at > newest.observed_at ? window : newest, null);
}

function cutoff(window, grace, now, from) {
  if (!window) return {reason: ""};
  const reset = window.resets_at;
  if (!finite(reset) || reset <= now || reset <= from) return {reason: "開始より後の次回リセット日時を確認できません。"};
  const until = reset - grace * DAY;
  if (until <= from || until <= now) return {reason: "猶予を取ると許可の期間が残りません。"};
  return {until};
}

function recommendation(engine, account, scope, model, entries, grace, now) {
  if (model.accounts[engine]?.account !== account || !knownAccount(account)) {
    return {reason: ""};
  }
  const inventory = model.claude_inventory.filter(right => right.account === account && right.count > 0 &&
    seconds(right.until) > now && ["all", "weekly_all"].includes(right.scope));
  if (engine === "claude" && !inventory.length) return {reason: INVENTORY_HELP};
  const scopeForAdvice = engine === "claude" ? inventory[0].scope : scope;
  const window = weeklyWindow(engine, scopeForAdvice, model);
  const estimate = forecast(window, now);
  const reached = estimate.kind === "reached";
  const reset = window?.resets_at;
  if (reached && !(reset > now)) return {reason: "上限に到達していますがリセット日時が分からないため提案できません。"};
  if (estimate.kind === "sufficient") return {reason: "今週は許可不要です。リセットまでもつ見込みです。"};
  if (!reached && estimate.kind !== "exhausted") return {reason: ""};
  const stopped = reset - estimate.at;
  const cause = reached ? "既に上限に到達" : `このペースでは${dateJst(estimate.at)}に上限に達する見込み`;
  const evidence = `${cause}。リセットまで約${(stopped / DAY).toFixed(1)}日止まる見込みです。`;
  if (stopped <= grace * DAY) return {reason: `今週は許可不要です。 ${evidence}`};
  const {until, reason} = cutoff(window, grace, now, now);
  if (!until) return {reason: `${reason} ${evidence}`};
  if (engine === "claude" && !inventory.some(right => right.scope === scopeForAdvice && seconds(right.until) > estimate.at)) {
    return {reason: "在庫の期限が上限到達の見込みより前のため提案できません。"};
  }
  if (entries.some(entry => entry.engine === engine && entry.account === account && entry.scope === scopeForAdvice &&
    entry.count >= 1 && seconds(entry.from) <= now && seconds(entry.until) >= until)) {
    return {reason: `期間を覆う許可が設定済みです。 ${evidence}`};
  }
  return {reason: `おすすめ: 1回、今から次の週次リセットの${grace}日前まで。 ${evidence}`, until, scope: scopeForAdvice};
}

function permissionSummary(entry, model) {
  const now = Date.now() / 1000;
  const from = seconds(entry.from);
  const until = seconds(entry.until);
  const status = entry.count === 0 ? "回数0（使わない）" : now < from ? "開始前" : now >= until ? "期限切れ" : "期間内";
  const observed = model.accounts[entry.engine];
  const plan = observed?.account === entry.account ? observed : null;
  return `${planLabel(entry.engine, plan)} · ${entry.count}回 · ${dateJst(from)}〜${dateJst(until)} · ${status} · ${entry.id}`;
}

export function renderResetPermissions(parent, entries, model, save, ui, reissuable = {}, reissue) {
  model ??= {accounts: {}, claude_inventory: [], windows: []};
  const {element, field, form, button, submit, setWriteUnavailable} = ui;
  parent.append(element("p", "Codexは指定した期間と回数の範囲で、エージェントが自動でresetを使えます。Claudeは自動で使わず、在庫と利用枠に応じて公式画面で使うよう案内します。許可が空なら自動のresetは行いません。"));
  for (const [index, entry] of [...entries, null].entries()) {
    const box = element("details");
    box.dataset.editor = `usage_reset_permissions:${entry?.id ?? ""}`;
    const summary = element("summary", entry ? permissionSummary(entry, model) : "許可を追加");
    summary.className = "permission-caption text-clip";
    summary.title = summary.textContent;
    box.append(summary);
    if (entry && Object.hasOwn(reissuable, entry.id)) {
      const remaining = reissuable[entry.id];
      summary.textContent += " · 消費を停止中";
      summary.title = summary.textContent;
      if (remaining === null) {
        box.append(element("p", "消費を停止中: この端末の消費記録を確認できないため、再発行できません。"));
      } else {
        box.append(element("p", "消費を停止中: 旧端末の回数を確認できません。他端末の未共有の消費は残りに含まれません。"));
        box.append(button(`共有許可を発行（${remaining}回）`, () => reissue(entry.id, remaining)));
      }
    }
    const editor = form(box, () => {
      const account = selectedAccount();
      const scope = selectedScope();
      const start = takeInstant(from);
      const end = takeInstant(until);
      if (!account || !scope || !start || !end || seconds(start) >= seconds(end)) {
        invalid.textContent = "アカウントと範囲を選び、開始より後の終了日時を入力してください。";
        return;
      }
      const engineName = engine.value;
      const row = {id: entry?.id ?? permitId(engineName, Date.now() / 1000), engine: engineName,
        account, scope, count: count.valueAsNumber, from: start, until: end};
      const next = entries.slice();
      next[index] = row;
      void save(next, box);
    });

    const engine = field(editor, "エンジン", entry?.engine ?? "codex", Object.keys(ENGINES), "text", ENGINES);
    const account = field(editor, "アカウント", "", []);
    account.dataset.permissionAccount = "";
    const accountDetails = element("details");
    accountDetails.append(element("summary", "詳細: アカウントの直接入力"));
    const direct = field(accountDetails, "アカウントを直接入力", "");
    direct.dataset.permissionDirect = "";
    editor.append(accountDetails);
    const scope = field(editor, "範囲", "", []);
    const scopeNotice = element("p");
    const codexScope = localButton(button, "codexへ変更", () => {
      scope.value = "codex";
      recalculate();
    });
    editor.append(scopeNotice, codexScope);
    const count = field(editor, "回数", entry?.count ?? 1, undefined, "number");
    count.min = "0";
    count.required = true;
    const from = field(editor, "開始（このブラウザーの時刻）", formatLocalMinute(entry?.from), undefined, "datetime-local");
    from.required = true;
    from.parentElement.append(localButton(button, "今", () => {
      fillInstant(from, Date.now() / 1000);
      recalculate();
    }));
    const until = field(editor, "終了（このブラウザーの時刻）", formatLocalMinute(entry?.until), undefined, "datetime-local");
    until.required = true;
    const grace = field(editor, "リセット前の猶予", "1", ["1", "2", "3"], "text", {"1": "1日", "2": "2日", "3": "3日"});
    const weekly = localButton(button, "", () => fillInstant(until, selectedCutoff().until));
    weekly.dataset.observedDeadline = "";
    const explanation = element("p");
    explanation.id = `${until.id}-weekly`;
    weekly.setAttribute("aria-describedby", explanation.id);
    const duration = element("div");
    duration.className = "permission-actions";
    duration.append(element("span", "リセット日時に基づかない目安"),
      ...[["24時間後", DAY], ["7日後", 7 * DAY]].map(([label, offset]) =>
        localButton(button, label, () => fillInstant(until, Date.now() / 1000 + offset))));
    const advice = element("p");
    const apply = localButton(button, "この提案を入力", () => {
      const now = Date.now() / 1000;
      const proposal = recommendation(engine.value, selectedAccount(), selectedScope(), model, entries, Number(grace.value), now);
      if (!proposal.until) return;
      if (engine.value === "claude") scope.value = proposal.scope;
      count.value = 1;
      fillInstant(from, now);
      fillInstant(until, proposal.until);
      recalculate();
    });
    apply.dataset.observedDeadline = "";
    const invalid = element("p");
    invalid.setAttribute("role", "alert");
    editor.append(weekly, explanation, duration, advice, apply, invalid);
    const saveButton = submit(editor, entry ? "保存" : "追加");
    if (entry) box.append(button("許可を削除", () => save(entries.filter((_, position) => position !== index), box)));
    parent.append(box);

    function selectedAccount() {
      return account.value === "" || account.parentElement.hidden ? direct.value.trim() : account.value;
    }

    function selectedScope() {
      return engine.value === "codex" ? scope.value || "codex" : scope.value;
    }

    function rebuildChoices() {
      const name = engine.value;
      const profile = model.accounts[name];
      const original = entry?.engine === name ? entry : null;
      const preferred = account.value || original?.account;
      const available = profile?.account;
      const selectable = knownAccount(available) &&
        (name === "codex" || model.claude_inventory.some(right => right.account === available));
      const options = selectable ? [new Option(planLabel(name, profile), available)] : [];
      if (original && (!selectable || original.account !== available)) options.unshift(new Option("このホストでは観測していないアカウント", original.account));
      account.parentElement.hidden = options.length === 0;
      account.replaceChildren(...options);
      if (Array.from(account.options).some(option => option.value === preferred)) account.value = preferred;
      rebuildScope();
    }

    function rebuildScope() {
      const name = engine.value;
      const original = entry?.engine === name && entry.account === selectedAccount() ? entry : null;
      const previous = scope.value || original?.scope;
      const offered = name === "codex" ? ["codex"] : [
        ...new Set(model.claude_inventory.filter(right => right.account === selectedAccount()).map(right => right.scope)),
      ];
      const matching = offered.length > 0;
      if (original && !offered.includes(original.scope)) offered.unshift(original.scope);
      scope.replaceChildren(...offered.map(value =>
        new Option(name === "claude" ? claudeResetScopeName(value, model.windows) : value, value)));
      if (offered.includes(previous)) scope.value = previous;
      scope.parentElement.hidden = name === "codex" || !offered.length;
      direct.parentElement.hidden = account.value !== "" && !account.parentElement.hidden;
      accountDetails.hidden = direct.parentElement.hidden;
      codexScope.hidden = name !== "codex" || scope.value === "codex";
      scopeNotice.textContent = !codexScope.hidden ? "自動のresetの対象外の範囲です。" :
        (name === "claude" && !matching ? INVENTORY_HELP : "");
      recalculate();
    }

    function selectedCutoff() {
      return cutoff(model.accounts[engine.value]?.account === selectedAccount() ? weeklyWindow(engine.value, selectedScope(), model) : null, Number(grace.value), Date.now() / 1000, seconds(takeInstant(from)));
    }

    function recalculate() {
      const window = weeklyWindow(engine.value, selectedScope(), model);
      const {until: automatic, reason} = selectedCutoff();
      weekly.textContent = `次の週次リセットの${grace.value}日前まで`;
      setWriteUnavailable(weekly, !automatic);
      explanation.textContent = reason || (automatic ? `${quotaName(window)}: ${dateJst(window.resets_at)}にリセット予定。` : "");
      const proposal = recommendation(engine.value, selectedAccount(), selectedScope(), model, entries, Number(grace.value), Date.now() / 1000);
      advice.textContent = proposal.reason;
      apply.hidden = !proposal.until;
      codexScope.hidden = engine.value !== "codex" || selectedScope() === "codex";
      setWriteUnavailable(saveButton, !selectedScope());
    }

    engine.dataset.draftTrigger = "";
    account.dataset.draftTrigger = "";
    direct.dataset.draftTrigger = "";
    scope.dataset.draftTrigger = "";
    grace.dataset.draftTrigger = "";
    engine.addEventListener("change", rebuildChoices);
    account.addEventListener("change", rebuildScope);
    direct.addEventListener("input", rebuildScope);
    direct.addEventListener("change", rebuildScope);
    for (const input of [scope, grace, from, until]) input.addEventListener("change", recalculate);
    fillInstant(from, entry ? seconds(entry.from) : Date.now() / 1000);
    rebuildChoices();
    fillInstant(until, entry ? seconds(entry.until) : selectedCutoff().until);
    for (const input of editor.querySelectorAll("input, select")) {
      input.dataset.initialValue = input.value;
      input.dataset.initialInstant = input.dataset.instant ?? "";
    }
  }
}

function localButton(button, label, action) {
  const control = button(label, action);
  control.dataset.localEdit = "";
  return control;
}

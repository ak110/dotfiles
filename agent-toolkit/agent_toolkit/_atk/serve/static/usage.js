import {recordUpdateSuccess, setDisplayedReadError, BASE_PATH} from "./common.js";
import {registerScreen} from "./shell.js";
import {DAY, claudeResetScopeName, dateJst, forecast, quotaName} from "./usage_display.js";

const view = document.getElementById("usage-content");
const refreshButton = document.getElementById("usage-refresh");
const overview = document.getElementById("usage-overview");
const attention = document.getElementById("usage-attention");
const antigravity = document.getElementById("usage-antigravity");
const antigravityStatus = document.getElementById("usage-antigravity-status");
const antigravityWindows = document.getElementById("usage-antigravity-windows");
const percent = new Intl.NumberFormat("ja-JP", {style: "percent", maximumFractionDigits: 1});
let antigravityPending = false;
let antigravityObservedAt;
const providerNames = {claude: "Claude", codex: "Codex"};
const providers = Object.keys(providerNames);
let pending = false;
let timer;
let data;
let refreshed = false;

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function link(text, href) {
  const node = element("a", text);
  node.href = href;
  return node;
}

function values(parent, pairs) {
  const list = element("dl", "", "metric-details");
  for (const [label, value] of pairs) {
    if (value == null) continue;
    const row = element("div");
    row.append(element("dt", label), element("dd", String(value)));
    list.append(row);
  }
  if (list.childElementCount) parent.append(list);
}

function booleanStatus(value, yes, no) {
  return value === true ? yes : value === false ? no : "未取得";
}

function namedStatus(value, names) {
  return value == null ? "未取得" : Object.hasOwn(names, value) ? names[value] : "不明";
}

function claudeWindowStatus(window) {
  const group = window.group;
  const groupIsPeriod = (group === "weekly" && (window.duration_seconds === 7 * DAY ||
    window.window_id === "seven_day_overage_included")) || (group === "session" && window.duration_seconds === 5 * 3600);
  return [
    ["利用枠の区分", group == null || groupIsPeriod ? null : namedStatus(group, {weekly: "週", session: "セッション"})],
    ["警告状態", namedStatus(window.severity, {normal: "通常", warning: "警告"})],
    ["利用枠の状態", booleanStatus(window.is_active, "有効", "無効")],
  ];
}

function codexUsageStatus(metadata) {
  const credits = metadata.credits;
  return [
    ["通常利用", booleanStatus(metadata.ordinaryUsageAllowed, "利用可能", "利用不可")],
    ["支出上限", booleanStatus(metadata.spendControlReached, "到達", "未到達")],
    ["上限に達した枠", namedStatus(metadata.rateLimitReachedType, {weekly: "週", primary: "第1枠", secondary: "第2枠"})],
    ["クレジット", booleanStatus(credits?.hasCredits, "あり", "なし")],
    ["クレジットの上限", booleanStatus(credits?.unlimited, "なし", "あり")],
    ["クレジット残高", credits?.balance ?? "未取得"],
  ];
}

function remaining(seconds) {
  const hours = Math.max(0, Math.floor(seconds / 3600));
  const days = Math.floor(hours / 24);
  if (!hours) return "1時間未満";
  return (days ? `${days}日` : "") + (hours % 24 ? `${hours % 24}時間` : "") + "後";
}

function forecastText(result) {
  switch (result.kind) {
    case "reached": return "上限に到達";
    case "exhausted": return `上限に達する見込み: ${dateJst(result.at)}`;
    case "sufficient": return `リセットまでもつ見込み（リセット時点で約${result.percent}%）。`;
    case "expired":
    case "zero": return "";
    case "early": return "観測時点の経過が短いか期間外のため見込みは出せません。";
    default: return "使用率・期間・リセット日時のいずれかが不明のため見込みは出せません。";
  }
}

function predictionFor(window, now) {
  return Number.isFinite(window.resets_at) && window.resets_at <= now ? {kind: "expired"} : forecast(window, now);
}

function quota(window, now, id, prediction, refreshFailed) {
  const row = element("article", "", "list-row metric-row");
  const title = quotaName(window);
  const elapsed = (now - window.resets_at + window.duration_seconds) / window.duration_seconds;
  const heading = element("h4", title);
  heading.id = id;
  heading.tabIndex = -1;
  row.append(heading);
  const fraction = window.used_percent;
  const expired = prediction.kind === "expired";
  const measured = Number.isFinite(fraction) && !expired;
  const observed = window.usage_observed_at ?? window.observed_at;
  const older = Number.isFinite(observed) && now - observed >= 3600;
  const stale = measured && (older || refreshFailed || observed !== window.observed_at || !Number.isFinite(observed));
  if (stale) {
    row.dataset.stale = "";
    const source = Number.isFinite(observed) ? `前回の使用率の観測: ${dateJst(observed)}${older ? "（1時間以上前）" : ""}` :
      "使用率の観測時刻は不明";
    row.append(element("p", source, "secondary-text"));
  }
  const label = expired ? "使用率不明（前回の観測はリセット前）" :
    measured ? `使用 ${fraction}% · 残り ${Math.round((100 - fraction) * 100) / 100}%` : "使用率・残りとも不明";
  row.append(element("p", label, stale ? "metric-snapshot-value" : measured ? "metric-value" : "metric-unavailable"));
  const bar = element("div", "", "metric-track");
  if (measured) {
    const progress = element("progress");
    progress.max = 100;
    progress.value = fraction;
    progress.setAttribute("aria-label", `${title}の${stale ? "前回の" : ""}使用率`);
    if (["reached", "exhausted"].includes(prediction.kind)) progress.dataset.warning = "";
    bar.append(progress);
  }
  const segments = window.duration_seconds === 7 * DAY ? 7 : window.duration_seconds === 5 * 3600 ? 5 : 0;
  for (let index = 1; index < segments; index += 1) {
    const tick = element("span", "", "metric-tick");
    tick.style.insetInlineStart = `${100 * index / segments}%`;
    tick.setAttribute("aria-hidden", "true");
    bar.append(tick);
  }
  const hasElapsed = Number.isFinite(window.resets_at) && Number.isFinite(elapsed) && elapsed >= 0 && elapsed < 1;
  if (hasElapsed) {
    const marker = element("span", `期間の経過 ${Math.round(100 * elapsed)}%`, "metric-marker");
    const offset = `${100 * elapsed}%`;
    bar.style.setProperty("--metric-marker-position", offset);
    const needle = element("span", "", "metric-needle");
    needle.setAttribute("aria-hidden", "true");
    bar.append(needle, marker);
  }
  if (measured || hasElapsed) {
    row.append(bar);
    if (segments) row.append(element("p", segments === 7 ? "1目盛り＝1日" : "1目盛り＝1時間", "secondary-text metric-scale"));
  }
  const recovery = dateJst(window.resets_at);
  values(row, [
    [expired ? "前回のリセット予定" : "次回リセット",
      window.resets_at > now ? `${recovery}（${remaining(window.resets_at - now)}）` : recovery],
    ...(window.engine === "claude" ? claudeWindowStatus(window) : []),
  ]);
  const forecastLabel = forecastText(prediction);
  if (forecastLabel) {
    const estimated = ["exhausted", "sufficient"].includes(prediction.kind);
    const warning = ["reached", "exhausted"].includes(prediction.kind);
    const oldRejection = window.rejected && (refreshFailed || now - window.observed_at >= 3600);
    const oldLimit = prediction.kind === "reached" && (window.rejected ? oldRejection : stale);
    const basis = (estimated && stale) || oldLimit ? "前回の観測: " : "";
    row.append(element("p", basis + forecastLabel, warning ? "metric-warning" : ""));
  }
  const evidence = element("details", "", "metric-evidence");
  evidence.dataset.metricDetail = JSON.stringify([window.engine, window.window_id, window.scope]);
  evidence.append(element("summary", "計測の詳細"));
  values(evidence, [
    ["枠の観測", !stale || observed !== window.observed_at ? dateJst(window.observed_at) : null],
    ["枠の識別子", window.window_id],
  ]);
  row.append(evidence);
  return row;
}

function jumpTo(label, id) {
  const button = element("button", label, "button-text");
  button.type = "button";
  button.dataset.metricJump = id;
  button.addEventListener("click", () => document.getElementById(id)?.focus());
  return button;
}

function describeAttention(items, windows, settled) {
  if (!overview || !attention) return;
  const message = items.length ? `${items.length}件の確認事項があります。` : !settled ? "" : windows.length ?
    "上限に達した枠や、リセット前に上限に達する見込みの枠はありません。" : "表示できる利用枠の観測はありません。";
  overview.hidden = !message;
  if (overview.textContent !== message) overview.textContent = message;
  const focused = attention.contains(document.activeElement) ? document.activeElement : null;
  const oldTarget = focused?.dataset.metricJump;
  attention.replaceChildren(...items);
  attention.hidden = !items.length;
  if (oldTarget) {
    const target = [...attention.querySelectorAll("button")].find(button => button.dataset.metricJump === oldTarget);
    (target ?? (refreshButton.disabled ? document.getElementById(oldTarget) : refreshButton))?.focus({preventScroll: true});
  }
}

function extra(parent, metadata) {
  const spend = metadata.extra_usage_spend;
  const currency = metadata.currency;
  const limit = metadata.extra_usage_limit;
  let formatter;
  if (/^[A-Z]{3}$/.test(currency ?? "")) {
    formatter = new Intl.NumberFormat("ja-JP", {style: "currency", currency});
  }
  const unit = formatter ? 10 ** formatter.resolvedOptions().maximumFractionDigits : 1;
  const amount = value => !Number.isFinite(value) ? null : formatter?.format(value / unit) ?? `${value}（通貨の最小単位）`;
  values(parent, [
    ["追加課金", metadata.extra_usage_enabled == null ? "不明" : metadata.extra_usage_enabled ? "有効" : "無効"],
    ["使用額", amount(spend)], ["上限", amount(limit)],
    ["上限に対する使用率", Number.isFinite(metadata.extra_usage_utilization) ? `${metadata.extra_usage_utilization}%` : null],
  ]);
}

function render(payload, reportFailure = false) {
  const now = Date.now() / 1000;
  const url = {
    claude: "https://claude.ai/settings/usage?from=cc_cli_limit_message",
    codex: "https://chatgpt.com/settings/usage?tab=overview",
  };
  const body = document.createDocumentFragment();
  const items = [];
  const addIssue = (label, id, description, priority) => {
    const item = element("li");
    item.append(jumpTo(label, id), element("span", description));
    items.push({item, priority});
  };
  for (const engine of providers) {
    const name = providerNames[engine];
    const section = element("section", "", "metric-section");
    const metadata = payload.metadata[engine] ?? {};
    const provider = element("header", "", "metric-provider");
    const heading = element("h3", name, "section-heading");
    heading.id = `usage-${engine}`;
    heading.tabIndex = -1;
    provider.append(heading, link("公式の利用状況を開く", url[engine]));
    section.append(provider);
    const rows = payload.windows.filter(window => window.engine === engine);
    const failed = payload.refresh_failures?.some(message => message.startsWith(name + ":"));
    const refreshUnavailable = reportFailure && (failed || !payload.refresh_failures?.length);
    const settled = (refreshed || payload.last_refresh_at) && !failed && !refreshUnavailable;
    if (failed && reportFailure) addIssue(name, heading.id, "最新の利用枠を取得できません。", 0);
    if (engine === "codex" && (metadata.ordinaryUsageAllowed === false || metadata.spendControlReached === true)) {
      const reasons = [
        metadata.ordinaryUsageAllowed === false ? "通常利用不可" : "",
        metadata.spendControlReached === true ? "支出上限に到達" : "",
      ];
      addIssue(name, `usage-${engine}-recovery`, reasons.filter(Boolean).join(" · "), 0);
    }
    values(section, [["プラン", metadata.contract || (settled ? "未取得" : null)]]);
    if (rows.length === 0 && settled) section.append(element("p", "利用枠の観測はありません。"));
    if (rows.length === 0 && refreshUnavailable) section.append(element("p", "利用枠は未取得です。", "secondary-text"));
    for (const window of rows) {
      const id = `usage-${engine}-quota-${encodeURIComponent(JSON.stringify([window.window_id, window.scope]))}`;
      const prediction = predictionFor(window, now);
      section.append(quota(window, now, id, prediction, refreshUnavailable));
      if (["reached", "exhausted", "expired", "missing"].includes(prediction.kind) || window.severity === "warning") {
        const description = prediction.kind === "reached" ? "上限の状態を確認" :
          prediction.kind === "exhausted" ? "上限到達の予測を確認" :
            prediction.kind === "expired" ? "要再取得" :
              window.severity === "warning" ? "提供元の警告あり" : "見込みに必要な値が未取得";
        const priority = prediction.kind === "reached" ? 0 : prediction.kind === "exhausted" ? 1 : 2;
        addIssue(`${name} · ${quotaName(window)}`, id, description, priority);
      }
    }
    const recovery = element("div", "", "metric-recovery");
    const recoveryHeading = element("h4", "リセット権", "section-heading");
    recoveryHeading.id = `usage-${engine}-recovery`;
    recoveryHeading.tabIndex = -1;
    recovery.append(recoveryHeading);
    if (engine === "claude") {
      recovery.append(element("p", "在庫は手入力の値です。リセット権は公式の利用状況画面で使います。"));
      if (!payload.claude_inventory.length) recovery.append(element("p", "登録した在庫はありません。"));
      for (const right of payload.claude_inventory) {
        const name = claudeResetScopeName(right.scope, payload.windows);
        recovery.append(element("p", `${name} · ${right.count}回 · ${dateJst(Date.parse(right.until) / 1000)}まで`));
      }
    } else if (settled || Object.keys(metadata).length) {
      const status = [
        ["在庫", metadata.reset_available_count == null ? "未取得" : `${metadata.reset_available_count}回`],
        ...codexUsageStatus(metadata),
      ];
      const available = status.filter(([, value]) => value !== "未取得");
      values(recovery, available);
      if (available.length < status.length) {
        const names = status.filter(([, value]) => value === "未取得").map(([name]) => name).join("・");
        recovery.append(element("p", `未取得: ${names}。期限は取得できません。`, "secondary-text"));
      } else recovery.append(element("p", "期限は取得できません。", "secondary-text"));
      for (const right of metadata.reset_credit_details ?? []) {
        const kind = namedStatus(right.resetType, {codexRateLimits: "Codexの利用枠"});
        const state = namedStatus(right.status, {used: "使用済み", available: "利用可能"});
        recovery.append(element("p", `${kind} · ${state}`));
      }
    } else if (refreshUnavailable) recovery.append(element("p", "在庫は未取得です。", "secondary-text"));
    const permissions = payload.permissions.filter(entry => entry.engine === engine);
    recovery.append(element("h4", "リセットの許可", "section-heading"));
    if (!permissions.length) recovery.append(element("p", "期間内または今後の許可はありません。"));
    for (const entry of permissions) {
      const scope = engine === "claude" ? claudeResetScopeName(entry.scope, payload.windows) : entry.scope;
      recovery.append(element("p", `${scope} · ${entry.count}回 · ${dateJst(Date.parse(entry.from) / 1000)}〜${dateJst(Date.parse(entry.until) / 1000)}`));
    }
    if (engine === "claude") {
      recovery.append(element("h4", "追加利用", "section-heading"));
      extra(recovery, metadata);
    }
    section.append(recovery);
    body.append(section);
  }
  const active = document.activeElement;
  const selected = active?.closest("[data-metric-detail]");
  const open = new Set([...view.querySelectorAll("[data-metric-detail][open]")].map(node => node.dataset.metricDetail));
  let focusTarget;
  for (const detail of body.querySelectorAll("[data-metric-detail]")) {
    if (open.has(detail.dataset.metricDetail)) detail.open = true;
    if (detail.dataset.metricDetail === selected?.dataset.metricDetail) focusTarget = detail.querySelector("summary");
  }
  if (!focusTarget && view.contains(active) && active.id) {
    focusTarget = [...body.querySelectorAll("[id]")].find(node => node.id === active.id);
  }
  if (!focusTarget && view.contains(active) && active.tagName === "A") {
    focusTarget = [...body.querySelectorAll("a")].find(link => link.href === active.href);
  }
  for (const child of [...view.children]) {
    if (child !== antigravity) child.remove();
  }
  antigravity.before(body);
  if (focusTarget) focusTarget.focus({preventScroll: true});
  describeAttention(items.sort((a, b) => a.priority - b.priority).map(({item}) => item), payload.windows, refreshed || payload.last_refresh_at);
}

async function request(path, body) {
  const response = await fetch(BASE_PATH + path, {signal: AbortSignal.timeout(90_000), ...(body ? {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  } : {})});
  const result = await response.json();
  if (!response.ok) throw new Error([result.error, result.next_action].filter(Boolean).join(" "));
  return result;
}

async function update(force) {
  if (pending) return;
  pending = true;

  refreshButton.disabled = true;
  const loading = document.getElementById("usage-loading");
  loading.hidden = Boolean(data);
  if (overview && !data) overview.hidden = true;
  let displayedFailure = false;
  try {
    for (const delay of [0, 1500]) {
      if (delay) await new Promise(resolve => setTimeout(resolve, delay));
      try {
        if (!data) {
          data = await request("/api/usage");
          render(data);
          loading.hidden = data.windows.length > 0 || Boolean(data.last_refresh_at);
          if (!force && data.refresh_due === false) {
            refreshButton.hidden = false;
            recordUpdateSuccess("usage", "refresh");
            return;
          }
        }
        data = await request("/api/usage/refresh", {force: force && !delay});
        refreshed = true;
        const failed = Boolean(data.refresh_failures?.length);
        displayedFailure = Boolean(delay) && failed;
        render(data, displayedFailure);
        if (!failed) {
          refreshButton.hidden = false;
          refreshButton.disabled = false;
          recordUpdateSuccess("usage", "refresh");
          return;
        }
      } catch {
        // pyfltr: allow-comment 前回の値は次の取得が成功するまで表示する。
      }
    }
    if (data) {
      if (!displayedFailure) render(data, true);
    } else if (overview) {
      overview.textContent = "利用枠は未取得です。";
      overview.hidden = false;
    }
    refreshButton.hidden = true;
    setDisplayedReadError("usage", "refresh", "利用枠を取得できません。", () => update(false), () => updateProviders(true));
  } finally {
    pending = false;
    refreshButton.disabled = false;
    loading.hidden = true;
  }
}

async function updateAntigravity(force) {
  if (antigravityPending) return;
  antigravityPending = true;
  if (!antigravity.hidden) {
    antigravityStatus.textContent =
      `Antigravityを再取得中です。この間、再取得はClaudeとCodexだけに適用されます。前回の観測: ${dateJst(antigravityObservedAt)}`;
  }
  try {
    const result = await request("/api/usage/antigravity", {force});
    const groups = new Map();
    for (const window of result.windows) {
      if (!groups.has(window.group)) groups.set(window.group, []);
      groups.get(window.group).push(window);
    }
    const content = document.createDocumentFragment();
    for (const [name, windows] of groups) {
      const group = element("div");
      group.setAttribute("role", "group");
      group.setAttribute("aria-label", name);
      group.append(element("p", name, "secondary-text"));
      for (const window of windows) {
        const row = element("article", "", "list-row metric-row");
        row.append(element("h4", window.name));
        const showRemaining = Number.isFinite(window.remaining_fraction) &&
          (!Number.isFinite(window.resets_at) || window.resets_at > Date.now() / 1000);
        const amount = showRemaining ? percent.format(window.remaining_fraction) : "不明";
        const observed = element("p");
        observed.append(
          element("span", "観測残量 ", "secondary-text"),
          element("span", amount, showRemaining ? "metric-value" : "metric-unavailable"),
        );
        row.append(observed);
        values(row, [["リセット時刻", dateJst(window.resets_at)]]);
        group.append(row);
      }
      content.append(group);
    }
    antigravityObservedAt = result.observed_at;
    antigravityWindows.replaceChildren(content);
    antigravity.hidden = !groups.size;
    antigravityStatus.textContent = result.stale ? staleAntigravity() : `取得時刻: ${dateJst(antigravityObservedAt)}`;
  } catch {
    if (!antigravity.hidden) antigravityStatus.textContent = staleAntigravity();
  } finally {
    antigravityPending = false;
  }
}

function staleAntigravity() {
  return `前回の観測: ${dateJst(antigravityObservedAt)}。最新の利用枠を取得できません。`;
}

function updateProviders(force) {
  void update(force);
  void updateAntigravity(force);
}

function visibility() {
  clearInterval(timer);
  if (document.hidden) return;
  updateProviders(false);
  timer = setInterval(() => updateProviders(false), 300000);
}

registerScreen("usage", {init: () => {
  refreshButton.addEventListener("click", () => updateProviders(true));
  document.addEventListener("visibilitychange", visibility);
  visibility();
}});

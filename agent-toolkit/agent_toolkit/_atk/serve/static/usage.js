import {setUpdateError, BASE_PATH} from "./common.js";
import {registerScreen} from "./shell.js";
import {DAY, claudeResetScopeName, dateJst, forecast, quotaName} from "./usage_display.js";

const view = document.getElementById("usage-content");
const refreshButton = document.getElementById("usage-refresh");
const visible = () => document.body.dataset.screen === "usage" && !document.hidden;
let pending = false;
let timer;
let data;

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
  parent.append(list);
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

function forecastText(result, reset) {
  switch (result.kind) {
    case "reached": return "上限に到達";
    case "expired": return "リセット済みの可能性。再取得すると更新されます。";
    case "exhausted": return `このペースでは${dateJst(result.at)}に上限に達する見込み（リセットは${dateJst(reset)}）。`;
    case "sufficient": return `リセットまでもつ見込み（リセット時点で約${result.percent}%）。`;
    case "zero": return "使用率が0%のため見込みは出せません。";
    case "early": return "観測時点の経過が短いか期間外のため見込みは出せません。";
    default: return "使用率・期間・リセット日時のいずれかが不明のため見込みは出せません。";
  }
}

function quota(window, now) {
  const row = element("article", "", "list-row metric-row");
  const title = quotaName(window);
  const elapsed = (now - window.resets_at + window.duration_seconds) / window.duration_seconds;
  const expired = Number.isFinite(window.resets_at) && window.resets_at <= now;
  const prediction = expired ? {kind: "expired"} : forecast(window, now);
  row.append(element("h3", title));
  const bar = element("div", "", "metric-track");
  const fraction = window.used_percent;
  const label = prediction.kind === "expired" ? "不明（前回の観測はリセット前）" :
    Number.isFinite(fraction) ? `使用 ${fraction}% · 残り ${Math.round((100 - fraction) * 100) / 100}%` : "使用率・残りとも不明";
  row.append(element("p", label, "metric-value"));
  if (Number.isFinite(fraction) && prediction.kind !== "expired") {
    const progress = element("progress");
    progress.max = 100;
    progress.value = fraction;
    progress.setAttribute("aria-label", `${title}の使用率`);
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
  if (Number.isFinite(window.resets_at) && Number.isFinite(elapsed) && elapsed >= 0 && elapsed < 1) {
    const marker = element("span", "現在", "metric-marker");
    marker.style.insetInlineStart = `${100 * elapsed}%`;
    marker.title = `期間の${Math.round(100 * elapsed)}%が経過`;
    bar.append(marker);
  }
  row.append(bar);
  const observed = window.usage_observed_at ?? window.observed_at;
  const recovery = dateJst(window.resets_at);
  values(row, [
    ["次回リセット", window.resets_at > now ? `${recovery}（${remaining(window.resets_at - now)}）` : recovery],
    ["観測", dateJst(window.observed_at) + (now - window.observed_at >= 3600 ? "（1時間以上前）" : "")],
    ["使用率の観測", observed !== window.observed_at ? dateJst(observed) : null],
    ["枠の識別子", window.window_id],
    ...(window.engine === "claude" ? claudeWindowStatus(window) : []),
  ]);
  const message = element("p", forecastText(prediction, window.resets_at));
  if (["reached", "exhausted"].includes(prediction.kind)) {
    message.className = "metric-warning";
    message.setAttribute("role", "status");
  }
  row.append(message);
  return row;
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

function render(payload) {
  const now = Date.now() / 1000;
  const url = {
    claude: "https://claude.ai/settings/usage?from=cc_cli_limit_message",
    codex: "https://chatgpt.com/settings/usage?tab=overview",
  };
  const names = {claude: "Claude", codex: "Codex"};
  const body = document.createDocumentFragment();
  for (const engine of ["claude", "codex"]) {
    const section = element("section", "", "metric-section");
    const metadata = payload.metadata[engine] ?? {};
    section.append(element("h2", names[engine], "page-heading"), link("公式の利用状況を開く", url[engine]));
    values(section, [["プラン", metadata.contract || "未取得"]]);
    const rows = payload.windows.filter(window => window.engine === engine);
    if (rows.length === 0) section.append(element("p", "利用枠の観測はありません。"));
    else for (const window of rows) section.append(quota(window, now));
    section.append(element("h3", "リセット権"));
    if (engine === "claude") {
      extra(section, metadata);
      section.append(element("p", "在庫は手入力の値です。リセット権は公式の利用状況画面で使います。"));
      if (!payload.claude_inventory.length) section.append(element("p", "登録した在庫はありません。"));
      for (const right of payload.claude_inventory) {
        const name = claudeResetScopeName(right.scope, payload.windows);
        section.append(element("p", `${name} · ${right.count}回 · ${dateJst(Date.parse(right.until) / 1000)}まで`));
      }
    } else {
      values(section, [
        ["在庫", metadata.reset_available_count == null ? "未取得" : `${metadata.reset_available_count}回`],
        ["期限", "取得できない"],
        ...codexUsageStatus(metadata),
      ]);
      for (const right of metadata.reset_credit_details ?? []) {
        const kind = namedStatus(right.resetType, {codexRateLimits: "Codexの利用枠"});
        const state = namedStatus(right.status, {used: "使用済み", available: "利用可能"});
        section.append(element("p", `${kind} · ${state}`));
      }
    }
    const permissions = payload.permissions.filter(entry => entry.engine === engine);
    section.append(element("h3", "リセットの許可"));
    if (!permissions.length) section.append(element("p", "期間内または今後の許可はありません。"));
    for (const entry of permissions) {
      section.append(element("p", `${entry.scope} · ${entry.count}回 · ${dateJst(Date.parse(entry.from) / 1000)}〜${dateJst(Date.parse(entry.until) / 1000)}`));
    }
    const failure = (payload.refresh_failures ?? []).find(item => item.startsWith(`${names[engine]}:`));
    if (failure) {
      const observed = rows.length ? Math.max(...rows.map(window => window.observed_at)) : null;
      section.append(element("p", `取得失敗: ${failure}。最後の観測: ${dateJst(observed)}`, "metric-warning"));
    }
    body.append(section);
  }
  view.replaceChildren(body);
}

async function request(path, body) {
  const response = await fetch(BASE_PATH + path, body ? {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  } : undefined);
  const result = await response.json();
  if (!response.ok) throw new Error([result.error, result.next_action].filter(Boolean).join(" "));
  return result;
}

async function update(force) {
  if (pending || !visible()) return;
  pending = true;

  refreshButton.disabled = true;
  try {
    if (!data) {
      data = await request("/api/usage");
      render(data);
    }
    if (!visible()) return;
    data = await request("/api/usage/refresh", {force});
    render(data);
    setUpdateError("usage", "refresh", data.refresh_failures?.length ? "利用枠の取得に失敗した項目があります。" : "", () => update(true));
  } catch (error) {
    setUpdateError("usage", "refresh", `利用枠の取得に失敗しました。 ${error.message}`, () => update(true));
  } finally {
    pending = false;
    refreshButton.disabled = false;
  }
}

function visibility() {
  clearInterval(timer);
  if (!visible()) return;
  void update(false);
  timer = setInterval(() => void update(false), 300000);
}

registerScreen("usage", {init: () => {
  refreshButton.addEventListener("click", () => void update(true));
  document.addEventListener("visibilitychange", visibility);
  document.addEventListener("atk-screen-change", visibility);
  visibility();
}});

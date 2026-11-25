export const DAY = 86400;
const jst = new Intl.DateTimeFormat("ja-JP", {
  timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit",
  weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

export function dateJst(seconds) {
  return Number.isFinite(seconds) ? `${jst.format(new Date(seconds * 1000))} JST` : "不明";
}

function nonEmptyText(value) {
  return typeof value === "string" ? value.trim() : "";
}

function claudeScopedTarget(scope) {
  if (scope === "model:opus") return "Opus";
  if (scope === "model:sonnet") return "Sonnet";
  try {
    const target = JSON.parse(scope);
    const model = nonEmptyText(target?.model?.display_name) || nonEmptyText(target?.model?.id);
    const surface = nonEmptyText(target?.surface) || nonEmptyText(target?.surface?.display_name);
    if (surface) return `${model || "モデル不明"}（${surface}）`;
    if (model) return model;
  } catch {
    return "対象不明";
  }
  return "対象不明";
}

export function quotaName(window) {
  const {window_id: kind, scope, duration_seconds: seconds, limit_name: name} = window;
  if (window.engine === "claude" && kind === "seven_day_overage_included") return "週の利用枠（追加利用分を含む）";
  const period = seconds === 5 * 3600 ? "5時間" : seconds === 7 * DAY ? "週" : "";
  let target = kind;
  if (window.engine === "codex") target = name || kind;
  else if (kind === "session" || kind === "weekly_all") target = "全体";
  else if (kind === "weekly_scoped") target = claudeScopedTarget(scope);
  return [period, target || kind].filter(Boolean).join(" · ");
}

export function claudeResetScopeName(scope, windows) {
  if (scope === "all") return "全体";
  const observed = windows.find(window => window.engine === "claude" && window.aliases?.includes(scope));
  if (observed) return quotaName(observed);
  if (scope === "weekly_all") return "週 · 全体";
  if (scope.trimStart().startsWith("{") || scope === "model:opus" || scope === "model:sonnet") {
    return quotaName({engine: "claude", window_id: "weekly_scoped", scope, duration_seconds: 7 * DAY});
  }
  return scope;
}

export function forecast(window, now = Date.now() / 1000) {
  const reset = window?.resets_at;
  const percent = window?.used_percent;
  if (window?.rejected || percent >= 100) return {kind: "reached", at: now};
  if (Number.isFinite(reset) && reset <= now) return {kind: "expired"};
  const duration = window?.duration_seconds;
  if (!Number.isFinite(percent) || !Number.isFinite(reset) || !(duration > 0)) return {kind: "missing"};
  if (percent === 0) return {kind: "zero"};
  const start = reset - duration;
  const sample = window.usage_observed_at ?? window.observed_at;
  const elapsed = sample - start;
  if (!Number.isFinite(elapsed) || elapsed < duration * 0.01 || elapsed > duration) return {kind: "early"};
  const at = start + elapsed * 100 / percent;
  return at < reset ? {kind: "exhausted", at} : {kind: "sufficient", percent: Math.round(percent * duration / elapsed)};
}

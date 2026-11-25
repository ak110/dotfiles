export const DAY = 86400;
const jst = new Intl.DateTimeFormat("ja-JP", {
  timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit",
  weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

export function dateJst(seconds) {
  return Number.isFinite(seconds) ? `${jst.format(new Date(seconds * 1000))} JST` : "不明";
}

export function quotaName(window) {
  const {window_id: kind, scope, duration_seconds: seconds, limit_name: name} = window;
  const period = seconds === 5 * 3600 ? "5時間" : seconds === 7 * DAY ? "週" : "";
  const target = window.engine === "codex" ? name || kind : {
    session: "全体", weekly_all: "全体", weekly_scoped: scope,
  }[kind];
  return [period, target || kind].filter(Boolean).join(" · ");
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

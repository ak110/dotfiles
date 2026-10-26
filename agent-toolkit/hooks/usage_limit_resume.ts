import type { EngineInterface, On, SessionRateLimit, Timer } from "claude-code";
import { automatedMessage } from "./automated_message.ts";
import { exitStatePath } from "./session_exit.ts";

// Python側のclaude_usage_limit.RECHECK_SECONDSと同じ再確認間隔を使う。
const RECHECK_MS = 300_000;
export const USAGE_LIMIT_RESUME_PROMPT = automatedMessage(
  "usage-limit-resume",
  "notice",
  "利用上限で中断した作業を解除後に再開する発火である。" +
    "agent-toolkit:user-confirmation-and-reportのreferences/usage-limit.mdに従って再開情報を読み、最初の未完了工程から続けよ。" +
    "未完了の作業が無い場合、既に続行済みの場合、ユーザーが中止を指示している場合は、何もせず終えよ。",
);

function maximumWindow(windows: SessionRateLimit[]): SessionRateLimit | undefined {
  return windows.filter((window) => window.kind === "five_hour" || window.kind === "seven_day")
    .reduce<SessionRateLimit | undefined>((maximum, window) =>
      maximum === undefined || window.percentUsed > maximum.percentUsed ? window : maximum, undefined);
}

// 委譲先の判定はregister.tsのsession.startが初期化する。環境変数の所有者を同じ初期化処理で設定する。
export function register(on: On, session: { delegated: boolean }): void {
  const state: Reservation = { latestTurnAt: -Infinity, generation: 0 };

  on("turn.start", async ($, e, next) => {
    state.latestTurnAt = await $.clock.now();
    return next(e);
  });

  // session_exit.tsのmatcherなしの登録と区別する。全turnのIDを受け取り、agentIdは本文で除外する。
  on("turn.complete", { turnId: /^/ }, async ($, e, next) => {
    const result = await next(e);
    if (session.delegated || e.agentId !== undefined) return result;
    const configured = await $.env.get("CLAUDE_CONFIG_DIR");
    const home = (await $.env.get("HOME")) || (await $.env.get("USERPROFILE"));
    const path = exitStatePath(await $.session.id(), configured, home, "rate-limit");
    let requested = false;
    if (path !== undefined) {
      try {
        requested = (await $.fs.read(path)) === "requested";
        if (requested) await $.fs.write(path, "consumed");
      } catch { /* 初回の未作成ファイルは予約要求を持たない。 */ }
    }
    const window = maximumWindow((await $.session.usage()).rateLimits);
    if (!requested && (window === undefined || window.percentUsed < 100)) return result;
    await schedule($, state);
    return result;
  });

}

type Reservation = { timer?: Timer; latestTurnAt: number; generation: number };

async function schedule($: EngineInterface, state: Reservation): Promise<void> {
  state.timer?.cancel();
  const mine = ++state.generation;
  const window = maximumWindow((await $.session.usage()).rateLimits);
  const now = await $.clock.now();
  const resetsAt = window?.resetsAt === undefined ? NaN : Date.parse(window.resetsAt);
  const knownReset = Number.isFinite(resetsAt) && resetsAt > now;
  const due = knownReset ? resetsAt + 1 : now + RECHECK_MS;
  $.ui.status(`利用上限の解除後に再開予定: ${new Date(due).toISOString()}`);
  state.timer = $.clock.after(due - now, async () => {
    if (mine !== state.generation) return;
    state.timer = undefined;
    const current = knownReset ? undefined : maximumWindow((await $.session.usage()).rateLimits);
    if (mine !== state.generation) return;
    if (state.latestTurnAt >= due) {
      $.ui.status(undefined);
      return;
    }
    if (current !== undefined && current.percentUsed >= 100) {
      await schedule($, state);
      return;
    }
    $.ui.status(undefined);
    await $.prompt.submit({ text: USAGE_LIMIT_RESUME_PROMPT });
  });
}

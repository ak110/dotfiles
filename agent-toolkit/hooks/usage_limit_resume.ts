import type { EngineInterface, On, SessionRateLimit, Timer } from "claude-code";
import { automatedMessage, startNextTurn } from "./automated_message.ts";
import { pluginLaunchCommand, pluginLaunchRoot } from "./plugin_launch.ts";
import { exitStatePath } from "./session_exit.ts";

// Python側のclaude_usage_limit.RECHECK_SECONDSと同じ再確認間隔を使う。
const RECHECK_MS = 300_000;
const SERVER_RETRY_MS = [60_000, 300_000, 900_000];
const REQUEST_POLL_MS = 250;
const REQUEST_POLL_COUNT = 8;

export function usageLimitResumePrompt(root: string): string {
  return resumePrompt(root, "usage-limit-resume", "利用上限");
}

function resumePrompt(root: string, source: string, cause: string): string {
  return automatedMessage(
    source,
    "notice",
    `${cause}による中断後、このターンを自動的に開始した。` +
    `${JSON.stringify(`${root}/skills/atk-mode/references/usage-limit.md`)}を読み、` +
      "同書に従って再開情報を取得し、最初の未完了工程から続けよ。" +
      "未完了の作業が無い場合、既に続行済みの場合、ユーザーが中止を指示している場合は、何もせず終えよ。",
  );
}

function maximumWindow(windows: SessionRateLimit[]): SessionRateLimit | undefined {
  return windows.filter((window) => window.kind === "five_hour" || window.kind === "seven_day")
    .reduce<SessionRateLimit | undefined>((maximum, window) =>
      maximum === undefined || window.percentUsed > maximum.percentUsed ? window : maximum, undefined);
}

async function readClaimedProcessLoopOwner($: EngineInterface): Promise<string | undefined> {
  if (!(await $.env.get("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_CLAIM"))) return undefined;
  try {
    const result = await $.process.run(
      pluginLaunchCommand($.plugin.root, "agent_toolkit/_common/process_loop_session.py"),
      { timeoutMs: 5000 },
    );
    const owner = result.stdout.trim();
    if (result.exitCode === 0 && !result.isStdoutTruncated && /^[A-Za-z0-9_-]+$/.test(owner)) return owner;
  } catch {
    return undefined;
  }
  return undefined;
}

// 委譲先の判定はregister.tsのsession.startが初期化する。環境変数の所有者を同じ初期化処理で設定する。
export function register(on: On, session: { delegated: boolean }): void {
  const state: Reservation = { turnsStarted: 0, generation: 0, serverRetries: 0, stopped: false };

  let claimedOwner: string | undefined;
  let mainTurn: { id: string; promptId: Promise<string | undefined> } | undefined;

  on("turn.start", async ($, e, next) => {
    if (e.agentId === undefined) {
      state.turnsStarted++;
      mainTurn = { id: e.turnId, promptId: $.session.info().then((info) => info.promptId).catch(() => undefined) };
      cancel($, state);
    }
    return next(e);
  });

  // session_exit.tsのmatcherなしの登録と区別する。全turnのIDを受け取り、agentIdは本文で除外する。
  on("turn.complete", { turnId: /^/ }, async ($, e, next) => {
    const turn = state.turnsStarted;
    const identity = mainTurn;
    const result = await next(e);
    if (session.delegated || e.agentId !== undefined || (identity !== undefined && identity.id !== e.turnId)) return result;
    // process-loopとrun-skillが旧プロセスの終了後に接続を選ぶため、ホスト内の予約を重ねない。
    if (await $.env.get("AGENT_TOOLKIT_RUN_SKILL_ID")) return result;
    const sessionId = await $.session.id();
    if (await $.env.get("AGENT_TOOLKIT_PROCESS_LOOP_SESSION") === "1") {
      const explicitOwner = await $.env.get("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID");
      const owner = explicitOwner || (claimedOwner ??= await readClaimedProcessLoopOwner($));
      if (owner === undefined || owner === sessionId) return result;
    }
    const configured = await $.env.get("CLAUDE_CONFIG_DIR");
    const home = (await $.env.get("HOME")) || (await $.env.get("USERPROFILE"));
    const promptId = await identity?.promptId;
    const ratePath = promptId === undefined ? undefined : exitStatePath(sessionId, configured, home, "rate-limit", promptId);
    const serverPath = promptId === undefined ? undefined : exitStatePath(sessionId, configured, home, "server-error", promptId);

    const check = async (attempt: number): Promise<void> => {
      if (turn !== state.turnsStarted) return;
      const rateRequested = await consume($, ratePath);
      const serverRequested = await consume($, serverPath);
      const window = maximumWindow((await $.session.usage()).rateLimits);
      if (turn !== state.turnsStarted) return;

      if (e.isAborted) {
        state.serverRetries = 0;
        cancel($, state);
      } else if (rateRequested || (window !== undefined && window.percentUsed >= 100)) {
        if (!serverRequested) state.serverRetries = 0;
        await schedule($, state, { rateLimit: window });
      } else if (serverRequested) {
        await schedule($, state, "server-error");
      } else if (e.reason === "error" && serverPath !== undefined && attempt < REQUEST_POLL_COUNT) {
        // StopFailureのコマンドhookがturn.completeより後に書く場合がある。 pyfltr: allow-comment
        state.timer = $.clock.after(REQUEST_POLL_MS * 2 ** attempt, async () => {
          if (turn !== state.turnsStarted) return;
          state.timer = undefined;
          await check(attempt + 1);
        });
      } else {
        state.serverRetries = 0;
        if (state.stopped && e.reason === "answer") {
          state.stopped = false;
          $.ui.status(undefined);
        }
      }
    };
    await check(0);
    return result;
  });

}

async function consume($: EngineInterface, path: string | undefined): Promise<boolean> {
  if (path === undefined) return false;
  try {
    if (await $.fs.read(path) !== "requested") return false;
    await $.fs.write(path, "consumed");
    return true;
  } catch {
    return false;
  }
}

type Reservation = { timer?: Timer; turnsStarted: number; generation: number; serverRetries: number; stopped: boolean };

function cancel($: EngineInterface, state: Reservation): void {
  state.generation++;
  if (state.timer === undefined) return;
  state.timer.cancel();
  state.timer = undefined;
  $.ui.status(undefined);
}

async function schedule(
  $: EngineInterface,
  state: Reservation,
  cause: "server-error" | { rateLimit: SessionRateLimit | undefined },
): Promise<void> {
  cancel($, state);
  const mine = state.generation;
  const now = await $.clock.now();
  if (mine !== state.generation) return;

  const serverError = cause === "server-error";
  const resetsAt = serverError ? NaN : Date.parse(cause.rateLimit?.resetsAt ?? "");
  const knownReset = Number.isFinite(resetsAt) && resetsAt > now;
  const delay = serverError ? SERVER_RETRY_MS[state.serverRetries] : knownReset ? resetsAt + 1 - now : RECHECK_MS;
  if (delay === undefined) {
    state.stopped = true;
    $.ui.status("サーバー側の一時的なAPIエラーによる自動再開を停止: 連続再開の上限に到達。続行するには手動で入力してください。");
    return;
  }

  state.stopped = false;
  const label = serverError ? "サーバー側の一時的なAPIエラーの後に再開予定" : "利用上限の解除後に再開予定";
  $.ui.status(`${label}: ${new Date(now + delay).toISOString()}`);
  state.timer = $.clock.after(delay, async () => {
    if (mine !== state.generation) return;
    const current = knownReset ? undefined : maximumWindow((await $.session.usage()).rateLimits);
    if (mine !== state.generation) return;
    state.timer = undefined;
    if (current !== undefined && current.percentUsed >= 100) {
      await schedule($, state, { rateLimit: current });
      return;
    }
    $.ui.status(undefined);
    const root = pluginLaunchRoot($.plugin.root);
    if (serverError) {
      state.serverRetries++;
      await startNextTurn($, resumePrompt(root, "server-error-resume", "サーバー側の一時的なAPIエラー"));
    } else {
      await startNextTurn($, usageLimitResumePrompt(root));
    }
  });
}

import { pluginLaunchCommand } from "./plugin_launch.ts";
import type { EngineInterface, On } from "claude-code";
import { automatedMessage, changedWarning, startNextTurn } from "./automated_message.ts";
import { isMarked, mountPeriodicRecheck, parseCronJobs, parseSchedule } from "./periodic_recheck.ts";

const SESSION_ID = /^[A-Za-z0-9_-]+$/;

function isAbsolute(path: string): boolean {
  return path.startsWith("/") || /^[A-Za-z]:[\\/]/.test(path);
}

// 終了要求の状態ファイルのパスを、セッションIDと設定ディレクトリの環境変数の値から求める。
// `$`を受け取らない形で`register.ts`の`session.start`・`ui_requests.tsx`と共有する。
export function exitStatePath(
  sessionId: string,
  configured: string | undefined,
  home: string | undefined,
  kind: "marker" | "request" | "recheck" | "rate-limit" | "server-error" | "compaction" | "ui-request" | "ui-response" | "restart",
  promptId?: string,
): string | undefined {
  if (!SESSION_ID.test(sessionId) || (promptId !== undefined && !SESSION_ID.test(promptId))) return undefined;
  let root: string | undefined;
  if (configured && isAbsolute(configured)) root = configured;
  else if (home && isAbsolute(home)) root = `${home}/.claude`;
  if (!root) return undefined;
  const shard = [...sessionId].reduce((sum, character) => sum + character.charCodeAt(0), 0) % 64;
  const directory = kind === "ui-request" || kind === "ui-response" ? `ui-requests/${shard.toString(16).padStart(2, "0")}/` : "";
  return `${root}/agent-toolkit-function-hooks/${directory}${kind}-${sessionId}${promptId === undefined ? "" : `.${promptId}`}.txt`;
}

async function statePath($: EngineInterface, sessionId: string, kind: "marker" | "request" | "recheck"): Promise<string | undefined> {
  const configured = await $.env.get("CLAUDE_CONFIG_DIR");
  const home = (await $.env.get("HOME")) || (await $.env.get("USERPROFILE"));
  return exitStatePath(sessionId, configured, home, kind);
}

function cleanupWarning(reason: string): string {
  return automatedMessage("periodic-recheck", "warn", `待機状況の定期確認taskを自動削除できなかった（理由: ${reason}）。` +
    "taskは保持した。`CronList`・`CronDelete`の許可と公開状態を確認せよ。" +
    "次にターンを終えるとき、待機対象が全て終了していれば自動削除を再判定する。");
}

// Stopが同じターンの非同期判定を書き、ここで一度だけ消費する。欠落・不明は回収の許可にしない。
// 通常はStopが判定と同時に`collectCommonTasks`を要求して回収を済ませており、ここでは残ったtaskの再評価と失敗の通知を担う。
async function collectPeriodicRecheck($: EngineInterface, sessionId: string, aborted: boolean): Promise<string | null | undefined> {
  const path = await statePath($, sessionId, "recheck");
  if (!path) return undefined;
  try {
    const verdict = await $.fs.read(path);
    await $.fs.write(path, "consumed");
    if (aborted) return undefined;
    if (verdict === "pending-background") return (await mountPeriodicRecheck($)) || undefined;
    if (verdict !== "complete") return undefined;
  } catch (error) {
    return isMissing(error) ? undefined : cleanupWarning(String(error));
  }
  return collectCommonTasks($);
}

// 共通promptと一致する非durableの定期再確認taskを削除し、削除できなかった場合の警告を返す。対象が無ければ`null`を返す。
// Stopの要求（`ui_requests.tsx`の`periodic_recheck_collect`）と`turn.complete`の両方から呼ぶ。
export async function collectCommonTasks($: EngineInterface): Promise<string | null> {
  try {
    // 軽量探索などCronListを持たない会話は、この回収機能の適用対象外である。
    // 非公開ツールをtool.checkへ渡すと例外になり、正常な返却後に不要な通知ターンが発生する。
    if (!(await $.tool.list()).some((tool) => tool.name === "CronList")) return null;
    const listDecision = (await $.tool.check({ tool: "CronList", input: {} })).decision;
    if (listDecision !== "allow") return cleanupWarning(`CronList: ${listDecision}`);
    const listed = await $.tool.call({ tool: "CronList" });
    if (listed.deny !== undefined) return cleanupWarning(`CronList: ${listed.deny}`);
    if (listed.isError === true) return cleanupWarning(`CronList: ${listed.text ?? "一覧取得エラー"}`);
    const marked = parseCronJobs(listed.result).filter((job) => job.durable !== true && isMarked(job.prompt));
    if (marked.length === 0) return null;
    const root = $.plugin.root;
    const schedule = await $.process.run(
      pluginLaunchCommand(root, "agent_toolkit/atk.py", ["wait-schedule"]),
      { timeoutMs: 60_000 },
    );
    const common = schedule.exitCode === 0 ? parseSchedule(schedule.stdout) : undefined;
    if (!common) return cleanupWarning(`共通promptの取得失敗: ${schedule.stderr.trim() || schedule.exitCode}`);
    const failures: string[] = [];
    for (const job of marked) {
      if (job.prompt !== common.prompt) continue;
      try {
        const decision = (await $.tool.check({ tool: "CronDelete", input: { id: job.id } })).decision;
        if (decision !== "allow") {
          failures.push(`${job.id}: ${decision}`);
          continue;
        }
        const deleted = await $.tool.call({ tool: "CronDelete", id: job.id });
        if (deleted.deny !== undefined || deleted.isError === true) failures.push(`${job.id}: ${deleted.deny ?? deleted.text ?? "削除エラー"}`);
      } catch (error) {
        failures.push(`${job.id}: CronDelete: ${String(error)}`);
      }
    }
    return failures.length ? cleanupWarning(failures.join("、")) : null;
  } catch (error) {
    return cleanupWarning(String(error));
  }
}

function isMissing(error: unknown): boolean {
  if (error && typeof error === "object" && "code" in error && error.code === "ENOENT") return true;
  return String(error).includes("ENOENT");
}

// `/exit`は終了とともに停止するセッション限りの作業が残ると確認画面を表示し、無人のセッションはそこで止まる。
// セッション限りのcron taskは終了で消えるため、確認画面で「Exit and stop tasks」を選ぶのと同じ結果になるよう
// `/exit`の直前に削除する。durableなtaskは終了後も残す指定のため削除しない。
// 許可が`allow`でない環境で`$.tool.call`を呼ぶと権限確認ダイアログで同じく止まるため、先に`$.tool.check`で判定する。
// 一覧の取得と削除に失敗しても`/exit`は実行する（結果は削除しない場合の確認画面と同じ）。
// 一覧の拒否（`deny`）とエラー（`isError`）は`result`に一覧を持たないため、成功した一覧だけを走査する。
async function deleteSessionCrons($: EngineInterface): Promise<void> {
  try {
    if ((await $.tool.check({ tool: "CronList", input: {} })).decision !== "allow") return;
    const listed = await $.tool.call({ tool: "CronList" });
    if (listed.deny !== undefined || listed.isError === true) return;
    for (const job of parseCronJobs(listed.result)) {
      if (job.durable === true) continue;
      if ((await $.tool.check({ tool: "CronDelete", input: { id: job.id } })).decision !== "allow") continue;
      await $.tool.call({ tool: "CronDelete", id: job.id });
    }
  } catch {
    // 削除を諦めて終了要求の処理を続ける。
  }
}

async function completionPath($: EngineInterface): Promise<string | undefined> {
  const launch = await $.env.get("AGENT_TOOLKIT_COMPLETION_LAUNCH_ID");
  if (!launch || !/^[0-9a-f]{32}$/.test(launch)) return undefined;
  const base = await statePath($, launch, "request");
  return base?.replace(/request-([^/]+)$/, "completion-exit-$1");
}

async function cancelCompletion($: EngineInterface, path: string | undefined): Promise<void> {
  if (path && await $.fs.exists(path)) await $.fs.write(path, "{}");
}

async function hasOtherCrons($: EngineInterface): Promise<boolean> {
  if ((await $.tool.check({ tool: "CronList", input: {} })).decision !== "allow") return true;
  const listed = await $.tool.call({ tool: "CronList" });
  if (listed.deny !== undefined || listed.isError === true) return true;
  const jobs = parseCronJobs(listed.result).filter((job) => job.durable !== true);
  if (!jobs.length) return false;
  const schedule = await $.process.run(
    pluginLaunchCommand($.plugin.root, "agent_toolkit/atk.py", ["wait-schedule"]),
    { timeoutMs: 60_000 },
  );
  const common = schedule.exitCode === 0 && !schedule.isStdoutTruncated ? parseSchedule(schedule.stdout) : undefined;
  return !common || jobs.some((job) => job.prompt !== common.prompt);
}

async function completeAndExit(
  $: EngineInterface, sessionId: string, aborted: boolean, unchanged: () => boolean,
): Promise<void> {
  const path = await completionPath($);
  if (!path) return;
  try {
    const raw = await $.fs.read(path);
    const state = JSON.parse(raw);
    if (state?.session !== sessionId || !["requested", "approved"].includes(state.status)) return;
    const surfaces = await $.session.surfaces();
    const terminalOnly = surfaces.includes("terminal") && surfaces.every((surface) => surface === "terminal");
    if (aborted || state.status !== "approved" || !terminalOnly || await hasOtherCrons($) ||
        Object.keys((await $.state.get({ plugin: "agent-toolkit", key: "backgroundWaits" })).value ?? {}).length > 0 ||
        await collectCommonTasks($) !== null || await $.fs.read(path) !== raw || !unchanged()) {
      await cancelCompletion($, path);
      return;
    }
    if (!unchanged()) {
      await cancelCompletion($, path);
      return;
    }
    // `/exit`は終了の完了前に戻るため、戻った後も`exiting`を残し、終了後のランチャーが報告を表示できるようにする。pyfltr: allow-comment
    // 終了しなかった場合は次の入力かターン開始で取り消す。pyfltr: allow-comment
    await $.fs.write(path, JSON.stringify({ ...state, status: "exiting" }));
    await $.command.run({ command: "exit" });
  } catch {
    await cancelCompletion($, path);
  }
}

export function register(on: On): void {
  let inputSequence = 0;
  on("prompt.submit", { text: /^/ }, async ($, e, next) => {
    inputSequence++;
    await cancelCompletion($, await completionPath($));
    return next(e);
  });
  on("turn.start", { turnId: /^/ }, async ($, e, next) => {
    if (e.agentId === undefined) {
      inputSequence++;
      await cancelCompletion($, await completionPath($));
    }
    return next(e);
  });
  const deliveredFailures = new Map<string, string>();
  on("turn.complete", async ($, e, next) => {
    const sequence = inputSequence;
    const result = await next(e);
    if (e.agentId !== undefined) return result;
    const sessionId = await $.session.id();
    const request = await statePath($, sessionId, "request");
    if (!request) return result;
    let content: string;
    try {
      content = await $.fs.read(request);
    } catch (error) {
      if (isMissing(error)) content = "consumed";
      else throw error;
    }
    if (content !== "requested") {
      await completeAndExit($, sessionId, e.isAborted, () => inputSequence === sequence);
      const outcome = await collectPeriodicRecheck($, sessionId, e.isAborted);
      // 未点検のturnは解消の観測に数えず、実際の成功・対象消滅で通知の既往を解く。
      const note = outcome === undefined ? undefined : changedWarning(deliveredFailures, sessionId, outcome ?? undefined);
      if (note) {
        // 同じ失敗による通知ターンの反復を避け、処置できる本文を会話へ一度届ける。
        $.clock.after(0, async () => { await startNextTurn($, note); });
      }
      return result;
    }
    await $.fs.write(request, "consumed");
    await deleteSessionCrons($);
    await $.command.run({ command: "exit" });
    return result;
  });
}

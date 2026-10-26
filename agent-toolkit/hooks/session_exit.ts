import { pluginLaunchCommand } from "./plugin_launch.ts";
import type { EngineInterface, On } from "claude-code";
import { automatedMessage, changedWarning } from "./automated_message.ts";
import { isMarked, parseCronJobs, parseSchedule } from "./periodic_recheck.ts";

const SESSION_ID = /^[A-Za-z0-9_-]+$/;

function isAbsolute(path: string): boolean {
  return path.startsWith("/") || /^[A-Za-z]:[\\/]/.test(path);
}

// 終了要求の状態ファイルのパスを、セッションIDと設定ディレクトリの環境変数の値から求める。
// hooks moduleは`$`を別ファイルの関数へ渡せないため、`$`を受け取らない形で`register.ts`の`session.start`と共有する。
export function exitStatePath(
  sessionId: string,
  configured: string | undefined,
  home: string | undefined,
  kind: "marker" | "request" | "recheck" | "rate-limit",
): string | undefined {
  if (!SESSION_ID.test(sessionId)) return undefined;
  let root: string | undefined;
  if (configured && isAbsolute(configured)) root = configured;
  else if (home && isAbsolute(home)) root = `${home}/.claude`;
  if (!root) return undefined;
  return `${root}/agent-toolkit-function-hooks/${kind}-${sessionId}.txt`;
}

async function statePath($: EngineInterface, sessionId: string, kind: "marker" | "request" | "recheck"): Promise<string | undefined> {
  const configured = await $.env.get("CLAUDE_CONFIG_DIR");
  const home = (await $.env.get("HOME")) || (await $.env.get("USERPROFILE"));
  return exitStatePath(sessionId, configured, home, kind);
}

function cleanupWarning(reason: string): string {
  return automatedMessage("periodic-recheck", "warn", `定期再確認taskの自動回収を完了できなかった（理由: ${reason}）。` +
    "taskは保持する。必要な許可とツールの公開状態を確認し、次のStopで全待機終了の判定が得られたときにmodが再評価する。");
}

// Stopが同じターンの非同期判定を書き、ここで一度だけ消費する。欠落・不明は回収の許可にしない。
async function collectPeriodicRecheck($: EngineInterface, sessionId: string, aborted: boolean): Promise<string | null | undefined> {
  const path = await statePath($, sessionId, "recheck");
  if (!path) return undefined;
  try {
    const verdict = await $.fs.read(path);
    await $.fs.write(path, "consumed");
    if (verdict !== "complete" || aborted) return undefined;
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
    return isMissing(error) ? undefined : cleanupWarning(String(error));
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

export function register(on: On): void {
  const deliveredFailures = new Map<string, string>();
  on("turn.complete", async ($, e, next) => {
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
      const outcome = await collectPeriodicRecheck($, sessionId, e.isAborted);
      // 未点検のturnは解消の観測に数えず、実際の成功・対象消滅で通知の既往を解く。
      const note = outcome === undefined ? undefined : changedWarning(deliveredFailures, sessionId, outcome ?? undefined);
      if (note) {
        // 同じ失敗による通知ターンの反復を避け、処置できる本文を会話へ一度届ける。
        $.clock.after(0, async () => { await $.prompt.submit({ text: note }); });
      }
      return result;
    }
    await $.fs.write(request, "consumed");
    await deleteSessionCrons($);
    await $.command.run({ command: "exit" });
    return result;
  });
}

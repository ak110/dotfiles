import { pluginLaunchCommand } from "./plugin_launch.ts";
import type { EngineInterface, On } from "claude-code";
import { automatedMessage, changedWarning } from "./automated_message.ts";

// `agents_server`の`start`の処理の中で、メインの定期再確認のtaskを`CronCreate`で装着し、結果をメインの会話へ届ける。
// モデルが起動前に`atk wait-schedule`と`CronCreate`を呼ぶ手順は装着漏れを残し、起動までの呼び出しも増やすため、
// 作成前にmod自身の回収能力も確認する。装着できない場合は理由と完了通知による待機を案内する。
// cron式とpromptの共通本文は`atk wait-schedule`の出力から得る。本文の定義元はPython側の1か所であり、
// モデルが自ら装着する手順も同じ出力を使う。
// `Agent`ツールのサブエージェント（`agentId`を持つ呼出主体）へは装着も通知もしない。`CronList`はメインと共有され、
// サブエージェントが作成したtaskのpromptは親の会話へ届くため、サブエージェントの待機を再確認できない。
// サブエージェントは完了通知で待機を解く。
// 設計と不採用とした代替は`docs/development/design-hooks.md`「`start`の処理の中での定期再確認の装着」にある。

// Claude Codeがプラグインのstdio MCPサーバーのツールへ付ける名前（`mcp__plugin_<プラグイン名>_<サーバー名>__<ツール名>`）。
// matcherの無い`tool.call`のhookは`send_to_user.tsx`が登録済みで同じイベントへ2件目を登録できないため、ツール名をmatcherに指定する。
// `hooks.json`のPreToolUseの`matcher`も同じ綴りを使う。
const START_TOOL = "mcp__plugin_agent-toolkit_agents_server__start";
const RUNTIME_REFERENCE = "`agent-toolkit:delegation`の`references/claude-code-runtime.md`「待機中の定期再確認と背景転換」";
// 定期再確認のpromptの1行目に置く標識。`agent-toolkit/agent_toolkit/_hooks/user_prompt_submit.py`の`PERIODIC_RECHECK_MARKER`と
// 同じリテラルとし、一致は`agent-toolkit/skills/delegation/references/runtime_contract_invariant_test.py`が確かめる。
export const PERIODIC_RECHECK_MARKER = '<atk-auto source="periodic-recheck" kind="periodic-recheck">';
const CRON_EXPRESSION = /^\S+( \S+){4}$/;
// `atk wait-schedule`は`claude auth status`を最大5秒待つ。`uv run`の環境同期を含めても収まる上限を取る。
const WAIT_SCHEDULE_TIMEOUT_MS = 60_000;

// 装着の結果は保持するtask IDを伝えるだけで対処を要さないため`notice`、
// 装着できなかった事実は許可とツールの公開状態の確認を要するため`warn`とする。
function notice(kind: "notice" | "warn", body: string): string {
  return automatedMessage("periodic-recheck", kind, body);
}

// 装着の結果。`failed`は同じ会話へ同じ失敗の通知を重ねない判定に使う。
type MountOutcome = { note: string; failed: boolean };

function notMounted(reason: string): MountOutcome {
  return {
    note: notice(
      "warn",
      `agent-toolkitのmodは定期再確認を装着できなかった（理由: ${reason}）。` +
        `taskの自動回収に必要な許可と公開状態を確認する。待機は${RUNTIME_REFERENCE}の完了通知と終端観測で続ける。`,
    ),
    failed: true,
  };
}

export function isMarked(prompt: string): boolean {
  return (prompt.split("\n", 1)[0] ?? "").trim() === PERIODIC_RECHECK_MARKER;
}

// メインが既に定期再確認のtaskを持つかを判定する。
// 装着はメインに限るため、標識付きのtaskは全てメインのものとみなす。
// モデルが規範の手順で作成したtaskとmodの再読込前に作成したtaskも数え、重複作成を避ける。
function holdsTask(markedIds: string[]): boolean {
  return markedIds.length > 0;
}

type Schedule = { cron: string; prompt: string };

type CronJob = { id: string; prompt: string; durable?: boolean };

// ホストのツール宣言がunknownを返す場合も、回収判断に使う項目を全件確認する。
export function parseCronJobs(result: unknown): CronJob[] {
  if (typeof result !== "object" || result === null || !("jobs" in result) || !Array.isArray(result.jobs)) {
    throw new Error("CronListがtask一覧を返さなかった");
  }
  return result.jobs.map((job: unknown) => {
    if (typeof job !== "object" || job === null || !("id" in job) || typeof job.id !== "string" ||
        !("prompt" in job) || typeof job.prompt !== "string") {
      throw new Error("CronListのtaskに有効なid・prompt・durableがない");
    }
    const durable = "durable" in job ? job.durable : undefined;
    if (durable !== undefined && typeof durable !== "boolean") {
      throw new Error("CronListのtaskに有効なid・prompt・durableがない");
    }
    return { id: job.id, prompt: job.prompt, durable };
  });
}

// `atk wait-schedule`の標準出力から、cron式と標識で始まるpromptを取り出す。形が合わなければ`undefined`を返す。
export function parseSchedule(stdout: string): Schedule | undefined {
  let parsed: unknown;
  try {
    parsed = JSON.parse(stdout);
  } catch {
    return undefined;
  }
  if (typeof parsed !== "object" || parsed === null) return undefined;
  const { cron, prompt } = parsed as { cron?: unknown; prompt?: unknown };
  if (typeof cron !== "string" || typeof prompt !== "string") return undefined;
  if (!CRON_EXPRESSION.test(cron.trim()) || !isMarked(prompt)) return undefined;
  return { cron: cron.trim(), prompt };
}

// 装着の結果をメインへ届ける本文を返す。既にtaskを持つ場合は`undefined`を返し、何も届けない。
async function mount($: EngineInterface): Promise<MountOutcome | undefined> {
  const listDecision = (await $.tool.check({ tool: "CronList", input: {} })).decision;
  if (listDecision !== "allow") {
    return notMounted(`\`CronList\`の許可の判定が\`${listDecision}\``);
  }
  const listed = await $.tool.call({ tool: "CronList" });
  if (listed.deny !== undefined) return notMounted(`\`CronList\`が拒否された: ${listed.deny}`);
  if (listed.isError === true) return notMounted(`\`CronList\`が失敗した: ${listed.text ?? "本文なし"}`);
  const markedIds = parseCronJobs(listed.result).filter((job) => isMarked(job.prompt)).map((job) => job.id);
  if (holdsTask(markedIds)) return undefined;

  const root = $.plugin.root;
  const schedule = await $.process.run(
    pluginLaunchCommand(root, "agent_toolkit/atk.py", ["wait-schedule"]),
    { timeoutMs: WAIT_SCHEDULE_TIMEOUT_MS },
  );
  const parsed = schedule.exitCode === 0 ? parseSchedule(schedule.stdout) : undefined;
  if (parsed === undefined) {
    return notMounted(
      `\`atk wait-schedule\`がcron式と標識付きのpromptを返さなかった（終了コード${schedule.exitCode}、` +
        `標準エラー: ${schedule.stderr.trim() || "なし"}）`,
    );
  }
  const { cron } = parsed;

  // 作成前に回収能力も確認する。既存IDが無い段階では検査専用IDを使い、削除は呼ばない。
  try {
    const deleteDecision = (await $.tool.check({ tool: "CronDelete", input: { id: "periodic-recheck-capability-check" } })).decision;
    if (deleteDecision !== "allow") return notMounted(`\`CronDelete\`の許可の判定が\`${deleteDecision}\``);
  } catch (error) {
    return notMounted(`\`CronDelete\`の能力確認が失敗した: ${String(error)}`);
  }
  const input = { cron, prompt: parsed.prompt, recurring: true };
  const createDecision = (await $.tool.check({ tool: "CronCreate", input })).decision;
  if (createDecision !== "allow") {
    return notMounted(`\`CronCreate\`の許可の判定が\`${createDecision}\``);
  }
  const created = await $.tool.call({ tool: "CronCreate", ...input });
  if (created.deny !== undefined) return notMounted(`\`CronCreate\`が拒否された: ${created.deny}`);
  if (created.isError === true) return notMounted(`\`CronCreate\`が失敗した: ${created.text ?? "本文なし"}`);
  const createdTask: unknown = created.result;
  if (typeof createdTask !== "object" || createdTask === null || !("id" in createdTask) || typeof createdTask.id !== "string") {
    return notMounted("`CronCreate`がtask IDを返さなかった");
  }
  return {
    note: notice(
      "notice",
      `agent-toolkitのmodが定期再確認のtaskを作成した（task ID: ${createdTask.id}、cron: ${cron}、` +
        `request bucket: main）。全待機対象の終端と結果回収後にmodが共通promptのtaskを自動回収する。` +
        `追加義務付きのtaskはセッション終了時に回収する。再利用とresume・compaction後の確認は${RUNTIME_REFERENCE}に従う。`,
    ),
    failed: false,
  };
}

export function register(on: On): void {
  // 会話ごとに、最後に届けた装着失敗の本文を持つ。装着は`start`ごとに評価し直し、同じ失敗が続く間は通知だけを省く。
  // 既往はmodの読込ごとに初期化し、再読込後の最初の失敗は届ける。
  const deliveredFailures = new Map<string, string>();
  // `start`の結果は変えず、装着の結果を同じ呼び出しの後にモデルが読む文脈として加える。
  // 起動が拒否または失敗した呼び出しでは待機対象が生じないため装着しない。
  on("tool.call", { tool: START_TOOL }, async ($, e, next) => {
    const result = await next(e);
    if (result.deny !== undefined || result.isError === true) return result;
    // サブエージェントへは装着も通知もせず、メインの失敗の既往も変えない。
    if (e.agentId !== undefined) return result;
    let outcome: MountOutcome | undefined;
    try {
      outcome = await mount($);
    } catch (error) {
      // `$.tool.call`は呼び出し先のツールが公開されていない場合にrejectする。
      outcome = notMounted(`装着の処理が例外で終わった: ${String(error)}`);
    }
    let sessionId: string | undefined;
    try {
      sessionId = await $.session.id();
    } catch {
      // 会話を識別できない場合は抑止せずに届ける。
      sessionId = undefined;
    }
    if (sessionId !== undefined) {
      const warning = changedWarning(deliveredFailures, sessionId, outcome?.failed === true ? outcome.note : undefined);
      if (outcome?.failed === true && warning === undefined) return result;
    }
    if (outcome === undefined) return result;
    return { ...result, context: [...(result.context ?? []), outcome.note] };
  }).catch(($, e, next) => next(e));
}

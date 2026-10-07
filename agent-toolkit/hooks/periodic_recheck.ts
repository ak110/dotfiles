import type { EngineInterface, On } from "claude-code";

// `agents_server`の`start`の処理の中で、メインの定期再確認のtaskを`CronCreate`で装着し、結果をメインの会話へ届ける。
// モデルが起動前に`atk wait-schedule`と`CronCreate`を呼ぶ手順は装着漏れを残し、起動までの呼び出しも増やすため、
// 装着をモデルの遵守に依存させない。装着できない場合は、モデルが規範の手順で装着するよう案内する。
// cron式とpromptの共通本文は`atk wait-schedule --format json`の出力から得る。本文の定義元はPython側の1か所であり、
// モデルが自ら装着する手順も同じ出力を使う。
// `Agent`ツールのサブエージェント（`agentId`を持つ呼出主体）へは装着も通知もしない。`CronList`はメインと共有され、
// サブエージェントが作成したtaskのpromptは親の会話へ届くため、サブエージェントの待機を再確認できない。
// サブエージェントは完了通知で待機を解く。
// 設計と不採用とした代替は`docs/development/design-hooks.md`「`start`の処理の中での定期再確認の装着（2026年10月7日）」にある。

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
// 装着できなかった事実はモデルが自ら装着して原因を除けるため`warn`とする。
function notice(kind: "notice" | "warn", body: string): string {
  return `<atk-auto source="periodic-recheck" kind="${kind}">\n${body}\n</atk-auto>`;
}

function notMounted(reason: string): string {
  return notice(
    "warn",
    `agent-toolkitのmodは定期再確認を装着できなかった（理由: ${reason}）。` +
      `待機でターンを終える場合は、${RUNTIME_REFERENCE}に従って自ら装着する。`,
  );
}

function isMarked(prompt: string): boolean {
  return (prompt.split("\n", 1)[0] ?? "").trim() === PERIODIC_RECHECK_MARKER;
}

// メインが既に定期再確認のtaskを持つかを判定する。
// 装着はメインに限るため、標識付きのtaskは全てメインのものとみなす。
// モデルが規範の手順で作成したtaskとmodの再読込前に作成したtaskも数え、重複作成を避ける。
function holdsTask(markedIds: string[]): boolean {
  return markedIds.length > 0;
}

type Schedule = { cron: string; prompt: string };

// `atk wait-schedule --format json`の標準出力から、cron式と標識で始まるpromptを取り出す。形が合わなければ`undefined`を返す。
function parseSchedule(stdout: string): Schedule | undefined {
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

// 装着の結果をメインへ届ける本文を返す。サブエージェントの呼び出しと、既にtaskを持つ場合は`undefined`を返し、何も届けない。
async function mount($: EngineInterface, agentId: string | undefined): Promise<string | undefined> {
  if (agentId !== undefined) return undefined;
  if ((await $.tool.check({ tool: "CronList", input: {} })).decision === "deny") {
    return notMounted("`CronList`の許可の判定が`deny`");
  }
  const listed = await $.tool.call({ tool: "CronList" });
  if (listed.deny !== undefined) return notMounted(`\`CronList\`が拒否された: ${listed.deny}`);
  if (listed.isError === true) return notMounted(`\`CronList\`が失敗した: ${listed.text ?? "本文なし"}`);
  const markedIds = listed.result.jobs.filter((job) => isMarked(job.prompt)).map((job) => job.id);
  if (holdsTask(markedIds)) return undefined;

  const root = $.plugin.root;
  const schedule = await $.process.run(
    [
      "uv",
      "run",
      "--project",
      root,
      "--locked",
      "--no-default-groups",
      `${root}/agent_toolkit/atk.py`,
      "wait-schedule",
      "--request-bucket",
      "main",
      "--format",
      "json",
    ],
    { timeoutMs: WAIT_SCHEDULE_TIMEOUT_MS },
  );
  const parsed = schedule.exitCode === 0 ? parseSchedule(schedule.stdout) : undefined;
  if (parsed === undefined) {
    return notMounted(
      `\`atk wait-schedule --request-bucket main --format json\`がcron式と標識付きのpromptを返さなかった（終了コード${schedule.exitCode}、` +
        `標準エラー: ${schedule.stderr.trim() || "なし"}）`,
    );
  }
  const { cron } = parsed;

  const input = { cron, prompt: parsed.prompt, recurring: true };
  if ((await $.tool.check({ tool: "CronCreate", input })).decision === "deny") {
    return notMounted("`CronCreate`の許可の判定が`deny`");
  }
  const created = await $.tool.call({ tool: "CronCreate", ...input });
  if (created.deny !== undefined) return notMounted(`\`CronCreate\`が拒否された: ${created.deny}`);
  if (created.isError === true) return notMounted(`\`CronCreate\`が失敗した: ${created.text ?? "本文なし"}`);
  return notice(
    "notice",
    `agent-toolkitのmodが定期再確認のtaskを作成した（task ID: ${created.result.id}、cron: ${cron}、` +
      `request bucket: main）。このtask IDを保持し、再利用、resumeとcompaction後の確認、` +
      `待機する全対象の終端後の\`CronDelete\`は${RUNTIME_REFERENCE}に従う。`,
  );
}

export function register(on: On): void {
  // `start`の結果は変えず、装着の結果を同じ呼び出しの後にモデルが読む文脈として加える。
  // 起動が拒否または失敗した呼び出しでは待機対象が生じないため装着しない。
  on("tool.call", { tool: START_TOOL }, async ($, e, next) => {
    const result = await next(e);
    if (result.deny !== undefined || result.isError === true) return result;
    let note: string | undefined;
    try {
      note = await mount($, e.agentId);
    } catch (error) {
      // `$.tool.call`は呼び出し先のツールが公開されていない場合にrejectする。
      note = notMounted(`装着の処理が例外で終わった: ${String(error)}`);
    }
    if (note === undefined) return result;
    return { ...result, context: [...(result.context ?? []), note] };
  }).catch(($, e, next) => next(e));
}

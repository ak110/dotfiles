import { pluginLaunchCommand } from "./plugin_launch.ts";
import type { EngineInterface, On } from "claude-code";
import { automatedMessage } from "./automated_message.ts";
import { exitStatePath } from "./session_exit.ts";

const TOOL_NAME = "compact_conversation";

// 実ホストの版・入力・結果と再検証はdocs/development/audit-records.mdの
// 「agent-toolkit/hooks/compact_conversation.ts：会話圧縮の予約：2026年10月9日」に記録する。
// 待機時の自動経路とsession.compactの契約は同書の
// 「agent-toolkit/hooks/compact_conversation.ts：会話圧縮の予約：2026年10月23日」にある。

export const COMPACT_CONVERSATION_TOOL = {
  name: TOOL_NAME,
  description:
    "Claude Codeメインの会話圧縮を引数なしで予約する。受付は完了ではない。予約後は現在のターンを終え、ホストの圧縮結果を待つ。未完了の予約は重ねず、完了または失敗後は再予約できる。",
  inputSchema: {
    type: "object",
    additionalProperties: false,
  },
  isDeferred: false,
};

// 委譲先からの呼び出しへ`register.ts`が返す拒否の本文。委譲先への登録と呼び出しの除外は`register.ts`が行う。
export const COMPACT_CONVERSATION_DELEGATED_DENY = "メイン専用のツールであり、圧縮は予約していない。委譲元へ必要な作業状態を最終応答で返せ。";

type BackgroundWait = { id: string; command: string; used: boolean };
type Decision = { at: string; task: string; reason: string; tokens?: number; window?: number; pending: boolean; turnRunning: boolean };
type CompactState = { pending: boolean; turnRunning: boolean; scheduled: boolean; waits: Map<string, BackgroundWait>; decisions: Decision[] };

// 判定の記録は直近だけを保持する。調査で見送り理由を後から確かめる用途に足り、会話中に増え続けない。
const DECISION_LIMIT = 20;

// 待機時圧縮の判定理由を会話別の状態ファイルへ残す。debugログはprocess-loopの`--debug=hooks`の分類に
// modの診断が含まれず、見送り理由を後から判別できなかったため（docs/development/design-hooks.md）。
// 記録の失敗は圧縮の判定と通常の結果回収を変えない。
async function diagnose($: EngineInterface, state: CompactState, task: string, reason: string, context?: { tokens?: number; window: number }): Promise<void> {
  $.ui.log(`wait_compaction task=${task} reason=${reason}`, { to: "debug" });
  state.decisions.push({
    at: new Date().toISOString(),
    task,
    reason,
    ...(context ? { tokens: context.tokens, window: context.window } : {}),
    pending: state.pending,
    turnRunning: state.turnRunning,
  });
  state.decisions.splice(0, Math.max(0, state.decisions.length - DECISION_LIMIT));
  try {
    const home = (await $.env.get("HOME")) || (await $.env.get("USERPROFILE"));
    const path = exitStatePath(await $.session.id(), await $.env.get("CLAUDE_CONFIG_DIR"), home, "compaction");
    if (path) await $.fs.write(path, JSON.stringify({ decisions: state.decisions }));
  } catch {
    // 状態ファイルを書けない環境でもui.logの診断は残り、圧縮の判定は続ける。
  }
}

function usageThreshold(context: { tokens?: number; window: number }): boolean | undefined {
  if (typeof context.tokens !== "number" || !Number.isFinite(context.tokens) || context.tokens < 0 || !Number.isFinite(context.window) || context.window <= 0) return undefined;
  return context.tokens >= Math.max(400_000, context.window * 0.4);
}

async function automatic($: EngineInterface, state: CompactState, wait: BackgroundWait): Promise<void> {
  // 評価済みの待機は前回の判定理由が記録済みで、turn完了ごとの再評価で記録を埋めない。
  if (wait.used) return;
  if (!state.waits.has(wait.id)) { await diagnose($, state, wait.id, "wait_finished"); return; }
  if (state.pending) { await diagnose($, state, wait.id, "pending"); return; }
  try {
    const contextUsage = (await $.session.usage()).context;
    const high = usageThreshold(contextUsage);
    if (high === undefined) { await diagnose($, state, wait.id, "usage_unknown", contextUsage); return; }
    if (!high) { await diagnose($, state, wait.id, "below_threshold", contextUsage); return; }
    wait.used = true;
    const root = $.plugin.root;
    const checked = await $.process.run(pluginLaunchCommand(root, "agent_toolkit/atk.py", [
      "run-script", "wait-compaction-context", "--", "--session-id", await $.session.id(),
      `--command=${wait.command}`,
    ]), { timeoutMs: 30_000 });
    if (checked.exitCode !== 0 || checked.isStdoutTruncated) { await diagnose($, state, wait.id, "ownership_check_failed", contextUsage); return; }
    if (state.pending || !state.waits.has(wait.id)) { await diagnose($, state, wait.id, "wait_changed", contextUsage); return; }
    const context: unknown = JSON.parse(checked.stdout);
    if (typeof context !== "object" || context === null || (context as { eligible?: unknown }).eligible !== true) { await diagnose($, state, wait.id, "ownership_unconfirmed", contextUsage); return; }
    state.pending = true;
    await diagnose($, state, wait.id, "scheduled", contextUsage);
    schedule($, state);
  } catch (error) {
    // 同じ背景待機を自動再試行せず、通常の結果回収を続ける。
    wait.used = true;
    $.ui.toast(automatedMessage("compact-conversation", "warn", `待機時の会話圧縮を見送った。原因: ${String(error)}。保持した待機識別子で結果回収を続けよ。`));
  }
}

function schedule($: EngineInterface, state: CompactState): void {
    if (!state.pending || state.turnRunning || state.scheduled) return;
    state.scheduled = true;
    $.clock.after(0, async () => {
      state.scheduled = false;
      if (state.turnRunning || !state.pending) return;
      try {
        for (const wait of state.waits.values()) wait.used = true;
        // headlessでは直接APIが拒否されるため、ホストが実行する/compactを使う。
        // 実ホストの制約と両経路の観測は監査記録の
        // 「agent-toolkit/hooks/compact_conversation.ts：会話圧縮の予約：2026年11月4日」にある。
        if ((await $.session.surfaces()).length === 0) {
          await $.command.run({ command: "compact" });
        } else {
          const result = await $.session.compact();
          if (result.skip !== undefined) {
            $.ui.toast(automatedMessage("compact-conversation", "warn", `会話圧縮は見送られた。予約状態は解除済み。通常の作業を続け、圧縮が必要ならcompact_conversationを引数なしで再予約せよ。理由: ${result.skip}`));
          }
        }
        state.pending = false;
      } catch (error) {
        state.pending = false;
        $.ui.toast(automatedMessage("compact-conversation", "warn", `会話圧縮の予約実行に失敗した。予約状態は解除済み。通常の作業を続け、圧縮が必要ならcompact_conversationを引数なしで再予約せよ。原因: ${String(error)}`));
      }
    });
}

export function register(on: On, session: { delegated: boolean }): void {
  // modはセッションごとに読み込まれる。予約中だけ状態を保持する。
  const state: CompactState = { pending: false, turnRunning: false, scheduled: false, waits: new Map(), decisions: [] };
  on("session.compact", { trigger: /^(manual|auto|plugin|precompute)$/ }, async ($, e, next) => {
    if (e.agentId !== undefined) return next(e);
    const sessionId = await $.session.id();
    const resolved = await $.process.run(pluginLaunchCommand($.plugin.root, "skills/delegation/scripts/compaction_record.py", [
      "--session-id", sessionId,
    ]), { timeoutMs: 30_000 });
    if (resolved.exitCode !== 0 || resolved.isStdoutTruncated) return next(e);
    const record: unknown = JSON.parse(resolved.stdout);
    const path = typeof record === "object" && record !== null ? (record as { record_path?: unknown }).record_path : undefined;
    if (typeof path !== "string" || !path) return next(e);
    if (e.trigger === "precompute") return { skip: "原本ログを参照する圧縮では要約の先行生成を行わない。" };
    return {
      messages: [{
        role: "user" as const,
        text: automatedMessage("compact-conversation", "notice",
          // 受け取る側は圧縮が起きたことを知らない。出来事、要約が無いこと、最初の操作の順で伝える。
          // 原本の所在はこの直後に届くSessionStartの通知が示すため、ここでは重ねない。
          "この会話は圧縮され、ここより前のやり取りは現在の文脈に残っていない。圧縮の代わりになる要約は作っていない。" +
          "最初に、この直後に届く会話圧縮後の通知が示すファイルとコマンドで、ユーザー発話の原文と会話の流れを取得せよ。" +
          "取得した原文と、現行の作業契約、計画と進捗、引き継ぎ記録から、未完了の工程を復元して続けよ。"),
        toolUses: [],
      }],
    };
  }).catch(($, e, next) => next(e));
  on("turn.start", { turnId: /^/ }, async ($, e, next) => {
    if (e.agentId === undefined) {
      state.turnRunning = true;
    }
    return next(e);
  });
  on("turn.complete", { turnId: /^/ }, async ($, e, next) => {
    const result = await next(e);
    if (e.agentId === undefined) {
      state.turnRunning = false;
      schedule($, state);
      if (!session.delegated) {
        for (const wait of state.waits.values()) await automatic($, state, wait);
      }
    }
    return result;
  });
  on("tool.call", { tool: "Bash" }, async ($, e, next) => {
    const result = await next(e);
    const raw: unknown = result.result;
    const id = typeof raw === "object" && raw !== null ? (raw as { backgroundTaskId?: unknown }).backgroundTaskId : undefined;
    if (typeof id !== "string" || !id) {
      // 背景実行を要求したのに待機IDを得られない場合は、hook未実行と区別できるよう理由を残す。
      // 前景のBashは待機ではないため記録しない。
      if (e.run_in_background === true) await diagnose($, state, "unknown", id === undefined ? "background_id_missing" : "background_id_invalid");
      return result;
    }
    if (state.waits.has(id)) return result;
    // 背景待機を登録しない理由も残し、待機を検出しなかった場合と検出後の見送りを区別できるようにする。
    if (session.delegated) { await diagnose($, state, id, "delegated_session"); return result; }
    if (e.agentId !== undefined) { await diagnose($, state, id, "subagent"); return result; }
    if (typeof e.command !== "string") { await diagnose($, state, id, "command_unknown"); return result; }
    const wait: BackgroundWait = { id, command: e.command, used: false };
    state.waits.set(id, wait);
    $.clock.after(0, async () => {
      await automatic($, state, wait);
    });
    return result;
  });
  on("session.append", { door: /^(delivery|queue)$/ }, ($, e, next) => {
    if (e.agentId === undefined) {
      for (const block of e.message.content) {
        if (block.type !== "text" || typeof block.text !== "string") continue;
        for (const notice of block.text.matchAll(/<task-notification>[\s\S]*?<\/task-notification>/g)) {
          const id = notice[0].match(/<task-id>([^<]+)<\/task-id>/)?.[1];
          const status = notice[0].match(/<status>([^<]+)<\/status>/)?.[1];
          if (id && status && ["completed", "failed", "killed"].includes(status)) state.waits.delete(id);
        }
      }
    }
    return next(e);
  });
  on("tool.call", { tool: /__compact_conversation$/ }, ($, e, next) => {
    if (e.tool !== `mcp__${$.plugin.name}__${TOOL_NAME}`) return next(e);
    if (state.pending) return { result: "未完了の圧縮予約があるため追加していない。現在のターンを終えてホストの結果を待て。" };
    state.pending = true;
    schedule($, state);
    return { result: "会話圧縮を予約した。まだ完了していない。現在のターンを終えてホストの圧縮結果を待て。" };
  });
}

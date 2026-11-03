import { pluginLaunchCommand } from "./plugin_launch.ts";
import type { EngineInterface, On } from "claude-code";
import { automatedMessage } from "./automated_message.ts";

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
export const COMPACT_CONVERSATION_DELEGATED_DENY = "メインの会話で使うツール。";

type BackgroundWait = { id: string; command: string; ready: boolean; used: boolean };
type CompactState = { pending: boolean; turnRunning: boolean; scheduled: boolean; automaticPaused: boolean; waits: Map<string, BackgroundWait> };

function usageThreshold(context: { tokens?: number; window: number }): boolean | undefined {
  if (typeof context.tokens !== "number" || !Number.isFinite(context.tokens) || context.tokens < 0 || !Number.isFinite(context.window) || context.window <= 0) return undefined;
  return context.tokens >= Math.min(400_000, context.window * 0.4);
}

async function automatic($: EngineInterface, state: CompactState, wait: BackgroundWait): Promise<void> {
  if (wait.used || !wait.ready || !state.waits.has(wait.id)) return;
  const diagnose = (reason: string) => $.ui.log(`wait_compaction task=${wait.id} reason=${reason}`, { to: "debug" });
  if (state.turnRunning || state.pending) { diagnose(state.turnRunning ? "turn_running" : "pending"); return; }
  try {
    const contextUsage = (await $.session.usage()).context;
    const high = usageThreshold(contextUsage);
    if (high === undefined) { diagnose("usage_unknown"); return; }
    if (!high) { state.automaticPaused = false; diagnose("below_threshold"); return; }
    if (state.automaticPaused) { diagnose("paused"); return; }
    wait.used = true;
    const root = $.plugin.root;
    const checked = await $.process.run(pluginLaunchCommand(root, "agent_toolkit/atk.py", [
      "run-script", "wait-compaction-context", "--", "--session-id", await $.session.id(),
      `--command=${wait.command}`,
    ]), { timeoutMs: 30_000 });
    if (checked.exitCode !== 0 || checked.isStdoutTruncated) { diagnose("ownership_check_failed"); return; }
    if (state.turnRunning || state.pending || !state.waits.has(wait.id)) { diagnose("wait_changed"); return; }
    const context: unknown = JSON.parse(checked.stdout);
    if (typeof context !== "object" || context === null || (context as { eligible?: unknown }).eligible !== true) { diagnose("ownership_unconfirmed"); return; }
    state.pending = true;
    state.automaticPaused = true;
    diagnose("scheduled");
    schedule($, state);
  } catch (error) {
    // 同じ背景待機を自動再試行せず、通常の結果回収を続ける。
    wait.used = true;
    $.ui.toast(automatedMessage("compact-conversation", "warn", `待機時の会話圧縮を見送った。結果回収を続ける。原因: ${String(error)}`));
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
        const result = await $.session.compact();
        state.pending = false;
        state.automaticPaused = true;
        if (result.skip !== undefined) {
          $.ui.toast(automatedMessage("compact-conversation", "warn", `会話圧縮は見送られた。予約状態は解除済み。理由: ${result.skip}`));
        }
      } catch (error) {
        state.pending = false;
        $.ui.toast(automatedMessage("compact-conversation", "warn", `会話圧縮の予約実行に失敗した。予約状態は解除済みで再予約できる。原因: ${String(error)}`));
      }
    });
}

export function register(on: On, session: { delegated: boolean }): void {
  // modはセッションごとに読み込まれる。予約中だけ状態を保持する。
  const state: CompactState = { pending: false, turnRunning: false, scheduled: false, automaticPaused: false, waits: new Map() };
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
          `会話の要約生成を省き、保存済みの原本から作業を復元する。\n会話ID: ${sessionId}\n原本ログ: ${JSON.stringify(path)}\n` +
          "現行の作業契約、計画・進捗、引き継ぎ記録と、原本の当初の依頼・最新の訂正・認可・未完了の操作を確認し、同じ工程から続けよ。" +
          "原本は発話順・主体・参照位置を保ち、必要な本文を範囲指定で取得する。要約や固定長で省略した断片だけで要求と認可を確定しない。"),
        toolUses: [],
      }],
    };
  }).catch(($, e, next) => next(e));
  on("turn.start", { turnId: /^/ }, async ($, e, next) => {
    if (e.agentId === undefined) {
      state.turnRunning = true;
      if (state.automaticPaused) {
        try {
          if (usageThreshold((await $.session.usage()).context) === false) state.automaticPaused = false;
        } catch {
          // 利用率が取得できるまでは再圧縮の抑止を保つ。
        }
      }
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
    if (session.delegated || e.agentId !== undefined || typeof e.command !== "string") return result;
    const raw: unknown = result.result;
    const id = typeof raw === "object" && raw !== null ? (raw as { backgroundTaskId?: unknown }).backgroundTaskId : undefined;
    if (typeof id !== "string" || !id || state.waits.has(id)) return result;
    const wait: BackgroundWait = { id, command: e.command, ready: false, used: false };
    state.waits.set(id, wait);
    $.clock.after(60_000, async () => {
      wait.ready = true;
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

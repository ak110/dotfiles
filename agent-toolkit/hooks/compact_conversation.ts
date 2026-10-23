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
type CompactState = { pending: boolean; turnRunning: boolean; scheduled: boolean; turnStartedAt: number; automaticPaused: boolean; waits: Map<string, BackgroundWait> };

async function automatic($: EngineInterface, state: CompactState, wait: BackgroundWait): Promise<void> {
  if (wait.used || !wait.ready || state.turnRunning || state.pending || !state.waits.has(wait.id)) return;
  try {
    const percent = (await $.session.usage()).context.percent;
    if (typeof percent !== "number" || !Number.isFinite(percent)) return;
    if (percent < 50) { state.automaticPaused = false; return; }
    if (state.automaticPaused) return;
    wait.used = true;
    const root = $.plugin.root;
    const checked = await $.process.run([
      "uv", "run", "--project", root, "--locked", "--no-default-groups", `${root}/agent_toolkit/atk.py`,
      "run-script", "wait-compaction-context", "--", "--session-id", await $.session.id(),
      `--command=${wait.command}`, "--not-before", String(state.turnStartedAt / 1000),
    ], { timeoutMs: 30_000 });
    if (checked.exitCode !== 0 || checked.isStdoutTruncated || state.turnRunning || state.pending || !state.waits.has(wait.id)) return;
    const context: unknown = JSON.parse(checked.stdout);
    if (typeof context !== "object" || context === null || (context as { eligible?: unknown }).eligible !== true) return;
    state.pending = true;
    state.automaticPaused = true;
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
  const state: CompactState = { pending: false, turnRunning: false, scheduled: false, turnStartedAt: 0, automaticPaused: false, waits: new Map() };
  on("turn.start", { turnId: /^/ }, async ($, e, next) => {
    if (e.agentId === undefined) {
      state.turnRunning = true;
      state.turnStartedAt = await $.clock.now();
      if (state.automaticPaused) {
        try {
          const percent = (await $.session.usage()).context.percent;
          if (typeof percent === "number" && percent < 50) state.automaticPaused = false;
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
    if (session.delegated || e.agentId !== undefined || !e.command.includes("agents wait")) return result;
    const raw: unknown = result.result;
    const id = typeof raw === "object" && raw !== null ? (raw as { backgroundTaskId?: unknown }).backgroundTaskId : undefined;
    if (typeof id !== "string" || !id || state.waits.has(id)) return result;
    const wait: BackgroundWait = { id, command: e.command, ready: false, used: false };
    state.waits.set(id, wait);
    $.clock.after(5000, async () => {
      wait.ready = true;
      await automatic($, state, wait);
    });
    return result;
  });
  on("session.append", { door: /^(delivery|queue|attachment)$/ }, ($, e, next) => {
    if (e.agentId === undefined) {
      for (const block of e.message.content) {
        if (block.type !== "text") continue;
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
    if (state.pending) return { result: "未完了の圧縮予約があるため追加していない。現在のターンを終えてホストの結果を待つ。" };
    state.pending = true;
    schedule($, state);
    return { result: "会話圧縮を予約した。まだ完了していない。現在のターンを終えてホストの圧縮結果を待つ。" };
  });
}

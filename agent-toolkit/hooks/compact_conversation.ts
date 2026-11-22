import { pluginLaunchCommand } from "./plugin_launch.ts";
import type { EngineInterface, On } from "claude-code";
import { automatedMessage, startNextTurn } from "./automated_message.ts";
import { exitStatePath } from "./session_exit.ts";

// 実ホストの版・入力・結果と再検証はdocs/development/audit-records-compaction.mdの
// 「agent-toolkit/hooks/compact_conversation.ts：会話圧縮の予約：2026年10月9日」に記録する。
// 待機時の自動処理とsession.compactの契約は同資料の
// 「agent-toolkit/hooks/compact_conversation.ts：会話圧縮の予約：2026年10月23日」にある。
// 圧縮の要約を原本の所在へ置き換える方式の観測と再検証は同資料の
// 「agent-toolkit/hooks/compact_conversation.ts：原本参照圧縮：2026年11月2日」にある。

import type { BackgroundWait } from "./ui_requests.tsx";
type Decision = { at: string; task: string; reason: string; tokens?: number; window?: number; pending: boolean; turnRunning: boolean };
type CompactState = { pending: boolean; turnRunning: boolean; scheduled: boolean; delayed: Set<string>; decisions: Decision[] };

// 判定の記録は直近だけを保持する。調査で見送り理由を後から確かめる用途に足り、会話中に増え続けない。
const DECISION_LIMIT = 20;

// 待機時圧縮の判定理由を会話別の状態ファイルへ残す。debugログはprocess-loopの`--debug=hooks`の分類に
// modの診断が含まれず、見送り理由を後から判別できなかったため（docs/development/design-hooks-compaction-resume.md）。
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
  if (wait.replExpiresAt !== undefined && wait.replExpiresAt <= Date.now()) return;
  // 評価済みの待機は前回の判定理由が記録済みで、turn完了ごとの再評価で記録を埋めない。
  if (wait.used) return;
  if (!(await $.state.get({ plugin: "agent-toolkit", key: "backgroundWaits" })).value?.[wait.id]) { await diagnose($, state, wait.id, "wait_finished"); return; }
  const now = await $.clock.now();
  if (!Number.isFinite(wait.startedAt)) {
    // 再読込前の状態に開始時刻が無い場合、60秒経過の証拠にはせず、ここから計測する。
    wait.startedAt = now;
    const waits = (await $.state.get({ plugin: "agent-toolkit", key: "backgroundWaits" })).value ?? {};
    if (waits[wait.id]) await $.state.set({ plugin: "agent-toolkit", key: "backgroundWaits" }, { ...waits, [wait.id]: wait });
  }
  const remaining = 60_000 - (now - wait.startedAt);
  if (remaining > 0) {
    if (!state.delayed.has(wait.id)) {
      state.delayed.add(wait.id);
      $.clock.after(remaining, async () => {
        state.delayed.delete(wait.id);
        const waits = (await $.state.get({ plugin: "agent-toolkit", key: "backgroundWaits" })).value ?? {};
        const current = Object.hasOwn(waits, wait.id) ? waits[wait.id] : undefined;
        if (current) await automatic($, state, current);
      });
    }
    return;
  }
  if (state.pending) { await diagnose($, state, wait.id, "pending"); return; }
  try {
    const contextUsage = (await $.session.usage()).context;
    const high = usageThreshold(contextUsage);
    if (high === undefined) { await diagnose($, state, wait.id, "usage_unknown", contextUsage); return; }
    if (!high) { await diagnose($, state, wait.id, "below_threshold", contextUsage); return; }
    wait.used = true;
    const heldWaits = (await $.state.get({ plugin: "agent-toolkit", key: "backgroundWaits" })).value ?? {};
    if (heldWaits[wait.id]) await $.state.set({ plugin: "agent-toolkit", key: "backgroundWaits" }, { ...heldWaits, [wait.id]: wait });
    const root = $.plugin.root;
    const checked = await $.process.run(pluginLaunchCommand(root, "agent_toolkit/atk.py", [
      "run-script", "wait-compaction-context", "--", "--session-id", await $.session.id(),
      `--command=${wait.command}`,
    ]), { timeoutMs: 30_000 });
    if (checked.exitCode !== 0 || checked.isStdoutTruncated) { await diagnose($, state, wait.id, "ownership_check_failed", contextUsage); return; }
    if (state.pending || !(await $.state.get({ plugin: "agent-toolkit", key: "backgroundWaits" })).value?.[wait.id]) { await diagnose($, state, wait.id, "wait_changed", contextUsage); return; }
    const context: unknown = JSON.parse(checked.stdout);
    if (typeof context !== "object" || context === null || (context as { eligible?: unknown }).eligible !== true) { await diagnose($, state, wait.id, "ownership_unconfirmed", contextUsage); return; }
    state.pending = true;
    await diagnose($, state, wait.id, "scheduled", contextUsage);
    schedule($, state);
  } catch (error) {
    // 同じ背景待機を自動再試行せず、通常の結果回収を続ける。
    wait.used = true;
    const heldWaits = (await $.state.get({ plugin: "agent-toolkit", key: "backgroundWaits" })).value ?? {};
    if (heldWaits[wait.id]) await $.state.set({ plugin: "agent-toolkit", key: "backgroundWaits" }, { ...heldWaits, [wait.id]: wait });
    $.ui.toast(automatedMessage("compact-conversation", "warn", `待機時の会話圧縮を見送った。原因: ${String(error)}。保持した待機識別子で結果回収を続けよ。`));
  }
}

function schedule($: EngineInterface, state: CompactState): void {
    if (!state.pending || state.turnRunning || state.scheduled) return;
    state.scheduled = true;
    $.clock.after(0, async () => {
      state.scheduled = false;
      if (state.turnRunning || !state.pending) return;
      const resume = (await $.state.get({ plugin: "agent-toolkit", key: "compactRequested" })).value === "resume";
      await $.state.set({ plugin: "agent-toolkit", key: "compactRequested" }, false);
      let compacted = false;
      try {
        const interactive = (await $.session.surfaces()).length > 0;
        if (resume && !interactive) {
          state.pending = false;
          $.ui.toast(automatedMessage("compact-conversation", "warn", "対話画面が無いため圧縮と自動再開を取り消した。圧縮だけが必要ならcompact_conversationを引数なしで再予約せよ。"));
          return;
        }
        const waits = (await $.state.get({ plugin: "agent-toolkit", key: "backgroundWaits" })).value ?? {};
        await $.state.set({ plugin: "agent-toolkit", key: "backgroundWaits" }, Object.fromEntries(Object.entries(waits).map(([id, wait]) => [id, { ...wait, used: true }])));
        // headlessでは直接APIが拒否されるため、ホストが実行する/compactを使う。
        // 実ホストの制約と両経路の観測は監査記録の
        // 「agent-toolkit/hooks/compact_conversation.ts：会話圧縮の予約：2026年11月4日」にある。
        if (!interactive) {
          await $.command.run({ command: "compact" });
        } else {
          const result = await $.session.compact();
          compacted = result.skip === undefined;
          if (!compacted) {
            $.ui.toast(automatedMessage("compact-conversation", "warn", `会話圧縮は見送られた。予約状態は解除済み。通常の作業を続け、圧縮が必要ならcompact_conversationを引数なしで再予約せよ。理由: ${result.skip}`));
          }
        }
        state.pending = false;
      } catch (error) {
        state.pending = false;
        $.ui.toast(automatedMessage("compact-conversation", "warn", `会話圧縮の予約実行に失敗した。予約状態は解除済み。通常の作業を続け、圧縮が必要ならcompact_conversationを引数なしで再予約せよ。原因: ${String(error)}`));
      }
      if (resume && compacted) {
        try {
          await startNextTurn($, automatedMessage("compact-conversation", "notice", "会話の圧縮が完了した。作業契約と圧縮前の記録から残る工程を復元して作業を続けよ。"));
        } catch (error) {
          $.ui.toast(automatedMessage("compact-conversation", "warn", `会話の圧縮は完了したが、自動再開に失敗した。作業を続ける時は次の入力で再開を指示せよ。原因: ${String(error)}`));
        }
      }
    });
}

export function register(on: On, session: { delegated: boolean; backgroundChanged?: boolean }): void {
  // modはセッションごとに読み込まれる。予約中だけ状態を保持する。
  const state: CompactState = { pending: false, turnRunning: false, scheduled: false, delayed: new Set(), decisions: [] };
  on("session.start", { cwd: /^/ }, async ($, e, next) => {
    const result = await next(e);
    state.turnRunning = (await $.state.get({ plugin: "agent-toolkit", key: "turnRunning" })).value ?? false;
    if (!session.delegated) {
      const waits = (await $.state.get({ plugin: "agent-toolkit", key: "backgroundWaits" })).value ?? {};
      for (const wait of Object.values(waits)) await automatic($, state, wait);
    }
    return result;
  });
  on("session.compact", { trigger: /^(manual|auto|plugin|precompute)$/ }, async ($, e, next) => {
    if (e.agentId !== undefined) return next(e);
    const sessionId = await $.session.id();
    const resolved = await $.process.run(pluginLaunchCommand($.plugin.root, "skills/atk-mode/scripts/compaction_record.py", [
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
          // 読む対象と読み直す入口・手順書はこの直後に届くSessionStartの通知が示すため、ここでは重ねない。
          "会話が圧縮され、圧縮前のやり取りと要約は現在の文脈にない。" +
          "直後の復元通知が示す作業契約と会話の要約を読み、作業状態を復元せよ。" +
          "同じ通知が求める入口と手順書の読み直しも、作業を再開する前に行え。"),
        toolUses: [],
      }],
    };
  }).catch(($, e, next) => next(e));
  let observing = false;
  on("turn.start", { turnId: /^/ }, async ($, e, next) => {
    if (e.agentId === undefined) {
      state.turnRunning = true;
      await $.state.set({ plugin: "agent-toolkit", key: "turnRunning" }, true);
      if (!observing) {
        observing = true;
        $.clock.every(100, async () => {
          if (!session.backgroundChanged || session.delegated) return;
          session.backgroundChanged = false;
          const waits = (await $.state.get({ plugin: "agent-toolkit", key: "backgroundWaits" })).value ?? {};
          for (const wait of Object.values(waits)) await automatic($, state, wait);
        });
      }
    }
    return next(e);
  });
  on("turn.complete", { turnId: /^/ }, async ($, e, next) => {
    const result = await next(e);
    if (e.agentId === undefined) {
      state.turnRunning = false;
      await $.state.set({ plugin: "agent-toolkit", key: "turnRunning" }, false);
      if ((await $.state.get({ plugin: "agent-toolkit", key: "compactRequested" })).value) {
        state.pending = true;
      }
      schedule($, state);
      if (!session.delegated) {
        const waits = (await $.state.get({ plugin: "agent-toolkit", key: "backgroundWaits" })).value ?? {};
        for (const wait of Object.values(waits)) await automatic($, state, wait);
      }
    }
    return result;
  });
  on("session.append", { door: /^(delivery|queue)$/ }, async ($, e, next) => {
    if (e.agentId === undefined) {
      for (const block of e.message.content) {
        if (block.type !== "text" || typeof block.text !== "string") continue;
        for (const notice of block.text.matchAll(/<task-notification>[\s\S]*?<\/task-notification>/g)) {
          const id = notice[0].match(/<task-id>([^<]+)<\/task-id>/)?.[1];
          const status = notice[0].match(/<status>([^<]+)<\/status>/)?.[1];
          if (id && status && ["completed", "failed", "killed"].includes(status)) {
            const waits = (await $.state.get({ plugin: "agent-toolkit", key: "backgroundWaits" })).value ?? {};
            delete waits[id];
            await $.state.set({ plugin: "agent-toolkit", key: "backgroundWaits" }, waits);
          }
        }
      }
    }
    return next(e);
  });
}

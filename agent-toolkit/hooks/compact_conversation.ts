import { pluginLaunchCommand } from "./plugin_launch.ts";
import type { EngineInterface, On } from "claude-code";
import { automatedMessage, startNextTurn } from "./automated_message.ts";

// 実ホストの版・入力・結果と再検証はdocs/development/audit-records-compaction.mdの
// 「agent-toolkit/hooks/compact_conversation.ts：会話圧縮の予約：2026年10月9日」に記録する。
// 圧縮の要約を原本の所在へ置き換える方式の観測と再検証は同資料の
// 「agent-toolkit/hooks/compact_conversation.ts：原本参照圧縮：2026年11月2日」にある。

type CompactState = { pending: boolean; turnRunning: boolean; scheduled: boolean };

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

export function register(on: On): void {
  // modはセッションごとに読み込まれる。予約中だけ状態を保持する。
  const state: CompactState = { pending: false, turnRunning: false, scheduled: false };
  on("session.start", { cwd: /^/ }, async ($, e, next) => {
    const result = await next(e);
    state.turnRunning = (await $.state.get({ plugin: "agent-toolkit", key: "turnRunning" })).value ?? false;
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
  on("turn.start", { turnId: /^/ }, async ($, e, next) => {
    if (e.agentId === undefined) {
      state.turnRunning = true;
      await $.state.set({ plugin: "agent-toolkit", key: "turnRunning" }, true);
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

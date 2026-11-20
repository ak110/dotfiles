import type { EngineInterface, Register } from "claude-code";

import { automatedMessage, startNextTurn } from "./automated_message.ts";
import { register as registerCompactConversation } from "./compact_conversation.ts";
import { exitStatePath, register as registerSessionExit } from "./session_exit.ts";
import { register as registerUsageLimitResume } from "./usage_limit_resume.ts";
import { turnEndAfterDelivery } from "./send_to_user.tsx";
import { register as registerUIRequests } from "./ui_requests.tsx";
import { register as registerRuntimeRestart } from "./runtime_restart.ts";
import { register as registerAgentPanel } from "./agent_panel.ts";
import { register as registerRuntimeChanges } from "./runtime_changes.ts";

// hooks.jsonの`modules`は1つのプラグインにつき1つのhooks moduleだけを受け付け、照合条件の無い同じイベントのhookは
// 1回しか登録できないため、各機能の`session.start`の処理をここへまとめる。

// 委譲先セッションの判定。`agent_toolkit/_common/delegated_session.py`の`is_delegated`と同じ条件を持つ。
// hooks moduleはPythonの関数を呼べないため、同じ条件をここへ置く。
// 2つの環境変数名を読むテスト以外のファイルは本ファイルだけとし、`delegated_session_env_invariant_test.py`が確かめる。
// 条件を変える場合はPython側と両方を変える。
// Codex backendの委譲先は`AGENT_TOOLKIT_DELEGATED_SESSION`を持たないため、`AGENT_TOOLKIT_OWNER_SESSION`も入力とする。
async function isDelegated($: EngineInterface): Promise<boolean> {
  return (await $.env.get("AGENT_TOOLKIT_DELEGATED_SESSION")) === "1" || Boolean(await $.env.get("AGENT_TOOLKIT_OWNER_SESSION"));
}

export const register: Register = (on) => {
  const usageLimitSession = { delegated: true, backgroundChanged: false, processLoop: false };
  // MCPはmodの無い会話にも同じツールを公開する。画面を持つmodが、その会話の説明だけを初期公開する。
  on("tool.describe", { tool: "mcp__plugin_agent-toolkit_agents_server__send_to_user" }, async ($, e, next) => {
    const result = await next(e);
    if (!(await $.session.surfaces()).includes("terminal") || await isDelegated($)) return result;
    return {
      ...result,
      description: "この会話のメインは、ユーザーへの発話（進捗、完了報告、質問、確認依頼を含む）をすべてこのツールのmessageで送る。" +
        turnEndAfterDelivery + "表示先が使えない時は通常の応答本文で伝える。委譲先はこのツールを使わず委譲元へ返す。" + result.description,
      isDeferred: false,
    };
  });
  on("session.start", async ($, e, next) => {
    usageLimitSession.delegated = await isDelegated($);
    usageLimitSession.processLoop = (await $.env.get("AGENT_TOOLKIT_PROCESS_LOOP_SESSION")) === "1";
    // 操作パネルはメイン会話だけへ、最初の発話より前に登録する。
    if (!(await isDelegated($))) {
      await $.command.register({ name: "atk", description: "担当への追送・effort変更・推定費用を表示する", immediate: true });
    }
    const result = await next(e);
    const change = (await $.state.get({ plugin: "agent-toolkit", key: "runtimeChange" })).value;
    if (change?.phase === "running" && !change.notified) {
      await $.state.set({ plugin: "agent-toolkit", key: "runtimeChange" }, { ...change, phase: "done", notified: true });
      $.clock.after(0, async () => {
        await startNextTurn($, automatedMessage("runtime-changes", "notice", "変更反映中にmodが再読込された。消費済み操作は再実行しない。再読込前の応答と、未応答の対象を次の記録で確認して続行せよ。再接続・反映の成功を未確認のまま表示するな。\n" + JSON.stringify(change)));
      });
    }
    // `atk agents-exit-session`の終了要求を受け付ける準備ができたことを示す印を書き、前の要求を消費済みにする。
    const sessionId = await $.session.id();
    const configured = await $.env.get("CLAUDE_CONFIG_DIR");
    const home = (await $.env.get("HOME")) || (await $.env.get("USERPROFILE"));
    const restart = exitStatePath(sessionId, configured, home, "restart");
    if (restart) {
      let waits: { id: string; command: string }[] | undefined;
      try {
        const record: unknown = JSON.parse(await $.fs.read(restart));
        if (record && typeof record === "object" && "phase" in record && record.phase === "pending" && "waits" in record && Array.isArray(record.waits) && record.waits.every((wait) => wait && typeof wait === "object" && typeof wait.id === "string" && typeof wait.command === "string")) waits = record.waits;
      } catch { /* 初回起動と未完の書込みは再開の証拠にしない。 */ }
      if (waits !== undefined && !usageLimitSession.delegated) {
        await $.fs.write(restart, JSON.stringify({ phase: "resumed", waits }));
        $.clock.after(0, async () => {
          await startNextTurn($, automatedMessage("runtime-restart", "notice", "再起動後の会話を再開した。停止済み待機の識別: " + JSON.stringify(waits) + "。保持した作業記録と委譲先の状態を確認し、引数なしのatk agents waitを背景で発行し直して結果を回収せよ。未完了の工程から続けよ。"));
        });
      }
    }
    const marker = exitStatePath(sessionId, configured, home, "marker");
    const request = exitStatePath(sessionId, configured, home, "request");
    const recheck = exitStatePath(sessionId, configured, home, "recheck");
    if (recheck) await $.fs.write(recheck, "consumed");
    if (marker && request) {
      await $.fs.write(request, "consumed");
      await $.fs.write(marker, "ready");
    }
    return result;
  });
  // 前ターンが中断して受渡しを消費できなかった場合も、新ターンへ判定を持ち越さない。
  on("turn.start", { turnId: /^/ }, async ($, e, next) => {
    if (e.agentId === undefined) {
      const configured = await $.env.get("CLAUDE_CONFIG_DIR");
      const home = (await $.env.get("HOME")) || (await $.env.get("USERPROFILE"));
      const path = exitStatePath(await $.session.id(), configured, home, "recheck");
      if (path) await $.fs.write(path, "consumed");
    }
    return next(e);
  });
  registerSessionExit(on);
  registerCompactConversation(on, usageLimitSession);
  registerUIRequests(on, usageLimitSession);
  registerUsageLimitResume(on, usageLimitSession);
  registerRuntimeRestart(on, usageLimitSession);
  registerAgentPanel(on);
  registerRuntimeChanges(on);
};

import type { EngineInterface, Register } from "claude-code";

import { COMPACT_CONVERSATION_TOOL, register as registerCompactConversation } from "./compact_conversation.ts";
import { register as registerPeriodicRecheck } from "./periodic_recheck.ts";
import { register as registerSendToUser, SEND_TO_USER_DELEGATED_DENY, SEND_TO_USER_TOOL } from "./send_to_user.tsx";
import { exitStatePath, register as registerSessionExit } from "./session_exit.ts";

// hooks.jsonの`modules`は1つのプラグインにつき1つのhooks moduleだけを受け付け、照合条件の無い同じイベントのhookは
// 1回しか登録できない。また`$`は別ファイルの関数へ渡せないため、各機能の`session.start`の処理をここへまとめる。

// 委譲先セッションの判定。`agent_toolkit/_common/delegated_session.py`の`is_delegated`と同じ条件を持つ。
// hooks moduleはPythonの関数を呼べず、`$`を受け取る関数を別ファイルへ置けないため、同じ条件をここへ置く。
// 2つの環境変数名を読むテスト以外のファイルは本ファイルだけとし、`delegated_session_env_invariant_test.py`が確かめる。
// 条件を変える場合はPython側と両方を変える。
// Codex backendの委譲先は`AGENT_TOOLKIT_DELEGATED_SESSION`を持たないため、`AGENT_TOOLKIT_OWNER_SESSION`も入力とする。
async function isDelegated($: EngineInterface): Promise<boolean> {
  return (await $.env.get("AGENT_TOOLKIT_DELEGATED_SESSION")) === "1" || Boolean(await $.env.get("AGENT_TOOLKIT_OWNER_SESSION"));
}

// メイン用ツールの名前と、委譲先からの呼び出しへ返す拒否の本文。委譲先の結果は画面ではなく委譲元へ返る。
const MAIN_ONLY_TOOLS: Readonly<Record<string, string>> = {
  [SEND_TO_USER_TOOL.name]: SEND_TO_USER_DELEGATED_DENY,
};

function delegatedDeny(plugin: string, tool: string): string | undefined {
  const prefix = `mcp__${plugin}__`;
  return tool.startsWith(prefix) ? MAIN_ONLY_TOOLS[tool.slice(prefix.length)] : undefined;
}

export const register: Register = (on) => {
  // 同じmoduleの同じイベントでは先に登録したhookが先に判定するため、メイン用ツールの各機能の許可より前に置く。
  // 判定が失敗した場合は従来の各機能の判定へ渡し、メインの報告手段を止めない。
  on("tool.check", { tool: /__send_to_user$/ }, async ($, e, next) => {
    const reason = delegatedDeny($.plugin.name, e.tool);
    return reason !== undefined && (await isDelegated($)) ? { decision: "deny", reason } : next(e);
  }).catch(($, e, next) => next(e));
  on("tool.call", { tool: /__send_to_user$/ }, async ($, e, next) => {
    const reason = delegatedDeny($.plugin.name, e.tool);
    return reason !== undefined && (await isDelegated($)) ? { deny: reason } : next(e);
  }).catch(($, e, next) => next(e));
  on("session.start", async ($, e, next) => {
    // ツールは最初の発話より前に一覧へ載るよう、`next`より前に登録する。
    // send_to_userはメイン用ツールであり、結果本文を委譲元へ返す委譲先には登録しない。
    if (!(await isDelegated($))) await $.tool.register(SEND_TO_USER_TOOL);
    if (!(await $.env.get("AGENT_TOOLKIT_OWNER_SESSION"))) await $.tool.register(COMPACT_CONVERSATION_TOOL);
    const result = await next(e);
    // `atk agents-exit-session`の終了要求を受け付ける準備ができたことを示す印を書き、前の要求を消費済みにする。
    const sessionId = await $.session.id();
    const configured = await $.env.get("CLAUDE_CONFIG_DIR");
    const home = (await $.env.get("HOME")) || (await $.env.get("USERPROFILE"));
    const marker = exitStatePath(sessionId, configured, home, "marker");
    const request = exitStatePath(sessionId, configured, home, "request");
    if (marker && request) {
      await $.fs.write(request, "consumed");
      await $.fs.write(marker, "ready");
    }
    return result;
  });
  registerSessionExit(on);
  registerCompactConversation(on);
  registerSendToUser(on);
  registerPeriodicRecheck(on);
};

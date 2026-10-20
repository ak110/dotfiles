import type { EngineInterface, Register } from "claude-code";

import { automatedMessage } from "./automated_message.ts";
import { COMPACT_CONVERSATION_DELEGATED_DENY, COMPACT_CONVERSATION_TOOL, register as registerCompactConversation } from "./compact_conversation.ts";
import { register as registerPeriodicRecheck } from "./periodic_recheck.ts";
import { register as registerResponseLanguage } from "./response_language.ts";
import { register as registerSendToUser, SEND_TO_USER_DELEGATED_DENY, SEND_TO_USER_TOOL } from "./send_to_user.tsx";
import { exitStatePath, register as registerSessionExit } from "./session_exit.ts";
import { register as registerUsageLimitResume } from "./usage_limit_resume.ts";

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

// 登録と呼出主体の判定は同じ一覧を使う。ここへ追加したツールは全てメイン専用となり、
// 各機能が登録条件やagentIdの判定を重ねる必要はない。
const MAIN_ONLY_TOOLS = [
  { tool: SEND_TO_USER_TOOL, deny: SEND_TO_USER_DELEGATED_DENY },
  { tool: COMPACT_CONVERSATION_TOOL, deny: COMPACT_CONVERSATION_DELEGATED_DENY },
] as const;

function mainOnlyDeny(plugin: string, tool: string): string | undefined {
  const prefix = `mcp__${plugin}__`;
  const deny = tool.startsWith(prefix) ? MAIN_ONLY_TOOLS.find((entry) => entry.tool.name === tool.slice(prefix.length))?.deny : undefined;
  return deny === undefined ? undefined : automatedMessage("main-only-tool", "block", deny);
}

export const register: Register = (on) => {
  const usageLimitSession = { delegated: true };
  // ツール名のmatcherを別に列挙せず、登録一覧へ追加した全ツールに同じ判定を適用する。
  on("tool.check", { tool: /^mcp__/ }, async ($, e, next) => {
    const reason = mainOnlyDeny($.plugin.name, e.tool);
    if (reason === undefined) return next(e);
    return e.agentId !== undefined || (await isDelegated($)) ? { decision: "deny", reason } : { decision: "allow" };
  }).catch(($, e, next) => next(e));
  on("tool.call", { tool: /^mcp__/ }, async ($, e, next) => {
    const reason = mainOnlyDeny($.plugin.name, e.tool);
    return reason !== undefined && (e.agentId !== undefined || (await isDelegated($))) ? { deny: reason } : next(e);
  }).catch(($, e, next) => next(e));
  on("session.start", async ($, e, next) => {
    usageLimitSession.delegated = await isDelegated($);
    // ツールは最初の発話より前に一覧へ載るよう、`next`より前に登録する。
    // 登録一覧のツールはメイン用であり、委譲先には登録しない。
    if (!(await isDelegated($))) {
      for (const { tool } of MAIN_ONLY_TOOLS) await $.tool.register(tool);
    }
    const result = await next(e);
    // `atk agents-exit-session`の終了要求を受け付ける準備ができたことを示す印を書き、前の要求を消費済みにする。
    const sessionId = await $.session.id();
    const configured = await $.env.get("CLAUDE_CONFIG_DIR");
    const home = (await $.env.get("HOME")) || (await $.env.get("USERPROFILE"));
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
  registerCompactConversation(on);
  registerSendToUser(on);
  registerResponseLanguage(on);
  registerPeriodicRecheck(on);
  registerUsageLimitResume(on, usageLimitSession);
};

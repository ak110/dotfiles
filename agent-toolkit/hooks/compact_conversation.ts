import type { On } from "claude-code";

const TOOL_NAME = "compact_conversation";

// 実ホストの版・入力・結果と再検証はdocs/development/audit-records.mdの
// 「agent-toolkit/hooks/compact_conversation.ts：会話圧縮の予約：2026年10月9日」に記録する。

export const COMPACT_CONVERSATION_TOOL = {
  name: TOOL_NAME,
  description:
    "Claude Codeメインの会話圧縮を予約する。instructionsは任意の圧縮指示。受付は完了ではない。予約後は現在のターンを終え、ホストの圧縮結果を待つ。未完了の予約は重ねず、完了または失敗後は再予約できる。",
  inputSchema: {
    type: "object",
    properties: { instructions: { type: "string", description: "圧縮後に保持する情報と再開のための指示。" } },
    additionalProperties: false,
  },
  isDeferred: false,
};

export function register(on: On): void {
  // modはセッションごとに読み込まれる。予約中だけ状態を保持し、永続予約や解除入口は持たない。
  let pending = false;
  on("tool.check", { tool: /__compact_conversation$/ }, async ($, e, next) => {
    if (e.tool !== `mcp__${$.plugin.name}__${TOOL_NAME}`) return next(e);
    return e.agentId === undefined && !(await $.env.get("AGENT_TOOLKIT_OWNER_SESSION")) ? { decision: "allow" } : { decision: "deny", reason: "メインの会話で使うツール。" };
  });
  on("tool.call", { tool: /__compact_conversation$/ }, async ($, e, next) => {
    if (e.tool !== `mcp__${$.plugin.name}__${TOOL_NAME}`) return next(e);
    if (e.agentId !== undefined || (await $.env.get("AGENT_TOOLKIT_OWNER_SESSION"))) return { deny: "メインの会話で圧縮を予約する。" };
    const instructions = "instructions" in e ? e.instructions : undefined;
    if (instructions !== undefined && typeof instructions !== "string") {
      return { deny: "instructionsには文字列を指定するか、省略する。圧縮は予約していない。" };
    }
    if (pending) return { result: "未完了の圧縮予約があるため追加していない。現在のターンを終えてホストの結果を待つ。" };
    pending = true;
    // command.runはhook内で拒否されるため、hookが応答した後のタイマーから呼ぶ。
    $.clock.after(0, async () => {
      try {
        await $.command.run({ command: "compact", ...(instructions === undefined ? {} : { args: instructions }) });
        pending = false;
      } catch (error) {
        pending = false;
        await $.prompt.submit({ text: `会話圧縮の予約実行に失敗した。予約状態は解除済みで再予約できる。原因: ${String(error)}` });
      }
    });
    return { result: "会話圧縮を予約した。まだ完了していない。現在のターンを終えてホストの圧縮結果を待つ。" };
  });
}

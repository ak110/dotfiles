import type { On, ProcessRunResult, SessionMessage } from "claude-code";
import { automatedMessage } from "./automated_message.ts";

// Function hooksから同じPythonの判定CLIを起動する。本文は単一の値引数として渡す。
export function languageCheckCommand(root: string, text: string): string[] {
  return ["uv", "run", "--no-config", "--no-project", "--python", "3.15", "python", `${root}/scripts/launch.py`, "agent_toolkit/_common/response_language_check.py", `--text=${text}`];
}

export function languageCheckFailure(diagnostic: string): { deny: string } {
  return { deny: automatedMessage("response-language", "block", `記述言語を確認できず、操作していない。診断: ${diagnostic}。` +
    "同じ呼び出しを繰り返さず、この診断をagent-toolkitの不具合としてユーザーへ報告する。") };
}

export function languageCheckResult(root: string, checked: ProcessRunResult): { deny: string } | undefined {
  const checker = `${root}/agent_toolkit/_common/response_language_check.py`;
  if (checked.exitCode !== 0 || checked.isStdoutTruncated || checked.isStderrTruncated) {
    return languageCheckFailure(`検査スクリプト ${checker}、終了コード ${checked.exitCode}、標準出力の切り詰め ${checked.isStdoutTruncated}、標準エラーの切り詰め ${checked.isStderrTruncated}`);
  }
  let parsed: unknown;
  try { parsed = JSON.parse(checked.stdout); }
  catch { return languageCheckFailure(`検査スクリプト ${checker}、終了コード ${checked.exitCode}、標準出力がJSONとして不正`); }
  if (parsed === null || typeof parsed !== "object") return languageCheckFailure(`検査スクリプト ${checker}、判定結果がobjectではない`);
  const language = parsed as { outcome?: unknown; body?: unknown; fix?: unknown };
  if (!["warn", "pass", "skip"].includes(String(language.outcome)) || typeof language.fix !== "string" || (language.body !== null && typeof language.body !== "string")) {
    return languageCheckFailure(`検査スクリプト ${checker}、判定結果のoutcome・body・fixが契約外`);
  }
  return language.outcome === "warn" ? { deny: automatedMessage("response-language", "block", `${language.body ?? "英語主体の本文を検出した。"}${language.fix}`) } : undefined;
}

// 直近の可視本文だけを取得する。本文の無い呼出は遡り、send_to_userの入力も表示する本文として扱う。
export function latestVisibleText(messages: readonly SessionMessage[], sendTool: string): string | undefined {
  for (let index = messages.length - 1; index >= 0; index--) {
    const message = messages[index]!;
    if (message.role !== "assistant") continue;
    for (let toolIndex = message.toolUses.length - 1; toolIndex >= 0; toolIndex--) {
      const call = message.toolUses[toolIndex]!;
      if (call.tool === sendTool && typeof call.input.message === "string" && call.input.message.trim()) return call.input.message;
    }
    if (message.text.trim()) return message.text;
  }
  return undefined;
}

export function register(on: On): void {
  // modの登録ごとに直前の通過本文だけを保持し、セッションIDが変われば再判定する。
  let lastPassed: { sessionId: string; text: string } | undefined;
  on("tool.call", { tool: /^/ }, async ($, e, next) => {
    // mod内部の呼出はモデルの応答に属さない。send_to_userは直接入力、サブエージェントはSubagentStopが判定する。
    const sendTool = `mcp__${$.plugin.name}__send_to_user`;
    if (next.origin.plugin !== "engine" || e.agentId !== undefined || e.tool === sendTool) return next(e);
    const previous = lastPassed;
    // 本文取得や判定が失敗した場合も、それ以前の通過結果を次へ持ち越さない。
    lastPassed = undefined;
    let text: string | undefined;
    let sessionId: string;
    try {
      text = latestVisibleText(await $.session.messages(), sendTool);
      if (text === undefined) return next(e);
      sessionId = await $.session.id();
    } catch { return languageCheckFailure("応答本文またはセッションIDの取得で例外が発生"); }
    if (previous?.sessionId === sessionId && previous.text === text) {
      lastPassed = previous;
      return next(e);
    }
    let checked;
    try { checked = await $.process.run(languageCheckCommand($.plugin.root, text), { timeoutMs: 30_000 }); }
    catch { return languageCheckFailure("言語判定のprocess.runで例外が発生"); }
    const denied = languageCheckResult($.plugin.root, checked);
    if (denied === undefined) lastPassed = { sessionId, text };
    return denied ?? next(e);
  }).catch(() => languageCheckFailure("応答本文の取得または言語判定hookで例外が発生"));
}

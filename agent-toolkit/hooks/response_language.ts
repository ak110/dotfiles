import type { ProcessRunResult, ToolCallResult } from "claude-code";
import { automatedMessage } from "./automated_message.ts";
import { pluginLaunchCommand } from "./plugin_launch.ts";

// Function hooksから同じPythonの判定CLIを起動する。本文は単一の値引数として渡す。
export function languageCheckCommand(root: string, text: string): string[] {
  return pluginLaunchCommand(root, "agent_toolkit/_common/response_language_check.py", [`--text=${text}`]);
}

// 判定結果の区分。英語主体の検出だけを拒否とし、判定基盤の障害（判定不能）は元の操作を止めずに診断だけを届ける。
// 判定不能を拒否へ変換すると、日本語へ書き直しても解消しない障害で全ての操作と表示が止まるためである。
export type LanguageCheck = { kind: "violation"; deny: string } | { kind: "passed" } | { kind: "unavailable"; diagnostic: string };

export function languageCheckUnavailable(diagnostic: string): LanguageCheck {
  return { kind: "unavailable", diagnostic };
}

// 判定不能の診断を元の操作の結果の後に読む文脈として返す。入力本文と判定プロセスの出力は載せない。
export function languageCheckNotice(diagnostic: string): string {
  return automatedMessage("response-language", "warn", `記述言語を判定できず、元の操作を続けた。診断: ${diagnostic}。\n` +
    "次の操作: 元の操作は再発行するな。同じ障害が続く場合は、この診断をagent-toolkitの不具合としてユーザーへ報告せよ。");
}

export function languageCheckResult(root: string, checked: ProcessRunResult): LanguageCheck {
  const checker = `${root}/agent_toolkit/_common/response_language_check.py`;
  if (checked.exitCode !== 0 || checked.isStdoutTruncated || checked.isStderrTruncated) {
    return languageCheckUnavailable(`検査スクリプト ${checker}、終了コード ${checked.exitCode}、標準出力の切り詰め ${checked.isStdoutTruncated}、標準エラーの切り詰め ${checked.isStderrTruncated}`);
  }
  let parsed: unknown;
  try { parsed = JSON.parse(checked.stdout); }
  catch { return languageCheckUnavailable(`検査スクリプト ${checker}、終了コード ${checked.exitCode}、標準出力がJSONとして不正`); }
  if (parsed === null || typeof parsed !== "object") return languageCheckUnavailable(`検査スクリプト ${checker}、判定結果がobjectではない`);
  const language = parsed as { outcome?: unknown; body?: unknown; fix?: unknown };
  if (!["warn", "pass", "skip"].includes(String(language.outcome)) || typeof language.fix !== "string" || (language.body !== null && typeof language.body !== "string")) {
    return languageCheckUnavailable(`検査スクリプト ${checker}、判定結果のoutcome・body・fixが契約外`);
  }
  return language.outcome === "warn"
    ? { kind: "violation", deny: automatedMessage("response-language", "block", `${language.body ?? "英語主体の本文を検出した。"}${language.fix}`) }
    : { kind: "passed" };
}

// 判定不能の診断を会話ごとに1回だけ届ける。同じ会話で同じ診断が続く間は`undefined`を返し、
// 判定に成功した場合と診断が変わった場合は再び届ける。会話を識別できない場合は抑止しない。
export function deliverOnce(delivered: Map<string, string>, sessionId: string | undefined, check: LanguageCheck): string | undefined {
  if (check.kind !== "unavailable") {
    if (sessionId !== undefined) delivered.delete(sessionId);
    return undefined;
  }
  if (sessionId === undefined) return languageCheckNotice(check.diagnostic);
  if (delivered.get(sessionId) === check.diagnostic) return undefined;
  delivered.set(sessionId, check.diagnostic);
  return languageCheckNotice(check.diagnostic);
}

// 結果の後にモデルが読む文脈へ診断を加える。拒否の結果は文脈を持てないため、そのまま返す。
export function withNotice(result: ToolCallResult, note: string | undefined): ToolCallResult {
  if (note === undefined || result.deny !== undefined) return result;
  return { ...result, context: [...(result.context ?? []), note] };
}

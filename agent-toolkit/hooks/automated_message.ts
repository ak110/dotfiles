import type { EngineInterface } from "claude-code";

// modが会話へ渡す本文を`atk-auto`要素で囲む共通処理。本文自身は変更せず、属性値だけをXMLの文字参照へ置き換える。
function attribute(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll("\r", "&#13;").replaceAll("\n", "&#10;").replaceAll("\t", "&#9;");
}

export function automatedMessage(source: string, kind: string, body: string): string {
  return `<atk-auto source="${attribute(source)}" kind="${attribute(kind)}">\n${body}\n</atk-auto>`;
}

// 本文を次のターンの入力として会話へ投入する。Claude Codeはpluginのpromptを会話がidleになってから実行するため、
// 実行中のターンには届かず、ターンの終了後にこの本文だけで新しいターンが始まる。
// 実行中のターンへ届ける本文は、hookの要求への応答（`ui_requests.tsx`の`reason`）に載せ、hookがツール結果へ添える。
export async function startNextTurn($: EngineInterface, text: string): Promise<void> {
  await $.prompt.submit({ text });
}

// 同じ会話で続く同一通知だけを省き、解消・内容変更・別会話の最初の通知を届ける。
export function changedWarning(history: Map<string, string>, sessionId: string, note: string | undefined): string | undefined {
  if (note === undefined) {
    history.delete(sessionId);
    return undefined;
  }
  if (history.get(sessionId) === note) return undefined;
  history.set(sessionId, note);
  return note;
}

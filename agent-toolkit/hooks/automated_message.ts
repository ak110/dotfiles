// modが会話へ渡す本文を`atk-auto`要素で囲む共通処理。本文自身は変更せず、属性値だけをXMLの文字参照へ置き換える。
function attribute(value: string): string {
  return value.replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll("\r", "&#13;").replaceAll("\n", "&#10;").replaceAll("\t", "&#9;");
}

export function automatedMessage(source: string, kind: string, body: string): string {
  return `<atk-auto source="${attribute(source)}" kind="${attribute(kind)}">\n${body}\n</atk-auto>`;
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
